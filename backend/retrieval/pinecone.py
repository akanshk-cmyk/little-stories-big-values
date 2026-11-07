"""Opt-in Vectors API adapter. No index provisioning or automatic fallback.

Separate dense/sparse indexes use version-prefixed IDs, so a partial staging
write cannot overwrite the previous active generation. Source text stays local.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
from backend.contracts.common import canonical_json,content_hash
from backend.workflows.checkpoints import run_lock
from .algorithms import reciprocal_rank_fusion

API_VERSION='2025-10'


class PublicationRegistry:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'retrieval-publications.sqlite'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS publications (
                id TEXT PRIMARY KEY,scope TEXT,status TEXT,payload TEXT,error_code TEXT);
                CREATE TABLE IF NOT EXISTS active_indexes(scope TEXT PRIMARY KEY,publication TEXT);''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=20)
        try:
            with db:yield db
        finally:db.close()

    def begin(self,key,scope,manifest):
        payload=canonical_json(manifest)
        with self.db() as db:
            row=db.execute('SELECT payload FROM publications WHERE id=?',(key,)).fetchone()
            if row and row[0]!=payload:raise ValueError('Publication identity was reused with different records.')
            db.execute('INSERT INTO publications VALUES(?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET status=excluded.status,error_code=NULL',
                       (key,scope,'staging',payload))

    def active(self,key,scope):
        with self.db() as db:
            return db.execute('SELECT 1 FROM active_indexes a JOIN publications p ON a.publication=p.id WHERE a.scope=? AND p.id=? AND p.status=?',
                              (scope,key,'active')).fetchone() is not None

    def promote(self,key,scope):
        with self.db() as db:
            db.execute("UPDATE publications SET status='retired' WHERE id IN (SELECT publication FROM active_indexes WHERE scope=?)",(scope,))
            db.execute("UPDATE publications SET status='active' WHERE id=? AND status='staging'",(key,))
            if db.execute('SELECT changes()').fetchone()[0]!=1:raise ValueError('Only a validated staged generation may be promoted.')
            db.execute('INSERT OR REPLACE INTO active_indexes VALUES(?,?)',(scope,key))

    def fail(self,key,error_code):
        with self.db() as db:db.execute("UPDATE publications SET status='failed',error_code=? WHERE id=?",(error_code,key))


class PineconeAdapter:
    def __init__(self,dense_host,sparse_host,api_key,registry,*,enabled=False,client=None):
        self.dense_host=self._host(dense_host);self.sparse_host=self._host(sparse_host)
        if self.dense_host==self.sparse_host:raise ValueError('This adapter requires two distinct indexes.')
        self.api_key=api_key;self.registry=registry;self.enabled=enabled;self.client=client
        self.owns_client=False;self.attempts=[]

    @staticmethod
    def _host(value):
        parsed=urlsplit(value)
        if (parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.pinecone.io') or
            parsed.username or parsed.password or parsed.port not in (None,443) or parsed.path not in ('','/') or parsed.query or parsed.fragment):
            raise ValueError('Use an explicit trusted Pinecone HTTPS index host.')
        return value.rstrip('/')

    def close(self):
        if self.owns_client:self.client.close()

    def _enabled(self,index):
        if not self.enabled:raise PermissionError('Pinecone is disabled; no network request was sent.')
        if not index.corpus.cloud:raise PermissionError('Load an explicitly cloud-authorized corpus first.')
        if not index.units:raise PermissionError('No eligible material may be published or queried.')
        if not isinstance(self.api_key,str) or not self.api_key.strip():raise PermissionError('Pinecone credentials are not configured.')
        index.corpus.assert_current()

    def _request(self,host,method,route,**kwargs):
        if not self.enabled:raise PermissionError('Pinecone is disabled.')
        if self.client is None:
            import httpx
            self.client=httpx.Client(timeout=20,follow_redirects=False,trust_env=False);self.owns_client=True
        started=time.perf_counter();status='failed'
        try:
            response=self.client.request(method,host+route,headers={'Api-Key':self.api_key,'X-Pinecone-Api-Version':API_VERSION},**kwargs)
            response.raise_for_status();result=response.json();status='succeeded'
            return result
        finally:
            self.attempts.append(dict(operation=route,status=status,latency_ms=round((time.perf_counter()-started)*1000,3)))

    def _identity(self,index):
        scope=content_hash([index.corpus.namespace,index.corpus.scope.model_dump(mode='json')])
        key=content_hash([scope,index.index_version,self.dense_host,self.sparse_host])
        return key,scope

    def records(self,index):
        dense=[];sparse=[]
        for unit,vector in zip(index.units,index.vectors):
            parent=index.parents[unit['chunk_id']]
            # Version prefix isolates staging writes from active records.
            key=index.index_version+'-'+unit['id']
            metadata=dict(index_version=index.index_version,corpus_hash=index.corpus.corpus_hash,
                sparse_model_hash=index.sparse.model_hash,encoder_signature=index.encoder.signature,
                source_id=parent['source_id'],edition_id=parent['edition_id'],snapshot_id=parent['source_snapshot_id'],
                generation=index.corpus.scope.generation,eligibility_epoch=index.corpus.epoch,
                unit_id=unit['id'],chunk_id=unit['chunk_id'],content_hash=unit['content_hash'],
                parent_content_hash=parent['content_hash'],start=unit['start'],end=unit['end'],
                pdf_pages=[str(page) for page in unit['pdf_pages']],review_scope='parent_inspiration_only')
            dense.append(dict(id=key,values=vector,metadata=metadata))
            sparse.append(dict(id=key,sparseValues=index.sparse.vector(index.sparse.documents[unit['id']]),metadata=metadata))
        return dense,sparse

    @staticmethod
    def _same_vector(stored,expected):
        return len(stored)==len(expected) and all(type(a) in (int,float) and math.isfinite(a) and
            math.isclose(a,b,rel_tol=1e-5,abs_tol=1e-6) for a,b in zip(stored,expected))

    def publish(self,index,*,verify_attempts=3,verify_delay=.2):
        self._enabled(index)
        if type(verify_attempts) is not int or not 1<=verify_attempts<=5 or not 0<=verify_delay<=2:
            raise ValueError('Use bounded publication verification settings.')
        key,scope=self._identity(index)
        dense,sparse=self.records(index)
        if any(not row['sparseValues']['indices'] for row in sparse):
            raise ValueError('A passage has no sparse terms; strict two-index publication is blocked.')
        manifest=dict(index_version=index.index_version,namespace=index.corpus.namespace,dimension=index.encoder.dimension,
            corpus_hash=index.corpus.corpus_hash,sparse_model_hash=index.sparse.model_hash,
            dense_host=self.dense_host,sparse_host=self.sparse_host,record_count=len(dense),
            records_hash=content_hash([dense,sparse]),api_version=API_VERSION)
        with run_lock(self.registry.folder,scope):
            if self.registry.active(key,scope):return manifest
            self.registry.begin(key,scope,manifest)
            try:
                for host,records in ((self.dense_host,dense),(self.sparse_host,sparse)):
                    for start in range(0,len(records),100):
                        index.corpus.assert_current()
                        batch=records[start:start+100]
                        result=self._request(host,'POST','/vectors/upsert',json=dict(namespace=index.corpus.namespace,vectors=batch))
                        if type(result.get('upsertedCount')) is not int or result['upsertedCount']!=len(batch):raise ValueError('Incomplete index write; generation remains inactive.')
                for attempt in range(verify_attempts):
                    complete=True
                    for host,records in ((self.dense_host,dense),(self.sparse_host,sparse)):
                        for start in range(0,len(records),100):
                            batch=records[start:start+100]
                            fetched=self._request(host,'GET','/vectors/fetch',params=[('namespace',index.corpus.namespace)]+[('ids',r['id']) for r in batch])
                            if fetched.get('namespace')!=index.corpus.namespace:raise ValueError('Fetch returned a different namespace.')
                            values=fetched.get('vectors',{})
                            if not set(values)<=set(r['id'] for r in batch):raise ValueError('Fetch returned out-of-scope records.')
                            for expected in batch:
                                stored=values.get(expected['id'])
                                if not stored:complete=False;continue
                                if stored.get('id')!=expected['id'] or stored.get('metadata')!=expected['metadata']:
                                    raise ValueError('Index read-back metadata mismatch.')
                                if 'values' in expected:
                                    valid=self._same_vector(stored.get('values',[]),expected['values'])
                                else:
                                    actual=stored.get('sparseValues',{})
                                    valid=actual.get('indices')==expected['sparseValues']['indices'] and self._same_vector(actual.get('values',[]),expected['sparseValues']['values'])
                                if not valid:raise ValueError('Index read-back vector mismatch.')
                    if complete:break
                    if attempt+1<verify_attempts:time.sleep(verify_delay)
                if not complete:raise RuntimeError('Index visibility did not converge within the verification budget.')
                index.corpus.assert_current();self.registry.promote(key,scope)
            except Exception as error:
                self.registry.fail(key,type(error).__name__)
                raise
        return manifest

    def search(self,index,query,*,limit=5,rerank=True):
        self._enabled(index)
        if not query.strip() or type(limit) is not int or not 1<=limit<=20:raise ValueError('Invalid query or limit.')
        key,scope=self._identity(index)
        if not self.registry.active(key,scope):raise PermissionError('Both indexes must be verified and active before querying.')
        if index.encoder.count(query,query=True)>index.encoder.limit:raise ValueError('Query token budget exceeded.')
        expected={row['id']:row for row in self.records(index)[0]}
        filter=dict(index_version={'$eq':index.index_version},corpus_hash={'$eq':index.corpus.corpus_hash},
            sparse_model_hash={'$eq':index.sparse.model_hash},eligibility_epoch={'$eq':index.corpus.epoch})
        base=dict(namespace=index.corpus.namespace,topK=20,includeMetadata=True,includeValues=False,filter=filter)
        dense=self._request(self.dense_host,'POST','/query',json=base|{'vector':index.encoder.encode([query],query=True)[0]})
        weights=index.sparse.query_weights(query)
        sparse=self._request(self.sparse_host,'POST','/query',json=base|{'sparseVector':index.sparse.vector(weights)}) if weights else dict(matches=[],namespace=index.corpus.namespace)
        def validate(result,positive_only=False):
            if result.get('namespace')!=index.corpus.namespace:raise ValueError('Query returned a different namespace.')
            hits=[];seen=set()
            for row in result['matches']:
                target=expected.get(row['id']);score=row['score']
                if not target or row.get('metadata')!=target['metadata'] or type(score) not in (int,float) or not math.isfinite(score):
                    raise ValueError('Query returned unknown, stale or invalid records.')
                if row['id'] in seen:raise ValueError('Query returned duplicate records.')
                seen.add(row['id'])
                if not positive_only or score>0:hits.append(dict(id=target['metadata']['unit_id'],score=score))
            return sorted(hits,key=lambda h:(-h['score'],h['id']))
        hits=reciprocal_rank_fusion(validate(dense),validate(sparse,True))
        index.corpus.assert_current()
        return dict(method='pinecone_hybrid_rerank' if rerank else 'pinecone_hybrid',
            hits=index.finish(query,hits,rerank=rerank,limit=limit),index_version=index.index_version,
            corpus_hash=index.corpus.corpus_hash,evidence_sufficiency='PENDING',external_processing=True)
