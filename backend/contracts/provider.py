"""Provider-neutral contracts. Credentials and headers never enter these models."""
import json
from typing import Literal
from pydantic import Field,model_validator
from .common import Contract,Identifier,Digest,Text,NonNegative,content_hash
from .requests import ProviderMode


def json_object(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON fields are not permitted.')
            result[key]=value
        return result
    def bad_constant(value):raise ValueError('Non-finite JSON is not permitted.')
    try:value=json.loads(text,object_pairs_hook=pairs,parse_constant=bad_constant)
    except (RecursionError,OverflowError):raise ValueError('JSON nesting exceeds the supported structure.') from None
    if not isinstance(value,dict):raise ValueError('Expected a JSON object.')
    return value


class ModelRequest(Contract):
    run_id: Identifier
    thread_id: Identifier
    operation_id: Identifier
    request_hash: Digest
    mode: ProviderMode = 'auto'
    role: Literal['writer','reviewer','correction','source_reviewer']
    prompt_version: Identifier
    reviewer_version: Identifier
    schema_version: Literal['provider-contract-v1'] = 'provider-contract-v1'
    output_schema: Literal['story_draft','review_report','source_qa_report','compact_review_report','compact_story_draft']
    system_prompt: Text
    input_json: str = Field(min_length=2,max_length=150000)
    evidence_required: bool = False
    evidence_bundle_id: Identifier | None = None
    evidence_bundle_hash: Digest | None = None
    draft_hash: Digest | None = None
    source_subject_hash: Digest | None = None
    max_tokens: int = Field(default=4096,ge=64,le=12000)
    temperature: float = Field(default=.2,ge=0,le=2)
    deadline_seconds: float = Field(default=120.,ge=1,le=300)

    @model_validator(mode='after')
    def coherent(self):
        json_object(self.input_json)
        expected={'reviewer':'review_report','source_reviewer':'source_qa_report'}.get(self.role,'story_draft')
        if self.output_schema!=expected and not (self.role=='reviewer' and self.output_schema=='compact_review_report') and not (self.role in ('writer','correction') and self.output_schema=='compact_story_draft'):raise ValueError('Role and response contract do not match.')
        if self.role=='source_reviewer' and not self.source_subject_hash:raise ValueError('Source reviewer requires an exact subject hash.')
        if self.role=='reviewer' and not self.draft_hash:raise ValueError('A reviewer must be bound to an exact saved draft.')
        if bool(self.evidence_bundle_id)!=bool(self.evidence_bundle_hash):raise ValueError('Evidence ID/hash must be supplied together.')
        if self.evidence_required and not self.evidence_bundle_id:raise ValueError('Required evidence references are missing.')
        return self


class ProviderResponse(Contract):
    provider: Literal['groq','nebius']
    model_id: Text
    reported_model_id: Text | None = None
    operation_id: Identifier
    attempt_id: Identifier
    output_schema: Literal['story_draft','review_report','source_qa_report','compact_review_report','compact_story_draft']
    output_json: str = Field(min_length=2,max_length=200000)
    output_hash: Digest
    provider_request_id: Identifier | None = None
    input_tokens: NonNegative | None = None
    output_tokens: NonNegative | None = None
    latency_ms: NonNegative
    finish_reason: Literal['stop'] = 'stop'
    cached: bool = False

    @model_validator(mode='after')
    def verified_output(self):
        if content_hash(json_object(self.output_json))!=self.output_hash:raise ValueError('Response hash mismatch.')
        return self
