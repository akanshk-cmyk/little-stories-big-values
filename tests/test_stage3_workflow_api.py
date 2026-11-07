"""Loopback-only asynchronous API checks with explicitly injected fixtures."""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import test_festivals as existing
from test_stage3_workflows import adapters, request
from backend.workflows.service import WorkflowService


class WorkflowApi(unittest.TestCase):
    call = existing.ApprovalApi.call

    @classmethod
    def setUpClass(cls):
        cls.server=existing.ThreadingHTTPServer(('127.0.0.1',0),existing.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close()

    def setUp(self):
        existing.ApprovalApi.setUp(self)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service=WorkflowService(self.temp.name,adapters(),enabled=True)
        self.addCleanup(self.service.close)
        self.inject=patch.object(self.server,'workflow_service',self.service,create=True)
        self.inject.start();self.addCleanup(self.inject.stop)

    def command(self, **updates):
        return {'request_key':'api-request','request':request().model_dump(mode='json'),
                'workflow':'stage3_replay'}|updates

    def wait(self, url):
        deadline=time.monotonic()+10
        last=None
        while time.monotonic()<deadline:
            status,body=self.call(url)
            last=(status,body)
            if status==200 and body['values'].get('status')=='awaiting_parent_approval':
                return body['values']
            if status not in (200,409):self.fail(str((status,body)))
            time.sleep(0.02)
        self.fail('Worker did not reach the parent checkpoint. Last response: '+str(last))

    def test_disabled_default_and_legacy_health(self):
        with patch.object(self.server,'workflow_service',None):
            self.assertEqual(self.call('/api/workflow-runs',self.command())[0],404)
            self.assertEqual(self.call('/api/workflow-runs/unknown')[0],404)
            self.assertEqual(self.call('/api/health')[0],200)

    def test_async_start_poll_resume_and_approve(self):
        status,body=self.call('/api/workflow-runs',self.command())
        self.assertEqual(status,202)
        state=self.wait(body['status_url'])
        self.assertEqual(self.call(body['status_url']+'/resume',{'approved':True})[0],400)
        status,approved=self.call(body['status_url']+'/approve',{'book_version_hash':state['book_version_hash']})
        self.assertEqual(status,200,approved)
        self.assertEqual(approved['values']['status'],'complete')
        self.assertEqual(approved['provenance'],'offline_replay')

    def test_strict_input_csrf_and_other_session(self):
        self.assertEqual(self.call('/api/workflow-runs',self.command(provider='groq'))[0],400)
        self.assertEqual(self.call('/api/workflow-runs',self.command(),token=False)[0],403)
        _,body=self.call('/api/workflow-runs',self.command())
        self.wait(body['status_url'])
        other=existing.build_opener(existing.HTTPCookieProcessor(existing.http.cookiejar.CookieJar()))
        self.assertEqual(self.call(body['status_url'],client=other)[0],404)
