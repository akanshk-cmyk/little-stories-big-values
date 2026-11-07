"""Trusted source-review bridge. No routes, cloud requests or automatic approval.

Parent-inspiration review records are deliberately not consulted as qualified
authority. Caller injects current reviewer/usage-management authorization.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Literal

from pydantic import Field, model_validator

from backend.contracts.common import Contract, Digest, Identifier, Positive, Text, content_hash, text_hash
from backend.contracts.evidence import SourceReference
from backend.contracts.hosted_retrieval import ParsedDocument
from backend.workflows.checkpoints import run_lock
from .llamaparse import ParseRequest


class ProcessingPermission(Contract):
    request: ParseRequest
    rights_basis: Text
    usage_authorization_id: Identifier
    max_pages: Positive = Field(le=3000)

    @model_validator(mode='after')
    def within_allowance(self):
        if self.request.expected_page_count > self.max_pages:
            raise ValueError('The parsing request exceeds the authorized page allowance.')
        return self


class PageSpan(Contract):
    pdf_page: Positive
    start: int = Field(ge=0)
    end: Positive

    @model_validator(mode='after')
    def nonempty_span(self):
        if self.end <= self.start:
            raise ValueError('Select a nonempty source span.')
        return self


class PassageQualification(Contract):
    extraction_id: Digest
    spans: tuple[PageSpan, ...] = Field(min_length=1, max_length=20)
    expected_text_hash: Digest
    scope: Literal['parent_inspiration', 'traditional_claims']
    extraction_checked: bool = False
    boundaries_checked: bool = False
    edition_verified: bool = False
    cultural_context_checked: bool = False
    child_suitability_checked: bool = False
    rights_permitted: bool = False
    index_text_permitted: bool = False
    rights_basis: Text
    edition_reference: Text
    cultural_review_basis: Text
    notes: Text


class QualifiedPassage(Contract):
    reference: SourceReference
    text: Text
    scope: Literal['parent_inspiration', 'traditional_claims']
    spans: tuple[PageSpan, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def matching_text(self):
        if text_hash(self.text) != self.reference.content_hash:
            raise ValueError('Reviewed passage text does not match its reference.')
        return self


class EligiblePassages(Contract):
    seal_field = 'manifest_hash'
    owner_id: Identifier
    extraction_id: Digest
    source_id: Identifier
    snapshot_id: Identifier
    edition_id: Identifier
    purpose: Literal['parent_inspiration', 'traditional_claims']
    eligibility_epoch: int = Field(ge=0)
    destination_hash: Digest
    passages: tuple[QualifiedPassage, ...] = Field(min_length=1)
    manifest_hash: Digest | None = None


class HostedSourceReview:
    def __init__(self, folder, workspace, settings, *, can_manage=None, usage_allowed=None, automated_authorize=None, rights_authorize=None, automated_version='source-qa-v1'):
        self.automated_authorize=automated_authorize or (lambda *args:False)
        self.rights_authorize=rights_authorize or (lambda *args:False)
        self.automated_version=automated_version
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'hosted-source-review.sqlite'
        self.workspace, self.settings = workspace, settings
        # Must verify a real privileged principal; never a browser-supplied role flag.
        self.can_manage = can_manage or (lambda actor, action, owner, source: False)
        self.usage_allowed = usage_allowed or (lambda permission, destination: False)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS permissions (
                  id TEXT PRIMARY KEY,owner TEXT NOT NULL,source TEXT NOT NULL,
                  actor TEXT NOT NULL,payload TEXT NOT NULL,active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS extractions (
                  id TEXT PRIMARY KEY,owner TEXT NOT NULL,source TEXT NOT NULL,
                  payload TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS qualifications (
                  id TEXT PRIMARY KEY,extraction TEXT NOT NULL,owner TEXT NOT NULL,
                  actor TEXT NOT NULL,payload TEXT NOT NULL,active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS automated_review_attempts (
                  id TEXT PRIMARY KEY,owner TEXT NOT NULL,extraction TEXT NOT NULL,command TEXT NOT NULL,report TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS automated_reviews (
                  id TEXT PRIMARY KEY,owner TEXT NOT NULL,extraction TEXT NOT NULL,command TEXT NOT NULL,report TEXT NOT NULL,active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS review_events (
                  id INTEGER PRIMARY KEY,kind TEXT NOT NULL,record_id TEXT NOT NULL,
                  actor TEXT NOT NULL,created REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _destination(self):
        return dict(region=self.settings.region, organization_id=self.settings.organization_id,
                    project_id=self.settings.project_id, api_generation=self.settings.api_generation)

    def _manager(self, actor, action, owner, source):
        if self.can_manage(actor, action, owner, source) is not True:
            raise PermissionError('Current source-review authority is required.')

    def _source(self, owner, source, snapshot, original_hash):
        value = self.workspace.summary(source, owner)
        if value['snapshot_id'] != snapshot or value['original_hash'] != original_hash:
            raise PermissionError('The source version no longer matches the reviewed record.')
        # Original bytes must still exist and pass the workspace integrity check.
        self.workspace.original(source, owner)
        return value

    def grant_processing(self, actor, permission):
        if not isinstance(permission, ProcessingPermission):
            raise TypeError('A validated processing permission is required.')
        request = permission.request
        if not self.settings.organization_id or not self.settings.project_id or self.settings.api_generation != 'v2':
            raise ValueError('An explicit hosted parsing destination is required.')
        self._manager(actor, 'grant_processing', request.owner_id, request.source_id)
        self._source(request.owner_id, request.source_id, request.snapshot_id, request.original_file_hash)
        if self.usage_allowed(permission, self._destination()) is not True:
            raise PermissionError('A current, independently authorized usage allowance is required.')
        payload = dict(permission=permission.model_dump(mode='json'), destination=self._destination())
        key = content_hash(payload)
        with self.db() as db:
            db.execute('INSERT INTO permissions VALUES(?,?,?,?,?,1) '
                       'ON CONFLICT(id) DO UPDATE SET actor=excluded.actor,active=1',
                       (key, request.owner_id, request.source_id, actor, json.dumps(payload)))
            db.execute('INSERT INTO review_events(kind,record_id,actor,created) VALUES(?,?,?,?)',
                       ('processing_granted', key, actor, time.time()))
        return key

    def authorize_parse(self, request, operation):
        """Inject into LlamaParseAdapter; checked again before each outgoing call."""
        if operation not in ('submit', 'upload', 'parse', 'poll'):
            return False
        self._source(request.owner_id, request.source_id, request.snapshot_id, request.original_file_hash)
        with self.db() as db:
            rows = db.execute('SELECT * FROM permissions WHERE owner=? AND source=? AND active=1',
                              (request.owner_id, request.source_id)).fetchall()
        for row in rows:
            payload = json.loads(row['payload'])
            permission = ProcessingPermission.model_validate_json(json.dumps(payload['permission']))
            if permission.request == request and payload['destination'] == self._destination():
                self._manager(row['actor'], 'grant_processing', request.owner_id, request.source_id)
                return self.usage_allowed(permission, self._destination()) is True
        return False

    def withdraw_processing(self, actor, owner, permission_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM permissions WHERE id=? AND owner=?', (permission_id, owner)).fetchone()
            if not row:
                raise PermissionError('Processing permission is unavailable.')
            self._manager(actor, 'grant_processing', owner, row['source'])
            db.execute('UPDATE permissions SET active=0 WHERE id=?', (permission_id,))
            db.execute('INSERT INTO review_events(kind,record_id,actor,created) VALUES(?,?,?,?)',
                       ('processing_withdrawn', permission_id, actor, time.time()))

    def capture(self, owner, adapter, job_id):
        """Copy validated extraction from an owned job; never overwrite local pages."""
        document = adapter.document(owner, job_id)
        job = adapter.jobs.get(owner, job_id)
        request = ParseRequest.model_validate_json(json.dumps(job['request']))
        if job['destination'] != self._destination() or not self.authorize_parse(request, 'poll'):
            raise PermissionError('Current processing permission is required to capture extraction.')
        if (request.owner_id, request.source_id, request.snapshot_id, request.original_file_hash) != (
                owner, document.source_id, document.snapshot_id, document.original_file_hash):
            raise PermissionError('Parsing job provenance mismatch.')
        if request.expected_page_count != document.expected_page_count:
            raise ValueError('Parsing page count changed.')
        source = self._source(owner, document.source_id, document.snapshot_id, document.original_file_hash)
        key = content_hash([owner, document.model_dump(mode='json')])
        payload = dict(document=document.model_dump(mode='json'), source_edition_id=source['edition_id'],
                       original_snapshot_id=source['snapshot_id'], source_epoch=source['epoch'],
                       destination=self._destination(), request=request.model_dump(mode='json'))
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO extractions(id,owner,source,payload) VALUES(?,?,?,?)',
                       (key, owner, document.source_id, json.dumps(payload)))
        return key

    def _extraction(self, owner, key):
        with self.db() as db:
            row = db.execute('SELECT * FROM extractions WHERE id=? AND owner=?', (key, owner)).fetchone()
        if not row:
            raise PermissionError('Extraction is unavailable for this account.')
        payload = json.loads(row['payload'])
        document = ParsedDocument.model_validate_json(json.dumps(payload['document']))
        if content_hash([owner, document.model_dump(mode='json')]) != key:
            raise ValueError('Saved extraction integrity check failed.')
        source = self._source(owner, document.source_id, document.snapshot_id, document.original_file_hash)
        if (source['epoch'], source['edition_id']) != (payload['source_epoch'], payload['source_edition_id']):
            raise PermissionError('Source review changed; extraction requires renewed source reconciliation.')
        if payload['destination'] != self._destination():
            raise PermissionError('The hosted destination changed.')
        return dict(row), payload, document

    @staticmethod
    def _passage(document, command):
        parts, previous = [], None
        for span in command.spans:
            if previous and (span.pdf_page < previous.pdf_page or
                             (span.pdf_page == previous.pdf_page and span.start < previous.end)):
                raise ValueError('Review spans must be ordered and nonoverlapping.')
            if span.pdf_page > len(document.pages):
                raise ValueError('Review references an unavailable page.')
            text = document.pages[span.pdf_page - 1].text
            if span.end > len(text):
                raise ValueError('Review span exceeds the source page.')
            parts.append(text[span.start:span.end])
            previous = span
        result = '\n\n'.join(parts)
        if text_hash(result) != command.expected_text_hash:
            raise ValueError('The reviewed text changed.')
        return result

    def qualify(self, actor, owner, command):
        if not isinstance(command, PassageQualification):
            raise TypeError('A validated passage qualification is required.')
        with run_lock(self.folder, command.extraction_id):
            _, payload, document = self._extraction(owner, command.extraction_id)
            self._manager(actor, 'qualify_' + command.scope, owner, document.source_id)
            if not all((command.extraction_checked, command.boundaries_checked, command.edition_verified,
                        command.cultural_context_checked, command.child_suitability_checked,
                        command.rights_permitted, command.index_text_permitted)):
                raise ValueError('All source checks and hosted text-storage rights are required.')
            text = self._passage(document, command)
            # Validate existing downstream text limits before persisting an approval.
            if not text.strip() or len(text) > 12000:
                raise ValueError('Select a nonblank passage up to 12,000 characters.')
            data = command.model_dump(mode='json')
            key = content_hash([owner, data])
            with self.db() as db:
                db.execute('INSERT INTO qualifications VALUES(?,?,?,?,?,1) '
                           'ON CONFLICT(id) DO UPDATE SET actor=excluded.actor,active=1',
                           (key, command.extraction_id, owner, actor, json.dumps(data)))
                db.execute('UPDATE extractions SET epoch=epoch+1 WHERE id=?', (command.extraction_id,))
                db.execute('INSERT INTO review_events(kind,record_id,actor,created) VALUES(?,?,?,?)',
                           ('passage_qualified', key, actor, time.time()))
            return key

    def record_automated_report(self,owner,command,report):
        from .source_qa import validate_source_report
        if not isinstance(command,PassageQualification):raise TypeError('Validated passage required.')
        _,_,document=self._extraction(owner,command.extraction_id)
        if self.automated_authorize(owner,document.source_id,self.automated_version) is not True:
            raise PermissionError('Internal source QA authority is required.')
        report=validate_source_report(report,document,command,self.automated_version)
        key=content_hash([owner,command.model_dump(mode='json'),report.model_dump(mode='json'),'automated_qa'])
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO automated_review_attempts VALUES(?,?,?,?,?,?)',(key,owner,command.extraction_id,command.model_dump_json(),report.model_dump_json(),time.time()))
        return key

    def qualify_automated(self,owner,command,report):
        from .source_qa import validate_source_report
        if not isinstance(command,PassageQualification):raise TypeError('Validated passage required.')
        with run_lock(self.folder,command.extraction_id):
            _,_,document=self._extraction(owner,command.extraction_id)
            if self.automated_authorize(owner,document.source_id,self.automated_version) is not True:
                raise PermissionError('Internal automated review authority is required.')
            report=validate_source_report(report,document,command,self.automated_version)
            self.record_automated_report(owner,command,report)
            if report.overall_result!='PASS':raise PermissionError('Automated source QA must fully PASS.')
            if self.rights_authorize(owner,document.source_id,command.scope,self._destination()) is not True:
                raise PermissionError('Usage rights must be authorized independently of the QA model.')
            text=self._passage(document,command)
            if not text.strip() or len(text)>12000:raise ValueError('Invalid source passage size.')
            key=content_hash([owner,command.model_dump(mode='json'),report.model_dump(mode='json'),'automated_qa'])
            with self.db() as db:
                inserted=db.execute('INSERT OR IGNORE INTO automated_reviews VALUES(?,?,?,?,?,1)',(key,owner,command.extraction_id,command.model_dump_json(),report.model_dump_json())).rowcount
                if inserted:
                    db.execute('UPDATE extractions SET epoch=epoch+1 WHERE id=?',(command.extraction_id,))
                    db.execute('INSERT INTO review_events(kind,record_id,actor,created) VALUES(?,?,?,?)',('automated_source_qa_pass',key,'source-qa-agent',time.time()))
            return key

    def withdraw_passage(self, actor, owner, qualification_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM qualifications WHERE id=? AND owner=?',
                             (qualification_id, owner)).fetchone()
        if not row:
            raise PermissionError('Passage qualification is unavailable.')
        with run_lock(self.folder, row['extraction']):
            _, _, doc = self._extraction(owner, row['extraction'])
            command = PassageQualification.model_validate_json(row['payload'])
            self._manager(actor, 'qualify_' + command.scope, owner, doc.source_id)
            with self.db() as db:
                db.execute('UPDATE qualifications SET active=0 WHERE id=?', (qualification_id,))
                db.execute('UPDATE extractions SET epoch=epoch+1 WHERE id=?', (row['extraction'],))
                db.execute('INSERT INTO review_events(kind,record_id,actor,created) VALUES(?,?,?,?)',
                           ('passage_withdrawn', qualification_id, actor, time.time()))

    def eligible(self, owner, extraction_id, *, purpose='traditional_claims'):
        if purpose not in ('traditional_claims', 'parent_inspiration'):
            raise ValueError('Unsupported source purpose.')
        if not isinstance(extraction_id, str) or re.fullmatch(r'[a-f0-9]{64}', extraction_id) is None:
            raise ValueError('Invalid extraction identifier.')
        with run_lock(self.folder, extraction_id):
            row, payload, doc = self._extraction(owner, extraction_id)
            with self.db() as db:
                reviews = db.execute('SELECT * FROM qualifications WHERE extraction=? AND owner=? AND active=1 '
                                     'ORDER BY id', (extraction_id, owner)).fetchall()
            passages = []
            snapshot = 'snapshot-' + content_hash([doc.snapshot_id, doc.parse_version, extraction_id])
            for review in reviews:
                command = PassageQualification.model_validate_json(review['payload'])
                if command.scope != purpose:
                    continue
                if content_hash([owner, command.model_dump(mode='json')]) != review['id']:
                    raise ValueError('Passage review integrity failure.')
                self._manager(review['actor'], 'qualify_' + purpose, owner, doc.source_id)
                text = self._passage(doc, command)
                ref = SourceReference(source_id=doc.source_id, snapshot_id=snapshot,
                    edition_id=payload['source_edition_id'], chunk_id='passage-' + review['id'],
                    parent_section_id='extraction-' + extraction_id,
                    pdf_pages=tuple(sorted(set(span.pdf_page for span in command.spans))),
                    content_hash=text_hash(text), review_record_ids=('review-' + review['id'],),
                    eligibility_epoch=row['epoch'], review_status='approved',
                    eligibility_status='eligible', rights_status='permitted')
                passages.append(QualifiedPassage(reference=ref, text=text, scope=purpose, spans=command.spans))
            from .source_qa import validate_source_report
            with self.db() as db:
                automated=db.execute('SELECT * FROM automated_reviews WHERE extraction=? AND owner=? AND active=1 ORDER BY id',(extraction_id,owner)).fetchall()
            for item in automated:
                command=PassageQualification.model_validate_json(item['command'])
                if command.scope!=purpose:continue
                if self.automated_authorize(owner,doc.source_id,self.automated_version) is not True:continue
                if self.rights_authorize(owner,doc.source_id,purpose,self._destination()) is not True:continue
                report=validate_source_report(json.loads(item['report']),doc,command,self.automated_version)
                if report.overall_result!='PASS':continue
                expected=content_hash([owner,command.model_dump(mode='json'),report.model_dump(mode='json'),'automated_qa'])
                if expected!=item['id']:raise ValueError('Automated source QA integrity failure.')
                text=self._passage(doc,command)
                ref=SourceReference(source_id=doc.source_id,snapshot_id=snapshot,edition_id=payload['source_edition_id'],chunk_id='auto-passage-'+item['id'],parent_section_id='extraction-'+extraction_id,
                    pdf_pages=tuple(sorted({span.pdf_page for span in command.spans})),content_hash=text_hash(text),review_record_ids=('automated-qa-'+item['id'],),eligibility_epoch=row['epoch'],review_status='approved',eligibility_status='eligible',rights_status='permitted')
                passages.append(QualifiedPassage(reference=ref,text=text,scope=purpose,spans=command.spans))
            if not passages:
                raise PermissionError('No currently qualified passages for the requested purpose.')
            return EligiblePassages(owner_id=owner, extraction_id=extraction_id,
                source_id=doc.source_id, snapshot_id=snapshot, edition_id=payload['source_edition_id'],
                purpose=purpose, eligibility_epoch=row['epoch'],
                destination_hash=content_hash(self._destination()), passages=tuple(passages))

    def assert_current(self, manifest):
        """Call before publication/search/reranking/writing; no cached approval."""
        if not isinstance(manifest, EligiblePassages):
            raise TypeError('A validated eligibility manifest is required.')
        current = self.eligible(manifest.owner_id, manifest.extraction_id, purpose=manifest.purpose)
        if current.manifest_hash != manifest.manifest_hash:
            raise PermissionError('Source eligibility changed; rebuild the evidence/index version.')
        return True
