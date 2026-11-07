"""Persistent, owner-scoped large-PDF intake and source-review records.

Parent decisions are explicitly parent-inspiration reviews, never qualified
approval for traditional claims. No index publication or model calls occur.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from time import time
from uuid import uuid4
from pydantic import Field
from backend.contracts.common import Contract, Digest, Identifier, Text, canonical_json, content_hash, text_hash
from backend.workflows.checkpoints import run_lock
from .processing import page_record, detect_boundaries, validate_boundaries, build_chunks

MAX_PDF_BYTES=50*1024*1024
MAX_PAGES=3000
MAX_EXTRACTED_CHARACTERS=12_000_000


class PassageReview(Contract):
    expected_generation: int = Field(ge=1)
    chunk_id: Identifier
    content_hash: Digest
    action: str = Field(pattern=r'^(allow|restrict|reject|withdraw)$')
    extraction_checked: bool = False
    cultural_context_checked: bool = False
    child_suitability_checked: bool = False
    rights_permitted: bool = False
    rights_basis: Text | None = None
    notes: Text
    external_processing_permitted: bool = False
    pinecone_storage_permitted: bool = False


class SourceWorkspace:
    def __init__(self, folder):
        self.folder=Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        (self.folder/'originals').mkdir(exist_ok=True)
        self.path=self.folder/'source-workspace.sqlite'
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='source-ingestion')
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents (
                  id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,edition TEXT NOT NULL,
                  original_hash TEXT NOT NULL,status TEXT NOT NULL,page_count INTEGER,
                  generation INTEGER NOT NULL DEFAULT 0,epoch INTEGER NOT NULL DEFAULT 0,
                  audit TEXT,error TEXT,created REAL NOT NULL,
                  UNIQUE(owner,original_hash,title,edition));
                CREATE TABLE IF NOT EXISTS pages (
                  source_id TEXT,page INTEGER,payload TEXT NOT NULL,PRIMARY KEY(source_id,page));
                CREATE TABLE IF NOT EXISTS generations (
                  source_id TEXT,generation INTEGER,payload TEXT NOT NULL,
                  PRIMARY KEY(source_id,generation));
                CREATE TABLE IF NOT EXISTS chunks (
                  source_id TEXT,generation INTEGER,id TEXT,payload TEXT NOT NULL,
                  PRIMARY KEY(source_id,generation,id));
                CREATE TABLE IF NOT EXISTS source_reviews (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT,generation INTEGER,chunk_id TEXT,
                  owner TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
            ''')

    def close(self):self.pool.shutdown(wait=True)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:yield db
        finally:db.close()

    def _record(self, source_id, owner):
        with self.db() as db:
            row=db.execute('SELECT * FROM documents WHERE id=? AND owner=?',(source_id,owner)).fetchone()
        if not row:raise PermissionError('Source not found in this parent session.')
        return dict(row)

    def create(self, stream, size, *, owner, title, edition):
        if type(size) is not int or not 0<size<=MAX_PDF_BYTES:raise ValueError('Choose a PDF up to 50 MB.')
        if any(not isinstance(v,str) or not v.strip() or len(v)>240 for v in (owner,title,edition)):
            raise ValueError('Parent identity, title and edition label are required. Use unknown if the edition is unknown.')
        temp=self.folder/'originals'/('upload-'+uuid4().hex+'.tmp')
        digest=hashlib.sha256();remaining=size
        try:
            with temp.open('wb') as file:
                while remaining:
                    block=stream.read(min(65536,remaining))
                    if not block:raise ValueError('The PDF upload was incomplete.')
                    file.write(block);digest.update(block);remaining-=len(block)
            with temp.open('rb') as file:
                if file.read(5)!=b'%PDF-':raise ValueError('Choose a valid PDF.')
            original_hash=digest.hexdigest()
            original=self.folder/'originals'/(original_hash+'.pdf')
            if original.exists():
                with original.open('rb') as stored:
                    if hashlib.file_digest(stored,'sha256').hexdigest()!=original_hash:
                        raise ValueError('Stored original failed integrity verification.')
            else:temp.replace(original)
            source_id=uuid4().hex
            with self.db() as db:
                db.execute('INSERT OR IGNORE INTO documents '
                    '(id,owner,title,edition,original_hash,status,created) VALUES(?,?,?,?,?,?,?)',
                    (source_id,owner,title.strip(),edition.strip(),original_hash,'queued',time()))
                row=db.execute('SELECT id FROM documents WHERE owner=? AND original_hash=? AND title=? AND edition=?',
                    (owner,original_hash,title.strip(),edition.strip())).fetchone()
            return row['id']
        finally:
            temp.unlink(missing_ok=True)

    def original(self, source_id, owner):
        row=self._record(source_id,owner)
        path=self.folder/'originals'/(row['original_hash']+'.pdf')
        with path.open('rb') as file:
            if hashlib.file_digest(file,'sha256').hexdigest()!=row['original_hash']:
                raise ValueError('Original PDF failed integrity verification.')
        return path

    def submit(self, source_id, owner):
        self._record(source_id,owner)
        return self.pool.submit(self.process,source_id,owner)

    def process(self, source_id, owner, *, batch_size=20):
        self._record(source_id,owner)
        with run_lock(self.folder,source_id):
            row=self._record(source_id,owner)
            if row['status']=='ready':return self.summary(source_id,owner)
            try:
                from pypdf import PdfReader
                with self.original(source_id,owner).open('rb') as original:
                    reader=PdfReader(original)
                    if reader.is_encrypted:raise ValueError('Password-protected PDFs are not supported.')
                    count=len(reader.pages)
                    if not 0<count<=MAX_PAGES:raise ValueError('Use a PDF with 1–3,000 pages.')
                    with self.db() as db:
                        db.execute('UPDATE documents SET status=?,page_count=?,error=NULL WHERE id=?',('extracting',count,source_id))
                        existing={r['page']:json.loads(r['payload']) for r in db.execute('SELECT * FROM pages WHERE source_id=?',(source_id,))}
                    size=sum(len(p['raw_text']) for p in existing.values())
                    batch=[]
                    for index in range(count):
                        if index+1 in existing:continue
                        page=page_record(index+1,reader.pages[index].extract_text() or '')
                        size+=len(page['raw_text'])
                        if size>MAX_EXTRACTED_CHARACTERS:raise ValueError('Extracted text exceeds the 12 million character processing limit.')
                        batch.append(page)
                        if len(batch)>=batch_size:
                            self._save_pages(source_id,batch);batch=[]
                    self._save_pages(source_id,batch)
                pages=self._pages(source_id)
                if not any(p['clean_text'] for p in pages):raise ValueError('No readable text found. OCR is not available; use a text-based PDF.')
                self._generation(source_id,owner,pages,detect_boundaries(pages),confirmed=False,target=300,overlap=50)
            except Exception as error:
                message=str(error) if isinstance(error,ValueError) else 'This PDF could not be processed. Check the original file.'
                with self.db() as db:db.execute('UPDATE documents SET status=?,error=? WHERE id=?',('failed',message,source_id))
                raise
            return self.summary(source_id,owner)

    def _save_pages(self, source_id, pages):
        with self.db() as db:
            for page in pages:
                db.execute('INSERT OR IGNORE INTO pages VALUES(?,?,?)',(source_id,page['pdf_page'],canonical_json(page)))

    def _pages(self, source_id):
        with self.db() as db:
            pages=[json.loads(row['payload']) for row in db.execute('SELECT payload FROM pages WHERE source_id=? ORDER BY page',(source_id,))]
        from backend.contracts.common import text_hash
        for page in pages:
            if text_hash(page['raw_text'])!=page['raw_hash'] or text_hash(page['clean_text'])!=page['clean_hash']:
                raise ValueError('Stored page failed integrity verification.')
        return pages

    def page(self, source_id, owner, number):
        self._record(source_id,owner)
        with self.db() as db:row=db.execute('SELECT payload FROM pages WHERE source_id=? AND page=?',(source_id,number)).fetchone()
        if not row:raise ValueError('This page has not been extracted yet.')
        page=json.loads(row['payload'])
        if text_hash(page['raw_text'])!=page['raw_hash'] or text_hash(page['clean_text'])!=page['clean_hash']:
            raise ValueError('Stored page failed integrity verification.')
        return page

    def _generation(self, source_id, owner, pages, boundaries, *, confirmed,target,overlap):
        row=self._record(source_id,owner);generation=row['generation']+1
        edition_id='edition-'+content_hash([row['original_hash'],row['edition']])[:24]
        snapshot='snapshot-'+content_hash([row['original_hash'],edition_id])
        if confirmed:
            boundaries=[dict(b,confidence='reviewed') for b in boundaries]
        sections,chunks,audit=build_chunks(pages,boundaries,snapshot_id=snapshot,generation=generation,target=target,overlap=overlap)
        for chunk in chunks:
            chunk.update(source_id=source_id,document_title=row['title'],edition_id=edition_id,source_snapshot_id=snapshot)
        if not audit['coverage_pass']:raise ValueError('Chunk coverage validation failed.')
        payload=dict(boundaries=boundaries,sections=sections,target_words=target,overlap_words=overlap,
                     boundaries_confirmed=confirmed,audit=audit)
        with self.db() as db:
            current=db.execute('SELECT generation FROM documents WHERE id=?',(source_id,)).fetchone()['generation']
            if current!=row['generation']:raise ValueError('The source generation changed. Refresh before editing.')
            db.execute('INSERT INTO generations VALUES(?,?,?)',(source_id,generation,canonical_json(payload)))
            db.executemany('INSERT INTO chunks VALUES(?,?,?,?)',
                           ((source_id,generation,c['chunk_id'],canonical_json(c)) for c in chunks))
            db.execute('UPDATE documents SET generation=?,status=?,audit=?,epoch=epoch+1,error=NULL WHERE id=?',
                       (generation,'ready',canonical_json(audit),source_id))

    def boundaries(self, source_id, owner, expected_generation, boundaries, *, target=300,overlap=50):
        row=self._record(source_id,owner)
        if row['status']!='ready' or row['generation']!=expected_generation:
            raise ValueError('Refresh the source before confirming boundaries.')
        with run_lock(self.folder,source_id):
            row=self._record(source_id,owner)
            if row['status']!='ready' or row['generation']!=expected_generation:
                raise ValueError('Refresh the source before confirming boundaries.')
            pages=self._pages(source_id)
            boundaries=validate_boundaries(pages,boundaries)
            self._generation(source_id,owner,pages,boundaries,confirmed=True,target=target,overlap=overlap)
        return self.summary(source_id,owner)

    def summary(self, source_id, owner):
        row=self._record(source_id,owner)
        with self.db() as db:
            extracted=db.execute('SELECT COUNT(*) FROM pages WHERE source_id=?',(source_id,)).fetchone()[0]
            count=db.execute('SELECT COUNT(*) FROM chunks WHERE source_id=? AND generation=?',(source_id,row['generation'])).fetchone()[0]
            generation=db.execute('SELECT payload FROM generations WHERE source_id=? AND generation=?',(source_id,row['generation'])).fetchone()
        metadata=json.loads(generation['payload']) if generation else None
        return {k:row[k] for k in ('id','title','edition','original_hash','status','page_count','generation','epoch','error')}|dict(
            extracted_pages=extracted,chunk_count=count,snapshot_id='snapshot-'+content_hash([
                row['original_hash'],'edition-'+content_hash([row['original_hash'],row['edition']])[:24]]),
            edition_id='edition-'+content_hash([row['original_hash'],row['edition']])[:24],
            metadata=metadata,authority='parent',traditional_claims_eligible=False,index_status='not_indexed')

    def list(self, owner):
        with self.db() as db:
            rows=db.execute('SELECT id,title,edition,status,page_count,generation FROM documents WHERE owner=? ORDER BY created DESC',(owner,)).fetchall()
        return [dict(row) for row in rows]

    def chunks(self, source_id, owner, *, offset=0,limit=20):
        row=self._record(source_id,owner)
        if not 0<=offset or not 1<=limit<=100:raise ValueError('Invalid passage range.')
        with self.db() as db:
            rows=db.execute('SELECT payload FROM chunks WHERE source_id=? AND generation=? ORDER BY rowid LIMIT ? OFFSET ?',
                            (source_id,row['generation'],limit,offset)).fetchall()
        return [json.loads(r['payload'])|self.eligibility(source_id,owner,json.loads(r['payload'])['chunk_id']) for r in rows]

    def review(self, source_id, owner, command):
        with run_lock(self.folder,source_id):
            row=self._record(source_id,owner)
            if row['status']!='ready' or row['generation']!=command.expected_generation:
                raise ValueError('The passage version changed. Refresh and review it again.')
            with self.db() as db:
                chunk=db.execute('SELECT payload FROM chunks WHERE source_id=? AND generation=? AND id=?',
                                 (source_id,row['generation'],command.chunk_id)).fetchone()
                metadata=json.loads(db.execute('SELECT payload FROM generations WHERE source_id=? AND generation=?',
                                              (source_id,row['generation'])).fetchone()['payload'])
                if not chunk or json.loads(chunk['payload'])['content_hash']!=command.content_hash:
                    raise ValueError('The reviewed passage hash does not match.')
                saved=json.loads(chunk['payload'])
                if text_hash(saved['text'])!=saved['content_hash']:
                    raise ValueError('Stored passage failed integrity verification.')
                if command.action=='allow' and (not metadata['boundaries_confirmed'] or not all((
                    command.extraction_checked,command.cultural_context_checked,command.child_suitability_checked,
                    command.rights_permitted,command.rights_basis))):
                    raise ValueError('Confirm boundaries, extraction, cultural context, suitability and permission before allowing a passage.')
                payload=command.model_dump(mode='json')|dict(authority='parent',scope='parent_inspiration_only',
                    expert_certification=False,edition_id='edition-'+content_hash([row['original_hash'],row['edition']])[:24])
                db.execute('INSERT INTO source_reviews(source_id,generation,chunk_id,owner,payload,created) VALUES(?,?,?,?,?,?)',
                    (source_id,row['generation'],command.chunk_id,owner,canonical_json(payload),time()))
                db.execute('UPDATE documents SET epoch=epoch+1 WHERE id=?',(source_id,))
        return self.eligibility(source_id,owner,command.chunk_id)

    def eligibility(self, source_id, owner, chunk_id, *, purpose='parent_inspiration'):
        row=self._record(source_id,owner)
        with self.db() as db:
            chunk=db.execute('SELECT payload FROM chunks WHERE source_id=? AND generation=? AND id=?',
                             (source_id,row['generation'],chunk_id)).fetchone()
            review=db.execute('SELECT id,payload FROM source_reviews WHERE source_id=? AND generation=? AND chunk_id=? AND owner=? ORDER BY id DESC LIMIT 1',
                (source_id,row['generation'],chunk_id,owner)).fetchone()
        decision=json.loads(review['payload']) if review else None
        saved=json.loads(chunk['payload']) if chunk else None
        valid=bool(saved and text_hash(saved['text'])==saved['content_hash'])
        eligible=bool(row['status']=='ready' and purpose=='parent_inspiration' and valid and decision and
                      decision['action']=='allow' and decision['content_hash']==saved['content_hash'])
        return dict(eligibility_status='eligible_parent_inspiration' if eligible else 'blocked',
            review_status=decision['action'] if decision else 'pending',eligibility_epoch=row['epoch'],
            review_record_ids=[str(review['id'])] if review else [],traditional_claims_eligible=False,
            external_processing_permitted=bool(eligible and decision['external_processing_permitted']),
            pinecone_storage_permitted=bool(eligible and decision.get('pinecone_storage_permitted',False)),
            rights_status='permitted_by_parent' if eligible else 'unverified',review=decision)
