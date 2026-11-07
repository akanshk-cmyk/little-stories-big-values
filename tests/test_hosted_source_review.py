"""Synthetic qualification records, never actual cultural approval or paid calls."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr, ValidationError
from pypdf import PdfWriter

from backend.contracts.common import text_hash
from backend.ingestion.hosted_review import (
    HostedSourceReview, PageSpan, PassageQualification, ProcessingPermission)
from backend.ingestion.llamaparse import LlamaParseAdapter, ParseJobs, ParseRequest, text_hash_bytes
from backend.ingestion.workspace import SourceWorkspace
from backend.retrieval.hosted_config import HostedSettings


class HostedSourceReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.workspace = SourceWorkspace(self.folder / 'sources')
        self.addCleanup(self.workspace.close)
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buf = BytesIO()
        writer.write(buf)
        self.pdf = buf.getvalue()
        self.source = self.workspace.create(BytesIO(self.pdf), len(self.pdf), owner='owner1',
                                            title='Synthetic fixture', edition='Fixture edition')
        summary = self.workspace.summary(self.source, 'owner1')
        self.spec = ParseRequest(owner_id='owner1', source_id=self.source, snapshot_id=summary['snapshot_id'],
            original_file_hash=text_hash_bytes(self.pdf), expected_page_count=1,
            tier='cost_effective', parser_version='2026-09-28')
        self.settings = HostedSettings(enabled=True, api_key=SecretStr('fixture'),
            organization_id='org1', project_id='project1', api_generation='v2')
        self.actions = []
        self.principals = {'reviewer1'}
        def manage(actor, action, owner, source):
            self.actions.append((actor, action, owner, source))
            return actor in self.principals and owner == 'owner1' and source == self.source
        self.manage = manage
        self.registry = HostedSourceReview(self.folder / 'reviews', self.workspace, self.settings,
            can_manage=manage, usage_allowed=lambda permission, destination:
                permission.usage_authorization_id == 'fixture-usage1')
        self.calls = []
        self.text = 'Synthetic child shares a paper flower.'
        def handler(request):
            self.calls.append(request)
            if request.url.path.endswith('/files'):
                return httpx.Response(200, json={'id': 'file1', 'project_id': 'project1'})
            if request.method == 'POST':
                return httpx.Response(200, json={'id': 'job1', 'status': 'PENDING'})
            return httpx.Response(200, json={'job': {'id': 'job1', 'status': 'COMPLETED'},
                'markdown': {'pages': [{'page_number': 1, 'success': True, 'markdown': self.text}]}})
        self.adapter = LlamaParseAdapter(self.settings, ParseJobs(self.folder / 'jobs'),
            authorize=self.registry.authorize_parse, transport=httpx.MockTransport(handler))
        guard = patch('socket.socket', side_effect=AssertionError('Live socket forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def permission(self, **updates):
        return ProcessingPermission(**(dict(request=self.spec, rights_basis='Synthetic cloud-processing permission.',
            usage_authorization_id='fixture-usage1', max_pages=1) | updates))

    def capture(self):
        grant = self.registry.grant_processing('reviewer1', self.permission())
        job = self.adapter.start(self.spec, self.pdf)
        self.adapter.poll('owner1', job)
        extraction = self.registry.capture('owner1', self.adapter, job)
        return extraction, grant, job

    def command(self, extraction, **updates):
        return PassageQualification(**(dict(extraction_id=extraction,
            spans=(PageSpan(pdf_page=1, start=0, end=len(self.text)),),
            expected_text_hash=text_hash(self.text), scope='traditional_claims',
            extraction_checked=True, boundaries_checked=True, edition_verified=True,
            cultural_context_checked=True, child_suitability_checked=True,
            rights_permitted=True, index_text_permitted=True,
            rights_basis='Synthetic rights fixture only.', edition_reference='Synthetic edition record.',
            cultural_review_basis='Synthetic reviewer evidence, not actual tradition.',
            notes='Fixture only; no real cultural certification.') | updates))

    def test_processing_requires_explicit_scoped_grant(self):
        with self.assertRaises(PermissionError):
            self.adapter.start(self.spec, self.pdf)
        self.assertEqual(self.calls, [])
        self.registry.grant_processing('reviewer1', self.permission())
        self.adapter.start(self.spec, self.pdf)
        self.assertEqual(len(self.calls), 2)

    def test_page_allowance_and_parser_change_not_implicitly_permitted(self):
        with self.assertRaises(ValidationError):
            self.permission(request=self.spec.model_copy(update={'expected_page_count': 2}))
        self.registry.grant_processing('reviewer1', self.permission())
        changed = self.spec.model_copy(update={'parser_version': 'another-version'})
        self.assertFalse(self.registry.authorize_parse(changed, 'submit'))
        self.assertFalse(self.registry.authorize_parse(self.spec, 'unknown_operation'))

    def test_parent_cannot_grant_or_qualify(self):
        with self.assertRaises(PermissionError):
            self.registry.grant_processing('owner1', self.permission())
        extraction, _, _ = self.capture()
        with self.assertRaises(PermissionError):
            self.registry.qualify('owner1', 'owner1', self.command(extraction))

    def test_default_authority_denies(self):
        registry = HostedSourceReview(self.folder / 'denied', self.workspace, self.settings)
        with self.assertRaises(PermissionError):
            registry.grant_processing('reviewer1', self.permission())

    def test_reviewer_role_does_not_grant_spending_permission(self):
        registry = HostedSourceReview(self.folder / 'no-usage', self.workspace, self.settings,
                                       can_manage=self.manage)
        with self.assertRaises(PermissionError):
            registry.grant_processing('reviewer1', self.permission())
        with self.assertRaises(PermissionError):
            self.registry.grant_processing('reviewer1',
                self.permission(usage_authorization_id='not-approved'))

    def test_usage_allowance_withdrawal_stops_outgoing_calls(self):
        self.registry.grant_processing('reviewer1', self.permission())
        self.registry.usage_allowed = lambda *args: False
        with self.assertRaises(PermissionError):
            self.adapter.start(self.spec, self.pdf)
        self.assertEqual(self.calls, [])

    def test_grant_withdrawal_stops_poll_and_capture(self):
        grant = self.registry.grant_processing('reviewer1', self.permission())
        job = self.adapter.start(self.spec, self.pdf)
        self.registry.withdraw_processing('reviewer1', 'owner1', grant)
        with self.assertRaises(PermissionError):
            self.adapter.poll('owner1', job)
        self.assertEqual(len(self.calls), 2)

    def test_revoked_reviewer_role_stops_processing(self):
        self.registry.grant_processing('reviewer1', self.permission())
        self.principals.clear()
        with self.assertRaises(PermissionError):
            self.adapter.start(self.spec, self.pdf)
        self.assertEqual(self.calls, [])

    def test_wrong_source_or_owner_denied(self):
        for spec in (self.spec.model_copy(update={'owner_id': 'other'}),
                     self.spec.model_copy(update={'snapshot_id': 'stale'}),
                     self.spec.model_copy(update={'original_file_hash': 'a' * 64})):
            with self.subTest(spec=spec), self.assertRaises(PermissionError):
                self.registry.grant_processing('reviewer1', self.permission(request=spec))

    def test_capture_preserves_workspace_and_is_idempotent(self):
        before = self.workspace.summary(self.source, 'owner1')
        extraction, _, job = self.capture()
        self.assertEqual(self.registry.capture('owner1', self.adapter, job), extraction)
        self.assertEqual(self.workspace.summary(self.source, 'owner1'), before)
        self.assertFalse(before['traditional_claims_eligible'])
        with self.assertRaises(PermissionError):
            self.registry.eligible('owner1', extraction)

    def test_capture_requires_completed_owned_permitted_job(self):
        grant = self.registry.grant_processing('reviewer1', self.permission())
        job = self.adapter.start(self.spec, self.pdf)
        with self.assertRaises(RuntimeError):
            self.registry.capture('owner1', self.adapter, job)
        self.adapter.poll('owner1', job)
        with self.assertRaises(PermissionError):
            self.registry.capture('other', self.adapter, job)
        self.registry.withdraw_processing('reviewer1', 'owner1', grant)
        with self.assertRaises(PermissionError):
            self.registry.capture('owner1', self.adapter, job)

    def test_qualified_passage_preserves_exact_text_and_provenance(self):
        extraction, _, _ = self.capture()
        review = self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        passage = manifest.passages[0]
        self.assertEqual(passage.text, self.text)
        self.assertEqual(passage.reference.pdf_pages, (1,))
        self.assertEqual(passage.reference.review_record_ids, ('review-' + review,))
        self.assertNotEqual(manifest.snapshot_id, self.spec.snapshot_id)
        self.assertEqual(passage.spans[0].end, len(self.text))
        self.assertTrue(self.registry.assert_current(manifest))

    def test_parent_inspiration_never_upgraded_to_traditional(self):
        extraction, _, _ = self.capture()
        self.registry.qualify('reviewer1', 'owner1', self.command(extraction, scope='parent_inspiration'))
        self.assertEqual(self.registry.eligible('owner1', extraction, purpose='parent_inspiration').purpose,
                         'parent_inspiration')
        with self.assertRaises(PermissionError):
            self.registry.eligible('owner1', extraction, purpose='traditional_claims')

    def test_every_required_check_is_enforced(self):
        extraction, _, _ = self.capture()
        for field in ('extraction_checked', 'boundaries_checked', 'edition_verified',
                      'cultural_context_checked', 'child_suitability_checked',
                      'rights_permitted', 'index_text_permitted'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.registry.qualify('reviewer1', 'owner1', self.command(extraction, **{field: False}))

    def test_wrong_hash_page_range_and_overlapping_spans_rejected(self):
        extraction, _, _ = self.capture()
        changes = [dict(expected_text_hash='a' * 64),
                   dict(spans=(PageSpan(pdf_page=2, start=0, end=1),)),
                   dict(spans=(PageSpan(pdf_page=1, start=0, end=999),)),
                   dict(spans=(PageSpan(pdf_page=1, start=0, end=5),
                               PageSpan(pdf_page=1, start=1, end=6)))]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.registry.qualify('reviewer1', 'owner1', self.command(extraction, **change))

    def test_withdrawal_invalidates_frozen_manifest(self):
        extraction, _, _ = self.capture()
        review = self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        self.registry.withdraw_passage('reviewer1', 'owner1', review)
        with self.assertRaises(PermissionError):
            self.registry.assert_current(manifest)

    def test_qualification_change_invalidates_prior_epoch(self):
        extraction, _, _ = self.capture()
        command = self.command(extraction)
        self.registry.qualify('reviewer1', 'owner1', command)
        manifest = self.registry.eligible('owner1', extraction)
        self.registry.qualify('reviewer1', 'owner1', command)
        with self.assertRaises(PermissionError):
            self.registry.assert_current(manifest)

    def test_revoked_qualification_authority_blocks_saved_manifest(self):
        extraction, _, _ = self.capture()
        self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        self.principals.clear()
        with self.assertRaises(PermissionError):
            self.registry.assert_current(manifest)

    def test_source_epoch_change_blocks(self):
        extraction, _, _ = self.capture()
        self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        with self.workspace.db() as db:
            db.execute('UPDATE documents SET epoch=epoch+1 WHERE id=?', (self.source,))
        with self.assertRaises(PermissionError):
            self.registry.assert_current(manifest)

    def test_original_integrity_change_blocks(self):
        extraction, _, _ = self.capture()
        self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        self.workspace.original(self.source, 'owner1').write_bytes(b'%PDF-modified')
        with self.assertRaises(ValueError):
            self.registry.assert_current(manifest)

    def test_wrong_owner_manifest_and_path_ids_denied(self):
        extraction, _, _ = self.capture()
        with self.assertRaises(PermissionError):
            self.registry.eligible('other', extraction)
        for key in ('../outside', 'not-a-hash'):
            with self.assertRaises(ValueError):
                self.registry.eligible('owner1', key)
        self.assertFalse((self.folder / 'outside.lock').exists())

    def test_extraction_tampering_blocked(self):
        extraction, _, _ = self.capture()
        with self.registry.db() as db:
            row = db.execute('SELECT payload FROM extractions WHERE id=?', (extraction,)).fetchone()
            payload = json.loads(row['payload'])
            payload['document']['pages'][0]['text'] = 'Tampered'
            db.execute('UPDATE extractions SET payload=? WHERE id=?', (json.dumps(payload), extraction))
        with self.assertRaises(ValueError):
            self.registry.eligible('owner1', extraction)

    def test_destination_change_blocks_saved_manifest(self):
        extraction, _, _ = self.capture()
        self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        changed = HostedSettings(organization_id='org1', project_id='different', api_generation='v2')
        reopened = HostedSourceReview(self.folder / 'reviews', self.workspace, changed,
            can_manage=self.manage, usage_allowed=lambda *args: True)
        with self.assertRaises(PermissionError):
            reopened.assert_current(manifest)

    def test_restart_preserves_review_and_withdrawal(self):
        extraction, _, _ = self.capture()
        review = self.registry.qualify('reviewer1', 'owner1', self.command(extraction))
        manifest = self.registry.eligible('owner1', extraction)
        reopened = HostedSourceReview(self.folder / 'reviews', self.workspace, self.settings,
            can_manage=self.manage, usage_allowed=lambda *args: True)
        self.assertTrue(reopened.assert_current(manifest))
        reopened.withdraw_passage('reviewer1', 'owner1', review)
        with self.assertRaises(PermissionError):
            self.registry.assert_current(manifest)


if __name__ == '__main__':
    unittest.main()
