"""Explicit account-bound source preparation and bounded background jobs.

No automatic attachment, permission grants, source approvals or live enablement.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time

from pypdf import PdfReader

from backend.contracts.common import content_hash, Identifier
from pydantic import TypeAdapter
from backend.workflows.checkpoints import run_lock
from backend.retrieval.llamacloud import IndexPublicationPlan, PublicationOutcomeUnknown
from .llamaparse import ParseRequest, ParseOutcomeUnknown


class HostedServiceBusy(RuntimeError):
    pass


class HostedSourceService:
    def __init__(self, folder, accounts, workspace, parser, reviews, publisher, *,
                 enabled=False, parser_tier='cost_effective', parser_version=None,
                 index_parse_config_id=None, index_usage=None, max_steps=5,
                 poll_interval=2.0, queue_limit=8, source_qa=None):
        if type(enabled) is not bool or type(max_steps) is not int or not 1 <= max_steps <= 10:
            raise ValueError('Invalid hosted worker configuration.')
        if type(queue_limit) is not int or not 1 <= queue_limit <= 16:
            raise ValueError('Invalid hosted queue limit.')
        if type(poll_interval) not in (int, float) or not 0 <= poll_interval <= 30:
            raise ValueError('Invalid hosted poll interval.')
        if parser.authorize != reviews.authorize_parse or publisher.reviews is not reviews:
            raise ValueError('Hosted adapters must share the trusted source-review authority.')
        if parser.settings != publisher.settings or reviews.settings != parser.settings:
            raise ValueError('Hosted adapters must share one explicit destination configuration.')
        self.folder, self.accounts, self.workspace = Path(folder), accounts, workspace
        self.parser, self.reviews, self.publisher = parser, reviews, publisher
        self.enabled, self.parser_tier, self.parser_version = enabled, parser_tier, parser_version
        if source_qa is not None and not callable(source_qa):raise TypeError('Internal source QA must be callable.')
        self.source_qa=source_qa
        self.index_parse_config_id = index_parse_config_id
        self.index_usage = index_usage or (lambda owner, manifest: None)
        self.max_steps, self.poll_interval, self.queue_limit = max_steps, poll_interval, queue_limit
        self.stop = threading.Event()
        self.lock, self.futures = threading.RLock(), {}
        self.pool = None
        if enabled:
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.db() as db:
                db.executescript('''CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY,owner TEXT NOT NULL,source TEXT NOT NULL,kind TEXT NOT NULL,
                  command TEXT NOT NULL,state TEXT NOT NULL,details TEXT NOT NULL,error TEXT);
                  CREATE TABLE IF NOT EXISTS task_requests (
                  owner TEXT,request_key TEXT,task TEXT,PRIMARY KEY(owner,request_key));''')
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hosted-sources')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.folder / 'hosted-source-tasks.sqlite', timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def close(self):
        self.stop.set()
        if self.pool:
            self.pool.shutdown(wait=True)

    def _require(self, owner):
        if not self.enabled or self.stop.is_set():
            raise LookupError('Hosted source preparation is not enabled.')
        self.accounts.require(owner, role=None)

    def source(self, owner, source):
        self._require(owner)
        summary = self.workspace.summary(source, owner)
        with self.db() as db:
            rows = db.execute('SELECT id,kind,state,details,error FROM tasks WHERE owner=? AND source=? '
                              'ORDER BY rowid DESC LIMIT 10', (owner, source)).fetchall()
        return dict(source_id=source, title=summary['title'], edition=summary['edition'],
                    original_hash=summary['original_hash'], snapshot_id=summary['snapshot_id'],
                    source_eligibility='not_automatically_approved',
                    jobs=[dict(job_id=row['id'], kind=row['kind'], state=row['state'],
                               details=json.loads(row['details']), error_code=row['error']) for row in rows])

    def upload(self, owner, stream, size, *, title, edition):
        self._require(owner)
        source = self.workspace.create(stream, size, owner=owner, title=title, edition=edition)
        return self.source(owner, source) | dict(state='awaiting_processing_permission')

    def parse_request(self, owner, source):
        self.source(owner, source)
        if not self.parser_version:
            raise ValueError('A verified parser version must be configured.')
        original = self.workspace.original(source, owner)
        try:
            reader = PdfReader(original, strict=True)
            if reader.is_encrypted:
                raise ValueError('Encrypted PDFs are unsupported.')
            count = len(reader.pages)
        except Exception:
            raise ValueError('The uploaded PDF could not be validated.') from None
        summary = self.workspace.summary(source, owner)
        return ParseRequest(owner_id=owner, source_id=source, snapshot_id=summary['snapshot_id'],
            original_file_hash=summary['original_hash'], expected_page_count=count,
            tier=self.parser_tier, parser_version=self.parser_version)

    def _task(self, owner, task):
        self._require(owner)
        with self.db() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=? AND owner=?', (task, owner)).fetchone()
        if not row:
            raise PermissionError('Source job is unavailable for this account.')
        if content_hash([owner, row['source'], row['kind'], json.loads(row['command'])]) != task:
            raise ValueError('Saved source-job identity changed.')
        return dict(row)

    def status(self, owner, task):
        row = self._task(owner, task)
        return dict(job_id=row['id'], source_id=row['source'], kind=row['kind'], state=row['state'],
                    details=json.loads(row['details']), error_code=row['error'])

    def _save(self, owner, task, state, details, error=None):
        details['updated_at'] = time.time()
        with self.db() as db:
            db.execute('UPDATE tasks SET state=?,details=?,error=? WHERE id=? AND owner=?',
                       (state, json.dumps(details), error, task, owner))

    def _schedule(self, owner, task):
        self._require(owner)
        with self.lock:
            old = self.futures.get(task)
            if old and not old.done():
                return
            self.futures = {key: value for key, value in self.futures.items() if not value.done()}
            if len(self.futures) >= self.queue_limit:
                raise HostedServiceBusy('Source-processing queue is full; try again later.')
            self.futures[task] = self.pool.submit(self._execute, owner, task)

    def _queue(self, owner, source, kind, command, request_key):
        self._require(owner)
        TypeAdapter(Identifier).validate_python(request_key, strict=True)
        key = content_hash([owner, source, kind, command])
        with self.lock:
            same = self.futures.get(key)
            if sum(not future.done() for future in self.futures.values()) >= self.queue_limit and not (same and not same.done()):
                raise HostedServiceBusy('Source-processing queue is full; try again later.')
            with self.db() as db:
                previous = db.execute('SELECT task FROM task_requests WHERE owner=? AND request_key=?',
                                      (owner, request_key)).fetchone()
                if previous and previous['task'] != key:
                    raise ValueError('Request key was reused with different source-processing inputs.')
                db.execute('INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,NULL)',
                    (key, owner, source, kind, json.dumps(command), 'queued', json.dumps({'created_at': time.time()})))
                db.execute('INSERT OR IGNORE INTO task_requests VALUES(?,?,?)', (owner, request_key, key))
            state = self._task(owner, key)['state']
            if state == 'queued':
                self._schedule(owner, key)
        return dict(job_id=key, state=self.status(owner, key)['state'])

    def start_parse(self, owner, source, request_key):
        spec = self.parse_request(owner, source)
        if self.reviews.authorize_parse(spec, 'submit') is not True:
            raise PermissionError('Current cloud-processing permission and allowance are required.')
        return self._queue(owner, source, 'parse', spec.model_dump(mode='json'), request_key)

    def start_publication(self, owner, extraction, purpose, request_key):
        self._require(owner)
        manifest = self.reviews.eligible(owner, extraction, purpose=purpose)
        plan = IndexPublicationPlan(manifest=manifest, parse_config_id=self.index_parse_config_id,
                                    usage_authorization_id=self.index_usage(owner, manifest))
        self.publisher._guard(owner, plan, 'stage')
        return self._queue(owner, manifest.source_id, 'publication', plan.model_dump(mode='json'), request_key)

    def resume(self, owner, task):
        row = self._task(owner, task)
        if row['state'] not in ('queued', 'running', 'waiting_provider', 'paused'):
            raise ValueError('This job cannot be resumed automatically; review its saved outcome.')
        self._schedule(owner, task)
        return dict(job_id=task, state=self.status(owner, task)['state'])

    def activate(self, actor, owner, task):
        self.accounts.require(actor, 'admin')
        row = self._task(owner, task)
        details = json.loads(row['details'])
        if row['kind'] != 'publication' or details.get('phase') != 'awaiting_activation':
            raise ValueError('Verify this publication before activation.')
        result = self.publisher.activate(owner, details['publication_id'])
        self._save(owner, task, 'completed', details | dict(phase='active'))
        return result

    def _execute(self, owner, task):
        try:
            with run_lock(self.folder, task):
                row = self._task(owner, task)
                details = json.loads(row['details'])
                self._save(owner, task, 'running', details)
                try:
                    if row['kind'] == 'parse':
                        spec = ParseRequest.model_validate_json(row['command'])
                        key = details.get('parse_job_id')
                        if key is None:
                            key = self.parser.start(spec, self.workspace.original(row['source'], owner).read_bytes())
                            details['parse_job_id'] = key
                            self._save(owner, task, 'running', details)
                        for _ in range(self.max_steps):
                            if self.stop.is_set():
                                break
                            self._require(owner)
                            status = self.parser.poll(owner, key)
                            details['provider_state'] = status['state']
                            if status['state'] == 'extracted':
                                details.update(extraction_id=self.reviews.capture(owner, self.parser, key),
                                               phase='awaiting_internal_source_qa' if self.source_qa else 'awaiting_source_review')
                                if self.source_qa:
                                    if details.get('source_qa_started') and not details.get('source_qa_results'):
                                        raise ParseOutcomeUnknown('Reconcile the existing internal source QA operation.')
                                    if not details.get('source_qa_started'):
                                        details['source_qa_started']=True
                                        self._save(owner,task,'running',details)
                                        details['source_qa_results']=self.source_qa(owner,details['extraction_id'],self.reviews)
                                    details['phase']='source_qa_passed' if any(r.get('status')=='PASS' and r.get('automated_review_id') for r in details['source_qa_results']) else 'source_qa_blocked'
                                self._save(owner, task, 'completed', details)
                                return
                            if status['state'] != 'pending':
                                if status['state'] == 'outcome_unknown':
                                    raise ParseOutcomeUnknown('Reconcile prior parsing submission.')
                                raise ValueError('Parsing result requires review.')
                            if self.stop.wait(self.poll_interval):
                                break
                    else:
                        plan = IndexPublicationPlan.model_validate_json(row['command'])
                        key = details.get('publication_id')
                        if key is None:
                            key = self.publisher.stage(owner, plan)
                            details['publication_id'] = key
                            self._save(owner, task, 'running', details)
                        for _ in range(self.max_steps):
                            if self.stop.is_set():
                                break
                            self._require(owner)
                            status = self.publisher.check(owner, key)
                            details['provider_state'] = status['state']
                            if status['state'] == 'verified':
                                details['phase'] = 'awaiting_activation'
                                self._save(owner, task, 'completed', details)
                                return
                            if status['state'] != 'indexing':
                                raise ValueError('Publication requires review.')
                            if self.stop.wait(self.poll_interval):
                                break
                    self._save(owner, task, 'paused' if self.stop.is_set() else 'waiting_provider', details)
                except (ParseOutcomeUnknown, PublicationOutcomeUnknown):
                    self._save(owner, task, 'needs_attention', details, 'submission_needs_reconciliation')
                except Exception:
                    self._save(owner, task, 'needs_attention', details, 'source_job_blocked')
        except RuntimeError:
            # Another worker owns this persistent job. Do not overwrite its state.
            return
