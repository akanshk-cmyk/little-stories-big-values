"""Concise model review expanded deterministically to the unchanged strict rubric."""
from typing import Literal
from pydantic import Field,model_validator
from .common import Contract,Digest,Identifier,Text
from .reviews import ReviewReport,ReviewCheck,EvidencePointer


class CompactCheck(Contract):
    status:Literal['PASS','FAIL','PENDING']
    reason:Text
    page:int=Field(ge=1,le=6)
    quote:str=Field(max_length=12000)
    source_quote:str|None

    @model_validator(mode='after')
    def grounded_pass(self):
        if self.status=='PASS' and not self.quote.strip():raise ValueError('PASS requires an exact story quote.')
        return self


class CompactReview(Contract):
    draft_hash:Digest
    evidence_bundle_hash:Digest|None
    reviewer_version:Identifier
    factual_correctness:CompactCheck
    clarity:CompactCheck
    value_insight:CompactCheck
    engagement:CompactCheck
    tone_fit:CompactCheck
    critical_safety:CompactCheck


def expand_review(raw,draft,bundle):
    checks={}
    for key in ('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety'):
        c=getattr(raw,key);page_index=c.page-1;evidence=[]
        if c.quote:
            pointer='/pages/'+str(page_index)+'/text'
            if page_index>=len(draft.pages) or c.quote not in draft.pages[page_index].text:
                matches=['/pages/'+str(i)+'/text' for i,page in enumerate(draft.pages) if c.quote in page.text]
                matches.extend('/'+field for field in ('title','moral','parent_question','cultural_fact','family_activity')
                    if getattr(draft,field,None) and c.quote in getattr(draft,field))
                if len(matches)!=1:raise ValueError('Reviewer quote is missing or ambiguous in the exact story.')
                pointer=matches[0]
            evidence.append(EvidencePointer(artifact_id=draft.draft_id,pointer=pointer,quote=c.quote))
        if c.source_quote:
            matches=[(i,p) for i,p in enumerate(bundle.passages)] if bundle else []
            matches=[(i,p) for i,p in matches if c.source_quote in p.text]
            if len(matches)!=1:raise ValueError('Reviewer source quote is missing or ambiguous.')
            evidence.append(EvidencePointer(artifact_id=bundle.bundle_id,pointer='/passages/'+str(matches[0][0])+'/text',quote=c.source_quote))
        if key=='factual_correctness' and c.status=='PASS' and bundle and not c.source_quote:raise ValueError('Factual PASS requires exact source support.')
        checks[key]=ReviewCheck(status=c.status,reason=c.reason,evidence=tuple(evidence),requested_correction=c.reason if c.status=='FAIL' else None)
    return ReviewReport(draft_hash=raw.draft_hash,evidence_bundle_hash=raw.evidence_bundle_hash,reviewer_version=raw.reviewer_version,**checks)
