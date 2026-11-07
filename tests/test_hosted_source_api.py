"""Real loopback sessions/CSRF and mocked hosted services; no paid calls."""
import http.cookiejar
from io import BytesIO
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError

import httpx
from pydantic import SecretStr
from pypdf import PdfWriter

# Establish the existing temporary server-data sandbox before importing Handler.
import test_festivals as legacy_http_fixtures
import test_stage9_account_api as secure
from backend.accounts.http_api import AccountAPI
from backend.ingestion.workspace import SourceWorkspace
from backend.ingestion.hosted_review import HostedSourceReview, ProcessingPermission
from backend.ingestion.hosted_service import HostedSourceService
from backend.ingestion.llamaparse import LlamaParseAdapter, ParseJobs
from backend.retrieval.hosted_config import HostedSettings
from backend.retrieval.llamacloud import HostedPublications, LlamaCloudPublisher


class HostedSourceAPI(unittest.TestCase):
    call = secure.SecureAccountAPI.call
    setUpClass = classmethod(secure.SecureAccountAPI.setUpClass.__func__)
    tearDownClass = classmethod(secure.SecureAccountAPI.tearDownClass.__func__)

    def setUp(self):
        secure.SecureAccountAPI.setUp(self)
        self.parent = secure.SecureAccountAPI.sign_in(self)['account_id']
        self.admin = self.accounts.bootstrap_admin('admin1', secure.PASSWORD)['account_id']
        root = Path(self.temp.name)
        self.workspace = SourceWorkspace(root / 'hosted-originals')
        self.addCleanup(self.workspace.close)
        self.settings = HostedSettings(enabled=True, api_key=SecretStr('fixture-key'),
            organization_id='org1', project_id='project1', api_generation='v2')
        def manage(actor, action, owner, source):
            try:
                self.accounts.require(actor, 'admin')
                self.accounts.require(owner, role=None)
                return True  # Synthetic curation policy; not actual expert qualification.
            except PermissionError:
                return False
        self.reviews = HostedSourceReview(root / 'reviews', self.workspace, self.settings,
            can_manage=manage, usage_allowed=lambda permission, destination:
                permission.usage_authorization_id == 'fixture-parse-usage')
        self.calls, self.metadata = [], None
        self.parse_state = 'COMPLETED'
        self.timeout_upload = False
        self.text = 'Synthetic child shares a paper flower.'
        self.parser = LlamaParseAdapter(self.settings, ParseJobs(root / 'parse-jobs'),
            authorize=self.reviews.authorize_parse, transport=httpx.MockTransport(self.handler))
        self.publisher = LlamaCloudPublisher(self.settings, HostedPublications(root / 'publications'),
            self.reviews, transport=httpx.MockTransport(self.handler), authorize=lambda *args: True)
        self.service = self.make_service()
        self.addCleanup(self.service.close)
        self.api.hosted_sources = self.service  # Explicit fixture-only attachment.
        out = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(out)
        self.pdf = out.getvalue()

    def make_service(self):
        return HostedSourceService(Path(self.temp.name) / 'tasks', self.accounts, self.workspace,
            self.parser, self.reviews, self.publisher, enabled=True, parser_version='2026-09-28',
            index_parse_config_id='fixture-config', index_usage=lambda *args: 'fixture-index-usage',
            max_steps=1, poll_interval=0)

    def handler(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == '/api/v1/beta/files':
            if self.timeout_upload:
                raise httpx.ReadTimeout('private-provider-detail')
            file_id = 'parsed-source-file' if b'name="purpose"\r\n\r\nparse' in request.content else 'index-file'
            return httpx.Response(200, json={'id': file_id, 'project_id': 'project1'})
        if path == '/api/v2/parse':
            return httpx.Response(200, json={'id': 'parse-job', 'status': 'PENDING'})
        if path == '/api/v2/parse/parse-job':
            value = {'job': {'id': 'parse-job', 'status': self.parse_state}}
            if self.parse_state == 'COMPLETED':
                value['markdown'] = {'pages': [{'page_number': 1, 'success': True, 'markdown': self.text}]}
            return httpx.Response(200, json=value)
        if path == '/api/v1/beta/directories':
            return httpx.Response(200, json={'id': 'dir1', 'project_id': 'project1'})
        if path == '/api/v1/beta/directories/dir1/files' and request.method == 'POST':
            self.metadata = json.loads(request.content)['metadata']
            return httpx.Response(200, json={'id': 'entry1', 'file_id': 'index-file',
                'directory_id': 'dir1', 'project_id': 'project1'})
        if path == '/api/v1/indexes':
            return httpx.Response(200, json={'id': 'index1', 'source_directory_id': 'dir1', 'project_id': 'project1'})
        if path == '/api/v1/indexes/index1':
            return httpx.Response(200, json=dict(id='index1', source_directory_id='dir1', project_id='project1',
                output_directory_id='out1', export_config_id='export1', sync_in_progress=False,
                metadata={'status': 'ready'}, last_synced_at='2026-11-05T10:00:00Z',
                last_exported_at='2026-11-05T10:00:00Z'))
        if path.endswith('/files'):
            source = '/dir1/' in path
            value = dict(id='entry1' if source else 'parsed1', file_id='index-file', project_id='project1',
                         directory_id='dir1' if source else 'out1', metadata=self.metadata)
            return httpx.Response(200, json={'data': [value], 'has_more': False, 'next_cursor': None})
        if path == '/api/v1/retrieval/files/read':
            return httpx.Response(200, json={'content': self.text})
        raise AssertionError('Unexpected hosted fixture route.')

    def login(self, name):
        self.csrf = self.call('/api/health')[1]['csrf']
        status, value = self.call('/api/accounts/login', dict(login=name, password=secure.PASSWORD))
        self.assertEqual(status, 200, value)
        self.csrf = value['csrf']

    def upload(self, title='Fixture PDF', token=True):
        headers = {'Content-Type': 'application/pdf', 'X-Document-Title': title,
                   'X-Document-Edition': 'Synthetic edition'}
        if token:
            headers['X-Bala-CSRF'] = self.csrf
        req = Request(self.base + '/api/accounts/hosted-sources/upload', data=self.pdf, headers=headers, method='POST')
        try:
            response = self.client.open(req, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    def grant(self, source):
        spec = self.service.parse_request(self.parent, source)
        return self.reviews.grant_processing(self.admin, ProcessingPermission(request=spec,
            rights_basis='Synthetic transmission permission.', usage_authorization_id='fixture-parse-usage', max_pages=1))

    def wait(self, job):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            status, value = self.call('/api/accounts/hosted-sources/jobs/' + job)
            self.assertEqual(status, 200, value)
            if value['state'] not in ('queued', 'running'):
                return value
            time.sleep(.01)
        self.fail('Offline source worker did not finish.')

    def parsed(self):
        status, source = self.upload()
        self.assertEqual(status, 201, source)
        self.grant(source['source_id'])
        status, task = self.call('/api/accounts/hosted-sources/' + source['source_id'] + '/parse', {'request_key': 'parse1'})
        self.assertEqual(status, 202, task)
        return source['source_id'], task['job_id'], self.wait(task['job_id'])

    def test_internal_source_qa_automatically_runs_after_parse(self):
        from backend.ingestion.source_qa import SourceQAAgent,InternalSourceReview
        from backend.ingestion.hosted_review import PassageQualification,PageSpan
        from backend.contracts.common import text_hash
        from backend.contracts.source_qa import SourceQAReport,SourceQACheck,SourceQuote,SOURCE_CHECKS
        self.reviews.automated_authorize=lambda *args:True
        self.reviews.rights_authorize=lambda *args:True
        calls=[]
        def judge(prompt,payload):
            calls.append(payload)
            check=SourceQACheck(status='PASS',reason='Synthetic automated review.',quotes=(SourceQuote(pdf_page=1,quote=self.text),))
            return SourceQAReport(subject_hash=payload['subject_hash'],reviewer_version=payload['reviewer_version'],**dict.fromkeys(SOURCE_CHECKS,check))
        def candidates(owner,extraction,doc):
            return (PassageQualification(extraction_id=extraction,spans=(PageSpan(pdf_page=1,start=0,end=len(self.text)),),expected_text_hash=text_hash(self.text),scope='traditional_claims',rights_basis='Separately authorized synthetic rights.',edition_reference='Synthetic edition.',cultural_review_basis='Automated source QA only.',notes='No qualified human review claimed.'),)
        self.service.source_qa=InternalSourceReview(SourceQAAgent(judge=judge,authorize=lambda *args:True),candidates=candidates)
        source,job,result=self.parsed()
        self.assertEqual(result['details']['phase'],'source_qa_passed',result)
        self.assertEqual(len(calls),1)
        manifest=self.reviews.eligible(self.parent,result['details']['extraction_id'])
        self.assertTrue(manifest.passages[0].reference.review_record_ids[0].startswith('automated-qa-'))
        with self.reviews.db() as db:self.assertEqual(db.execute('select count(*) from qualifications').fetchone()[0],0)
        before=len(calls);self.call('/api/accounts/hosted-sources/jobs/'+job)
        self.assertEqual(len(calls),before)

    def test_upload_is_owned_and_does_not_start_cloud_processing(self):
        status, source = self.upload()
        self.assertEqual(status, 201, source)
        self.assertEqual(source['state'], 'awaiting_processing_permission')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.call('/api/accounts/hosted-sources/' + source['source_id'])[0], 200)
        self.assertTrue(self.call('/api/accounts/capabilities')[1]['hosted_sources_enabled'])

    def test_upload_and_json_mutations_require_session_csrf(self):
        self.assertEqual(self.upload(token=False)[0], 403)
        self.assertEqual(self.call('/api/accounts/hosted-sources/fake/parse', {'request_key': 'x'}, token=False)[0], 403)
        self.client = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertEqual(self.upload()[0], 401)
        self.assertEqual(self.calls, [])

    def test_parent_cannot_grant_review_or_activate_even_with_role_flag(self):
        for action in ('grant-processing', 'qualify', 'activate', 'publish', 'extraction'):
            status, _ = self.call('/api/accounts/hosted-sources/admin/' + action, {'role': 'admin'})
            self.assertEqual(status, 403)
        self.assertEqual(self.calls, [])

    def test_parse_requires_permission_and_cannot_accept_owner_override(self):
        _, source = self.upload()
        path = '/api/accounts/hosted-sources/' + source['source_id'] + '/parse'
        self.assertEqual(self.call(path, {'request_key': 'x'})[0], 403)
        self.assertEqual(self.call(path, {'request_key': 'x', 'owner_id': self.admin})[0], 400)
        self.assertEqual(self.calls, [])

    def test_background_parse_finishes_and_status_get_is_read_only(self):
        source, job, value = self.parsed()
        self.assertEqual(value['state'], 'completed', value)
        self.assertEqual(value['details']['phase'], 'awaiting_source_review')
        count = len(self.calls)
        for _ in range(3):
            self.assertEqual(self.call('/api/accounts/hosted-sources/jobs/' + job)[0], 200)
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'], 3)
        self.assertEqual(self.call('/api/accounts/hosted-sources/' + source + '/parse', {'request_key': 'parse1'})[1]['job_id'], job)
        self.assertEqual(self.call('/api/accounts/hosted-sources/' + source)[1]['jobs'][0]['job_id'], job)

    def test_other_parent_cannot_read_source_or_job(self):
        source, job, _ = self.parsed()
        secure.SecureAccountAPI.sign_in(self, 'parent2')
        self.assertEqual(self.call('/api/accounts/hosted-sources/' + source)[0], 403)
        self.assertEqual(self.call('/api/accounts/hosted-sources/jobs/' + job)[0], 403)

    def test_unknown_upload_outcome_requires_attention_not_retry(self):
        self.timeout_upload = True
        _, _, value = self.parsed()
        self.assertEqual(value['state'], 'needs_attention')
        self.assertEqual(value['error_code'], 'submission_needs_reconciliation')
        count = len(self.calls)
        self.assertEqual(self.call('/api/accounts/hosted-sources/jobs/' + value['job_id'] + '/resume', {})[0], 400)
        self.assertEqual(len(self.calls), count)

    def test_pending_job_restarts_and_resumes_same_provider_job(self):
        self.parse_state = 'RUNNING'
        _, job, value = self.parsed()
        self.assertEqual(value['state'], 'waiting_provider')
        self.service.close()
        self.service = self.make_service()
        self.addCleanup(self.service.close)
        self.api.hosted_sources = self.service
        self.parse_state = 'COMPLETED'
        self.assertEqual(self.call('/api/accounts/hosted-sources/jobs/' + job + '/resume', {})[0], 202)
        self.assertEqual(self.wait(job)['state'], 'completed')
        self.assertEqual(sum(c.method == 'POST' and c.url.path == '/api/v2/parse' for c in self.calls), 1)

    def test_admin_http_grant_and_qualified_publication_pipeline(self):
        _, source = self.upload()
        self.login('admin1')
        status, preview = self.call('/api/accounts/hosted-sources/admin/source',
                                   {'owner_id': self.parent, 'source_id': source['source_id']})
        self.assertEqual(status, 200, preview)
        status, grant = self.call('/api/accounts/hosted-sources/admin/grant-processing', {'permission': dict(
            request=preview['parse_request'], rights_basis='Synthetic permission only.',
            usage_authorization_id='fixture-parse-usage', max_pages=1)})
        self.assertEqual(status, 201, grant)
        self.login('parent1')
        status, task = self.call('/api/accounts/hosted-sources/' + source['source_id'] + '/parse', {'request_key': 'parse1'})
        self.assertEqual(status, 202, task)
        value = self.wait(task['job_id'])
        extraction = value['details']['extraction_id']
        self.login('admin1')
        status, document = self.call('/api/accounts/hosted-sources/admin/extraction',
                                    {'owner_id': self.parent, 'record_id': extraction})
        self.assertEqual(status, 200, document)
        from backend.contracts.common import text_hash
        command = dict(extraction_id=extraction, spans=[dict(pdf_page=1, start=0, end=len(self.text))],
            expected_text_hash=text_hash(self.text), scope='traditional_claims', extraction_checked=True,
            boundaries_checked=True, edition_verified=True, cultural_context_checked=True,
            child_suitability_checked=True, rights_permitted=True, index_text_permitted=True,
            rights_basis='Synthetic rights only.', edition_reference='Fixture edition.',
            cultural_review_basis='Synthetic policy fixture.', notes='Not actual cultural qualification.')
        status, reviewed = self.call('/api/accounts/hosted-sources/admin/qualify',
                                     {'owner_id': self.parent, 'qualification': command})
        self.assertEqual(status, 201, reviewed)
        status, task = self.call('/api/accounts/hosted-sources/admin/publish', dict(owner_id=self.parent,
            extraction_id=extraction, purpose='traditional_claims', request_key='publish1'))
        self.assertEqual(status, 202, task)
        self.login('parent1')
        value = self.wait(task['job_id'])
        self.assertEqual(value['details']['phase'], 'awaiting_activation', value)
        self.assertEqual(self.call('/api/accounts/hosted-sources/admin/activate',
                                  {'owner_id': self.parent, 'record_id': task['job_id']})[0], 403)
        self.login('admin1')
        status, value = self.call('/api/accounts/hosted-sources/admin/activate',
                                  {'owner_id': self.parent, 'record_id': task['job_id']})
        self.assertEqual(status, 200, value)
        self.assertEqual(value['state'], 'active')
        self.assertEqual(self.call('/api/accounts/hosted-sources/admin/withdraw-passage',
            {'owner_id': self.parent, 'record_id': reviewed['qualification_id']})[0], 200)
        with self.assertRaises(PermissionError):
            self.publisher.active(self.parent, value['publication_id'])

    def test_feature_disabled_and_unattached_are_unavailable(self):
        self.api.hosted_sources = None
        self.assertEqual(self.upload()[0], 404)
        self.assertEqual(self.call('/api/accounts/hosted-sources')[0], 404)
        self.assertFalse(self.call('/api/accounts/capabilities')[1]['hosted_sources_enabled'])

    def test_api_rejects_a_different_account_authority(self):
        with self.assertRaises(ValueError):
            AccountAPI(object(), self.ledger, self.registry, hosted_sources=self.service)

    def test_disabled_service_does_not_create_job_store(self):
        folder = Path(self.temp.name) / 'disabled-service'
        service = HostedSourceService(folder, self.accounts, self.workspace, self.parser,
                                      self.reviews, self.publisher)
        self.addCleanup(service.close)
        self.assertFalse(folder.exists())
        with self.assertRaises(LookupError):
            service.source(self.parent, 'unavailable')

    def test_request_key_cannot_be_rebound_to_another_source(self):
        self.parsed()
        _, other = self.upload('Another fixture PDF')
        self.grant(other['source_id'])
        before = len(self.calls)
        status, _ = self.call('/api/accounts/hosted-sources/' + other['source_id'] + '/parse',
                              {'request_key': 'parse1'})
        self.assertEqual(status, 400)
        self.assertEqual(len(self.calls), before)

    def test_cross_origin_write_is_rejected(self):
        req = Request(self.base + '/api/accounts/hosted-sources/fake/parse', method='POST', data=b'{}',
            headers={'Content-Type': 'application/json', 'X-Bala-CSRF': self.csrf, 'Origin': 'https://evil.invalid'})
        with self.assertRaises(HTTPError) as error:
            self.client.open(req)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_resume_rejects_policy_or_owner_overrides(self):
        self.parse_state = 'RUNNING'
        _, job, _ = self.parsed()
        self.assertEqual(self.call('/api/accounts/hosted-sources/jobs/' + job + '/resume',
                                  {'owner_id': self.admin})[0], 400)

    def test_queue_limit_does_not_start_another_job(self):
        self.service.queue_limit = 1
        _, first = self.upload()
        self.grant(first['source_id'])
        _, second = self.upload('Second queued fixture')
        self.grant(second['source_id'])
        entered, release = threading.Event(), threading.Event()
        original = self.parser.poll
        def blocked(owner, key):
            entered.set()
            release.wait(5)
            return original(owner, key)
        with patch.object(self.parser, 'poll', side_effect=blocked):
            _, task = self.call('/api/accounts/hosted-sources/' + first['source_id'] + '/parse',
                                {'request_key': 'first'})
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.call('/api/accounts/hosted-sources/' + second['source_id'] + '/parse',
                                          {'request_key': 'second'})[0], 409)
            finally:
                release.set()
            self.assertEqual(self.wait(task['job_id'])['state'], 'completed')

    def test_invalid_pdf_media_type_is_rejected(self):
        req = Request(self.base + '/api/accounts/hosted-sources/upload', method='POST', data=b'not-a-pdf',
            headers={'Content-Type': 'text/plain', 'X-Bala-CSRF': self.csrf})
        with self.assertRaises(HTTPError) as error:
            self.client.open(req)
        self.assertEqual(error.exception.code, 415)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
