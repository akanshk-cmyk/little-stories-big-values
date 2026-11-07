from typing import Literal
from pydantic import Field, model_validator
from .common import Contract, Digest, Identifier, NonNegative, Positive, Text, text_hash, unique


class SourceReference(Contract):
    source_id: Identifier
    snapshot_id: Identifier
    edition_id: Identifier
    chunk_id: Identifier
    parent_section_id: Identifier
    pdf_pages: tuple[Positive, ...] = Field(min_length=1)
    printed_pages: tuple[Text, ...] | None = None
    content_hash: Digest
    review_record_ids: tuple[Identifier, ...] = ()
    eligibility_epoch: NonNegative
    review_status: Literal['pending', 'approved', 'rejected', 'withdrawn'] = 'pending'
    eligibility_status: Literal['pending', 'eligible', 'blocked'] = 'pending'
    rights_status: Literal['unknown', 'permitted', 'restricted', 'denied'] = 'unknown'

    @model_validator(mode='after')
    def valid_reference(self):
        if self.pdf_pages != tuple(sorted(set(self.pdf_pages))):
            raise ValueError('PDF page references must be unique and increasing.')
        unique(self.review_record_ids, 'review references')
        if self.printed_pages is not None and len(self.printed_pages) != len(self.pdf_pages):
            raise ValueError('Printed page labels must match the PDF page references.')
        if self.eligibility_status == 'eligible' and (self.review_status != 'approved' or self.rights_status != 'permitted' or not self.review_record_ids):
            raise ValueError('Eligible evidence requires approved reviews and permitted rights.')
        return self


class SearchHit(Contract):
    reference: SourceReference
    index_version: Identifier
    retriever: Literal['dense', 'sparse', 'local_keyword']
    rank: Positive
    score: float


class RankedPassage(Contract):
    reference: SourceReference
    text: Text
    index_version: Identifier
    fused_rank: Positive
    rerank_score: float | None = None  # No invented reranking measurement for local retrieval.
    token_count: Positive

    @model_validator(mode='after')
    def matches_text(self):
        if text_hash(self.text) != self.reference.content_hash:
            raise ValueError('Passage text does not match its content hash.')
        return self


class EvidenceBundle(Contract):
    seal_field = 'bundle_hash'
    bundle_id: Identifier
    request_hash: Digest
    passages: tuple[RankedPassage, ...] = Field(min_length=1, max_length=5)
    context_chunk_ids: tuple[Identifier, ...] = ()
    index_version: Identifier
    retrieval_config_hash: Digest
    eligibility_epoch: NonNegative
    bundle_hash: Digest | None = None

    @model_validator(mode='after')
    def valid_bundle(self):
        ids = tuple(p.reference.chunk_id for p in self.passages)
        unique(ids, 'passage IDs')
        unique(self.context_chunk_ids, 'context references')
        if not set(self.context_chunk_ids) <= set(ids):
            raise ValueError('Expanded context must be present as validated passages.')
        for passage in self.passages:
            if passage.index_version != self.index_version or passage.reference.eligibility_epoch != self.eligibility_epoch:
                raise ValueError('Mixed index versions or eligibility epochs in evidence.')
            if passage.reference.eligibility_status != 'eligible':
                raise ValueError('Only eligible passages may enter a frozen evidence bundle.')
        return self
