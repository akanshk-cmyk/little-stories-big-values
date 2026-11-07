"""Text-only human consent and distinct deterministic output authorization."""
from typing import Literal
from pydantic import AwareDatetime,Field,model_validator
from .common import Contract,Identifier,Digest,Text,content_hash


class TextApprovalRequest(Contract):
    text_hash:Digest


class TextPage(Contract):
    page_number:int=Field(ge=1,le=6)
    text:Text


class StoryText(Contract):
    title:Text
    classification:Text
    fiction_disclosure:Text|None=None
    pages:tuple[TextPage,...]=Field(min_length=6,max_length=6)
    moral:Text
    parent_question:Text|None=None
    cultural_fact:Text|None=None
    family_activity:Text|None=None

    @model_validator(mode='after')
    def ordered(self):
        if tuple(p.page_number for p in self.pages)!=tuple(range(1,7)):raise ValueError('Text pages must be ordered.')
        return self


def story_text(draft):
    return StoryText(title=draft.title,classification=draft.classification,
        fiction_disclosure=draft.fiction_disclosure,
        pages=tuple(TextPage(page_number=p.page_number,text=p.text) for p in draft.pages),
        moral=draft.moral,parent_question=draft.parent_question,
        cultural_fact=draft.cultural_fact,family_activity=draft.family_activity)


class TextConsent(Contract):
    seal_field='consent_hash'
    consent_id:Identifier
    owner_id:Identifier
    run_id:Identifier
    request_hash:Digest
    text:StoryText
    text_hash:Digest
    review_hash_at_consent:Digest
    policy_version:Literal['story-text-consent-v1']='story-text-consent-v1'
    approved_at:AwareDatetime
    consent_hash:Digest|None=None

    @model_validator(mode='after')
    def text_matches(self):
        if content_hash(self.text)!=self.text_hash:raise ValueError('Consent text hash mismatch.')
        return self


class OutputRelease(Contract):
    seal_field='release_hash'
    release_id:Identifier
    kind:Literal['application_output']='application_output'
    owner_id:Identifier
    run_id:Identifier
    book_hash:Digest
    draft_hash:Digest
    text_hash:Digest
    consent_id:Identifier
    consent_hash:Digest
    review_hash:Digest
    evidence_hash:Digest|None=None
    character_hash:Digest
    asset_hash:Digest
    playbook_version:Identifier
    policy_version:Literal['application-output-v1']='application-output-v1'
    released_at:AwareDatetime
    release_hash:Digest|None=None
