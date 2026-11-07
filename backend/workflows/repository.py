"""Separate skeleton records; never writes current application book tables."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from uuid import uuid4
from backend.contracts.common import canonical_json, content_hash


class UnknownOutcome(RuntimeError):
    pass


class RunRepository:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'workflow-records.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(owner,request_key));
                CREATE TABLE IF NOT EXISTS artifacts (
                    run_id TEXT, id TEXT, digest TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS operations (
                    run_id TEXT, id TEXT, status TEXT NOT NULL, payload TEXT,
                    PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS workflow_approvals (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, owner TEXT NOT NULL,
                    book_hash TEXT NOT NULL, review_hash TEXT NOT NULL,
                    policy TEXT NOT NULL, created TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner, key, payload):
        fingerprint = content_hash(payload)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?)',
                       (uuid4().hex, owner, key, fingerprint, canonical_json(payload)))
            row = db.execute('SELECT * FROM runs WHERE owner=? AND request_key=?', (owner,key)).fetchone()
        if row['fingerprint'] != fingerprint:
            raise ValueError('This request key was already used with different preferences.')
        return row['id']

    def get(self, run_id, owner):
        with self.db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=? AND owner=?', (run_id,owner)).fetchone()
        if not row:
            raise PermissionError('Run not found in this parent account.')
        return json.loads(row['payload'])

    def save(self, run_id, artifact_id, payload):
        digest = content_hash(payload)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?,?)',
                       (run_id,artifact_id,digest,canonical_json(payload)))
            row = db.execute('SELECT digest FROM artifacts WHERE run_id=? AND id=?', (run_id,artifact_id)).fetchone()
        if row['digest'] != digest:
            raise ValueError('Immutable artifact cannot be overwritten.')
        return artifact_id

    def load(self, run_id, artifact_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM artifacts WHERE run_id=? AND id=?', (run_id,artifact_id)).fetchone()
        if not row:
            raise ValueError('Required artifact is missing.')
        payload = json.loads(row['payload'])
        if content_hash(payload) != row['digest']:
            raise ValueError('Stored artifact failed integrity verification.')
        return payload

    def operation(self, run_id, key, action):
        with self.db() as db:
            inserted = db.execute('INSERT OR IGNORE INTO operations VALUES(?,?,?,NULL)',
                                  (run_id,key,'started')).rowcount
            row = db.execute('SELECT * FROM operations WHERE run_id=? AND id=?', (run_id,key)).fetchone()
        if not inserted:
            if row['status'] == 'succeeded':
                return json.loads(row['payload'])
            # A process may have died after a provider accepted work. Never retry blindly.
            raise UnknownOutcome('An operation has no confirmed saved result; reconciliation is required.')
        try:
            result = action()
            encoded = canonical_json(result)
        except Exception:
            with self.db() as db:
                db.execute('UPDATE operations SET status=? WHERE run_id=? AND id=?', ('failed',run_id,key))
            raise
        with self.db() as db:
            db.execute('UPDATE operations SET status=?,payload=? WHERE run_id=? AND id=?',
                       ('succeeded',encoded,run_id,key))
        return json.loads(encoded)

    def approve(self, run_id, owner, book_hash, review_hash, policy):
        approval_id = uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO workflow_approvals VALUES(?,?,?,?,?,?,?)',
                       (approval_id,run_id,owner,book_hash,review_hash,policy,datetime.now(timezone.utc).isoformat()))
        return approval_id

    def check_approval(self, approval_id, run_id, owner, book_hash, review_hash, policy):
        with self.db() as db:
            row = db.execute('SELECT * FROM workflow_approvals WHERE id=?', (approval_id,)).fetchone()
        if not row or any(row[k] != v for k,v in dict(run_id=run_id,owner=owner,
                book_hash=book_hash,review_hash=review_hash,policy=policy).items()):
            raise PermissionError('Approval does not match this owner, run, review and book version.')
