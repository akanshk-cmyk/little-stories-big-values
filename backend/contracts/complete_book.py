"""Versioned complete-book policy; old text-consent contracts remain historical."""
from typing import Literal
from pydantic import AwareDatetime,Field,model_validator
from .common import Contract,Identifier,Digest,Text,content_hash
from . import BookVersion,StoryRequest


class CreationOptions(Contract):
    selected_source_ids:tuple[Identifier,...]=Field(default=(),max_length=8)
    voice_id:Identifier|None=None
    voice_model:Identifier|None=None
    audio_processing_consent:bool=False

    def validate_request(self,request):
        if request.narration_requested and not (self.voice_id and self.voice_model and self.audio_processing_consent):
            raise ValueError('Choose a voice and authorize audio preparation with the initial story request.')
        return self


class AudioArtifact(Contract):
    audio_id:Digest
    media_type:Literal['audio/mpeg','audio/wav']
    script_hash:Digest
    voice_id:Identifier
    model_id:Identifier
    alignment_status:Literal['PASS']
    alignment_evidence:Text
    checker_version:Identifier


class CompleteBookPreview(Contract):
    seal_field='complete_book_hash'
    book:BookVersion
    request_hash:Digest
    review_hash:Digest
    options_hash:Digest
    narration_requested:bool
    narration_script:tuple[Text,...]
    audio:AudioArtifact|None=None
    policy_version:Literal['complete-book-review-v1']='complete-book-review-v1'
    complete_book_hash:Digest|None=None

    @model_validator(mode='after')
    def audio_matches(self):
        if self.narration_requested != (self.audio is not None):raise ValueError('Requested audio must be complete before review.')
        if self.audio and self.audio.script_hash!=content_hash(self.narration_script):raise ValueError('Audio script mismatch.')
        return self


class CompleteBookApproval(Contract):
    seal_field='approval_hash'
    approval_id:Identifier
    owner_id:Identifier
    run_id:Identifier
    complete_book_hash:Digest
    base_book_hash:Digest
    review_hash:Digest
    approved_at:AwareDatetime
    policy_version:Literal['complete-book-review-v1']='complete-book-review-v1'
    approval_hash:Digest|None=None


class CompleteBookDecision(Contract):
    book_hash:Digest
    action:Literal['approve','request_changes','reject']
    request_key:Identifier
    notes:str=Field(default='',max_length=2000)

    @model_validator(mode='after')
    def revision_has_notes(self):
        if self.action=='request_changes' and not self.notes.strip():raise ValueError('Describe the requested change.')
        return self


class CompleteStoryStart(Contract):
    request_key:Identifier
    request:StoryRequest
    options:CreationOptions=Field(default_factory=CreationOptions)
    provider_mode:Literal['auto','groq','nebius']='auto'

    @model_validator(mode='after')
    def initial_authorization(self):
        self.options.validate_request(self.request)
        return self
