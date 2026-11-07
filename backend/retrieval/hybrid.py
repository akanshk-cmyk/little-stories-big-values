"""Local hybrid candidates. Results are not an approval or evidence-sufficiency verdict."""
import math
from time import perf_counter
from backend.contracts.common import content_hash,text_hash
from .algorithms import BM25,cosine,reciprocal_rank_fusion,split_text


class HybridIndex:
    def __init__(self,corpus,encoder,ranker=None):
        self.corpus=corpus;self.encoder=encoder;self.ranker=ranker
        corpus.assert_current()
        self.units=[];self.parents={p['chunk_id']:p for p in corpus.passages}
        for passage in corpus.passages:
            for piece in split_text(passage['text'],lambda text:encoder.count(text)<=encoder.limit):
                key='unit-'+content_hash([passage['chunk_id'],passage['content_hash'],piece['start'],piece['end'],encoder.signature])[:32]
                self.units.append(dict(id=key,chunk_id=passage['chunk_id'],text=piece['text'],
                    pdf_pages=[s['pdf_page'] for s in passage['page_spans'] if s['chunk_start']<piece['end'] and s['chunk_end']>piece['start']],
                    start=piece['start'],end=piece['end'],content_hash=text_hash(piece['text']),token_count=encoder.count(piece['text'])))
        if len(self.units)>20000:raise ValueError('This local index exceeds the 20,000-unit safety limit.')
        self.by_id={u['id']:u for u in self.units}
        self.sparse=BM25(self.units)
        self.vectors=encoder.encode([u['text'] for u in self.units]) if self.units else []
        if len(self.vectors)!=len(self.units):raise ValueError('Encoder omitted passage vectors.')
        for vector in self.vectors:
            if len(vector)!=encoder.dimension:raise ValueError('Encoder dimension mismatch.')
            cosine(vector,vector)
        self.index_version='index-'+content_hash([corpus.corpus_hash,corpus.epoch,corpus.scope.model_dump(mode='json'),encoder.signature,self.sparse.model_hash])[:32]
        corpus.assert_current()

    def legs(self,query,limit=20):
        self.corpus.assert_current()
        if self.encoder.count(query,query=True)>self.encoder.limit:raise ValueError('Query exceeds BGE token limit.')
        vector=self.encoder.encode([query],query=True)[0]
        dense=sorted((dict(id=unit['id'],score=cosine(vector,stored)) for unit,stored in zip(self.units,self.vectors)),
                     key=lambda h:(-h['score'],h['id']))[:limit]
        return dense,self.sparse.search(query,limit)

    def finish(self,query,hits,*,rerank,limit):
        for hit in hits:
            if hit['id'] not in self.by_id or not math.isfinite(hit['score']):raise ValueError('Invalid candidate identity or score.')
        self.corpus.assert_current([self.by_id[h['id']]['chunk_id'] for h in hits])
        ranked=[]
        if rerank and hits:
            if self.ranker is None:raise RuntimeError('FlashRank was requested but is unavailable; no automatic fallback.')
            pieces=[];mapping={}
            for hit in hits:
                unit=self.by_id[hit['id']]
                for piece in split_text(unit['text'],lambda text:self.ranker.count(query,text)<=self.ranker.limit):
                    key='pair-'+content_hash([unit['id'],query,piece['start'],piece['end']])[:32]
                    mapping[key]=(unit,hit,piece);pieces.append(dict(id=key,text=piece['text']))
            if len(pieces)>200:raise ValueError('Rerank subdivision exceeds the 200-pair limit.')
            scores=self.ranker.score(query,pieces)
            if set(scores)!=set(mapping) or any(not math.isfinite(v) for v in scores.values()):raise ValueError('Reranker returned missing, unknown or non-finite scores.')
            for key,score in scores.items():
                unit,hit,piece=mapping[key]
                ranked.append(dict(unit=unit,score=float(score),fusion_score=hit['score'],
                    rerank_span=[unit['start']+piece['start'],unit['start']+piece['end']]))
            ranked.sort(key=lambda row:(-row['score'],row['unit']['id']))
        else:ranked=[dict(unit=self.by_id[h['id']],score=h['score'],fusion_score=None,rerank_span=None) for h in hits]
        results=[];seen=set()
        for row in ranked:
            unit=row['unit'];key=unit['chunk_id']
            if key in seen:continue
            seen.add(key);parent=self.parents[key]
            results.append(dict(chunk_id=key,unit_id=unit['id'],score=row['score'],fusion_score=row['fusion_score'],
                text=unit['text'],source_id=parent['source_id'],edition_id=parent['edition_id'],snapshot_id=parent['source_snapshot_id'],
                generation=self.corpus.scope.generation,pdf_pages=unit['pdf_pages'],content_hash=unit['content_hash'],
                parent_content_hash=parent['content_hash'],parent_span=[unit['start'],unit['end']],rerank_span=row['rerank_span']))
            if len(results)>=limit:break
        self.corpus.assert_current([r['chunk_id'] for r in results])
        return results

    def search(self,query,*,mode='hybrid_rerank',limit=5):
        if mode not in ('bm25','dense','hybrid','hybrid_rerank'):raise ValueError('Choose an explicit retrieval method.')
        if not isinstance(query,str) or not query.strip() or type(limit) is not int or not 1<=limit<=20:raise ValueError('Invalid query or result limit.')
        started=perf_counter();self.corpus.assert_current()
        if not self.units:results=[]
        elif mode=='bm25':results=self.finish(query,self.sparse.search(query),rerank=False,limit=limit)
        else:
            dense,sparse=self.legs(query)
            hits=dense if mode=='dense' else reciprocal_rank_fusion(dense,sparse)
            results=self.finish(query,hits,rerank=mode=='hybrid_rerank',limit=limit)
        return dict(method=mode,status='blocked' if not self.units else ('matches' if results else 'no_match'),
            query=query,hits=results,corpus_hash=self.corpus.corpus_hash,index_version=self.index_version,
            eligibility_epoch=self.corpus.epoch,source_scope=self.corpus.scope.model_dump(mode='json'),
            eligible_chunk_ids=list(self.parents),eligible_count=len(self.parents),limit=limit,
            latency_ms=round((perf_counter()-started)*1000,3),evidence_sufficiency='PENDING',external_processing=False)
