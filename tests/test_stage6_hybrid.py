"""Offline algorithm/authorization/transport tests. Models are injected fixtures."""
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
from io import BytesIO
import httpx
from backend.contracts.common import parse_payload
from backend.ingestion.workspace import SourceWorkspace,PassageReview
from backend.retrieval.algorithms import BM25,cosine,reciprocal_rank_fusion,split_text
from backend.retrieval.eligibility import load_corpus
from backend.retrieval.hybrid import HybridIndex
from backend.retrieval.local_baseline import RetrievalCase,ScopeChanged
from backend.retrieval.pinecone import PublicationRegistry,PineconeAdapter
from evaluation.retrieval_v1.synthetic import prepare,OWNER,pdf


class FixtureEncoder:
    dimension=3;limit=18;signature='synthetic-encoder-v1'
    def __init__(self):self.calls=[]
    def count(self,text,*,query=False):return len(text.split())+2+int(query)
    def encode(self,texts,*,query=False):
        self.calls.extend(texts)
        if any(self.count(text,query=query)>self.limit for text in texts):raise ValueError('Fixture token overflow')
        return [[1+text.lower().count('share'),1+text.lower().count('cloud'),1+len(text.split())*.1] for text in texts]


class FixtureRanker:
    limit=22
    def __init__(self):self.calls=[]
    def count(self,query,text):return len(query.split())+len(text.split())+3
    def score(self,query,passages):
        if any(self.count(query,p['text'])>self.limit for p in passages):raise ValueError('Fixture pair overflow')
        self.calls.append(passages)
        return {p['id']:float('mangoes' in p['text']) for p in passages}


class FakePinecone:
    """Protocol fixture, not evidence that a live Pinecone account works."""
    def __init__(self):
        self.records={};self.requests=[];self.fail_host=None;self.query_failure=None
        self.hidden_reads=0;self.bad_metadata=False;self.bad_fetch_metadata=False

    def handle(self,request):
        self.requests.append(request)
        host=request.url.host
        if host==self.fail_host:return httpx.Response(503,json={'error':'Synthetic unavailable'})
        if request.method=='GET':
            if self.hidden_reads:
                self.hidden_reads-=1
                return httpx.Response(200,json={'vectors':{},'namespace':request.url.params['namespace']})
            namespace=request.url.params['namespace'];ids=request.url.params.get_list('ids')
            rows={key:self.records.get((host,namespace,key)) for key in ids}
            if self.bad_fetch_metadata:
                rows=json.loads(json.dumps(rows))
                for row in rows.values():
                    if row:row['metadata']['edition_id']='wrong-edition'
            return httpx.Response(200,json={'namespace':namespace,'vectors':{k:v for k,v in rows.items() if v}})
        body=json.loads(request.content);namespace=body['namespace']
        if request.url.path=='/vectors/upsert':
            for row in body['vectors']:self.records[host,namespace,row['id']]=row
            return httpx.Response(200,json={'upsertedCount':len(body['vectors'])})
        if host==self.query_failure:return httpx.Response(503,json={'error':'Synthetic query failure'})
        matches=[]
        for (saved_host,saved_namespace,key),row in self.records.items():
            if saved_host!=host or saved_namespace!=namespace:continue
            if not all(row['metadata'].get(k)==v['$eq'] for k,v in body['filter'].items()):continue
            if 'vector' in body:score=cosine(body['vector'],row['values'])
            else:
                query=dict(zip(body['sparseVector']['indices'],body['sparseVector']['values']))
                sparse=row['sparseValues'];score=sum(query.get(i,0)*v for i,v in zip(sparse['indices'],sparse['values']))
            metadata=dict(row['metadata'])
            if self.bad_metadata:metadata['edition_id']='wrong-edition'
            matches.append(dict(id=key,score=score,metadata=metadata))
        matches.sort(key=lambda row:(-row['score'],row['id']))
        return httpx.Response(200,json={'namespace':namespace,'matches':matches[:body['topK']]})


