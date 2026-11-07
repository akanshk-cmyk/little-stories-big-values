"""Append-only, owner-scoped consent/release records in the run database."""
from datetime import datetime,timezone
import json
from uuid import uuid4
from backend.contracts import content_hash,parse_payload
from backend.contracts.common import canonical_json
from backend.contracts.consent import TextConsent,OutputRelease,story_text


class Consents:
    def __init__(self,repository):
        self.repository=repository
        with repository.db() as db:db.executescript('''
            CREATE TABLE IF NOT EXISTS text_consents(id TEXT PRIMARY KEY,payload TEXT);
            CREATE TABLE IF NOT EXISTS current_text_consents(owner TEXT,run_id TEXT,consent_id TEXT,
                PRIMARY KEY(owner,run_id));
            CREATE TABLE IF NOT EXISTS application_releases(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE,payload TEXT);
        ''')

    def current(self,owner,run_id,request_hash,text_hash,db=None):
        if db is None:
            with self.repository.db() as connection:return self.current(owner,run_id,request_hash,text_hash,connection)
        row=db.execute('SELECT t.payload FROM text_consents t JOIN current_text_consents c ON c.consent_id=t.id '
            'WHERE c.owner=? AND c.run_id=?',(owner,run_id)).fetchone()
        if not row:return None
        record=parse_payload(TextConsent,json.loads(row[0]))
        if record.owner_id!=owner or record.run_id!=run_id:raise PermissionError('Consent owner mismatch.')
        return record if record.request_hash==request_hash and record.text_hash==text_hash else None

    def approve(self,owner,state,draft,expected_text_hash):
        text=story_text(draft);digest=content_hash(text)
        if expected_text_hash!=digest:raise ValueError('Story text changed. Read and approve the current words.')
        with self.repository.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=self.current(owner,state['run_id'],state['request_hash'],digest,db)
            if old:return old
            record=TextConsent(consent_id=uuid4().hex,owner_id=owner,run_id=state['run_id'],
                request_hash=state['request_hash'],text=text,text_hash=digest,
                review_hash_at_consent=content_hash(state['review']),approved_at=datetime.now(timezone.utc))
            db.execute('INSERT INTO text_consents VALUES(?,?)',(record.consent_id,record.model_dump_json()))
            db.execute('INSERT INTO current_text_consents VALUES(?,?,?) ON CONFLICT(owner,run_id) DO UPDATE SET consent_id=excluded.consent_id',
                (owner,state['run_id'],record.consent_id))
        return record

    def authorize(self,owner,state,book,consent):
        values=dict(owner_id=owner,run_id=state['run_id'],book_hash=book.book_version_hash,
            draft_hash=book.draft_hash,text_hash=consent.text_hash,consent_id=consent.consent_id,
            consent_hash=consent.consent_hash,review_hash=content_hash(state['review']),
            evidence_hash=book.evidence_bundle_hash,character_hash=book.character_manifest_hash,
            asset_hash=book.asset_manifest_hash,playbook_version=state['playbook_version'])
        fingerprint=content_hash(values)
        with self.repository.db() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self.current(owner,state['run_id'],state['request_hash'],consent.text_hash,db)
            if not current or current.consent_id!=consent.consent_id:
                raise PermissionError('Current story text consent is required.')
            row=db.execute('SELECT payload FROM application_releases WHERE fingerprint=?',(fingerprint,)).fetchone()
            if row:return parse_payload(OutputRelease,json.loads(row[0]))
            release=OutputRelease(release_id=uuid4().hex,released_at=datetime.now(timezone.utc),**values)
            # Existing graph receipt is an internal application release, not a
            # human book-consent event. The typed release must also exist.
            db.execute('INSERT INTO workflow_approvals VALUES(?,?,?,?,?,?,?)',(release.release_id,
                release.run_id,owner,release.book_hash,release.review_hash,release.playbook_version,
                release.released_at.isoformat()))
            db.execute('INSERT INTO application_releases VALUES(?,?,?)',
                (release.release_id,fingerprint,release.model_dump_json()))
        return release

    def verify(self,release_id,owner,state,book,consent):
        with self.repository.db() as db:row=db.execute('SELECT payload FROM application_releases WHERE id=?',(release_id,)).fetchone()
        if not row:raise PermissionError('No application output authorization exists.')
        release=parse_payload(OutputRelease,json.loads(row[0]))
        expected=dict(owner_id=owner,run_id=state['run_id'],book_hash=book.book_version_hash,
            draft_hash=book.draft_hash,text_hash=consent.text_hash,consent_id=consent.consent_id,
            consent_hash=consent.consent_hash,review_hash=content_hash(state['review']),
            evidence_hash=book.evidence_bundle_hash,character_hash=book.character_manifest_hash,
            asset_hash=book.asset_manifest_hash,playbook_version=state['playbook_version'])
        if any(getattr(release,k)!=v for k,v in expected.items()):raise PermissionError('Application release is stale.')
        self.repository.check_approval(release_id,state['run_id'],owner,release.book_hash,
            release.review_hash,state['playbook_version'])
        return release
