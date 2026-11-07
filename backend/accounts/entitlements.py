"""Atomic simulated plan accounting. No financial inputs or gateway calls."""
import json
import time
from uuid import uuid4
from backend.contracts.common import content_hash,canonical_json


class GenerationBlocked(PermissionError):
    MESSAGES={
        'generation_hold_expired':'The story credit hold expired. Confirm an available story credit before continuing.',
        'generation_limit_reached':'Story creation reached its AI request limit. Review this run before starting another request.'}

    def __init__(self,code):
        self.code=code
        super().__init__(self.MESSAGES[code])


class Entitlements:
    def __init__(self,accounts,*,clock=time.time,max_generation_operations_per_day=30,max_generation_operations_per_run=6):
        self.accounts=accounts;self.store=accounts.store;self.clock=clock
        if type(max_generation_operations_per_day) is not int or not 1<=max_generation_operations_per_day<=1000:
            raise ValueError('Choose a daily operation limit from 1 to 1000.')
        self.max_generation_operations_per_day=max_generation_operations_per_day
        if type(max_generation_operations_per_run) is not int or not 1<=max_generation_operations_per_run<=1000:raise ValueError('Choose a bounded per-run operation limit.')
        self.max_generation_operations_per_run=max_generation_operations_per_run

    def checkout(self,owner,key,outcome):
        self.accounts.require(owner)
        if outcome not in ('success','declined','cancelled') or not isinstance(key,str) or not 1<=len(key)<=100:
            raise ValueError('Choose a valid checkout result and request identifier.')
        fingerprint=content_hash([outcome,500,10,7]);now=self.clock()
        with self.store.db(write=True) as db:
            old=db.execute('SELECT * FROM checkouts WHERE account_id=? AND request_key=?',(owner,key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise ValueError('This checkout request changed.')
                return json.loads(old['payload'])
            receipt=dict(receipt_id=uuid4().hex,is_simulated=True,outcome=outcome,
                price_cents=500,currency='USD',story_limit=10,days=7,created_at=now)
            if outcome=='success':
                if db.execute('SELECT 1 FROM passes WHERE account_id=? AND expires>?',(owner,now)).fetchone():
                    raise ValueError('A weekly pass is already active. It does not renew automatically.')
                receipt.update(pass_id=uuid4().hex,starts_at=now,expires_at=now+7*86400)
                db.execute('INSERT INTO passes VALUES(?,?,?,?)',(receipt['pass_id'],owner,now,receipt['expires_at']))
            db.execute('INSERT INTO checkouts VALUES(?,?,?,?)',(owner,key,fingerprint,canonical_json(receipt)))
        return receipt

    @staticmethod
    def used(db,owner,fund,now):
        completed_and_reserved=db.execute("SELECT count(*) FROM reservations WHERE account_id=? AND fund=? AND "
            "(state='completed' OR (state='reserved' AND expires>?))",(owner,fund,now)).fetchone()[0]
        held=db.execute("SELECT count(*) FROM generation_holds WHERE account_id=? AND fund=? AND state='active' AND expires>?",
            (owner,fund,now)).fetchone()[0]
        return completed_and_reserved+held

    def _fund(self,db,owner,now):
        if self.used(db,owner,'starter',now)<3:return 'starter',now+1800
        active=db.execute('SELECT * FROM passes WHERE account_id=? AND starts<=? AND expires>? ORDER BY starts DESC LIMIT 1',(owner,now,now)).fetchone()
        if not active or self.used(db,owner,active['id'],now)>=10:raise PermissionError('No story credits are available.')
        return active['id'],min(now+1800,active['expires'])

    def authorize_generation(self,owner,run_id,request_hash):
        self.accounts.require(owner);now=self.clock()
        with self.store.db(write=True) as db:
            old=db.execute('SELECT * FROM generation_holds WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
            if old and old['request_hash']!=request_hash:raise ValueError('The generation request changed.')
            if db.execute('SELECT 1 FROM library WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone():return
            if old and old['state']=='active' and old['expires']>now:return
            if db.execute("SELECT 1 FROM reservations WHERE account_id=? AND run_id=? AND state='reserved' AND expires>?",(owner,run_id,now)).fetchone():return
            fund,expires=self._fund(db,owner,now)
            db.execute('INSERT INTO generation_holds VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,run_id) DO UPDATE SET '
                'fund=excluded.fund,state=excluded.state,expires=excluded.expires',(owner,run_id,request_hash,fund,'active',expires))

    @staticmethod
    def _generation_hold(db,owner,run_id,request_hash,now):
        hold=db.execute('SELECT * FROM generation_holds WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
        if not hold or hold['request_hash']!=request_hash or hold['state']!='active' or hold['expires']<=now:
            raise GenerationBlocked('generation_hold_expired')
        return hold

    def require_generation(self,owner,run_id,request_hash):
        self.accounts.require(owner)
        with self.store.db() as db:self._generation_hold(db,owner,run_id,request_hash,self.clock())
        return True

    def claim_generation_operation(self,owner,run_id,request_hash,key):
        self.accounts.require(owner);now=self.clock()
        with self.store.db(write=True) as db:
            self._generation_hold(db,owner,run_id,request_hash,now)
            old=db.execute('SELECT 1 FROM generation_operations WHERE account_id=? AND run_id=? AND operation_key=?',(owner,run_id,key)).fetchone()
            if old:return
            run_count=db.execute('SELECT count(*) FROM generation_operations WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()[0]
            day_start=(now//86400)*86400
            day_count=db.execute('SELECT count(*) FROM generation_operations WHERE account_id=? AND created>=?',(owner,day_start)).fetchone()[0]
            if run_count>=self.max_generation_operations_per_run or day_count>=self.max_generation_operations_per_day:
                raise GenerationBlocked('generation_limit_reached')
            db.execute('INSERT INTO generation_operations VALUES(?,?,?,?)',(owner,run_id,key,now))

    def balance(self,owner):
        self.accounts.require(owner);now=self.clock()
        with self.store.db() as db:
            free=max(0,3-self.used(db,owner,'starter',now))
            completed=db.execute("SELECT count(*) FROM reservations WHERE account_id=? AND fund='starter' AND state='completed'",(owner,)).fetchone()[0]
            active=db.execute('SELECT * FROM passes WHERE account_id=? AND starts<=? AND expires>? ORDER BY starts DESC LIMIT 1',(owner,now,now)).fetchone()
            return dict(is_simulated=True,starter_available=free,starter_completed=completed,
                starter_held=3-free-completed,weekly_available=(
                max(0,10-self.used(db,owner,active['id'],now)) if active else 0),
                weekly_expires_at=active['expires'] if active else None)

    def reserve(self,owner,run_id,book_hash):
        self.accounts.require(owner);now=self.clock()
        with self.store.db(write=True) as db:
            old=db.execute('SELECT * FROM reservations WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
            if old and old['book_hash']!=book_hash and (old['state']=='completed' or
                (old['state']=='reserved' and old['expires']>now)):
                raise ValueError('This story reservation belongs to another active or completed version.')
            if old and old['book_hash']!=book_hash and db.execute('SELECT 1 FROM library WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone():
                raise ValueError('A completed book cannot be rebound.')
            if old and (old['state']=='completed' or (old['state']=='reserved' and old['expires']>now)):return dict(old)
            generation=db.execute("SELECT * FROM generation_holds WHERE account_id=? AND run_id=? AND state='active' AND expires>?",(owner,run_id,now)).fetchone()
            if generation:
                fund,expires=generation['fund'],generation['expires']
                db.execute("UPDATE generation_holds SET state='bound' WHERE account_id=? AND run_id=?",(owner,run_id))
            else:fund,expires=self._fund(db,owner,now)
            reservation=old['id'] if old else uuid4().hex
            db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,run_id) DO UPDATE SET '
                'book_hash=excluded.book_hash,fund=excluded.fund,state=excluded.state,expires=excluded.expires',
                (reservation,owner,run_id,book_hash,fund,'reserved',expires))
            return dict(db.execute('SELECT * FROM reservations WHERE id=?',(reservation,)).fetchone())

    def release(self,owner,run_id):
        self.accounts.require(owner)
        with self.store.db(write=True) as db:
            db.execute("UPDATE reservations SET state='released' WHERE account_id=? AND run_id=? AND state='reserved'",(owner,run_id))
            db.execute("UPDATE generation_holds SET state='released' WHERE account_id=? AND run_id=? AND state='active'",(owner,run_id))

    def complete(self,owner,run_id,book_hash,payload):
        """Internal trusted workflow call, never a browser completion command."""
        self.accounts.require(owner);now=self.clock()
        encoded=canonical_json(payload)
        with self.store.db(write=True) as db:
            reservation=db.execute('SELECT * FROM reservations WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
            if not reservation or reservation['book_hash']!=book_hash:raise PermissionError('A matching story reservation is required.')
            old=db.execute('SELECT * FROM library WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
            if old:
                if old['book_hash']!=book_hash or old['payload']!=encoded:raise ValueError('A completed book cannot be replaced.')
                return
            if reservation['state']!='reserved' or reservation['expires']<=now:raise PermissionError('The story reservation expired. Review and reserve again.')
            db.execute("UPDATE reservations SET state='completed' WHERE id=?",(reservation['id'],))
            db.execute('INSERT INTO library VALUES(?,?,?,?,?)',(owner,run_id,book_hash,encoded,now))
            db.execute('INSERT OR IGNORE INTO library_versions VALUES(?,?,?,?,?)',(owner,run_id,book_hash,encoded,now))

    def publish_version(self,owner,run_id,book_hash,payload):
        """Internal only, after typed output authorization and fresh gates pass."""
        self.accounts.require(owner)
        with self.store.db() as db:old=db.execute('SELECT * FROM library WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
        if not old or old['book_hash']==book_hash:
            # Revision retries do not compare a new version with the original
            # credit binding, which intentionally stays immutable.
            if old:
                if old['payload']!=canonical_json(payload):raise ValueError('Published version cannot change.')
                return
            return self.complete(owner,run_id,book_hash,payload)
        encoded=canonical_json(payload);now=self.clock()
        with self.store.db(write=True) as db:
            current=db.execute('SELECT * FROM library WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
            reservation=db.execute("SELECT * FROM reservations WHERE account_id=? AND run_id=? AND state='completed'",(owner,run_id)).fetchone()
            if not current or not reservation:raise PermissionError('A completed original story is required for a version update.')
            if current['book_hash']==book_hash:
                if current['payload']!=encoded:raise ValueError('Published version cannot change.')
                return
            previous=json.loads(current['payload'])
            if payload['book']['version']<=previous['book']['version']:raise ValueError('Older book versions cannot replace the library pointer.')
            db.execute('INSERT OR IGNORE INTO library_versions VALUES(?,?,?,?,?)',
                (owner,run_id,current['book_hash'],current['payload'],current['created']))
            db.execute('INSERT INTO library_versions VALUES(?,?,?,?,?)',(owner,run_id,book_hash,encoded,now))
            db.execute('UPDATE library SET book_hash=?,payload=?,created=? WHERE account_id=? AND run_id=?',
                (book_hash,encoded,now,owner,run_id))

    def library(self,owner):
        self.accounts.require(owner)
        with self.store.db() as db:return [dict(run_id=r['run_id'],book_hash=r['book_hash'],created_at=r['created'],
                title=(json.loads(r['payload']).get('complete_preview',{}).get('narration_script') or ['Saved storybook'])[0])
            for r in db.execute('SELECT * FROM library WHERE account_id=? ORDER BY created DESC',(owner,))]

    def receipts(self,owner):
        self.accounts.require(owner)
        with self.store.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM checkouts WHERE account_id=?',(owner,))]

    def operation_usage(self,owner):
        self.accounts.require(owner);now=self.clock();start=(now//86400)*86400
        with self.store.db() as db:
            used=db.execute('SELECT count(*) FROM generation_operations WHERE account_id=? AND created>=?',(owner,start)).fetchone()[0]
        return dict(used=used,limit=self.max_generation_operations_per_day,resets_at=start+86400,timezone='UTC')

    def import_history(self,owner,key,items,*,consent):
        self.accounts.require(owner)
        if consent is not True or not isinstance(items,list) or len(items)>100:raise ValueError('Explicit consent and up to 100 history entries are required.')
        # Only descriptive metadata; never import financial, approval or QA authority.
        cleaned=[]
        for item in items:
            if not isinstance(item,dict) or set(item)-{'title','character','created_at'}:raise ValueError('History contains unsupported fields.')
            if any(not isinstance(v,str) or len(v)>200 for v in item.values()):raise ValueError('History values must be short text.')
            cleaned.append(dict(item,verified=False,can_export=False))
        digest=content_hash(cleaned)
        with self.store.db(write=True) as db:
            old=db.execute('SELECT digest FROM imports WHERE account_id=? AND request_key=?',(owner,key)).fetchone()
            if old and old[0]!=digest:raise ValueError('This import request changed.')
            db.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?,?,?)',(owner,key,digest,canonical_json(cleaned),self.clock()))
        return dict(imported=len(cleaned),verified=False,story_credits_granted=0)
