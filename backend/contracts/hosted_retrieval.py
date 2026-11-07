"""Internal hosted-service boundary contracts; not vendor API payload schemas.

These records confer neither source eligibility nor permission to transmit data.
An adapter must resolve hosted hits against the trusted local source registry.
"""
from typing import Literal

from pydantic import Field, model_validator

from .common import Contract, Digest, Identifier, Positive, Text, text_hash, unique


class HostedIndexBinding(Contract):
    """Locally versioned mapping, never an assertion of live readiness."""
    owner_id: Identifier
    source_id: Identifier
    snapshot_id: Identifier
    edition_id: Identifier
    organization_id: Identifier
    project_id: Identifier
    hosted_index_id: Identifier
    index_version: Identifier
    parse_version: Identifier
    retrieval_config_hash: Digest


class ParsedPage(Contract):
    pdf_page: Positive
    text: str = Field(max_length=200000)  # An actually blank page is legitimate.
    content_hash: Digest

    @model_validator(mode='after')
    def matches_text(self):
        if text_hash(self.text) != self.content_hash:
            raise ValueError('Extracted page text does not match its hash.')
        return self


class ParsedDocument(Contract):
    source_id: Identifier
    snapshot_id: Identifier
    original_file_hash: Digest
    parse_version: Identifier
    expected_page_count: Positive
    pages: tuple[ParsedPage, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def complete_page_mapping(self):
        numbers = tuple(page.pdf_page for page in self.pages)
        if numbers != tuple(range(1, self.expected_page_count + 1)):
            raise ValueError('Extraction requires every PDF page once, in order.')
        return self


class HostedSearchHit(Contract):
    """Untrusted search candidate, not an approved SourceReference."""
    hosted_file_id: Identifier
    hosted_chunk_id: Identifier
    text: Text
    pdf_pages: tuple[Positive, ...] = Field(min_length=1)
    content_hash: Digest
    rank: Positive
    score: float | None = None
    rerank_score: float | None = None

    @model_validator(mode='after')
    def valid_candidate(self):
        if self.pdf_pages != tuple(sorted(set(self.pdf_pages))):
            raise ValueError('PDF page references must be unique and increasing.')
        if text_hash(self.text) != self.content_hash:
            raise ValueError('Candidate text does not match its hash.')
        return self


class HostedSearchResult(Contract):
    binding: HostedIndexBinding
    query_hash: Digest
    retrieval_kind: Literal['hosted_hybrid'] = 'hosted_hybrid'
    hits: tuple[HostedSearchHit, ...] = Field(default=(), max_length=100)

    @model_validator(mode='after')
    def unique_hits(self):
        unique(tuple((hit.hosted_file_id, hit.hosted_chunk_id) for hit in self.hits),
               'hosted hit IDs')
        if tuple(hit.rank for hit in self.hits) != tuple(range(1, len(self.hits) + 1)):
            raise ValueError('Candidate ranks must be consecutive and ordered.')
        return self
