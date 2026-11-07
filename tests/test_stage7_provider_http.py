"""Actual loopback HTTP integration and configuration checks, without paid calls."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import httpx
import test_festivals as existing
from test_stage7_providers import SETTINGS,Clock,model_request,context_validator,draft
from backend.env_config import load_local_env
from backend.providers.attempt_ledger import AttemptLedger
from backend.providers.router import ProviderRouter


class LocalProvider(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.paths.append(self.path)
        primary=self.path.startswith('/openai/')
        status=503 if primary else 200
        payload={} if primary else {'id':'loopback-fixture','model':body['model'],
            'choices':[{'message':{'content':draft().model_dump_json()},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':12,'completion_tokens':34}}
        content=json.dumps(payload).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)


class LoopbackTransport(httpx.BaseTransport):
    def __init__(self,base):self.base=base;self.transport=httpx.HTTPTransport(retries=0)
    def handle_request(self,request):
        local=httpx.Request(request.method,self.base+request.url.path,headers=request.headers,
                            content=request.read(),extensions=request.extensions)
        return self.transport.handle_request(local)
    def close(self):self.transport.close()


class ProviderHttpTests(unittest.TestCase):
    def test_rate_limit_body_wait_is_honored_when_header_is_missing(self):
        clock=Clock();calls=[]
        def handler(req):
            calls.append(req)
            if len(calls)==1:return httpx.Response(429,json={'error':{'message':'Please try again in 7.8s.'}})
            return httpx.Response(200,json={'model':SETTINGS['groq'].writer_model,'choices':[{'message':{'content':draft().model_dump_json()},'finish_reason':'stop'}]})
        with tempfile.TemporaryDirectory() as folder,httpx.Client(transport=httpx.MockTransport(handler)) as client:
            router=ProviderRouter(SETTINGS,AttemptLedger(folder),enabled=True,clients={'groq':client,'nebius':client},clock=clock,sleep=clock.sleep,max_retry_delay=65)
            router.generate('parent1',model_request().model_copy(update={'mode':'groq'}),guard=lambda req:True,validate_output=context_validator)
            self.assertEqual(len(calls),2)
            self.assertGreaterEqual(clock(),7.8)

    def test_real_http_roundtrip_retries_then_falls_back(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),LocalProvider);server.paths=[]
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as folder:
            base=f'http://127.0.0.1:{server.server_port}'
            with httpx.Client(transport=LoopbackTransport(base),trust_env=False) as client:
                clock=Clock();router=ProviderRouter(SETTINGS,AttemptLedger(folder),enabled=True,
                    clients={'groq':client,'nebius':client},clock=clock,sleep=clock.sleep)
                try:response=router.generate('parent1',model_request(),guard=lambda req:True,validate_output=context_validator)
                finally:router.close()
            self.assertEqual(response.provider,'nebius');self.assertEqual(response.output_tokens,34)
            self.assertEqual(server.paths,['/openai/v1/chat/completions','/openai/v1/chat/completions','/v1/chat/completions'])

    def test_extended_env_loader_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'GROQ_MODEL':'already-configured'},clear=True):
            path=Path(folder)/'settings.env'
            path.write_text('GROQ_API_KEY=SYNTHETIC-KEY\nGROQ_MODEL=ignored\nGROQ_REVIEW_MODEL=review-fixture\nAI_PROVIDER_MODE=nebius\nUNSUPPORTED=ignored\n')
            load_local_env(path)
            import os
            self.assertEqual(os.environ['GROQ_MODEL'],'already-configured')
            self.assertEqual(os.environ['AI_PROVIDER_MODE'],'nebius');self.assertNotIn('UNSUPPORTED',os.environ)


class ReadinessApi(unittest.TestCase):
    call=existing.ApprovalApi.call
    @classmethod
    def setUpClass(cls):
        cls.server=existing.ThreadingHTTPServer(('127.0.0.1',0),existing.Handler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
    def setUp(self):existing.ApprovalApi.setUp(self)

    def test_readiness_cannot_claim_authentication_or_expose_keys(self):
        with patch.dict('os.environ',{'GROQ_API_KEY':'SYNTHETIC-SECRET-KEY','GROQ_MODEL':'fixture-model','AI_PROVIDER_MODE':'auto'}):
            status,report=self.call('/api/provider-readiness')
        self.assertEqual(status,200)
        self.assertTrue(report['providers']['groq']['writer_configured'])
        self.assertEqual(report['providers']['groq']['authentication'],'not_checked')
        self.assertFalse(report['new_router_live_enabled'])
        self.assertNotIn('SYNTHETIC-SECRET-KEY',json.dumps(report))
