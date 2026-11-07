"""The existing keyword score applied to fixed, reviewed Stage 4 chunks.

This changes neither the live source path nor its 300-word legacy splitter.
The adapter is for repeatable local comparisons on one fixed chunk corpus.
"""
from typing import Literal
from time import perf_counter
from pydantic import Field, model_validator
from backend.contracts.common import Contract, Identifier, Text, content_hash, unique
from backend.source_grounding import tokens, keyword_score, VERSION


class SourceScope(Contract):
    source_id: Identifier
    edition_id: Identifier
    snapshot_id: Identifier
    generation: int = Field(ge=1)


class RetrievalCase(Contract):
    test_id: Identifier
    query: Text
    source: SourceScope
    purpose: Literal['parent_inspiration','traditional'] = 'parent_inspiration'
    expected_chunk_ids: tuple[Identifier,...] = ()
    expectation: Literal['relevant','no_match','blocked']
    label_status: Literal['synthetic','human_reviewed','pending']
    label_reference: Text

    @model_validator(mode='after')
    def valid_expectation(self):
        unique(self.expected_chunk_ids,'expected passages')
        if self.expectation=='relevant' and not self.expected_chunk_ids:
            raise ValueError('A relevance case needs explicit expected passage IDs.')
        if self.expectation!='relevant' and self.expected_chunk_ids:
            raise ValueError('Negative cases cannot declare expected hits.')
        return self


class ScopeChanged(ValueError):pass


def assert_scope(summary, scope):
    if (summary['edition_id'],summary['snapshot_id'],summary['generation']) != (
            scope.edition_id,scope.snapshot_id,scope.generation):
        raise ScopeChanged('The requested source edition or chunk generation is no longer current.')


def search(workspace, owner, case, *, limit=5):
    if type(limit) is not int or not 1<=limit<=20:raise ValueError('Use a limit from 1 to 20.')
    started=perf_counter()
    summary=workspace.summary(case.source.source_id,owner)
    assert_scope(summary,case.source)
    epoch=summary['epoch'];hits=[];eligible_manifest=[];excluded=0
    for offset in range(0,summary['chunk_count'],100):
        for chunk in workspace.chunks(case.source.source_id,owner,offset=offset,limit=100):
            # Eligibility is checked before scoring. Traditional evidence needs
            # a qualified registry, which the parent-only workspace cannot grant.
            eligible=chunk['eligibility_status']=='eligible_parent_inspiration' and case.purpose=='parent_inspiration'
            if not eligible:
                excluded+=1;continue
            eligible_manifest.append(dict(chunk_id=chunk['chunk_id'],content_hash=chunk['content_hash'],
                pdf_pages=chunk['pdf_pages'],review_record_ids=chunk['review_record_ids']))
            score=keyword_score(chunk['text'],set(tokens(case.query)))
            if score:
                hits.append(dict(chunk_id=chunk['chunk_id'],score=round(score,4),
                    source_id=chunk['source_id'],edition_id=chunk['edition_id'],
                    snapshot_id=chunk['source_snapshot_id'],generation=case.source.generation,
                    pdf_pages=chunk['pdf_pages'],content_hash=chunk['content_hash'],text=chunk['text']))
    current=workspace.summary(case.source.source_id,owner)
    assert_scope(current,case.source)
    if current['epoch']!=epoch:raise ScopeChanged('Source permissions changed during the search; repeat with a fresh snapshot.')
    hits.sort(key=lambda h:(-h['score'],h['chunk_id']))
    hits=hits[:limit]
    # Never let a stale shortlist outlive current eligibility at the adapter boundary.
    for hit in hits:
        if workspace.eligibility(case.source.source_id,owner,hit['chunk_id'],purpose=case.purpose)['eligibility_status']!='eligible_parent_inspiration':
            raise ScopeChanged('A shortlisted passage was withdrawn.')
    return dict(method=VERSION+'-fixed-workspace-chunks',query=case.query,
        status='blocked' if not eligible_manifest else ('matches' if hits else 'no_match'),
        hits=hits,eligible_count=len(eligible_manifest),excluded_count=excluded,eligibility_epoch=epoch,
        eligible_chunk_ids=[p['chunk_id'] for p in eligible_manifest],
        corpus_hash=content_hash(eligible_manifest),source_scope=case.source.model_dump(mode='json'),
        latency_ms=round((perf_counter()-started)*1000,3),limit=limit,
        external_processing=False)


def evaluate_case(case, result):
    """Independent expected IDs; no LLM-generated relevance labels."""
    if case.label_status=='pending':
        return dict(test_id=case.test_id,status='PENDING',reason='Relevance labels have not been reviewed.')
    expected=set(case.expected_chunk_ids)
    actual=[h['chunk_id'] for h in result['hits']]
    if case.expectation=='relevant':
        if result['status']=='blocked' or not expected<=set(result['eligible_chunk_ids']):
            return dict(test_id=case.test_id,status='PENDING',reason='Required evidence is not eligible; retrieval quality is unresolved.')
        found=expected.intersection(actual)
        rank=next((i for i,chunk_id in enumerate(actual,1) if chunk_id in expected),None)
        return dict(test_id=case.test_id,status='PASS' if found else 'FAIL',
            recall_at_k=len(found)/len(expected),reciprocal_rank=1/rank if rank else 0,
            hit_at_k=bool(found),all_expected_found=len(found)==len(expected))
    correct=result['status']==('blocked' if case.expectation=='blocked' else 'no_match')
    return dict(test_id=case.test_id,status='PASS' if correct else 'FAIL',negative_case=True)
