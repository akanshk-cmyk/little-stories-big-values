"""Trusted workspace gate. Parent inspiration is never upgraded to scripture."""
from dataclasses import dataclass
from types import MappingProxyType
from backend.contracts.common import content_hash,text_hash
from .local_baseline import assert_scope,ScopeChanged


@dataclass(frozen=True)
class Corpus:
    passages: tuple
    corpus_hash: str
    namespace: str
    epoch: int
    scope: object
    assert_current: object
    cloud: bool


def load_corpus(workspace,owner,case,*,cloud=False):
    summary=workspace.summary(case.source.source_id,owner);assert_scope(summary,case.source)
    epoch=summary['epoch'];passages=[];manifest=[]
    for offset in range(0,summary['chunk_count'],100):
        for chunk in workspace.chunks(case.source.source_id,owner,offset=offset,limit=100):
            if case.purpose!='parent_inspiration' or chunk['eligibility_status']!='eligible_parent_inspiration':continue
            if cloud and not chunk.get('pinecone_storage_permitted',False):
                raise PermissionError('This corpus lacks explicit Pinecone-storage permission.')
            if (chunk['source_id'],chunk['edition_id'],chunk['source_snapshot_id'])!=(
                    case.source.source_id,case.source.edition_id,case.source.snapshot_id):
                raise ScopeChanged('Chunk provenance does not match the source scope.')
            if text_hash(chunk['text'])!=chunk['content_hash']:raise ScopeChanged('Passage integrity failure.')
            fields=('chunk_id','text','content_hash','source_id','edition_id','source_snapshot_id')
            spans=[];position=0;previous=None
            for span in chunk['page_spans']:
                if previous is not None:position+=2*(span['pdf_page']-previous)
                end=position+span['clean_end']-span['clean_start']
                spans.append(MappingProxyType(dict(pdf_page=span['pdf_page'],chunk_start=position,chunk_end=end)))
                position=end;previous=span['pdf_page']
            if position!=len(chunk['text']):raise ScopeChanged('Page spans do not reconstruct the passage.')
            passages.append(MappingProxyType({k:chunk[k] for k in fields}|{
                'pdf_pages':tuple(chunk['pdf_pages']),'page_spans':tuple(spans)}))
            manifest.append(dict(chunk_id=chunk['chunk_id'],content_hash=chunk['content_hash'],
                pdf_pages=chunk['pdf_pages'],review_record_ids=chunk['review_record_ids']))
    allowed={p['chunk_id']:p['content_hash'] for p in passages}
    def guard(ids=None):
        current=workspace.summary(case.source.source_id,owner);assert_scope(current,case.source)
        if current['epoch']!=epoch:raise ScopeChanged('Source permissions changed. Rebuild this index generation.')
        for key in (allowed if ids is None else ids):
            if key not in allowed:raise PermissionError('A result is outside the eligible corpus.')
            status=workspace.eligibility(case.source.source_id,owner,key,purpose=case.purpose)
            if status['eligibility_status']!='eligible_parent_inspiration' or (cloud and not status.get('pinecone_storage_permitted',False)):
                raise PermissionError('Passage authorization was withdrawn.')
    guard()
    return Corpus(tuple(passages),content_hash(manifest),'parent-'+content_hash(owner),epoch,case.source,guard,cloud)
