import json,tempfile,unittest,threading
from pathlib import Path
from unittest.mock import patch,Mock
from backend.readiness import check_local_readiness
from backend.accounts.identity import Accounts,LoginFailure
from backend.accounts.store import Store

class ReadinessTests(unittest.TestCase):
    def test_missing_sources_are_actionable_and_no_secrets_are_exposed(self):
        with tempfile.TemporaryDirectory() as folder:
            report=check_local_readiness(folder,{'GROQ_API_KEY':'private-secret-value','GROQ_MODEL':'model'},inspect_source=False)
        self.assertEqual(report['status'],'BLOCKED');self.assertEqual(report['paid_calls'],0)
        self.assertNotIn('private-secret-value',json.dumps(report))
        self.assertTrue(any(c['check']=='source_workspace' and c['status']=='BLOCKED' for c in report['checks']))

    def test_startup_source_preparation_cannot_make_a_paid_call_without_opt_in(self):
        from backend.local_ramayan_test import prepare_source
        with tempfile.TemporaryDirectory() as folder,patch('backend.local_ramayan_test.FOLDER',Path(folder)),patch('backend.local_ramayan_test.source',return_value=(None,None,'')),patch('backend.local_ramayan_test.SourceQAAgent') as agent:
            with self.assertRaisesRegex(ValueError,'will not run paid'):prepare_source(Mock())
            agent.assert_not_called()

class AccountRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=100000.;self.accounts=Accounts(Store(self.temp.name),clock=lambda:self.now)
        self.password='Synthetic-old-password-123';self.new='Synthetic-new-password-456'
        self.owner=self.accounts.register('parent1',self.password)['account_id']

    def test_recovery_is_single_use_and_revokes_all_sessions(self):
        first=self.accounts.login('parent1',self.password);second=self.accounts.login('parent1',self.password)
        code=self.accounts.create_recovery_code(self.owner,self.password)['recovery_code']
        with self.accounts.store.db() as db:self.assertNotIn(code,db.execute('SELECT code_hash FROM recovery_codes').fetchone()[0])
        self.accounts.recover_password('parent1',code,self.new)
        for session in (first,second):
            with self.assertRaises(PermissionError):self.accounts.authenticate(session['token'])
        with self.assertRaises(LoginFailure):self.accounts.recover_password('parent1',code,'Another-password-789')
        self.assertEqual(self.accounts.login('parent1',self.new)['account_id'],self.owner)

    def test_rotation_invalidates_previous_code(self):
        old=self.accounts.create_recovery_code(self.owner,self.password)['recovery_code']
        fresh=self.accounts.create_recovery_code(self.owner,self.password)['recovery_code']
        with self.assertRaises(LoginFailure):self.accounts.recover_password('parent1',old,self.new)
        self.accounts.recover_password('parent1',fresh,self.new)

    def test_wrong_password_cannot_create_code_or_change_password(self):
        with self.assertRaises(LoginFailure):self.accounts.create_recovery_code(self.owner,'Incorrect-password-123')
        with self.assertRaises(LoginFailure):self.accounts.change_password(self.owner,'Incorrect-password-123',self.new)

    def test_password_change_invalidates_recovery_and_old_sessions(self):
        session=self.accounts.login('parent1',self.password)
        code=self.accounts.create_recovery_code(self.owner,self.password)['recovery_code']
        self.accounts.change_password(self.owner,self.password,self.new)
        with self.assertRaises(PermissionError):self.accounts.authenticate(session['token'])
        with self.assertRaises(LoginFailure):self.accounts.recover_password('parent1',code,'Another-password-789')

    def test_recovery_failures_are_rate_limited_across_instances(self):
        code='x'*43
        for _ in range(5):
            with self.assertRaises(LoginFailure):self.accounts.recover_password('parent1',code,self.new)
        fresh=Accounts(self.accounts.store,clock=lambda:self.now)
        with self.assertRaises(LoginFailure) as blocked:fresh.recover_password('parent1',code,self.new)
        self.assertEqual(blocked.exception.code,'rate_limited')

class SavedRequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        import test_stage9_workflow as fixture
        from backend.accounts.workflow import AccountWorkflowService,AccountAdapters
        from test_contracts import request
        self.case=fixture.ManagedWorkflowTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        base=self.case.fixture.service();self.folder=Path(self.case.temp.name)/'recovery'
        self.service=AccountWorkflowService(self.folder,AccountAdapters(base.adapters,self.case.accounts,self.case.registry),self.case.accounts,self.case.ledger,enabled=True)
        self.addCleanup(self.service.close)
        self.owner=self.case.parent;self.req=request(child_profile_id=self.case.child)
        self.run=self.service.start(self.owner,'saved-request',self.req.model_dump(mode='json'),generation_mode='provider_synthetic')

    def test_queued_request_survives_service_restart(self):
        from backend.accounts.workflow import AccountWorkflowService
        fresh=AccountWorkflowService(self.folder,self.service.adapters,self.case.accounts,self.case.ledger,enabled=True);self.addCleanup(fresh.close)
        self.assertTrue(fresh.recovery_options(self.run,self.owner)['can_resume'])
        state=fresh.execute(self.run,self.owner)
        self.assertEqual(state['status'],'assets_pending')
        self.assertFalse(fresh.recovery_options(self.run,self.owner)['can_resume'])

    def test_uncertain_operation_is_not_repeated(self):
        with self.service.repository.db() as db:db.execute('INSERT INTO operations VALUES(?,?,?,NULL)',(self.run,'write-0','started'))
        self.assertFalse(self.service.recovery_options(self.run,self.owner)['can_resume'])
        with self.assertRaises(ValueError):self.service.resume_saved(self.run,self.owner)
        self.assertTrue(self.service.recovery_options(self.run,self.owner)['requires_attention'])
        self.assertEqual(self.case.fixture.calls,[])

    def test_foreign_owner_cannot_read_or_resume(self):
        other=self.case.accounts.register('other-parent','Synthetic-other-password')['account_id']
        with self.assertRaises(PermissionError):self.service.recovery_options(self.run,other)

    def test_inflight_submissions_are_deduplicated(self):
        gate=threading.Event();entered=threading.Event()
        def execute(*args):entered.set();gate.wait(5);return {'status':'queued'}
        with patch.object(self.service,'execute',side_effect=execute) as worker:
            try:
                first=self.service.submit(self.run,self.owner);self.assertTrue(entered.wait(2))
                second=self.service.submit(self.run,self.owner)
                self.assertIs(first,second);self.assertFalse(self.service.recovery_options(self.run,self.owner)['can_resume'])
            finally:gate.set();first.result(timeout=5)
            self.assertEqual(worker.call_count,1)

    def test_expired_hold_does_not_offer_resume(self):
        self.case.ledger.release(self.owner,self.run)
        self.assertFalse(self.service.recovery_options(self.run,self.owner)['can_resume'])
        self.assertTrue(self.service.recovery_options(self.run,self.owner)['requires_attention'])

    def test_new_revision_is_not_lost_when_previous_worker_is_finishing(self):
        gate=threading.Event();entered=threading.Event()
        def execute(*args):entered.set();gate.wait(5);return {'status':'awaiting_parent_approval'}
        with patch.object(self.service,'execute',side_effect=execute) as worker:
            try:
                first=self.service.submit(self.run,self.owner);self.assertTrue(entered.wait(2))
                with patch.object(self.service,'status',return_value={'values':{'revision_epoch':'new-revision'}}):
                    second=self.service.submit(self.run,self.owner)
                self.assertIsNot(first,second)
            finally:gate.set();first.result(timeout=5);second.result(timeout=5)
            self.assertEqual(worker.call_count,2)
