"""Index v2 protocol fixtures; not live cloud or real retrieval quality evidence."""
import json
import tempfile
import unittest

import httpx
from pydantic import ValidationError

import tests.test_hosted_source_review as fixtures
from backend.retrieval.llamacloud import (
    HostedPublications, IndexPublicationPlan, LlamaCloudPublisher,
    PublicationBlocked, PublicationOutcomeUnknown)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.source = fixtures.HostedSourceReviewTests()
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        extraction, _, _ = self.source.capture()
        self.review = self.source.registry.qualify('reviewer1', 'owner1', self.source.command(extraction))
        self.manifest = self.source.registry.eligible('owner1', extraction)
        self.plan = IndexPublicationPlan(manifest=self.manifest, parse_config_id='parse-config-fixture',
                                         usage_authorization_id='fixture-index-usage')
        self.store = HostedPublications(self.source.folder / 'publication')
        self.calls, self.metadata = [], None
        self.remote_state, self.synced = 'ready', '2026-11-05T10:00:00Z'
        self.read_content = self.manifest.passages[0].text
        self.extra_file, self.has_more, self.wrong_metadata = False, False, False
        self.permitted = True
        self.publisher = LlamaCloudPublisher(self.source.settings, self.store, self.source.registry,
            transport=httpx.MockTransport(self.handler), authorize=lambda *args: self.permitted)

    def handler(self, request):
        self.calls.append(request)
        self.assertEqual(request.url.host, 'api.cloud.llamaindex.ai')
        self.assertEqual(request.url.params['project_id'], 'project1')
        path = request.url.path
        if path == '/api/v1/beta/directories':
            return httpx.Response(200, json={'id': 'dir1', 'project_id': 'project1'})
        if path == '/api/v1/beta/files':
            self.assertIn(self.read_content.encode(), request.content)
            self.assertNotIn(b'%PDF-', request.content)
            return httpx.Response(200, json={'id': 'file1', 'project_id': 'project1'})
        if path == '/api/v1/beta/directories/dir1/files' and request.method == 'POST':
            payload = json.loads(request.content)
            self.metadata = payload['metadata']
            return httpx.Response(200, json={'id': 'entry1', 'file_id': 'file1',
                'directory_id': 'dir1', 'project_id': 'project1'})
        if path == '/api/v1/indexes':
            payload = json.loads(request.content)
            self.assertEqual(payload['sync_frequency'], 'manual')
            self.assertEqual(payload['products'][0]['product_config_id'], 'parse-config-fixture')
            return httpx.Response(200, json={'id': 'index1', 'source_directory_id': 'dir1',
                                            'project_id': 'project1'})
        if path == '/api/v1/indexes/index1':
            return httpx.Response(200, json=dict(id='index1', source_directory_id='dir1',
                output_directory_id='out1', export_config_id='export1', project_id='project1',
                sync_in_progress=self.remote_state != 'ready', metadata={'status': self.remote_state},
                last_synced_at=self.synced, last_exported_at=self.synced))
        if path.endswith('/files') and request.method == 'GET':
            source = '/dir1/' in path
            metadata = dict(self.metadata)
            if self.wrong_metadata:
                metadata['text_hash'] = 'a' * 64
            files = [dict(id='entry1' if source else 'parsed1', directory_id='dir1' if source else 'out1',
                          file_id='file1', project_id='project1', metadata=metadata)]
            if self.extra_file:
                files.append(dict(id='unreviewed', directory_id='dir1', project_id='project1', metadata={}))
            return httpx.Response(200, json={'data': files, 'has_more': self.has_more, 'next_cursor': None})
        if path == '/api/v1/retrieval/files/read':
            payload = json.loads(request.content)
            self.assertEqual(payload['file_id'], 'parsed1')
            self.assertEqual(payload['max_length'], len(self.manifest.passages[0].text) + 1)
            return httpx.Response(200, json={'content': self.read_content})
        raise AssertionError('Unexpected fixture route: ' + path)

    def test_staging_readback_activation_preserves_original_references(self):
        key = self.publisher.stage('owner1', self.plan)
        self.assertEqual(self.publisher.status('owner1', key)['state'], 'indexing')
        with self.assertRaises(PublicationBlocked):
            self.publisher.active('owner1', key)
        self.assertEqual(self.publisher.check('owner1', key)['state'], 'verified')
        self.publisher.activate('owner1', key)
        active = self.publisher.active('owner1', key)
        self.assertEqual(active['parsed_files'], {self.manifest.passages[0].reference.chunk_id: 'parsed1'})
        self.assertEqual(active['manifest_hash'], self.manifest.manifest_hash)
        self.assertFalse(self.publisher.status('owner1', key)['story_retrieval_connected'])
        self.assertEqual(len(self.calls), 8)

    def test_default_permission_and_live_transport_are_disabled(self):
        denied = LlamaCloudPublisher(self.source.settings, self.store, self.source.registry,
                                     transport=httpx.MockTransport(self.handler))
        with self.assertRaises(PermissionError):
            denied.stage('owner1', self.plan)
        live = LlamaCloudPublisher(self.source.settings, self.store, self.source.registry,
                                   authorize=lambda *args: True)
        with self.assertRaises(PermissionError):
            live.stage('owner1', self.plan)
        self.assertEqual(self.calls, [])

    def test_wrong_owner_and_path_key_cannot_activate(self):
        with self.assertRaises(PermissionError):
            self.publisher.stage('other', self.plan)
        with self.assertRaises(PermissionError):
            self.publisher.activate('owner1', '../escape')
        self.assertFalse((self.source.folder / 'escape.lock').exists())

    def test_unverified_publication_cannot_activate(self):
        key = self.publisher.stage('owner1', self.plan)
        with self.assertRaises(PublicationBlocked):
            self.publisher.activate('owner1', key)

    def test_repeated_stage_and_restart_do_not_duplicate_resources(self):
        key = self.publisher.stage('owner1', self.plan)
        reopened = LlamaCloudPublisher(self.source.settings, HostedPublications(self.store.folder),
            self.source.registry, transport=httpx.MockTransport(self.handler), authorize=lambda *args: True)
        self.assertEqual(reopened.stage('owner1', self.plan), key)
        self.assertEqual(len(self.calls), 4)

    def test_submission_timeout_never_blindly_resubmits(self):
        def timeout(request):
            self.calls.append(request)
            raise httpx.ReadTimeout('fixture-secret must not leak')
        self.publisher.transport = httpx.MockTransport(timeout)
        for _ in range(2):
            with self.assertRaises(PublicationOutcomeUnknown) as error:
                self.publisher.stage('owner1', self.plan)
            self.assertNotIn('fixture-secret', str(error.exception))
        self.assertEqual(len(self.calls), 1)

    def test_partial_upload_failure_does_not_create_index(self):
        original = self.handler
        def handler(request):
            if request.url.path.endswith('/files'):
                self.calls.append(request)
                raise httpx.ReadTimeout('timeout')
            return original(request)
        self.publisher.transport = httpx.MockTransport(handler)
        with self.assertRaises(PublicationOutcomeUnknown):
            self.publisher.stage('owner1', self.plan)
        self.assertEqual(len(self.calls), 2)

    def test_readback_mismatch_keeps_publication_blocked(self):
        key = self.publisher.stage('owner1', self.plan)
        self.read_content = 'Rewritten unsupported text'
        state = self.publisher.check('owner1', key)
        self.assertEqual(state['state'], 'blocked')
        with self.assertRaises(PublicationBlocked):
            self.publisher.activate('owner1', key)

    def test_extra_unreviewed_file_incomplete_listing_or_metadata_blocks(self):
        for attribute in ('extra_file', 'has_more', 'wrong_metadata'):
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as folder:
                publisher = LlamaCloudPublisher(self.source.settings, HostedPublications(folder),
                    self.source.registry, transport=httpx.MockTransport(self.handler), authorize=lambda *args: True)
                key = publisher.stage('owner1', self.plan)
                setattr(self, attribute, True)
                self.assertEqual(publisher.check('owner1', key)['state'], 'blocked')
                setattr(self, attribute, False)

    def test_revocation_blocks_publication_before_upload(self):
        self.source.registry.withdraw_passage('reviewer1', 'owner1', self.review)
        with self.assertRaises(PermissionError):
            self.publisher.stage('owner1', self.plan)
        self.assertEqual(self.calls, [])

    def test_revocation_after_activation_blocks_use(self):
        key = self.publisher.stage('owner1', self.plan)
        self.publisher.check('owner1', key)
        self.publisher.activate('owner1', key)
        self.source.registry.withdraw_passage('reviewer1', 'owner1', self.review)
        with self.assertRaises(PermissionError):
            self.publisher.active('owner1', key)

    def test_usage_permission_withdrawn_between_operations_stops_next_post(self):
        original = self.handler
        def handler(request):
            value = original(request)
            self.permitted = False
            return value
        self.publisher.transport = httpx.MockTransport(handler)
        with self.assertRaises(PermissionError):
            self.publisher.stage('owner1', self.plan)
        self.assertEqual(len(self.calls), 1)
        self.permitted = True
        self.publisher.transport = httpx.MockTransport(self.handler)
        self.publisher.stage('owner1', self.plan)
        self.assertEqual(len(self.calls), 4)

    def test_build_pending_and_failed_are_not_ready(self):
        key = self.publisher.stage('owner1', self.plan)
        self.remote_state = 'pending'
        self.assertEqual(self.publisher.check('owner1', key)['state'], 'indexing')
        self.remote_state = 'failed'
        self.assertEqual(self.publisher.check('owner1', key)['state'], 'failed')

    def test_remote_version_change_invalidates_previous_activation(self):
        key = self.publisher.stage('owner1', self.plan)
        self.publisher.check('owner1', key)
        self.publisher.activate('owner1', key)
        self.synced = '2026-11-05T11:00:00Z'
        self.assertEqual(self.publisher.check('owner1', key)['state'], 'blocked')
        with self.assertRaises(PublicationBlocked):
            self.publisher.active('owner1', key)

    def test_saved_readback_record_cannot_change_silently(self):
        key = self.publisher.stage('owner1', self.plan)
        self.publisher.check('owner1', key)
        job = self.store.get('owner1', key)
        job['verification']['index_id'] = 'changed'
        self.store.save('owner1', key, job)
        with self.assertRaises(ValueError):
            self.publisher.activate('owner1', key)

    def test_check_failure_or_remote_sync_disables_active_version(self):
        for mode in ('syncing', 'timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                publisher = LlamaCloudPublisher(self.source.settings, HostedPublications(folder),
                    self.source.registry, transport=httpx.MockTransport(self.handler), authorize=lambda *args: True)
                key = publisher.stage('owner1', self.plan)
                publisher.check('owner1', key)
                publisher.activate('owner1', key)
                if mode == 'syncing':
                    self.remote_state = 'syncing'
                else:
                    publisher.transport = httpx.MockTransport(lambda request: httpx.Response(503))
                self.assertEqual(publisher.check('owner1', key)['state'], 'indexing')
                with self.assertRaises(PublicationBlocked):
                    publisher.active('owner1', key)
                self.remote_state = 'ready'

    def test_duplicate_passages_and_large_batch_rejected(self):
        manifest = self.manifest.model_copy(update={'passages': self.manifest.passages * 21, 'manifest_hash': None})
        with self.assertRaises(ValidationError):
            self.plan.model_copy(update={'manifest': manifest})
        manifest = self.manifest.model_copy(update={'passages': self.manifest.passages * 2, 'manifest_hash': None})
        with self.assertRaises(ValidationError):
            self.plan.model_copy(update={'manifest': manifest})

    def test_existing_active_pointer_survives_failed_candidate(self):
        key = self.publisher.stage('owner1', self.plan)
        self.publisher.check('owner1', key)
        self.publisher.activate('owner1', key)
        different = self.plan.model_copy(update={'parse_config_id': 'another-approved-config'})
        def timeout(request):
            raise httpx.ReadTimeout('uncertain new directory')
        self.publisher.transport = httpx.MockTransport(timeout)
        with self.assertRaises(PublicationOutcomeUnknown):
            self.publisher.stage('owner1', different)
        self.publisher.transport = httpx.MockTransport(self.handler)
        self.assertEqual(self.publisher.active('owner1', key)['index_id'], 'index1')

    def test_wrong_project_and_redirect_receipts_never_activate(self):
        for response in (httpx.Response(200, json={'id': 'dir1', 'project_id': 'other'}),
                         httpx.Response(302, headers={'location': 'https://evil.invalid'})):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                calls = []
                def handler(request):
                    calls.append(request)
                    return response
                publisher = LlamaCloudPublisher(self.source.settings, HostedPublications(folder),
                    self.source.registry, transport=httpx.MockTransport(handler), authorize=lambda *args: True)
                with self.assertRaises(PublicationOutcomeUnknown):
                    publisher.stage('owner1', self.plan)
                self.assertEqual(len(calls), 1)

    def test_second_worker_cannot_publish_same_manifest(self):
        original = self.handler
        def handler(request):
            if request.url.path == '/api/v1/beta/directories':
                with self.assertRaises(RuntimeError):
                    self.publisher.stage('owner1', self.plan)
            return original(request)
        self.publisher.transport = httpx.MockTransport(handler)
        self.publisher.stage('owner1', self.plan)
        self.assertEqual(len(self.calls), 4)

    def test_check_limit_is_bounded_and_no_new_resources_created(self):
        publisher = LlamaCloudPublisher(self.source.settings, self.store, self.source.registry,
            transport=httpx.MockTransport(self.handler), authorize=lambda *args: True, max_checks=1)
        key = publisher.stage('owner1', self.plan)
        self.remote_state = 'working'
        publisher.check('owner1', key)
        with self.assertRaises(PublicationBlocked):
            publisher.check('owner1', key)
        self.assertEqual(len(self.calls), 5)


if __name__ == '__main__':
    unittest.main()
