"""Criteria-first episode contracts. Records never grant source authority."""
from typing import Literal
from pydantic import Field, model_validator
from .common import Contract, Identifier, Digest, Text, content_hash, unique
from .evidence import SourceReference


class SelectionPolicy(Contract):
    top_k: int = Field(default=5, ge=1, le=10)
    retrieval_pool_size: int = Field(default=20, ge=1, le=100)
    max_corrections: int = Field(default=2, ge=0, le=2)
    relevance_weight: float = Field(default=0.4, ge=0, le=1)
    age_weight: float = Field(default=0.2, ge=0, le=1)
    reliability_weight: float = Field(default=0.2, ge=0, le=1)
    adaptability_weight: float = Field(default=0.2, ge=0, le=1)
    version: Identifier = 'episode-selection-v1'

    @model_validator(mode='after')
    def valid_weights(self):
        if self.top_k > self.retrieval_pool_size:
            raise ValueError('Shortlist exceeds retrieval pool.')
        if abs(sum((self.relevance_weight,self.age_weight,self.reliability_weight,self.adaptability_weight))-1)>1e-8:
            raise ValueError('Ranking weights must sum to one.')
        return self


class EpisodeCandidate(Contract):
    episode_id: Identifier
    references: tuple[SourceReference,...] = Field(min_length=1)
    minimum_age: int = Field(ge=3,le=8)
    maximum_age: int = Field(ge=3,le=8)
    character_ids: tuple[Identifier,...] = Field(min_length=1)
    relevance: float = Field(ge=0,le=1)
    age_fit: float = Field(ge=0,le=1)
    reliability: float = Field(ge=0,le=1)
    adaptability: float = Field(ge=0,le=1)
    assessment: Literal['PASS','FAIL','PENDING'] = 'PENDING'
    assessment_reason: Text
    faithful_adaptation: bool = False
    policy_version: Identifier

    @model_validator(mode='after')
    def episode_boundary(self):
        if self.minimum_age>self.maximum_age:raise ValueError('Invalid age range.')
        unique(self.character_ids,'candidate characters')
        boundaries={(r.source_id,r.snapshot_id,r.edition_id,r.parent_section_id) for r in self.references}
        if len(boundaries)!=1:raise ValueError('An episode cannot merge source versions or episode boundaries.')
        return self


class ScriptBinding(Contract):
    seal_field='binding_hash'
    run_id: Identifier
    request_hash: Digest
    script_hash: Digest
    evidence_hash: Digest
    review_hash: Digest
    asset_hash: Digest
    selection_hash: Digest
    binding_hash: Digest|None=None
