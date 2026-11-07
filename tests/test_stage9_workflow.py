"""Synthetic provider -> actual asset registry -> account approval integration."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
from backend.accounts.assets import AssetRegistry
from backend.accounts.workflow import AccountAdapters,AccountWorkflowService
from backend import contracts as c
from backend.workflows.story_graph import PreparedContext
import test_stage8_content as fixture
from test_contracts import request


def picture(color='gold'):
    stream=BytesIO();Image.new('RGB',(100,100),color).save(stream,format='PNG');return stream.getvalue()


class ManagedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.accounts=Accounts(Store(self.temp.name))
        self.parent=self.accounts.register('parent1','Synthetic-password-123')['account_id']
        self.admin=self.accounts.bootstrap_admin('admin1','Synthetic-password-123')['account_id']
        self.child=self.accounts.add_child(self.parent,'Veyd',5)['child_id']
        self.ledger=Entitlements(self.accounts);self.registry=AssetRegistry(self.accounts)
        self.fixture=fixture.ContentGraphTests('test_writer_and_independent_reviewer_use_actual_saved_artifacts')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def workflow(self,evidence=False):
        self.fixture.bundle=evidence
        original=self.fixture.service()
        self.service=AccountWorkflowService(Path(self.temp.name)/'runs',
            AccountAdapters(original.adapters,self.accounts,self.registry),self.accounts,self.ledger,enabled=True)
        self.addCleanup(self.service.close)
        req=request(child_profile_id=self.child)
        self.run=self.service.start(self.parent,'request1',req.model_dump(mode='json'),generation_mode='provider_synthetic')
        state=self.service.execute(self.run,self.parent)
        self.assertEqual(state['status'],'assets_pending',state)
        self.draft=c.parse_payload(c.StoryDraft,self.service.repository.load(self.run,state['draft_id']))
        self.context=c.parse_payload(PreparedContext,self.service.repository.load(self.run,'context'))
        return state

    def asset(self,color='gold',approve=True):
        asset=self.registry.add(self.admin,picture(color),characters=('krishna','mira'),style='Synthetic fixture',description='A synthetic color tile, not actual story art.')['asset_id']
        if approve:self.registry.review(self.admin,asset,approve=True,rights_basis='Synthetic test tile created in this test.',gentle=True,notes='Fixture inspection, no cultural quality claim.')
        return asset

    def scenes(self,asset=None,pages=range(1,7)):
        asset=asset or self.asset()
        for page in pages:self.registry.inspect_scene(self.admin,self.draft,self.context.characters,page,asset,
            matches=True,notes='Synthetic scene-matching fixture, not actual visual-quality certification.')
        self.asset_id=asset
        return self.service.refresh_assets(self.run,self.parent)

    def test_new_images_are_pending_and_parent_cannot_approve(self):
        asset=self.asset(approve=False)
        with self.assertRaises(PermissionError):self.registry.approved(asset)
        with self.assertRaises(PermissionError):self.registry.review(self.parent,asset,approve=True,rights_basis='claim',gentle=True,notes='claim')

    def test_asset_approval_requires_rights_and_gentle_inspection(self):
        asset=self.asset(approve=False)
        with self.assertRaises(ValueError):self.registry.review(self.admin,asset,approve=True,rights_basis='',gentle=True,notes='No rights.')
        with self.assertRaises(ValueError):self.registry.review(self.admin,asset,approve=True,rights_basis='Fixture',gentle=False,notes='Not inspected.')

    def test_missing_scenes_never_grant_book_approval(self):
        self.workflow();state=self.scenes(pages=range(1,6))
        self.assertEqual(state['status'],'assets_pending')
        with self.assertRaises((ValueError,PermissionError)):self.service.approve(self.run,self.parent,'a'*64)
        balance=self.ledger.balance(self.parent)
        self.assertEqual(balance['starter_completed'],0)
        self.assertEqual(balance['starter_held'],1)

    def test_exact_book_review_approval_and_single_debit(self):
        self.workflow();state=self.scenes()
        self.assertEqual(state['status'],'awaiting_parent_approval',state)
        preview=self.service.preview(self.run,self.parent)
        self.assertEqual(preview.count('<img '),6);self.assertIn('Book version for parent review',preview)
        with self.assertRaises(ValueError):self.service.approve(self.run,self.parent,'b'*64)
        done=self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        self.assertEqual(done['status'],'complete',done)
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)
        self.assertEqual(self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])['status'],'complete')
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)
        output=self.service.approved_output(self.run,self.parent,state['book_version_hash'])
        self.assertEqual(output['result']['html'].count('<img '),6)
        self.assertIn('Parent-approved text; application-validated book version',output['result']['html'])
        self.assertEqual(len(self.fixture.calls),2)

    def test_other_parent_cannot_read_or_approve(self):
        self.workflow();state=self.scenes()
        other=self.accounts.register('parent2','Synthetic-password-123')['account_id']
        with self.assertRaises(PermissionError):self.service.approve_text(self.run,other,'a'*64)
        with self.assertRaises(PermissionError):self.service.preview(self.run,other)

    def test_scene_is_bound_to_exact_draft_and_character_design(self):
        self.workflow();self.scenes()
        manifest=self.registry.manifest(self.draft,self.context.characters)
        changed=self.draft.model_copy(update={'title':'A different draft','draft_hash':None})
        with self.assertRaises(PermissionError):self.registry.validate(changed,self.context.characters,manifest)
        changed_cast=self.context.characters.model_copy(update={'manifest_id':'new-cast','manifest_hash':None})
        with self.assertRaises(PermissionError):self.registry.validate(self.draft,changed_cast,manifest)

    def test_replacement_requires_new_scene_and_book_approval(self):
        self.workflow();state=self.scenes()
        self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        replacement=self.asset('blue')
        self.registry.inspect_scene(self.admin,self.draft,self.context.characters,1,replacement,matches=True,notes='Replacement synthetic scene.')
        with self.assertRaises(PermissionError):self.service.approved_output(self.run,self.parent,state['book_version_hash'])

    def test_replacement_before_approval_creates_new_book_version_and_rejects_stale_approval(self):
        self.workflow();before=self.scenes();replacement=self.asset('blue')
        self.registry.inspect_scene(self.admin,self.draft,self.context.characters,1,replacement,matches=True,notes='Reviewed replacement fixture.')
        after=self.service.refresh_assets(self.run,self.parent)
        self.assertEqual(after['status'],'awaiting_parent_approval',after)
        self.assertNotEqual(before['book_version_hash'],after['book_version_hash'])
        version=self.service.repository.load(self.run,'book-'+after['book_version_hash'])
        self.assertEqual(version['version'],2)
        with self.assertRaises(ValueError):self.service.approve(self.run,self.parent,before['book_version_hash'])
        self.assertEqual(self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])['status'],'complete')

    def test_parent_review_api_returns_exact_request_draft_and_sources_only_to_owner(self):
        from backend.accounts.http_api import AccountAPI
        self.workflow(evidence=True)
        api=AccountAPI(self.accounts,self.ledger,self.registry,self.service)
        payload,status=api.handle('GET',['api','accounts','runs',self.run,'review'],dict(account_id=self.parent,role='parent'))
        self.assertEqual(status,200);self.assertEqual(payload['draft']['draft_hash'],self.draft.draft_hash)
        self.assertEqual(payload['context']['evidence']['bundle_hash'],self.context.evidence.bundle_hash)
        other=self.accounts.register('other-parent','Synthetic-password-123')['account_id']
        with self.assertRaises(PermissionError):api.handle('GET',['api','accounts','runs',self.run,'review'],dict(account_id=other,role='parent'))

    def test_withdrawn_asset_blocks_approval_and_consumes_no_credit(self):
        self.workflow();state=self.scenes()
        self.registry.review(self.admin,self.asset_id,approve=False,rights_basis='Withdrawn',gentle=True,notes='Fixture withdrawal.')
        with self.assertRaises(PermissionError):self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],3)

    def test_tampered_image_bytes_are_not_exported(self):
        self.workflow();state=self.scenes()
        asset=self.registry.approved(self.asset_id)
        (self.registry.folder/asset['file']).write_bytes(picture('red'))
        with self.assertRaises(ValueError):self.service.preview(self.run,self.parent)

    def test_revoked_source_blocks_previously_approved_output(self):
        self.workflow(evidence=True);state=self.scenes()
        self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        self.fixture.eligible=False
        with self.assertRaises(PermissionError):self.service.approved_output(self.run,self.parent,state['book_version_hash'])

    def test_child_profile_ownership_is_enforced_before_generation(self):
        self.workflow()
        with self.assertRaises(PermissionError):self.service.start(self.parent,'bad',request().model_dump(mode='json'),generation_mode='provider_synthetic')

    def test_concurrent_approvals_complete_and_debit_at_most_once(self):
        self.workflow();state=self.scenes()
        def approve(_):
            try:return self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])['status']
            except RuntimeError:return 'busy'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(approve,range(2)))
        self.assertIn('complete',results)
        self.assertEqual(len(self.ledger.library(self.parent)),1)
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)

    def test_narration_authorization_is_bound_to_approved_script_and_voice_without_calls(self):
        self.workflow();state=self.scenes()
        with self.assertRaises(PermissionError):self.service.authorize_narration(self.run,self.parent,state['book_version_hash'],dict(voice_id='voice',model_id='model'))
        self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        authorization=self.service.authorize_narration(self.run,self.parent,state['book_version_hash'],dict(voice_id='voice',model_id='model'))
        self.assertEqual(authorization['script'][1:7],[p.text for p in self.draft.pages])
        self.assertEqual(authorization['status'],'authorized_not_generated')
        self.assertEqual(len(self.fixture.calls),2)

    def test_changed_voice_invalidates_previous_narration_authorization(self):
        self.workflow();state=self.scenes();book_hash=state['book_version_hash']
        self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        first=self.service.authorize_narration(self.run,self.parent,book_hash,dict(voice_id='one',model_id='model'))
        second=self.service.authorize_narration(self.run,self.parent,book_hash,dict(voice_id='two',model_id='model'))
        with self.assertRaises(PermissionError):self.service.narration_payload(self.run,self.parent,first['authorization_id'])
        self.assertEqual(self.service.narration_payload(self.run,self.parent,second['authorization_id'])['voice']['voice_id'],'two')

    def test_failed_preparation_releases_credit_and_does_not_add_to_library(self):
        self.workflow();state=self.scenes()
        with patch.object(self.service.adapters,'document',side_effect=RuntimeError('Synthetic rendering failure')):
            with self.assertRaises(RuntimeError):self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],3)
        self.assertEqual(self.ledger.library(self.parent),[])

    def test_completion_reconciliation_after_ledger_failure_does_not_regenerate_story(self):
        self.workflow();state=self.scenes();book_hash=state['book_version_hash']
        with patch.object(self.ledger,'complete',side_effect=RuntimeError('Synthetic interrupted completion')):
            with self.assertRaises(RuntimeError):self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        self.assertEqual(self.service.status(self.run,self.parent)['values']['status'],'complete')
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],3)
        done=self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        self.assertEqual(done['status'],'complete');self.assertEqual(len(self.fixture.calls),2)
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)

    def test_cached_pdf_tampering_is_detected_and_confirmed_pending_export_is_recovered(self):
        self.workflow();state=self.scenes();book_hash=state['book_version_hash']
        self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        def synthetic_pdf(document,output,work_dir):Path(output).write_bytes(b'%PDF-synthetic-protocol-fixture')
        with patch('backend.pdf_export_service.render_html_pdf',side_effect=synthetic_pdf) as render:
            original=self.service.pdf(self.run,self.parent,book_hash)
            folder=self.service.folder/'exports'/self.run/book_hash
            (folder/'book.pdf').replace(folder/'book.pending.pdf')
            self.assertEqual(self.service.pdf(self.run,self.parent,book_hash),original)
            self.assertEqual(render.call_count,1)
            (folder/'book.pdf').write_bytes(b'%PDF-tampered')
            with self.assertRaises(PermissionError):self.service.pdf(self.run,self.parent,book_hash)

    @unittest.skipUnless(os.environ.get('BALA_TEST_PDF')=='1','Set BALA_TEST_PDF=1 for installed browser PDF verification')
    def test_actual_pdf_contains_approved_text_and_static_images(self):
        from pypdf import PdfReader
        self.workflow(evidence=True);state=self.scenes()
        self.service.approve_text(self.run,self.parent,self.service.text_review(self.run,self.parent)['text_hash'])
        content=self.service.pdf(self.run,self.parent,state['book_version_hash'])
        self.assertTrue(content.startswith(b'%PDF-'))
        pdf=PdfReader(BytesIO(content));text=' '.join(p.extract_text() for p in pdf.pages)
        for page in range(1,7):self.assertIn('Page '+str(page)+'.',text)
        self.assertGreaterEqual(sum(len(p.images) for p in pdf.pages),6)
        self.assertIn('Source notes for parents',text)
        self.assertIn('edition1',text);self.assertIn('PDF pages 4',text)


if __name__=='__main__':unittest.main()
