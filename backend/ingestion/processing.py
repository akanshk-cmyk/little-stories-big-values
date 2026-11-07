"""Conservative normalization and structure-aware candidate chunks.

No tokenizer or embeddings are loaded. Candidates cannot be published to an
embedding index until that index's exact tokenizer has checked their limits.
"""
from collections import Counter
import re
from backend.contracts.common import content_hash, text_hash

NORMALIZATION_VERSION = 'preserve-lines-v1'
CHUNKER_VERSION = 'structure-word-candidate-v2'
HEADING = re.compile(r'^(BOOK|CANTO|CHAPTER)\s+([IVXLCDM]+|\d+)(?=\s|[.:\-–—]|$)\.?\s*(?:[:.\-–—]?\s*.*)?$',re.I)


def normalize(raw):
    # Preserve line boundaries and exact raw extraction separately. Never strip
    # recurring headers/footers without a reviewer-confirmed rule.
    return '\n'.join(re.sub(r'[\t \r\f\v]+',' ',line.replace('\x00','')).strip()
                     for line in raw.split('\n')).strip()


def page_record(number, raw):
    clean = normalize(raw)
    return dict(pdf_page=number,printed_page_label=None,raw_text=raw,clean_text=clean,
                raw_hash=text_hash(raw),clean_hash=text_hash(clean),word_count=len(clean.split()),
                normalization_version=NORMALIZATION_VERSION,
                quality_flags=['empty_page_requires_inspection'] if not clean else
                    (['replacement_character'] if '\ufffd' in clean else []))


def boundary_candidates(pages):
    boundaries = []
    for page in pages:
        offset = 0
        for number,line in enumerate(page['clean_text'].split('\n'),1):
            match = HEADING.fullmatch(line.strip())
            if match:
                boundaries.append(dict(pdf_page=page['pdf_page'],line=number,title=line.strip(),
                    kind=match.group(1).lower(),label=match.group(2),confidence='candidate',
                    clean_offset=offset))
            offset += len(line)+1
    return boundaries


def detect_boundaries(pages):
    candidates=boundary_candidates(pages)
    per_page=Counter(b['pdf_page'] for b in candidates)
    # Dense heading lists are likely contents pages, not proof of episode starts.
    # Text is retained unchanged; all inferred boundaries still need review.
    possible_contents={page for page,count in per_page.items() if count>=4}
    active={};result=[]
    for boundary in candidates:
        if boundary['pdf_page'] in possible_contents:continue
        kind,label=boundary['kind'],boundary['label'].upper()
        if active.get(kind)==label:continue
        if kind=='book':active={}
        elif kind=='canto':active.pop('chapter',None)
        active[kind]=label
        result.append(boundary)
    return result


def validate_boundaries(pages, boundaries):
    by_page = {p['pdf_page']:p for p in pages}
    seen = set()
    result = []
    for item in boundaries:
        if set(item) - {'pdf_page','line','title','kind','label','confidence','clean_offset'}:
            raise ValueError('Unknown boundary fields.')
        number,line = item['pdf_page'],item['line']
        if type(number) is not int or type(line) is not int or number not in by_page or line < 1:
            raise ValueError('Invalid page/line boundary.')
        lines = by_page[number]['clean_text'].split('\n')
        if line > len(lines) or (number,line) in seen:
            raise ValueError('Duplicate or missing boundary location.')
        title = item['title']
        if not isinstance(title,str) or not title.strip() or len(title)>240:
            raise ValueError('A short boundary title is required.')
        kind = item.get('kind','section')
        if kind not in ('book','canto','chapter','section'):
            raise ValueError('Unsupported boundary type.')
        label = item.get('label')
        if label is not None and (not isinstance(label,str) or len(label)>80):
            raise ValueError('Invalid boundary label.')
        seen.add((number,line))
        result.append(dict(pdf_page=number,line=line,title=title.strip(),kind=kind,label=label,
            confidence=item.get('confidence','reviewed'),clean_offset=sum(len(v)+1 for v in lines[:line-1])))
    return sorted(result,key=lambda b:(b['pdf_page'],b['line']))