class AlgorithmTests(unittest.TestCase):
    def test_bm25_sparse_dot_matches_local_scores(self):
        index=BM25([{'id':'a','text':'mango mango seed'},{'id':'b','text':'seed cloud'}])
        scores={row['id']:row['score'] for row in index.search('mango seed')}
        weights=index.query_weights('mango seed')
        for key,value in index.documents.items():
            self.assertAlmostEqual(sum(weights.get(i,0)*w for i,w in value.items()),scores[key])
        self.assertGreater(scores['a'],scores['b'])

    def test_sparse_version_changes_with_corpus(self):
        a=BM25([{'id':'a','text':'mango'}]);b=BM25([{'id':'a','text':'cloud'}])
        self.assertNotEqual(a.model_hash,b.model_hash)
        self.assertEqual(a.search('unmatched'),[])

    def test_rank_fusion_deduplicates_each_leg(self):
        result=reciprocal_rank_fusion([{'id':'a'},{'id':'a'},{'id':'b'}],[{'id':'b'},{'id':'a'}])
        self.assertEqual([r['id'] for r in result],['a','b'])
        self.assertAlmostEqual(result[0]['score'],1/61+1/62)

    def test_subdivision_preserves_words_offsets_and_progress(self):
        text=' '.join('word'+str(i) for i in range(80))
        pieces=split_text(text,lambda s:len(s.split())<=12)
        found=set()
        for piece in pieces:
            self.assertEqual(piece['text'],text[piece['start']:piece['end']])
            self.assertLessEqual(len(piece['text'].split()),12);found.update(piece['text'].split())
        self.assertEqual(found,set(text.split()))
        self.assertEqual(split_text('',lambda s:True),[])
        with self.assertRaises(ValueError):split_text('word',lambda s:False)

    def test_cosine_rejects_bad_dimensions_and_nonfinite_values(self):
        for a,b in [([0],[0]),([1,2],[1]),([math.nan],[1])]:
            with self.assertRaises(ValueError):cosine(a,b)


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=SourceWorkspace(Path(self.temp.name)/'sources');self.addCleanup(self.workspace.close)
        self.cases=[parse_payload(RetrievalCase,c) for c in prepare(self.workspace)]
        self.encoder=FixtureEncoder();self.ranker=FixtureRanker()

    def index(self,cloud=False,case=None):
        case=case or self.cases[0]
        return HybridIndex(load_corpus(self.workspace,OWNER,case,cloud=cloud),self.encoder,self.ranker)

    def cloud_index(self):
        scope=self.cases[0].source
        for chunk in self.workspace.chunks(scope.source_id,OWNER,limit=100):
            if chunk['eligibility_status']=='eligible_parent_inspiration':
                review=chunk['review']
                data={k:v for k,v in review.items() if k in PassageReview.model_fields}
                self.workspace.review(scope.source_id,OWNER,PassageReview(**(data|{'pinecone_storage_permitted':True})))
        return self.index(cloud=True)

    def adapter(self,*,enabled=True):
        remote=FakePinecone();client=httpx.Client(transport=httpx.MockTransport(remote.handle))
        self.addCleanup(client.close)
        registry=PublicationRegistry(Path(self.temp.name)/'publications')
        adapter=PineconeAdapter('https://dense.svc.pinecone.io','https://sparse.svc.pinecone.io',
            'SYNTHETIC-KEY',registry,enabled=enabled,client=client)
        return adapter,remote

    def test_only_eligible_passages_reach_encoder(self):
        index=self.index()
        self.assertEqual(len(index.parents),5)
        self.assertFalse(any('lantern' in s or 'silver boat' in s or 'ribbon' in s for s in self.encoder.calls))
        with self.assertRaises(TypeError):index.corpus.passages[0]['text']='Changed'

    def test_result_adapter_exposes_the_frozen_scope(self):
        from backend.retrieval.local_baseline import search
        index=self.index();result=index.search('mangoes',mode='bm25')
        baseline=search(self.workspace,OWNER,self.cases[0])
        self.assertEqual(result['corpus_hash'],baseline['corpus_hash'])
        self.assertEqual(result['eligibility_epoch'],baseline['eligibility_epoch'])
        self.assertEqual(result['source_scope'],self.cases[0].source.model_dump(mode='json'))

    def test_token_children_keep_their_actual_pdf_pages(self):
        raw=pdf(['One two.','Three four.'])
        source=self.workspace.create(BytesIO(raw),len(raw),owner=OWNER,title='Cross-page fixture',edition='synthetic')
        summary=self.workspace.process(source,OWNER)
        summary=self.workspace.boundaries(source,OWNER,summary['generation'],[])
        chunk=self.workspace.chunks(source,OWNER)[0]
        self.workspace.review(source,OWNER,PassageReview(expected_generation=summary['generation'],chunk_id=chunk['chunk_id'],
            content_hash=chunk['content_hash'],action='allow',extraction_checked=True,cultural_context_checked=True,
            child_suitability_checked=True,rights_permitted=True,rights_basis='Synthetic owned fixture.',notes='Synthetic only.'))
        data=self.cases[0].model_dump(mode='json')|dict(source=dict(source_id=source,edition_id=summary['edition_id'],
            snapshot_id=summary['snapshot_id'],generation=summary['generation']),expected_chunk_ids=[chunk['chunk_id']])
        case=parse_payload(RetrievalCase,data);encoder=FixtureEncoder();encoder.limit=4
        index=HybridIndex(load_corpus(self.workspace,OWNER,case),encoder)
        self.assertEqual([u['pdf_pages'] for u in index.units],[[1],[2]])
        self.assertEqual(index.search('three',mode='bm25')['hits'][0]['pdf_pages'],[2])

    def test_parent_scope_never_creates_traditional_vectors(self):
        index=self.index(case=self.cases[8])
        self.assertEqual(index.search('Krishna')['status'],'blocked')
        self.assertEqual(self.encoder.calls,[])

    def test_cloud_permission_is_explicit(self):
        with self.assertRaises(PermissionError):self.index(cloud=True)

    def test_ai_permission_does_not_grant_pinecone_storage(self):
        scope=self.cases[0].source
        for chunk in self.workspace.chunks(scope.source_id,OWNER,limit=100):
            if chunk['eligibility_status']=='eligible_parent_inspiration':
                data={k:v for k,v in chunk['review'].items() if k in PassageReview.model_fields}
                self.workspace.review(scope.source_id,OWNER,PassageReview(**(data|{'external_processing_permitted':True})))
        with self.assertRaises(PermissionError):self.index(cloud=True)

    def test_overlong_query_is_rejected_without_truncation(self):
        index=self.index()
        with self.assertRaises(ValueError):index.search(' '.join(['long']*40))

    def test_rerank_pair_subdivision_meets_its_own_budget(self):
        index=self.index();self.ranker.limit=12
        result=index.search('mango share child')
        self.assertTrue(result['hits'])
        self.assertTrue(all(self.ranker.count('mango share child',p['text'])<=12 for p in self.ranker.calls[-1]))
        self.assertEqual(result['evidence_sufficiency'],'PENDING')

    def test_missing_reranker_is_not_silently_bypassed(self):
        index=self.index();index.ranker=None
        with self.assertRaises(RuntimeError):index.search('mango')
        self.assertTrue(index.search('mango',mode='hybrid')['hits'])

    def test_withdrawal_blocks_before_new_encoding_or_reranking(self):
        index=self.index();before=len(self.encoder.calls)
        parent=index.corpus.passages[0]
        self.workspace.review(parent['source_id'],OWNER,PassageReview(expected_generation=index.corpus.scope.generation,
            chunk_id=parent['chunk_id'],content_hash=parent['content_hash'],action='withdraw',notes='Synthetic withdrawal.'))
        with self.assertRaises(ScopeChanged):index.search('mango')
        self.assertEqual(len(self.encoder.calls),before);self.assertEqual(self.ranker.calls,[])

    def test_unknown_candidates_never_reach_ranker(self):
        index=self.index()
        with self.assertRaises(ValueError):index.finish('mango',[dict(id='rogue',score=1.)],rerank=True,limit=3)
        self.assertEqual(self.ranker.calls,[])

    def test_invalid_reranker_output_blocks(self):
        index=self.index();self.ranker.score=lambda *args:{'unknown':1.}
        with self.assertRaises(ValueError):index.search('mango')

    def test_disabled_pinecone_sends_nothing(self):
        index=self.cloud_index();adapter,remote=self.adapter(enabled=False)
        with self.assertRaises(PermissionError):adapter.publish(index)
        self.assertEqual(remote.requests,[])

    def test_non_cloud_corpus_cannot_reach_adapter(self):
        adapter,remote=self.adapter()
        with self.assertRaises(PermissionError):adapter.publish(self.index())
        self.assertEqual(remote.requests,[])

    def test_publish_readback_and_query_parity(self):
        index=self.cloud_index();adapter,remote=self.adapter()
        manifest=adapter.publish(index,verify_delay=0)
        self.assertEqual(manifest['record_count'],len(index.units))
        local=index.search('Krishna mangoes',mode='hybrid',limit=3)
        cloud=adapter.search(index,'Krishna mangoes',rerank=False,limit=3)
        self.assertEqual([r['chunk_id'] for r in local['hits']],[r['chunk_id'] for r in cloud['hits']])
        self.assertFalse(any('mangoes' in r.content.decode() for r in remote.requests if r.content))
        self.assertNotIn('SYNTHETIC-KEY',str(adapter.attempts))
        self.assertTrue(all(r.headers['X-Pinecone-Api-Version']=='2025-10' for r in remote.requests))

    def test_visibility_delay_is_bounded_and_then_verified(self):
        index=self.cloud_index();adapter,remote=self.adapter();remote.hidden_reads=1
        adapter.publish(index,verify_attempts=2,verify_delay=0)
        self.assertTrue(adapter.registry.active(*adapter._identity(index)))

    def test_partial_publication_preserves_previous_generation(self):
        old=self.cloud_index();adapter,remote=self.adapter();adapter.publish(old,verify_delay=0)
        new_encoder=FixtureEncoder();new_encoder.signature='synthetic-encoder-v2'
        new=HybridIndex(old.corpus,new_encoder,self.ranker)
        remote.fail_host='sparse.svc.pinecone.io'
        with self.assertRaises(httpx.HTTPStatusError):adapter.publish(new,verify_delay=0)
        self.assertTrue(adapter.registry.active(*adapter._identity(old)))
        self.assertFalse(adapter.registry.active(*adapter._identity(new)))
        remote.fail_host=None
        self.assertTrue(adapter.search(old,'mango',rerank=False)['hits'])

    def test_invisible_records_do_not_become_active(self):
        index=self.cloud_index();adapter,remote=self.adapter();remote.hidden_reads=100
        with self.assertRaises(RuntimeError):adapter.publish(index,verify_attempts=1,verify_delay=0)
        self.assertFalse(adapter.registry.active(*adapter._identity(index)))

    def test_readback_metadata_mismatch_prevents_activation(self):
        index=self.cloud_index();adapter,remote=self.adapter();remote.bad_fetch_metadata=True
        with self.assertRaises(ValueError):adapter.publish(index,verify_delay=0)
        self.assertFalse(adapter.registry.active(*adapter._identity(index)))

    def test_withdrawn_cloud_permission_blocks_before_network(self):
        index=self.cloud_index();adapter,remote=self.adapter();adapter.publish(index,verify_delay=0)
        before=len(remote.requests);parent=index.corpus.passages[0]
        self.workspace.review(parent['source_id'],OWNER,PassageReview(expected_generation=index.corpus.scope.generation,
            chunk_id=parent['chunk_id'],content_hash=parent['content_hash'],action='withdraw',notes='Synthetic cloud withdrawal.'))
        with self.assertRaises(ScopeChanged):adapter.search(index,'mangoes')
        self.assertEqual(len(remote.requests),before)

    def test_stale_returned_metadata_blocks_reranking(self):
        index=self.cloud_index();adapter,remote=self.adapter();adapter.publish(index,verify_delay=0)
        remote.bad_metadata=True
        with self.assertRaises(ValueError):adapter.search(index,'mango')
        self.assertEqual(self.ranker.calls,[])

    def test_one_leg_query_failure_does_not_fallback(self):
        index=self.cloud_index();adapter,remote=self.adapter();adapter.publish(index,verify_delay=0)
        remote.query_failure='sparse.svc.pinecone.io'
        with self.assertRaises(httpx.HTTPStatusError):adapter.search(index,'mangoes')
        self.assertEqual(self.ranker.calls,[])

    def test_untrusted_host_rejected_before_credentials_can_be_sent(self):
        for host in ('http://dense.svc.pinecone.io','https://127.0.0.1','https://user:secret@dense.svc.pinecone.io'):
            with self.assertRaises(ValueError):PineconeAdapter(host,'https://sparse.svc.pinecone.io','key',None)
