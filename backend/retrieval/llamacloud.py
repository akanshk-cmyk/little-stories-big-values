"""Opt-in Index v2 publication of qualified text files. No search or UI routes.

Fresh directory/index per manifest; no mutation of an existing live corpus.
Strict read-back assumptions are fixture-verified, not live-account verified.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

import httpx
from pydantic import model_validator

from backend.contracts.common import Contract, Identifier, content_hash, text_hash
from backend.ingestion.hosted_review import EligiblePassages
from backend.workflows.checkpoints import run_lock


class IndexPublicationPlan(Contract):
    manifest: EligiblePassages
    parse_config_id: Identifier
    usage_authorization_id: Identifier

    @model_validator(mode='after')
    def bounded_sample(self):
        if len(self.manifest.passages) > 20:
            raise ValueError('This initial publication increment supports up to 20 reviewed passages.')
        ids = [p.reference.chunk_id for p in self.manifest.passages]
        if len(ids) != len(set(ids)):
            raise ValueError('Publication passage IDs must be unique.')
        return self


class PublicationBlocked(RuntimeError):
    pass


class PublicationOutcomeUnknown(PublicationBlocked):
    pass


class HostedPublications:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'llamacloud-publications.sqlite'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS publications (
              id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS active_publications(scope TEXT PRIMARY KEY,id TEXT NOT NULL);''')

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
            row = db.execute('SELECT payload FROM publications WHERE id=? AND owner=?', (key, owner)).fetchone()
        if not row:
            raise PermissionError('Index publication is unavailable for this account.')
        return json.loads(row[0])

    def save(self, owner, key, job):
        with self.db() as db:
            if db.execute('UPDATE publications SET payload=? WHERE id=? AND owner=?',
                          (json.dumps(job, allow_nan=False), key, owner)).rowcount != 1:
                raise PermissionError('Index publication is unavailable for this account.')


class LlamaCloudPublisher:
    def __init__(self, settings, store, reviews, *, authorize=None, transport=None,
                 allow_live_calls=False, max_checks=30):
        if type(allow_live_calls) is not bool:
            raise ValueError('Live execution authorization must be boolean.')
        if transport is not None and not isinstance(transport, httpx.MockTransport) and not allow_live_calls:
            raise PermissionError('Offline publication requires a mock transport.')
        if type(max_checks) is not int or not 1 <= max_checks <= 60:
            raise ValueError('Invalid publication check limit.')
        self.settings, self.store, self.reviews = settings, store, reviews
        self.authorize = authorize or (lambda plan, operation: False)
        self.transport, self.allow_live_calls, self.max_checks = transport, allow_live_calls, max_checks
        self.base = {'us': 'https://api.cloud.llamaindex.ai',
                     'eu': 'https://api.cloud.eu.llamaindex.ai'}[settings.region]

    def _destination(self):
        return dict(region=self.settings.region, organization_id=self.settings.organization_id,
                    project_id=self.settings.project_id, api_generation=self.settings.api_generation)

    def _guard(self, owner, plan, operation):
        if owner != plan.manifest.owner_id:
            raise PermissionError('Publication belongs to another account.')
        if not self.settings.enabled or self.settings.api_generation != 'v2':
            raise PermissionError('Hosted Index v2 publication is disabled.')
        if not self.settings.api_key.get_secret_value().strip() or not all((
                self.settings.organization_id, self.settings.project_id)):
            raise PermissionError('Hosted credentials and destination are required.')
        if not self.allow_live_calls and not isinstance(self.transport, httpx.MockTransport):
            raise PermissionError('Live publication is not authorized.')
        if content_hash(self._destination()) != plan.manifest.destination_hash:
            raise PermissionError('Eligible content belongs to another hosted destination.')
        self.reviews.assert_current(plan.manifest)
        # Must check current usage allowance AND the configured parse product before each operation.
        if self.authorize(plan, operation) is not True:
            raise PermissionError('Indexing usage and configuration authorization is missing.')

    @staticmethod
    def _id(value):
        class RemoteID(Contract):
            value: Identifier
        return RemoteID(value=value).value

    def _request(self, method, route, **kwargs):
        params = dict(organization_id=self.settings.organization_id, project_id=self.settings.project_id)
        params.update(kwargs.pop('params', {}))
        with httpx.Client(transport=self.transport, follow_redirects=False, trust_env=False,
                          timeout=httpx.Timeout(self.settings.read_seconds,
                                               connect=self.settings.connect_seconds)) as client:
            with client.stream(method, self.base + route, params=params,
                    headers={'Authorization': 'Bearer ' + self.settings.api_key.get_secret_value(),
                             'Accept': 'application/json'}, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    raise PublicationBlocked('provider_http_' + str(response.status_code))
                parts, size = [], 0
                for part in response.iter_bytes():
                    size += len(part)
                    if size > 2 * 1024 * 1024:
                        raise PublicationBlocked('provider_response_too_large')
                    parts.append(part)
                value = json.loads(b''.join(parts))
        if not isinstance(value, dict):
            raise PublicationBlocked('invalid_provider_response')
        return value

    def _post(self, owner, key, job, plan, operation, route, validate, **kwargs):
        self._guard(owner, plan, operation)
        previous = job['operations'].get(operation)
        if previous:
            if previous['state'] != 'received':
                raise PublicationOutcomeUnknown('Reconcile the previous indexing submission first.')
            return self._id(previous['receipt'])
        job['operations'][operation] = {'state': 'sending'}
        self.store.save(owner, key, job)
        start = time.monotonic()
        try:
            receipt = validate(self._request('POST', route, **kwargs))
            job['operations'][operation] = dict(state='received', receipt=receipt)
            return receipt
        except Exception:
            job.update(state='outcome_unknown', error_code='submission_needs_reconciliation')
            raise PublicationOutcomeUnknown('Indexing outcome is uncertain; reconcile before resubmission.') from None
        finally:
            job['operations'][operation]['elapsed_ms'] = round((time.monotonic() - start) * 1000, 3)
            self.store.save(owner, key, job)

    def _scoped_id(self, value):
        if value.get('project_id') != self.settings.project_id:
            raise ValueError('Provider destination mismatch.')
        return self._id(value['id'])

    @staticmethod
    def _metadata(key, manifest, passage):
        return dict(publication_id=key, passage_id=passage.reference.chunk_id,
                    text_hash=passage.reference.content_hash, source_id=manifest.source_id,
                    snapshot_id=manifest.snapshot_id, edition_id=manifest.edition_id,
                    purpose=manifest.purpose)

    def stage(self, owner, plan):
        if not isinstance(plan, IndexPublicationPlan):
            raise TypeError('A validated index publication plan is required.')
        self._guard(owner, plan, 'stage')
        key = content_hash([plan.model_dump(mode='json'), self._destination()])
        with run_lock(self.store.folder, key):
            job = dict(plan=plan.model_dump(mode='json'), destination=self._destination(),
                       state='staging', operations={}, checks=0, error_code=None, verification=None)
            with self.store.db() as db:
                db.execute('INSERT OR IGNORE INTO publications VALUES(?,?,?)', (key, owner, json.dumps(job)))
            job, _ = self._load(owner, key)
            if job['state'] == 'outcome_unknown' or any(op['state'] == 'sending' for op in job['operations'].values()):
                raise PublicationOutcomeUnknown('Reconcile the previous indexing submission first.')
            if job['state'] != 'staging':
                return key
            directory = self._post(owner, key, job, plan, 'directory', '/api/v1/beta/directories',
                self._scoped_id, json={'name': 'stories-' + key[:24], 'description': 'publication-' + key})
            for number, passage in enumerate(plan.manifest.passages):
                file_id = self._post(owner, key, job, plan, 'upload_' + str(number), '/api/v1/beta/files',
                    self._scoped_id, data={'purpose': 'user_data'},
                    files={'file': (passage.reference.chunk_id + '.txt', passage.text.encode('utf-8'), 'text/plain')})
                metadata = self._metadata(key, plan.manifest, passage)
                def check_added(value, file_id=file_id):
                    entry_id = self._scoped_id(value)
                    if value.get('directory_id') != directory or value.get('file_id') != file_id:
                        raise ValueError('Directory receipt does not match the staged file.')
                    return entry_id
                self._post(owner, key, job, plan, 'attach_' + str(number),
                    '/api/v1/beta/directories/' + directory + '/files', check_added,
                    json={'file_id': file_id, 'unique_id': passage.reference.chunk_id, 'metadata': metadata})
            def check_index(value):
                remote_id = self._scoped_id(value)
                if value.get('source_directory_id') != directory:
                    raise ValueError('Index source directory mismatch.')
                return remote_id
            self._post(owner, key, job, plan, 'index', '/api/v1/indexes', check_index,
                json={'name': 'stories-' + key[:24], 'source_directory_id': directory,
                      'products': [{'product_type': 'parse', 'product_config_id': plan.parse_config_id}],
                      'sync_frequency': 'manual', 'vector_target': 'DEFAULT', 'store_attachments': []})
            self._guard(owner, plan, 'stage_complete')
            job['state'] = 'indexing'
            self.store.save(owner, key, job)
        return key

    def _load(self, owner, key):
        job = self.store.get(owner, key)
        plan = IndexPublicationPlan.model_validate_json(json.dumps(job['plan']))
        if content_hash([plan.model_dump(mode='json'), job['destination']]) != key:
            raise ValueError('Publication integrity failure.')
        if job['destination'] != self._destination():
            raise PermissionError('Hosted publication destination changed.')
        if job.get('verification') and content_hash(job['verification']) != job.get('verification_hash'):
            raise ValueError('Saved publication verification changed.')
        return job, plan

    def _directory_files(self, directory):
        value = self._request('GET', '/api/v1/beta/directories/' + self._id(directory) + '/files',
                              params={'limit': 100})
        # Small sample only. Never activate based on an incomplete listing.
        if value.get('has_more') is not False or value.get('next_cursor'):
            raise ValueError('A verified complete directory listing is required.')
        if not isinstance(value.get('data'), list):
            raise ValueError('Invalid directory listing.')
        return value['data']

    def _verify_files(self, key, plan, directory, files, *, source=False, job=None):
        expected = {p.reference.chunk_id: (i, p) for i, p in enumerate(plan.manifest.passages)}
        found = {}
        for value in files:
            if value.get('directory_id') != directory or value.get('project_id') != self.settings.project_id:
                raise ValueError('Unexpected file destination.')
            metadata = value.get('metadata', {})
            local = metadata.get('passage_id')
            if local not in expected or local in found or value.get('deleted_at'):
                raise ValueError('Unexpected, duplicate or deleted indexed file.')
            number, passage = expected[local]
            if any(metadata.get(k) != v for k, v in self._metadata(key, plan.manifest, passage).items()):
                raise ValueError('Hosted provenance changed.')
            remote_id = self._id(value['id'])
            if source and (remote_id != job['operations']['attach_' + str(number)]['receipt'] or
                           value.get('file_id') != job['operations']['upload_' + str(number)]['receipt']):
                raise ValueError('Staged source file changed.')
            found[local] = remote_id
        if set(found) != set(expected):
            raise ValueError('Indexed passage coverage is incomplete.')
        return found

    def check(self, owner, key):
        job, plan = self._load(owner, key)
        self._guard(owner, plan, 'check')
        with run_lock(self.store.folder, key):
            job, plan = self._load(owner, key)
            if job['state'] not in ('indexing', 'verified', 'active'):
                return self.status(owner, key)
            if job['checks'] >= self.max_checks:
                raise PublicationBlocked('Verification limit reached; reconcile the existing publication.')
            job['checks'] += 1
            self.store.save(owner, key, job)
            started = time.monotonic()
            try:
                remote_id = self._id(job['operations']['index']['receipt'])
                directory = self._id(job['operations']['directory']['receipt'])
                index = self._request('GET', '/api/v1/indexes/' + remote_id,
                                      params={'expand': 'sync_in_progress'})
                if self._scoped_id(index) != remote_id or index.get('source_directory_id') != directory:
                    raise ValueError('Index identity changed.')
                state = index.get('metadata', {}).get('status')
                if state == 'failed':
                    job.update(state='failed', error_code='provider_index_failed')
                elif state == 'ready' and index.get('sync_in_progress') is False:
                    if not index.get('last_synced_at') or not index.get('last_exported_at'):
                        raise ValueError('Completed sync/export evidence is missing.')
                    prior = job.get('verification')
                    if prior and any(index.get(field) != prior[field] for field in (
                            'last_synced_at', 'last_exported_at', 'export_config_id', 'output_directory_id')):
                        raise ValueError('An immutable hosted index version changed.')
                    output = self._id(index['output_directory_id'])
                    self._guard(owner, plan, 'list_files')
                    self._verify_files(key, plan, directory, self._directory_files(directory), source=True, job=job)
                    self._guard(owner, plan, 'list_files')
                    mapping = self._verify_files(key, plan, output, self._directory_files(output))
                    for passage in plan.manifest.passages:
                        self._guard(owner, plan, 'read_back')
                        read = self._request('POST', '/api/v1/retrieval/files/read', json={
                            'index_id': remote_id, 'file_id': mapping[passage.reference.chunk_id],
                            'offset': 0, 'max_length': len(passage.text) + 1})
                        if read.get('content') != passage.text:
                            raise ValueError('Indexed text differs from the reviewed passage.')
                    self._guard(owner, plan, 'verified')
                    job['verification'] = dict(index_id=remote_id, output_directory_id=output,
                        parsed_files=mapping, export_config_id=self._id(index['export_config_id']),
                        last_synced_at=index['last_synced_at'], last_exported_at=index['last_exported_at'])
                    job['verification_hash'] = content_hash(job['verification'])
                    job.update(state='verified', error_code=None)
                else:
                    job.update(state='indexing', error_code=None)
            except (httpx.HTTPError, PublicationBlocked):
                job['error_code'] = 'verification_unavailable'
                # A previously active version must not remain usable after an unsuccessful recheck.
                job['state'] = 'indexing'
            except PermissionError:
                job.update(state='blocked', error_code='permission_changed')
                raise
            except Exception:
                job.update(state='blocked', error_code='publication_validation_failed')
            finally:
                job['last_check_elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)
                self.store.save(owner, key, job)
        return self.status(owner, key)

    @staticmethod
    def _scope(plan):
        m = plan.manifest
        return content_hash([m.owner_id, m.source_id, m.purpose, m.destination_hash])

    def activate(self, owner, key):
        self._load(owner, key)  # Verify identity before constructing a lock-file path.
        with run_lock(self.store.folder, key):
            job, plan = self._load(owner, key)
            self._guard(owner, plan, 'activate')
            if job['state'] != 'verified' or not job['verification']:
                raise PublicationBlocked('Only a fully read-back-verified publication may activate.')
            job['state'] = 'active'
            with self.store.db() as db:
                db.execute('UPDATE publications SET payload=? WHERE id=? AND owner=?',
                           (json.dumps(job), key, owner))
                db.execute('INSERT OR REPLACE INTO active_publications VALUES(?,?)', (self._scope(plan), key))
        return self.status(owner, key)

    def active(self, owner, key):
        job, plan = self._load(owner, key)
        self._guard(owner, plan, 'use_active')
        with self.store.db() as db:
            current = db.execute('SELECT id FROM active_publications WHERE scope=?', (self._scope(plan),)).fetchone()
        if job['state'] != 'active' or not current or current[0] != key:
            raise PublicationBlocked('This publication is not the active source version.')
        return dict(publication_id=key, index_version='publication-' + key,
                    manifest_hash=plan.manifest.manifest_hash, **job['verification'])

    def verify_active(self, owner, key):
        """Fresh remote identity/file checks without changing approval to verified.

        Search calls this before and after querying. No sync or activation occurs.
        """
        self.active(owner, key)  # Owner/identity check before constructing a lock path.
        with run_lock(self.store.folder, key):
            active = self.active(owner, key)
            job, plan = self._load(owner, key)
            try:
                self._guard(owner, plan, 'verify_active')
                index = self._request('GET', '/api/v1/indexes/' + self._id(active['index_id']),
                                      params={'expand': 'sync_in_progress'})
                if (self._scoped_id(index) != active['index_id'] or
                        index.get('source_directory_id') != job['operations']['directory']['receipt'] or
                        index.get('metadata', {}).get('status') != 'ready' or
                        index.get('sync_in_progress') is not False or any(
                            index.get(field) != active[field] for field in (
                                'last_synced_at', 'last_exported_at', 'export_config_id', 'output_directory_id'))):
                    raise PublicationBlocked('Active hosted index changed or is not ready.')
                directory = job['operations']['directory']['receipt']
                self._guard(owner, plan, 'list_files')
                self._verify_files(key, plan, directory, self._directory_files(directory), source=True, job=job)
                self._guard(owner, plan, 'list_files')
                mapping = self._verify_files(key, plan, active['output_directory_id'],
                                             self._directory_files(active['output_directory_id']))
                if mapping != active['parsed_files']:
                    raise PublicationBlocked('Active hosted file mapping changed.')
                self.active(owner, key)  # Pointer/rights may change during network work.
                return active
            except Exception:
                job.update(state='blocked', error_code='active_verification_failed')
                self.store.save(owner, key, job)
                raise PublicationBlocked('Active publication needs verification before use.') from None

    def status(self, owner, key):
        job = self.store.get(owner, key)
        uncertain = any(op['state'] == 'sending' for op in job['operations'].values())
        return dict(publication_id=key, state='outcome_unknown' if uncertain else job['state'],
                    error_code=job['error_code'], checks=job['checks'],
                    story_retrieval_connected=False)
