"""Write-ahead operation cache and append-only, content-free attempt events."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from backend.contracts import ProviderAttempt
from backend.contracts.common import canonical_json,content_hash
from .retry_policy import ProviderFailure


def timestamp():return datetime.now(timezone.utc).isoformat()


class AttemptLedger:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'provider-operations.sqlite'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS provider_operations (
                owner TEXT,operation_id TEXT,fingerprint TEXT,status TEXT,response TEXT,response_hash TEXT,error_code TEXT,
                PRIMARY KEY(owner,operation_id));
                CREATE TABLE IF NOT EXISTS provider_attempt_events (
                owner TEXT,operation_id TEXT,attempt_id TEXT,version INTEGER,payload TEXT,
                PRIMARY KEY(attempt_id,version));
                CREATE TABLE IF NOT EXISTS provider_scope_blocks (
                owner TEXT,request_hash TEXT,code TEXT,PRIMARY KEY(owner,request_hash));''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:yield db
        finally:db.close()

    def begin(self,owner,operation_id,fingerprint):
        with self.db() as db:
            inserted=db.execute('INSERT OR IGNORE INTO provider_operations VALUES(?,?,?,?,NULL,NULL,NULL)',
                (owner,operation_id,fingerprint,'started')).rowcount
            row=db.execute('SELECT * FROM provider_operations WHERE owner=? AND operation_id=?',(owner,operation_id)).fetchone()
        if row['fingerprint']!=fingerprint:raise ProviderFailure('operation_conflict')
        if inserted:return 'new',None
        if row['status']=='succeeded':
            if content_hash(json.loads(row['response']))!=row['response_hash']:raise ProviderFailure('cached_result_integrity')
            return 'cached',json.loads(row['response'])
        if row['status']=='started':return 'interrupted',None
        raise ProviderFailure(row['error_code'] or 'operation_stopped',unknown=row['status']=='outcome_unknown',refusal=row['error_code']=='safety_refusal')

    def terminal(self,owner,operation_id,status,*,response=None,error=None):
        with self.db() as db:
            db.execute('UPDATE provider_operations SET status=?,response=?,response_hash=?,error_code=? WHERE owner=? AND operation_id=? AND status=?',
                (status,canonical_json(response) if response is not None else None,
                 content_hash(response) if response is not None else None,error,owner,operation_id,'started'))

    def block_scope(self,owner,request_hash,code):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO provider_scope_blocks VALUES(?,?,?)',(owner,request_hash,code))

    def check_scope(self,owner,request_hash):
        with self.db() as db:row=db.execute('SELECT code FROM provider_scope_blocks WHERE owner=? AND request_hash=?',(owner,request_hash)).fetchone()
        if row:raise ProviderFailure(row['code'],unknown=row['code']=='outcome_unknown',refusal=row['code']=='safety_refusal')

    def started(self,owner,request,provider,model,number,*,fallback_reason=None):
        record=ProviderAttempt(attempt_id=uuid4().hex,record_version=1,operation_id=request.operation_id,
            requested_mode=request.mode,role=request.role,provider=provider,model_id=model,status='started',fallback_reason=fallback_reason)
        payload=dict(attempt=record.model_dump(mode='json'),run_id=request.run_id,thread_id=request.thread_id,
            prompt_version=request.prompt_version,schema_version=request.schema_version,reviewer_version=request.reviewer_version,
            evidence_bundle_id=request.evidence_bundle_id,evidence_bundle_hash=request.evidence_bundle_hash,
            draft_hash=request.draft_hash,attempt_number=number,deadline_seconds=request.deadline_seconds,
            request_hash=request.request_hash,output_schema=request.output_schema,
            primary_provider='groq' if request.mode=='auto' else request.mode,
            started_at=timestamp(),finished_at=None,validation_status='not_performed',reported_model_id=None,
            cost_uncertain=False)
        self._event(owner,request.operation_id,record.attempt_id,1,payload)
        return payload

    def finished(self,owner,request,started,*,status,latency_ms,error=None,candidate=None,response=None):
        raw=dict(started['attempt']);raw.update(record_version=2,status=status,latency_ms=latency_ms,
            provider_request_id=getattr(candidate,'provider_request_id',None) if candidate else getattr(error,'provider_request_id',None),
            input_tokens=getattr(candidate,'input_tokens',None) if candidate else getattr(error,'input_tokens',None),
            output_tokens=getattr(candidate,'output_tokens',None) if candidate else getattr(error,'output_tokens',None),
            error_code=error.code if error else None)
        record=ProviderAttempt.model_validate(raw)
        payload=dict(started,attempt=record.model_dump(mode='json'),finished_at=timestamp(),
            validation_status='passed' if status=='succeeded' else ('failed' if error and error.code in ('output_validation','credential_in_response') else 'not_performed'),
            reported_model_id=getattr(candidate,'reported_model_id',None),cost_uncertain=status=='outcome_unknown',
            output_hash=response['output_hash'] if response else None,finish_reason='stop' if status=='succeeded' else None)
        with self.db() as db:
            db.execute('INSERT INTO provider_attempt_events VALUES(?,?,?,?,?)',
                (owner,request.operation_id,record.attempt_id,2,canonical_json(payload)))
            if response is not None:
                db.execute('UPDATE provider_operations SET status=?,response=?,response_hash=?,error_code=NULL WHERE owner=? AND operation_id=? AND status=?',
                    ('succeeded',canonical_json(response),content_hash(response),owner,request.operation_id,'started'))
                if db.execute('SELECT changes()').fetchone()[0]!=1:raise ValueError('Operation is not eligible for completion.')

    def _event(self,owner,operation_id,attempt_id,version,payload):
        with self.db() as db:db.execute('INSERT INTO provider_attempt_events VALUES(?,?,?,?,?)',
            (owner,operation_id,attempt_id,version,canonical_json(payload)))

    def events(self,owner,operation_id):
        with self.db() as db:
            return [json.loads(row['payload']) for row in db.execute('SELECT payload FROM provider_attempt_events WHERE owner=? AND operation_id=? ORDER BY rowid',(owner,operation_id))]

    def interrupted(self,owner,request):
        # The previous process may have submitted an unfinished attempt. Append
        # an uncertainty event; do not guess token usage or actual completion.
        events=self.events(owner,request.operation_id)
        latest={e['attempt']['attempt_id']:e for e in events}
        for event in latest.values():
            if event['attempt']['status']!='started':continue
            raw=dict(event['attempt'],record_version=2,status='outcome_unknown',error_code='process_interrupted')
            record=ProviderAttempt.model_validate(raw)
            payload=dict(event,attempt=record.model_dump(mode='json'),recovered_at=timestamp(),cost_uncertain=True)
            self._event(owner,request.operation_id,record.attempt_id,2,payload)
        self.block_scope(owner,request.request_hash,'outcome_unknown')
        self.terminal(owner,request.operation_id,'outcome_unknown',error='outcome_unknown')