def build_chunks(pages, boundaries, *, snapshot_id, generation, target=300, overlap=50):
    if type(target) is not int or type(overlap) is not int or not 50<=target<=600 or not 0<=overlap<target//2:
        raise ValueError('Choose 50–600 target words and overlap smaller than half the target.')
    boundaries = validate_boundaries(pages,boundaries)
    page_starts = {}; text = ''; spans = []
    for page in pages:
        page_starts[page['pdf_page']] = len(text)
        start = len(text)
        text += page['clean_text']
        spans.append((start,len(text),page['pdf_page']))
        text += '\n\n'
    positions = [(page_starts[b['pdf_page']]+b['clean_offset'],b) for b in boundaries]
    if not positions or positions[0][0] != 0:
        positions.insert(0,(0,dict(title='Unassigned front matter',kind='section',label=None,
                                  confidence='unconfirmed',pdf_page=pages[0]['pdf_page'],line=1)))
    sections = []; chunks = []; book = None; canto = None
    covered = set()
    for index,(start,boundary) in enumerate(positions):
        end = positions[index+1][0] if index+1<len(positions) else len(text)
        if boundary['kind']=='book':book=boundary['title'];canto=None
        if boundary['kind']=='canto':canto=boundary['title']
        section_id='section-'+content_hash([snapshot_id,generation,start,end])[:24]
        section=dict(section_id=section_id,title=boundary['title'],book=book,canto=canto,
            chapter_or_section=boundary['title'],start_offset=start,end_offset=end,
            boundary_status=boundary['confidence'],pdf_page=boundary['pdf_page'],line=boundary['line'])
        sections.append(section)
        words=list(re.finditer(r'\S+',text[start:end]))
        offset=0
        while offset<len(words):
            stop=min(offset+target,len(words))
            # Prefer a sentence/verse-line end in the last 20% of the window.
            if stop<len(words):
                for candidate in range(stop,max(offset+1,stop-max(1,target//5)),-1):
                    word=words[candidate-1]
                    gap=text[start+word.end():start+words[candidate].start()]
                    if word.group().endswith(('.', '?', '!', ';', ':')) or '\n' in gap:
                        stop=candidate;break
            first=start+words[offset].start();last=start+words[stop-1].end()
            chunk_text=text[first:last]
            page_spans=[]
            for pstart,pend,page_number in spans:
                left,right=max(first,pstart),min(last,pend)
                if left<right:
                    page_spans.append(dict(pdf_page=page_number,clean_start=left-pstart,clean_end=right-pstart))
            chunk_id='chunk-'+content_hash([snapshot_id,generation,section_id,first,last,chunk_text])[:24]
            chunks.append(dict(chunk_id=chunk_id,parent_section_id=section_id,text=chunk_text,
                content_hash=text_hash(chunk_text),pdf_pages=[s['pdf_page'] for s in page_spans],
                page_spans=page_spans,printed_page_labels=None,start_offset=first,end_offset=last,
                start_word=offset,end_word=stop,word_count=stop-offset,book=book,canto=canto,
                chapter_or_section=boundary['title'],chunker_version=CHUNKER_VERSION,
                normalization_version=NORMALIZATION_VERSION,embedding_token_check='PENDING',
                reranker_token_check='PENDING',index_version=None))
            covered.update((section_id,n) for n in range(offset,stop))
            if stop==len(words):break
            offset=max(offset+1,stop-overlap)
    expected=sum(len(re.findall(r'\S+',text[s['start_offset']:s['end_offset']])) for s in sections)
    edges=Counter()
    for page in pages:
        lines=[v for v in page['clean_text'].split('\n') if v.strip()]
        if lines:
            edges.update(set((lines[0],lines[-1])))
    repetitive=[dict(text=line,page_count=count) for line,count in edges.items()
                if count>=max(3,len(pages)//3)][:100]
    audit=dict(unique_words_covered=len(covered),expected_words=expected,
        coverage_pass=len(covered)==expected,total_chunk_words=sum(c['word_count'] for c in chunks),
        repeated_edge_candidates=repetitive,automatic_header_removals=0,
        empty_pdf_pages=[p['pdf_page'] for p in pages if not p['clean_text']],
        boundary_count=len(boundaries),boundary_confirmation='required',
        tokenizer_validation='PENDING; not eligible for embedding publication')
    candidates=boundary_candidates(pages)
    heading_counts=Counter(b['pdf_page'] for b in candidates)
    audit.update(raw_heading_candidate_count=len(candidates),
        possible_contents_pages=[page for page,count in heading_counts.items() if count>=4],
        automatic_boundary_count=len(detect_boundaries(pages)),
        heading_rule='Dense heading lists and repeated active heading labels are not episode boundaries; reviewer confirmation required.')
    return sections,chunks,audit
