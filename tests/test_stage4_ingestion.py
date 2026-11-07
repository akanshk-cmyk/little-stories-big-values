"""Synthetic large-source extraction, chunk provenance and review controls."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from backend.contracts.common import parse_payload, text_hash
from backend.ingestion.processing import page_record, detect_boundaries, build_chunks
from backend.ingestion.workspace import SourceWorkspace, PassageReview, MAX_PDF_BYTES


def pdf_bytes(texts):
    writer=PdfWriter()
    for text in texts:
        page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 20 700 Td ('+escaped+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    output=BytesIO();writer.write(output);return output.getvalue()


class ChunkingTests(unittest.TestCase):
    def test_raw_text_preserved_and_missing_printed_label_not_invented(self):
        raw='  BOOK I  \r\n\tA gentle   story.\x00'
        page=page_record(1,raw)
        self.assertEqual(page['raw_text'],raw)
        self.assertEqual(page['clean_text'],'BOOK I\nA gentle story.')
        self.assertIsNone(page['printed_page_label'])

    def test_structure_boundary_coverage_overlap_and_offsets(self):
        pages=[page_record(1,'BOOK I\nCANTO I\n'+' '.join('word'+str(i) for i in range(180))),
               page_record(2,' '.join('next'+str(i) for i in range(100))+'\nCANTO II\nA new episode.'),page_record(3,'')]
        boundaries=detect_boundaries(pages)
        sections,chunks,audit=build_chunks(pages,boundaries,snapshot_id='snapshot1',generation=1,target=60,overlap=10)
        self.assertTrue(audit['coverage_pass'])
        self.assertEqual(audit['empty_pdf_pages'],[3])
        text=''.join(p['clean_text']+'\n\n' for p in pages)
        for chunk in chunks:
            self.assertEqual(chunk['text'],text[chunk['start_offset']:chunk['end_offset']])
            self.assertEqual(chunk['content_hash'],text_hash(chunk['text']))
            self.assertLessEqual(chunk['word_count'],60)
            self.assertEqual(chunk['embedding_token_check'],'PENDING')
            section=next(s for s in sections if s['section_id']==chunk['parent_section_id'])
            self.assertGreaterEqual(chunk['start_offset'],section['start_offset'])
            self.assertLessEqual(chunk['end_offset'],section['end_offset'])
            for span in chunk['page_spans']:
                page=pages[span['pdf_page']-1]
                self.assertIn(page['clean_text'][span['clean_start']:span['clean_end']],chunk['text'])
        self.assertGreater(audit['total_chunk_words'],audit['unique_words_covered'])
        self.assertTrue(any(len(c['pdf_pages'])>1 for c in chunks))

    def test_header_candidates_are_flagged_not_silently_removed(self):
        pages=[page_record(i,'Repeated header\nStory '+str(i)+'\nRepeated footer') for i in range(1,5)]
        _,_,audit=build_chunks(pages,[],snapshot_id='s1',generation=1)
        self.assertEqual(audit['automatic_header_removals'],0)
        self.assertEqual(len(audit['repeated_edge_candidates']),2)

    def test_invalid_or_duplicate_boundaries_rejected(self):
        pages=[page_record(1,'Chapter one\nA story.')]
        for boundaries in [[dict(pdf_page=2,line=1,title='Missing')],[dict(pdf_page=True,line=1,title='Bad')],
            [dict(pdf_page=1,line=9,title='Missing')],[dict(pdf_page=1,line=1,title='a')]*2]:
            with self.assertRaises(ValueError):build_chunks(pages,boundaries,snapshot_id='s1',generation=1)

    def test_heading_word_is_not_mistaken_for_roman_numeral(self):
        self.assertEqual(detect_boundaries([page_record(1,'CANTO LIGHTHOUSE')]),[])

    def test_contents_and_running_headings_do_not_create_duplicate_sections(self):
        pages=[page_record(1,'BOOK I\nCANTO I\nCANTO II\nCANTO III\nCANTO IV'),
               page_record(2,'BOOK I\nCANTO I\nA story begins.'),
               page_record(3,'CANTO I\nThe story continues.'),
               page_record(4,'CANTO II\nAnother episode.')]
        boundaries=detect_boundaries(pages)
        self.assertEqual([(b['pdf_page'],b['kind'],b['label']) for b in boundaries],[(2,'book','I'),(2,'canto','I'),(4,'canto','II')])
        sections,chunks,audit=build_chunks(pages,boundaries,snapshot_id='s1',generation=1)
        self.assertTrue(audit['coverage_pass'])
        self.assertEqual(audit['possible_contents_pages'],[1])
        self.assertEqual(audit['raw_heading_candidate_count'],9)

    def test_edition_and_generation_change_chunk_identity(self):
        pages=[page_record(1,'A gentle example.')]
        first=build_chunks(pages,[],snapshot_id='edition1',generation=1)[1][0]['chunk_id']
        for edition,generation in [('edition2',1),('edition1',2)]:
            self.assertNotEqual(first,build_chunks(pages,[],snapshot_id=edition,generation=generation)[1][0]['chunk_id'])


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=SourceWorkspace(self.temp.name);self.addCleanup(self.workspace.close)

    def upload(self,texts=None,edition='Unverified edition'):
        raw=pdf_bytes(texts or ['A gentle story about sharing.','Another page about kindness.'])
        source=self.workspace.create(BytesIO(raw),len(raw),owner='parent1',title='Synthetic source',edition=edition)
        return source,raw

    def ready(self):
        source,_=self.upload();self.workspace.process(source,'parent1')
        return source

    def confirm(self,source):
        summary=self.workspace.summary(source,'parent1')
        return self.workspace.boundaries(source,'parent1',summary['generation'],summary['metadata']['boundaries'])

    def command(self,source,**updates):
        chunk=self.workspace.chunks(source,'parent1')[0]
        summary=self.workspace.summary(source,'parent1')
        return PassageReview(**(dict(expected_generation=summary['generation'],chunk_id=chunk['chunk_id'],
            content_hash=chunk['content_hash'],action='allow',extraction_checked=True,cultural_context_checked=True,
            child_suitability_checked=True,rights_permitted=True,rights_basis='Synthetic fixture owned by this test.',
            notes='Synthetic review, not cultural certification.')|updates))

    def test_original_is_immutable_and_duplicate_upload_reuses_source(self):
        source,raw=self.upload();duplicate,_=self.upload()
        self.assertEqual(source,duplicate)
        self.assertEqual(self.workspace.original(source,'parent1').read_bytes(),raw)
        with self.assertRaises(PermissionError):self.workspace.original(source,'parent2')
        self.assertEqual(self.workspace.list('parent2'),[])

    def test_async_processing_preserves_page_and_edition_metadata(self):
        source,_=self.upload()
        summary=self.workspace.submit(source,'parent1').result(timeout=10)
        self.assertEqual(summary['status'],'ready')
        self.assertEqual(summary['extracted_pages'],2)
        chunk=self.workspace.chunks(source,'parent1')[0]
        self.assertEqual(chunk['edition_id'],summary['edition_id'])
        self.assertEqual(chunk['source_snapshot_id'],summary['snapshot_id'])
        self.assertEqual(chunk['eligibility_status'],'blocked')
        self.assertFalse(summary['traditional_claims_eligible'])

    def test_page_batch_resume_does_not_reextract_saved_pages(self):
        source,_=self.upload(['First page.','Second page.','Third page.'])
        original=self.workspace._save_pages;calls=[]
        def stop_after_batch(*args):
            original(*args);raise RuntimeError('Interrupted after page checkpoint')
        with patch.object(self.workspace,'_save_pages',side_effect=stop_after_batch):
            with self.assertRaises(RuntimeError):self.workspace.process(source,'parent1',batch_size=1)
        self.assertEqual(self.workspace.summary(source,'parent1')['extracted_pages'],1)
        from pypdf._page import PageObject
        extract=PageObject.extract_text
        def count(page,*args,**kwargs):calls.append(page);return extract(page,*args,**kwargs)
        with patch.object(PageObject,'extract_text',count):self.workspace.process(source,'parent1',batch_size=1)
        self.assertEqual(len(calls),2)
        self.assertEqual(self.workspace.summary(source,'parent1')['status'],'ready')

    def test_scan_only_source_stays_failed_and_unapproved(self):
        source,_=self.upload([''])
        with self.assertRaisesRegex(ValueError,'OCR'):self.workspace.process(source,'parent1')
        self.assertEqual(self.workspace.summary(source,'parent1')['status'],'failed')

    def test_review_requires_confirmed_boundaries_and_exact_hash(self):
        source=self.ready()
        with self.assertRaises(ValueError):self.workspace.review(source,'parent1',self.command(source))
        self.confirm(source)
        with self.assertRaises(ValueError):self.workspace.review(source,'parent1',self.command(source,content_hash='a'*64))
        for change in [dict(extraction_checked=False),dict(cultural_context_checked=False),
            dict(child_suitability_checked=False),dict(rights_permitted=False),dict(rights_basis=None)]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.workspace.review(source,'parent1',self.command(source,**change))

    def test_parent_allow_never_grants_traditional_or_external_permission(self):
        source=self.ready();self.confirm(source)
        result=self.workspace.review(source,'parent1',self.command(source))
        self.assertEqual(result['eligibility_status'],'eligible_parent_inspiration')
        self.assertFalse(result['external_processing_permitted'])
        self.assertFalse(result['traditional_claims_eligible'])
        self.assertEqual(result['review']['authority'],'parent')
        self.assertEqual(self.workspace.eligibility(source,'parent1',self.command(source).chunk_id,purpose='traditional')['eligibility_status'],'blocked')

    def test_withdrawal_changes_epoch_and_preserves_history(self):
        source=self.ready();self.confirm(source)
        allowed=self.workspace.review(source,'parent1',self.command(source))
        blocked=self.workspace.review(source,'parent1',self.command(source,action='withdraw'))
        self.assertEqual(blocked['eligibility_status'],'blocked')
        self.assertGreater(blocked['eligibility_epoch'],allowed['eligibility_epoch'])
        with self.workspace.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM source_reviews').fetchone()[0],2)

    def test_new_boundaries_invalidate_old_reviews_without_deleting_chunks(self):
        source=self.ready();self.confirm(source)
        old=self.command(source);self.workspace.review(source,'parent1',old)
        summary=self.workspace.summary(source,'parent1');old_count=summary['chunk_count']
        self.workspace.boundaries(source,'parent1',summary['generation'],[])
        self.assertEqual(self.workspace.chunks(source,'parent1')[0]['eligibility_status'],'blocked')
        with self.assertRaises(ValueError):self.workspace.review(source,'parent1',old)
        with self.workspace.db() as db:
            count=db.execute('SELECT COUNT(*) FROM chunks WHERE source_id=? AND generation=?',(source,summary['generation'])).fetchone()[0]
        self.assertEqual(count,old_count)

    def test_edition_change_does_not_reuse_chunk_ids(self):
        first=self.ready();second,_=self.upload(edition='Another unverified edition')
        self.workspace.process(second,'parent1')
        self.assertNotEqual(self.workspace.chunks(first,'parent1')[0]['chunk_id'],self.workspace.chunks(second,'parent1')[0]['chunk_id'])

    def test_oversized_invalid_and_short_uploads_rejected(self):
        for content,size in [(b'%PDF-',MAX_PDF_BYTES+1),(b'not a PDF',9),(b'%PDF-',100)]:
            with self.assertRaises(ValueError):self.workspace.create(BytesIO(content),size,owner='p1',title='Book',edition='Unknown')

    def test_parent_cannot_forge_qualified_reviewer_field(self):
        source=self.ready();self.confirm(source)
        command=self.command(source).model_dump(mode='json')|{'authority':'qualified'}
        with self.assertRaises(ValueError):parse_payload(PassageReview,command)

    def test_modified_original_and_page_fail_integrity_checks(self):
        source=self.ready()
        path=self.workspace.original(source,'parent1')
        path.write_bytes(b'Changed original')
        with self.assertRaisesRegex(ValueError,'integrity'):self.workspace.original(source,'parent1')
        with self.workspace.db() as db:
            payload=json.loads(db.execute('SELECT payload FROM pages WHERE source_id=? AND page=1',(source,)).fetchone()[0])
            payload['clean_text']='Changed text'
            db.execute('UPDATE pages SET payload=? WHERE source_id=? AND page=1',(json.dumps(payload),source))
        with self.assertRaisesRegex(ValueError,'integrity'):self.workspace.page(source,'parent1',1)

    def test_modified_passage_cannot_retain_eligibility(self):
        source=self.ready();self.confirm(source)
        command=self.command(source);self.workspace.review(source,'parent1',command)
        with self.workspace.db() as db:
            row=db.execute('SELECT payload FROM chunks WHERE source_id=? AND id=?',(source,command.chunk_id)).fetchone()
            payload=json.loads(row[0]);payload['text']='Modified passage'
            db.execute('UPDATE chunks SET payload=? WHERE source_id=? AND id=?',(json.dumps(payload),source,command.chunk_id))
        self.assertEqual(self.workspace.eligibility(source,'parent1',command.chunk_id)['eligibility_status'],'blocked')
        with self.assertRaisesRegex(ValueError,'integrity'):self.workspace.review(source,'parent1',command)
