"""V5 exact-script authorization gate. Opt-in; never reinterprets v4 approvals."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from backend.contracts.episode_selection import ScriptBinding
from backend.contracts.common import canonical_json


class ScriptApprovalGate:
    def __init__(self,folder,*,authorize,current):
        if not callable(authorize) or not callable(current):raise TypeError('Trusted ownership and current-artifact guards required.')
        self.authorize,self.current=authorize,current
        self.path=Path(folder)/'v5-script-approvals.sqlite'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS approvals(owner TEXT,run TEXT,binding TEXT,payload TEXT,active INTEGER,PRIMARY KEY(owner,run,binding))')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path)
        try:
            with db:yield db
        finally:db.close()

    def check_current(self,owner,binding):
        if not isinstance(binding,ScriptBinding):raise TypeError('Validated script binding required.')
        if self.authorize(owner,binding.run_id) is not True:raise PermissionError('Story ownership unavailable.')
        if self.current(owner,binding) is not True:raise PermissionError('Source, QA, script or assets are stale.')

    def approve(self,owner,binding):
        self.check_current(owner,binding)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE approvals SET active=0 WHERE owner=? AND run=?',(owner,binding.run_id))
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,1) ON CONFLICT(owner,run,binding) DO UPDATE SET active=1',(owner,binding.run_id,binding.binding_hash,canonical_json(binding)))
        return binding.binding_hash

    def invalidate(self,owner,run):
        if self.authorize(owner,run) is not True:raise PermissionError('Story ownership unavailable.')
        with self.db() as db:db.execute('UPDATE approvals SET active=0 WHERE owner=? AND run=?',(owner,run))

    def require(self,owner,binding):
        self.check_current(owner,binding)
        with self.db() as db:
            row=db.execute('SELECT payload FROM approvals WHERE owner=? AND run=? AND binding=? AND active=1',(owner,binding.run_id,binding.binding_hash)).fetchone()
        if not row or ScriptBinding.model_validate_json(row[0])!=binding:raise PermissionError('Approve the exact final script first.')
        return binding.binding_hash

    def narrate(self,owner,binding,*,script,submit):
        from backend.contracts.common import content_hash
        if content_hash(script)!=binding.script_hash:raise PermissionError('Narration script differs from approval.')
        self.require(owner,binding)
        return submit(script)
