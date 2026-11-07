"""Local HTTP source-review workflow. No real source is approved by tests."""
from io import BytesIO
import json
import tempfile
import threading
import time
import unittest
from urllib.request import Request
from urllib.error import HTTPError
from unittest.mock import patch
import test_festivals as existing
from test_stage4_ingestion import pdf_bytes
from backend.ingestion.workspace import SourceWorkspace


class WorkspaceApi(unittest.TestCase):
    call=existing.ApprovalApi.call

    @classmethod
    def setUpClass(cls):
        cls.server=existing.ThreadingHTTPServer(('127.0.0.1',0),existing.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()

    def setUp(self):
        existing.ApprovalApi.setUp(self)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=SourceWorkspace(self.temp.name);self.addCleanup(self.workspace.close)
        inject=patch.object(self.server,'source_workspace',self.workspace,create=True)
        inject.start();self.addCleanup(inject.stop)

    def upload(self,token=True):
        raw=pdf_bytes(['BOOK I','A gentle source about patience.'])
        headers={'Content-Type':'application/pdf','X-Document-Title':'Synthetic PDF','X-Document-Edition':'Unverified fixture'}
        if token:headers['X-Bala-CSRF']=self.csrf
        req=Request(self.base+'/api/source-workspace/upload',data=raw,headers=headers,method='POST')
        try:response=self.client.open(req,timeout=10)
        except HTTPError as error:response=error
        with response:return response.status,json.loads(response.read())

    def ready(self):
        status,result=self.upload();self.assertEqual(status,202,result)
        url=result['statusUrl'];deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            status,source=self.call(url)
            if source['status']=='ready':return url,source
            if source['status']=='failed':self.fail(str(source))
            time.sleep(.02)
        self.fail('Source worker timed out.')

    def test_upload_original_preview_and_boundary_review(self):
        url,source=self.ready()
        self.assertEqual(source['page_count'],2)
        status,page=self.call(url+'/pages?page=2');self.assertEqual(status,200)
        self.assertIn('patience',page['clean_text'])
        self.assertEqual(self.call(url+'/original')[0],200)
        status,updated=self.call(url+'/boundaries',dict(expected_generation=source['generation'],
            boundaries=source['metadata']['boundaries'],target_words=300,overlap_words=50))
        self.assertEqual(status,200,updated)
        _,chunks=self.call(url+'/chunks?limit=5');chunk=chunks[0]
        status,review=self.call(url+'/reviews',dict(expected_generation=updated['generation'],chunk_id=chunk['chunk_id'],
            content_hash=chunk['content_hash'],action='allow',extraction_checked=True,cultural_context_checked=True,
            child_suitability_checked=True,rights_permitted=True,rights_basis='This is a synthetic fixture.',notes='Reviewed fixture.'))
        self.assertEqual(status,201,review)
        self.assertFalse(review['traditional_claims_eligible'])

    def test_other_parent_cannot_read_or_review(self):
        url,source=self.ready()
        other=existing.build_opener(existing.HTTPCookieProcessor(existing.http.cookiejar.CookieJar()))
        for suffix in ('','/pages?page=1','/chunks','/original'):
            self.assertEqual(self.call(url+suffix,client=other)[0],404)
        self.assertEqual(self.call('/api/source-workspace',client=other),(200,[]))

    def test_csrf_stale_edit_and_untrusted_authority_rejected(self):
        self.assertEqual(self.upload(token=False)[0],403)
        url,source=self.ready()
        command=dict(expected_generation=999,boundaries=[],target_words=300,overlap_words=50)
        self.assertEqual(self.call(url+'/boundaries',command)[0],400)
        self.assertEqual(self.call(url+'/reviews',{'authority':'qualified'})[0],400)

    def test_workspace_screen_and_scripts_are_served(self):
        for path in ('/source-workspace.html','/source-workspace.js','/source-workspace.css'):
            self.assertEqual(self.call(path)[0],200)
