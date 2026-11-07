"""Opt-in LlamaParse v2 adapter. No routes, automatic workers or indexing.

Trusted callers provide bytes, source identity and an authorization callback.
State is committed before POSTs: uncertain outcomes never auto-resubmit.
"""
from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import time
from typing import Literal

import httpx
from pydantic import Field, model_validator
from pypdf import PdfReader

from backend.contracts.common import Contract, Digest, Identifier, content_hash, text_hash
from backend.contracts.hosted_retrieval import ParsedDocument, ParsedPage
from backend.workflows.checkpoints import run_lock


class ParseRequest(Contract):
    owner_id: Identifier
    source_id: Identifier
    snapshot_id: Identifier
    original_file_hash: Digest
    expected_page_count: int = Field(ge=1, le=3000)
    tier: Literal['cost_effective', 'agentic', 'agentic_plus']
    parser_version: Identifier

    @model_validator(mode='after')
    def pinned_version(self):
        if self.parser_version.lower() == 'latest':
            raise ValueError('Choose an explicit parser version before processing.')
        return self


class ParseBlocked(RuntimeError):
    """Safe error without vendor payloads, source text or credentials."""


class ParseOutcomeUnknown(ParseBlocked):
    pass


class ParseJobs:
    """Separate durable records; originals/source review tables are not changed."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'llamaparse-jobs.sqlite'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs '
                       '(id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, owner, key):
        with self.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=? AND owner=?', (key, owner)).fetchone()
        if not row:
            raise PermissionError('Parsing job is unavailable for this account.')
        return json.loads(row[0])

    def create(self, key, spec, destination):
        value = dict(request=spec.model_dump(mode='json'), destination=destination,
                     state='new', file_id=None, remote_job_id=None, polls=0,
                     error_code=None, document=None, attempts=[])
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?)',
                       (key, spec.owner_id, json.dumps(value)))
        return self.get(spec.owner_id, key)

    def save(self, owner, key, value):
        with self.db() as db:
            cursor = db.execute('UPDATE jobs SET payload=? WHERE id=? AND owner=?',
                                (json.dumps(value, allow_nan=False), key, owner))
            if cursor.rowcount != 1:
                raise PermissionError('Parsing job is unavailable for this account.')


class LlamaParseAdapter:
    def __init__(self, settings, jobs, *, authorize=None, allow_live_calls=False,
                 transport=None, max_polls=60):
        if type(allow_live_calls) is not bool:
            raise ValueError('Live-call authorization must be boolean.')
        if transport is not None and not isinstance(transport, httpx.MockTransport) and not allow_live_calls:
            raise PermissionError('Offline operation requires an explicit mock transport.')
        if type(max_polls) is not int or not 1 <= max_polls <= 120:
            raise ValueError('Invalid parsing poll limit.')
        self.settings, self.jobs = settings, jobs
        self.authorize = authorize or (lambda request, operation: False)
        self.allow_live_calls, self.transport, self.max_polls = allow_live_calls, transport, max_polls
        self.base = {'us': 'https://api.cloud.llamaindex.ai',
                     'eu': 'https://api.cloud.eu.llamaindex.ai'}[settings.region]

    def _guard(self, spec, operation):
        settings = self.settings
        if not settings.enabled or settings.api_generation != 'v2':
            raise PermissionError('LlamaParse v2 processing is disabled.')
        if not (settings.api_key.get_secret_value().strip() and settings.organization_id and settings.project_id):
            raise PermissionError('Parsing credentials and destination are required.')
        if not self.allow_live_calls and not isinstance(self.transport, httpx.MockTransport):
            raise PermissionError('Live document processing is not authorized.')
        # Trusted policy must check source ownership, transmission rights and usage allowance.
        if self.authorize(spec, operation) is not True:
            raise PermissionError('Document processing permission or usage allowance is missing.')

    def _destination(self):
        return dict(region=self.settings.region, organization_id=self.settings.organization_id,
                    project_id=self.settings.project_id, api_generation='v2')

    def _key(self, spec):
        return content_hash([spec.model_dump(mode='json'), self._destination()])

    @staticmethod
    def _remote_id(value):
        # Validate remote IDs before putting them into a URL or a persistent job.
        class RemoteID(Contract):
            value: Identifier
        return RemoteID(value=value).value

    def _request(self, method, route, **kwargs):
        with httpx.Client(transport=self.transport, follow_redirects=False, trust_env=False,
                          timeout=httpx.Timeout(self.settings.read_seconds,
                                               connect=self.settings.connect_seconds)) as client:
            with client.stream(method, self.base + route,
                    headers={'Authorization': 'Bearer ' + self.settings.api_key.get_secret_value(),
                             'Accept': 'application/json'}, **kwargs) as response:
                # Do not follow redirects/download links or retain provider error bodies.
                if not 200 <= response.status_code < 300:
                    raise ParseBlocked('provider_http_' + str(response.status_code))
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 32 * 1024 * 1024:
                        raise ParseBlocked('provider_response_too_large')
                    chunks.append(chunk)
                value = json.loads(b''.join(chunks))
        if not isinstance(value, dict):
            raise ParseBlocked('invalid_provider_response')
        return value

    def _save(self, key, spec, job):
        self.jobs.save(spec.owner_id, key, job)

    def _post(self, key, spec, job, operation, route, **kwargs):
        self._guard(spec, operation)
        job['state'] = operation + '_sending'
        self._save(key, spec, job)  # A crash after here cannot safely retry this POST.
        started = time.monotonic()
        outcome = 'unknown'
        try:
            result = self._request('POST', route, **kwargs)
            outcome = 'received'
            return result
        except Exception:
            job['state'], job['error_code'] = 'outcome_unknown', operation + '_outcome_unknown'
            self._save(key, spec, job)
            raise ParseOutcomeUnknown('Submission outcome is uncertain; reconcile before retrying.') from None
        finally:
            job['attempts'].append(dict(operation=operation, outcome=outcome,
                elapsed_ms=round((time.monotonic() - started) * 1000, 3)))
            self._save(key, spec, job)

    @staticmethod
    def _check_pdf(spec, pdf):
        if not isinstance(pdf, bytes) or not 0 < len(pdf) <= 50 * 1024 * 1024:
            raise ValueError('Choose PDF bytes up to 50 MB.')
        if not pdf.startswith(b'%PDF-') or text_hash_bytes(pdf) != spec.original_file_hash:
            raise ValueError('PDF bytes do not match the selected source.')
        try:
            reader = PdfReader(BytesIO(pdf), strict=True)
            if reader.is_encrypted or len(reader.pages) != spec.expected_page_count:
                raise ValueError('Unsupported encrypted PDF or incorrect page count.')
        except Exception:
            raise ValueError('PDF validation failed; no upload was sent.') from None

    def start(self, spec, pdf):
        if not isinstance(spec, ParseRequest):
            raise TypeError('A validated parsing request is required.')
        self._guard(spec, 'submit')
        self._check_pdf(spec, pdf)
        key = self._key(spec)
        with run_lock(self.jobs.folder, key):
            job = self.jobs.create(key, spec, self._destination())
            if job['state'] in ('upload_sending', 'parse_sending', 'outcome_unknown'):
                raise ParseOutcomeUnknown('Reconcile the prior submission before retrying.')
            if job['state'] == 'new':
                result = self._post(key, spec, job, 'upload', '/api/v1/beta/files',
                    params={'organization_id': self.settings.organization_id,
                            'project_id': self.settings.project_id}, data={'purpose': 'parse'},
                    files={'file': ('source.pdf', pdf, 'application/pdf')})
                try:
                    file_id = self._remote_id(result['id'])
                    if result.get('project_id') != self.settings.project_id:
                        raise ValueError('Unexpected upload destination.')
                except Exception:
                    job['state'], job['error_code'] = 'outcome_unknown', 'invalid_upload_receipt'
                    self._save(key, spec, job)
                    raise ParseOutcomeUnknown('Upload receipt needs reconciliation.') from None
                job.update(file_id=file_id, state='uploaded')
                self._save(key, spec, job)
            if job['state'] == 'uploaded':
                result = self._post(key, spec, job, 'parse', '/api/v2/parse',
                    json={'file_id': job['file_id'], 'tier': spec.tier, 'version': spec.parser_version})
                try:
                    job_id = self._remote_id(result['id'])
                except Exception:
                    job['state'], job['error_code'] = 'outcome_unknown', 'invalid_parse_receipt'
                    self._save(key, spec, job)
                    raise ParseOutcomeUnknown('Parse receipt needs reconciliation.') from None
                job.update(remote_job_id=job_id, state='pending')
                self._save(key, spec, job)
        return key

    def poll(self, owner, key):
        # Load owner-scoped record before using any remote identifier.
        job = self.jobs.get(owner, key)
        spec = ParseRequest.model_validate_json(json.dumps(job['request']))
        self._guard(spec, 'poll')
        if job['destination'] != self._destination():
            raise PermissionError('Parsing destination changed; use the original configuration.')
        with run_lock(self.jobs.folder, key):
            job = self.jobs.get(owner, key)
            if job['state'] != 'pending':
                return self.status(owner, key)
            if job['polls'] >= self.max_polls:
                raise ParseBlocked('Polling limit reached; reconcile the existing job, do not resubmit.')
            job['polls'] += 1
            self._save(key, spec, job)
            started = time.monotonic()
            outcome = 'failed'
            try:
                value = self._request('GET', '/api/v2/parse/' + self._remote_id(job['remote_job_id']),
                                      params={'expand': 'markdown'})
                remote = value['job']
                if remote['id'] != job['remote_job_id']:
                    raise ValueError('Job identity mismatch.')
                status = remote['status']
                if status == 'COMPLETED':
                    pages = value['markdown']['pages']
                    if not isinstance(pages, list):
                        raise ValueError('Invalid page collection.')
                    normalized = []
                    for page in pages:
                        if page.get('success') is not True:
                            raise ValueError('A source page was not extracted successfully.')
                        normalized.append(ParsedPage(pdf_page=page['page_number'], text=page['markdown'],
                                                     content_hash=text_hash(page['markdown'])))
                    document = ParsedDocument(source_id=spec.source_id, snapshot_id=spec.snapshot_id,
                        original_file_hash=spec.original_file_hash,
                        parse_version=content_hash([spec.tier, spec.parser_version, 'markdown-v2']),
                        expected_page_count=spec.expected_page_count, pages=tuple(normalized))
                    job.update(state='extracted', document=document.model_dump(mode='json'), error_code=None)
                elif status in ('FAILED', 'CANCELLED', 'CANCELED'):
                    job.update(state='failed', error_code='provider_job_failed')
                elif status not in ('PENDING', 'RUNNING', 'PROCESSING', 'QUEUED'):
                    raise ValueError('Unknown parsing status.')
                outcome = 'received'
            except (httpx.HTTPError, ParseBlocked):
                # GET can be retried by a later bounded poll; never starts another job.
                job['error_code'] = 'poll_unavailable'
            except Exception:
                job.update(state='invalid_result', error_code='result_validation_failed')
            finally:
                job['attempts'].append(dict(operation='poll', outcome=outcome,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 3)))
                self._save(key, spec, job)
        return self.status(owner, key)

    def status(self, owner, key):
        job = self.jobs.get(owner, key)
        state = job['state']
        if state in ('upload_sending', 'parse_sending'):
            state = 'outcome_unknown'  # Includes a process crash after a committed POST intent.
        return dict(job_id=key, state=state, error_code=job['error_code'], polls=job['polls'],
                    eligible_for_story=False, indexing_started=False)

    def document(self, owner, key):
        job = self.jobs.get(owner, key)
        if job['state'] != 'extracted':
            raise ParseBlocked('Extraction is not complete and validated.')
        return ParsedDocument.model_validate_json(json.dumps(job['document']))


def text_hash_bytes(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()
