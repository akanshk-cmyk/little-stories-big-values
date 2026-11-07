"""Synthetic PDF and HTTP fixtures only; no live service or held dataset."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr, ValidationError
from pypdf import PdfWriter

from backend.ingestion.llamaparse import (
    LlamaParseAdapter, ParseBlocked, ParseJobs, ParseOutcomeUnknown, ParseRequest, text_hash_bytes)
from backend.retrieval.hosted_config import HostedSettings


class LlamaParseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.jobs = ParseJobs(self.folder)
        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.write(output)
        self.pdf = output.getvalue()
        self.spec = ParseRequest(owner_id='owner1', source_id='source1', snapshot_id='snap1',
            original_file_hash=text_hash_bytes(self.pdf), expected_page_count=1,
            tier='cost_effective', parser_version='2026-09-28')
        self.settings = HostedSettings(enabled=True, api_key=SecretStr('fixture-secret'),
            organization_id='org1', project_id='project1', api_generation='v2')
        self.calls = []
        self.result = dict(job={'id': 'job1', 'status': 'COMPLETED'},
                           markdown={'pages': [{'page_number': 1, 'success': True, 'markdown': ''}]})
        guard = patch('socket.socket', side_effect=AssertionError('Live socket forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def handler(self, request):
        self.calls.append(request)
        if request.url.path == '/api/v1/beta/files':
            self.assertIn(b'name="purpose"', request.content)
            self.assertIn(self.pdf, request.content)
            self.assertNotIn('fixture-secret', request.url.path)
            return httpx.Response(200, json={'id': 'file1', 'project_id': 'project1'})
        if request.method == 'POST':
            self.assertEqual(json.loads(request.content),
                             {'file_id': 'file1', 'tier': 'cost_effective', 'version': '2026-09-28'})
            return httpx.Response(200, json={'id': 'job1', 'status': 'PENDING'})
        return httpx.Response(200, json=self.result)

    def adapter(self, handler=None, **kwargs):
        kwargs.setdefault('authorize', lambda spec, operation: True)
        kwargs.setdefault('transport', httpx.MockTransport(handler or self.handler))
        return LlamaParseAdapter(self.settings, self.jobs, **kwargs)

    def test_success_is_extracted_not_approved_or_indexed(self):
        adapter = self.adapter()
        key = adapter.start(self.spec, self.pdf)
        self.assertEqual(adapter.status('owner1', key)['state'], 'pending')
        state = adapter.poll('owner1', key)
        self.assertEqual(state['state'], 'extracted')
        self.assertFalse(state['eligible_for_story'])
        self.assertFalse(state['indexing_started'])
        self.assertEqual(adapter.document('owner1', key).pages[0].text, '')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[0].url.params['project_id'], 'project1')
        payload = self.jobs.get('owner1', key)
        self.assertEqual(len(payload['attempts']), 3)
        self.assertNotIn('fixture-secret', json.dumps(payload))

    def test_duplicate_submit_and_reopen_do_not_repeat_posts(self):
        key = self.adapter().start(self.spec, self.pdf)
        reopened = LlamaParseAdapter(self.settings, ParseJobs(self.folder),
            transport=httpx.MockTransport(self.handler), authorize=lambda *args: True)
        self.assertEqual(reopened.start(self.spec, self.pdf), key)
        self.assertEqual(len(self.calls), 2)
        reopened.poll('owner1', key)
        self.assertEqual(reopened.start(self.spec, self.pdf), key)
        self.assertEqual(len(self.calls), 3)

    def test_disabled_and_default_denial_send_nothing(self):
        for adapter in (LlamaParseAdapter(self.settings, self.jobs, transport=httpx.MockTransport(self.handler)),
                        self.adapter(authorize=lambda *args: False),
                        LlamaParseAdapter(HostedSettings(), self.jobs)):
            with self.assertRaises(PermissionError):
                adapter.start(self.spec, self.pdf)
        self.assertEqual(self.calls, [])

    def test_live_transport_is_blocked_without_authorization(self):
        with self.assertRaises(PermissionError):
            LlamaParseAdapter(self.settings, self.jobs, transport=httpx.HTTPTransport())
        adapter = LlamaParseAdapter(self.settings, self.jobs, authorize=lambda *args: True)
        with self.assertRaises(PermissionError):
            adapter.start(self.spec, self.pdf)

    def test_bad_pdf_hash_count_and_encryption_send_nothing(self):
        for spec, pdf in ((self.spec, b'%PDF-invalid'),
                          (self.spec.model_copy(update={'expected_page_count': 2}), self.pdf)):
            with self.assertRaises(ValueError):
                self.adapter().start(spec, pdf)
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.encrypt('password')
        buf = BytesIO()
        writer.write(buf)
        pdf = buf.getvalue()
        with self.assertRaises(ValueError):
            self.adapter().start(self.spec.model_copy(update={'original_file_hash': text_hash_bytes(pdf)}), pdf)
        self.assertFalse(self.calls)

    def test_unpinned_parser_rejected(self):
        with self.assertRaises(ValidationError):
            self.spec.model_copy(update={'parser_version': 'latest'})

    def test_upload_timeout_never_resubmits(self):
        def timeout(request):
            self.calls.append(request)
            raise httpx.ReadTimeout('fixture-secret should never leak')
        adapter = self.adapter(timeout)
        for _ in range(2):
            with self.assertRaises(ParseOutcomeUnknown) as caught:
                adapter.start(self.spec, self.pdf)
            self.assertNotIn('fixture-secret', str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    def test_parse_timeout_preserves_uploaded_file(self):
        def handler(request):
            if request.url.path == '/api/v2/parse':
                self.calls.append(request)
                raise httpx.ReadTimeout('timeout')
            return self.handler(request)
        adapter = self.adapter(handler)
        with self.assertRaises(ParseOutcomeUnknown):
            adapter.start(self.spec, self.pdf)
        key = adapter._key(self.spec)
        self.assertEqual(self.jobs.get('owner1', key)['file_id'], 'file1')
        with self.assertRaises(ParseOutcomeUnknown):
            adapter.start(self.spec, self.pdf)
        self.assertEqual(len(self.calls), 2)

    def test_crash_after_post_intent_does_not_resubmit(self):
        adapter = self.adapter()
        key = adapter._key(self.spec)
        record = self.jobs.create(key, self.spec, adapter._destination())
        record['state'] = 'upload_sending'
        self.jobs.save('owner1', key, record)
        self.assertEqual(adapter.status('owner1', key)['state'], 'outcome_unknown')
        with self.assertRaises(ParseOutcomeUnknown):
            adapter.start(self.spec, self.pdf)
        self.assertEqual(self.calls, [])

    def test_concurrent_worker_cannot_submit_same_job(self):
        adapter = self.adapter()
        def handler(request):
            if request.url.path == '/api/v1/beta/files':
                with self.assertRaises(RuntimeError):
                    adapter.start(self.spec, self.pdf)
            return self.handler(request)
        adapter.transport = httpx.MockTransport(handler)
        adapter.start(self.spec, self.pdf)
        self.assertEqual(len(self.calls), 2)

    def test_revocation_between_upload_and_parse_blocks_second_post(self):
        permitted = [True]
        def handler(request):
            response = self.handler(request)
            permitted[0] = False
            return response
        adapter = self.adapter(handler, authorize=lambda *args: permitted[0])
        with self.assertRaises(PermissionError):
            adapter.start(self.spec, self.pdf)
        self.assertEqual(len(self.calls), 1)
        permitted[0] = True
        adapter.transport = httpx.MockTransport(self.handler)
        adapter.start(self.spec, self.pdf)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1].url.path, '/api/v2/parse')

    def test_wrong_project_or_redirect_not_followed(self):
        for response in (httpx.Response(200, json={'id': 'file1', 'project_id': 'other'}),
                         httpx.Response(302, headers={'location': 'https://evil.invalid'})):
            with self.subTest(response=response):
                with tempfile.TemporaryDirectory() as folder:
                    calls = []
                    def handler(request):
                        calls.append(request)
                        return response
                    adapter = LlamaParseAdapter(self.settings, ParseJobs(folder),
                        transport=httpx.MockTransport(handler), authorize=lambda *args: True)
                    with self.assertRaises(ParseOutcomeUnknown):
                        adapter.start(self.spec, self.pdf)
                    self.assertEqual(len(calls), 1)

    def test_wrong_owner_cannot_poll_read_or_view(self):
        adapter = self.adapter()
        key = adapter.start(self.spec, self.pdf)
        for method in (adapter.poll, adapter.status, adapter.document):
            with self.assertRaises(PermissionError):
                method('other', key)
        self.assertEqual(len(self.calls), 2)

    def test_permission_withdrawal_prevents_poll(self):
        adapter = self.adapter()
        key = adapter.start(self.spec, self.pdf)
        adapter.authorize = lambda *args: False
        with self.assertRaises(PermissionError):
            adapter.poll('owner1', key)
        self.assertEqual(len(self.calls), 2)

    def test_destination_change_prevents_poll(self):
        key = self.adapter().start(self.spec, self.pdf)
        changed = HostedSettings(enabled=True, api_key=SecretStr('fixture'), api_generation='v2',
                                 organization_id='org1', project_id='other')
        adapter = LlamaParseAdapter(changed, self.jobs, transport=httpx.MockTransport(self.handler),
                                   authorize=lambda *args: True)
        with self.assertRaises(PermissionError):
            adapter.poll('owner1', key)
        self.assertEqual(len(self.calls), 2)

    def test_partial_failed_duplicate_or_mismatched_result_not_complete(self):
        invalid = [dict(job={'id': 'other', 'status': 'COMPLETED'}, markdown={'pages': []}),
                   dict(job={'id': 'job1', 'status': 'COMPLETED'}, markdown={'pages': []}),
                   dict(job={'id': 'job1', 'status': 'COMPLETED'},
                        markdown={'pages': [{'page_number': 1, 'success': False}]}),
                   dict(job={'id': 'job1', 'status': 'COMPLETED'},
                        markdown={'pages': [{'page_number': 1, 'success': True, 'markdown': 'x'},
                                            {'page_number': 1, 'success': True, 'markdown': 'x'}]}),
                   dict(job={'id': 'job1', 'status': 'NEW_UNKNOWN_STATUS'})]
        for result in invalid:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as folder:
                adapter = LlamaParseAdapter(self.settings, ParseJobs(folder),
                    transport=httpx.MockTransport(self.handler), authorize=lambda *args: True)
                self.result = result
                key = adapter.start(self.spec, self.pdf)
                self.assertEqual(adapter.poll('owner1', key)['state'], 'invalid_result')
                with self.assertRaises(ParseBlocked):
                    adapter.document('owner1', key)

    def test_transient_get_failure_retries_only_existing_job(self):
        fail = [True]
        def handler(request):
            if request.method == 'GET' and fail[0]:
                self.calls.append(request)
                fail[0] = False
                return httpx.Response(429, json={'message': 'private-provider-detail'})
            return self.handler(request)
        adapter = self.adapter(handler)
        key = adapter.start(self.spec, self.pdf)
        self.assertEqual(adapter.poll('owner1', key)['state'], 'pending')
        self.assertEqual(adapter.poll('owner1', key)['state'], 'extracted')
        self.assertEqual([c.method for c in self.calls], ['POST', 'POST', 'GET', 'GET'])

    def test_polling_limit_does_not_start_new_job(self):
        self.result = {'job': {'id': 'job1', 'status': 'RUNNING'}}
        adapter = self.adapter(max_polls=1)
        key = adapter.start(self.spec, self.pdf)
        self.assertEqual(adapter.poll('owner1', key)['state'], 'pending')
        with self.assertRaises(ParseBlocked):
            adapter.poll('owner1', key)
        self.assertEqual(len(self.calls), 3)

    def test_remote_failure_is_terminal_and_not_eligible(self):
        self.result = {'job': {'id': 'job1', 'status': 'FAILED'}}
        adapter = self.adapter()
        key = adapter.start(self.spec, self.pdf)
        self.assertEqual(adapter.poll('owner1', key)['state'], 'failed')
        adapter.start(self.spec, self.pdf)
        self.assertEqual(len(self.calls), 3)

    def test_malformed_receipt_and_untrusted_remote_id_stop(self):
        for payload in ({'id': '../escape', 'project_id': 'project1'},
                        {'id': 'file1'}, {'id': 123, 'project_id': 'project1'}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as folder:
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(200, json=payload)
                adapter = LlamaParseAdapter(self.settings, ParseJobs(folder),
                    transport=httpx.MockTransport(handler), authorize=lambda *args: True)
                with self.assertRaises(ParseOutcomeUnknown):
                    adapter.start(self.spec, self.pdf)
                self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
