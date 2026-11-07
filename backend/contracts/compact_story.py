"""Compact text-only model output; application supplies immutable identities."""
import re
from pydantic import Field,model_validator
from .common import Contract,Text,Identifier,content_hash
from .stories import StoryDraft,StoryPage,ClaimCitation

class CompactStoryPage(Contract):
    text:Text
    character_ids:tuple[Identifier,...]

class CompactStoryCitation(Contract):
    page_number:int=Field(ge=1,le=6)
    claim_text:Text=Field(max_length=120)
    source_quote_id:Identifier

class CompactStory(Contract):
    title:Text
    pages:tuple[CompactStoryPage,...]=Field(min_length=6,max_length=6)
    citations:tuple[CompactStoryCitation,...]=Field(min_length=0,max_length=3)
    moral:Text
    parent_question:Text

    @model_validator(mode='after')
    def narrative_not_caption(self):
        normalized=[' '.join(re.findall(r'\w+',p.text.casefold())) for p in self.pages]
        if len(set(normalized))!=len(normalized):
            raise ValueError('Repeated page text is incomplete. Each page must advance the story.')
        lengths=[len(re.findall(r"\b[\w]+(?:['-][\w]+)*\b",p.text)) for p in self.pages]
        reflection_words=len(re.findall(r'\b\w+\b',' '.join([self.title,self.moral,self.parent_question])))
        if min(lengths)<25 or sum(lengths)+reflection_words<180:
            raise ValueError('Write a complete story: at least 25 words on every page and 180 child-facing words including the title, moral and parent question, with connected actions and a resolution. One-line captions are incomplete.')
        return self

def source_quote_bank(bundle):
    bank=[]
    if not bundle:return bank
    for passage in bundle.passages:
        for paragraph in passage.text.split('\n\n'):
            lines=paragraph.splitlines(keepends=True);candidates=[]
            for i in range(len(lines)-1):
                quote=''.join(lines[i:i+2]).strip()
                if len(quote)<20 or len(quote)>180:continue
                score=len(re.findall(r'back|placed|disguise|form|friendly|friend|joy|clear|kind|help|water|grain|Guha|Hanum|courteous|sweet|spoke|reply',quote,re.I))
                candidates.append((score,i,quote))
            for _,_,quote in sorted(candidates,key=lambda item:(-item[0],item[1]))[:4]:
                if quote not in passage.text:raise ValueError('Quote-bank extraction changed source text.')
                bank.append(dict(source_quote_id='q-'+content_hash([bundle.bundle_hash,passage.reference.chunk_id,quote])[:12],chunk_id=passage.reference.chunk_id,source_quote=quote))
    return list({q['source_quote_id']:q for q in bank}.values())[:20]

def expand_story(raw,payload):
    bank={q['source_quote_id']:q for q in payload['source_quote_bank']}
    for citation in raw.citations:
        if citation.source_quote_id not in bank:raise ValueError('Source quote ID is not in the frozen evidence quote bank.')
    return StoryDraft(draft_id=payload['draft_id'],request_hash=payload['request_hash'],
        classification=payload['request']['story_type'],fiction_disclosure='original teaching-inspired fiction' if payload['request']['story_type']=='original_inspired' else None,
        title=raw.title,pages=tuple(StoryPage(page_number=i+1,text=p.text,
            character_ids=p.character_ids,scene_description=p.text,accessible_description=p.text,
            citations=tuple(ClaimCitation(chunk_id=bank[c.source_quote_id]['chunk_id'],claim_text=c.claim_text,source_quote=bank[c.source_quote_id]['source_quote'],claim_kind='factual') for c in raw.citations if c.page_number==i+1))
            for i,p in enumerate(raw.pages)),moral=raw.moral,parent_question=raw.parent_question,
        evidence_bundle_id=payload['evidence_bundle_id'],character_manifest_id=payload['character_manifest_id'],
        prompt_version=payload['prompt_version'])
