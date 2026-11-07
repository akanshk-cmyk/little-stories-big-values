"""Complete-book checkpoint through actual graph and mock provider transports."""
from io import BytesIO
from pathlib import Path
import hashlib,tempfile,time,unittest,wave,os
from unittest.mock import patch
from backend import contracts as c
from backend.accounts.complete_book import CompleteBookAdapters,CompleteBookWorkflowService
from backend.accounts.http_api import AccountAPI
from backend.contracts.complete_book import CompleteBookPreview
from backend.workflows.story_graph import PreparedContext
from test_contracts import request
import test_stage9_workflow as fixture

class CompleteBookTests(unittest.TestCase):
    def test_approval_survives_quota_only_change_without_allowing_generation_resume(self):
        self.start();case=self.case;case.scenes()
        with patch.object(case.service.entitlements,'max_generation_operations_per_day',36):
            self.assertTrue(case.service.review_book(case.run,case.parent)['approval_ready'])
            with self.assertRaises(ValueError):case.service.execute(case.run,case.parent)
            done=self.decide()
            self.assertEqual(done['status'],'complete')

    def test_completed_book_can_be_read_after_generation_version_changes(self):
        self.start();case=self.case;case.scenes();done=self.decide()
        with patch.object(case.service.adapters,'version','updated-generation-version'):
            review=case.service.review_book(case.run,case.parent)
            self.assertEqual(review['status'],'complete');self.assertTrue(review['preview'])
            self.assertEqual(case.service.approved_output(case.run,case.parent,done['complete_preview_hash'])['complete_book_hash'],done['complete_preview_hash'])
            with self.assertRaises(ValueError):case.service.execute(case.run,case.parent)
            with self.assertRaises(PermissionError):case.service.review_book(case.run,'other-parent')

    def test_unapproved_book_requires_revalidation_after_version_changes(self):
        self.start();case=self.case;case.scenes()
        with patch.object(case.service.adapters,'version','updated-generation-version'):
            review=case.service.review_book(case.run,case.parent)
            self.assertFalse(review['approval_ready']);self.assertEqual(review['status'],'incomplete')
            with self.assertRaises(ValueError):self.decide()

    def setUp(self):
        self.case=fixture.ManagedWorkflowTests('test_exact_book_review_approval_and_single_debit')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.audio_calls=0;self.audio_allowed=True
    def narrator(self,script,options):
        self.audio_calls+=1;stream=BytesIO()
        with wave.open(stream,'wb') as out:out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(b'\0\0'*800)
        return dict(content=stream.getvalue(),media_type='audio/wav')
    def checker(self,script,options,data):
        return dict(audio_id=hashlib.sha256(data).hexdigest(),media_type='audio/wav',script_hash=c.content_hash(script),
            voice_id=options.voice_id,model_id=options.voice_model,alignment_status='PASS',
            alignment_evidence='Synthetic silence fixture; protocol test only, no actual speech-fidelity claim.',checker_version='synthetic-check-v1')
    def start(self,narration=False,configured=True,evidence=False):
        case=self.case;case.fixture.bundle=evidence;base=case.fixture.service()
        adapter=CompleteBookAdapters(base.adapters,case.accounts,case.registry,media_folder=Path(case.temp.name)/'audio',
            narrator=self.narrator if configured else None,audio_checker=self.checker if configured else None,
            audio_authorize=lambda *_:self.audio_allowed,media_policy_version='synthetic-media-v1')
        case.service=CompleteBookWorkflowService(Path(case.temp.name)/'complete',adapter,case.accounts,case.ledger,enabled=True,public_generation_mode='provider_synthetic')
        self.addCleanup(case.service.close)
        opts=dict(voice_id='voice1',voice_model='model1',audio_processing_consent=True) if narration else {}
        case.run=case.service.start(case.parent,'complete1',request(child_profile_id=case.child,narration_requested=narration).model_dump(mode='json'),
            options=opts,generation_mode='provider_synthetic')
        state=case.service.execute(case.run,case.parent)
        if state.get('draft_id'):
            case.draft=c.parse_payload(c.StoryDraft,case.service.repository.load(case.run,state['draft_id']))
            case.context=c.parse_payload(PreparedContext,case.service.repository.load(case.run,state.get('context_id','context')))
        return state
    def decide(self,action='approve',book_hash=None,key='decision1',notes=''):
        case=self.case;state=case.service.status(case.run,case.parent)['values']
        return case.service.decide(case.run,case.parent,dict(action=action,book_hash=book_hash or state['complete_preview_hash'],request_key=key,notes=notes))
    def test_one_complete_checkpoint_and_one_completion_debit(self):
        case=self.case;self.start();state=case.scenes()
        review=case.service.review_book(case.run,case.parent)
        self.assertTrue(review['approval_ready']);self.assertEqual(review['approval_scope'],'complete_book')
        with self.assertRaises(ValueError):case.service.approve_text(case.run,case.parent,'a'*64)
        with self.assertRaises(ValueError):case.service.authorize_narration(case.run,case.parent,'a'*64,{})
        done=self.decide();self.assertEqual(done['status'],'complete')
        self.assertEqual(case.ledger.balance(case.parent)['starter_completed'],1)
        self.assertEqual(self.decide()['status'],'complete')
        self.assertEqual(case.ledger.balance(case.parent)['starter_completed'],1)
        self.assertEqual(len(case.fixture.calls),2)
        case.service.approved_output(case.run,case.parent,done['complete_preview_hash'])
    def test_missing_images_or_requested_audio_cannot_be_approved(self):
        case=self.case;state=self.start(narration=True,configured=False)
        self.assertEqual(state['status'],'assets_pending');self.assertFalse(case.service.review_book(case.run,case.parent)['approval_ready'])
        state=case.scenes();self.assertEqual(state['status'],'assets_pending')
        self.assertIn('narration',state['incomplete_reasons'][0])
        with self.assertRaises(PermissionError):self.decide(book_hash='a'*64)
        self.assertEqual(self.audio_calls,0);self.assertEqual(case.ledger.library(case.parent),[])
    def test_requested_audio_is_prepared_before_the_only_parent_checkpoint(self):
        case=self.case;self.start(narration=True);state=case.scenes()
        self.assertEqual(state['status'],'awaiting_parent_approval');self.assertEqual(self.audio_calls,1)
        review=case.service.review_book(case.run,case.parent);self.assertTrue(review['audio_available'])
        preview=c.parse_payload(CompleteBookPreview,review['preview'])
        content,mime=case.service.audio(case.run,case.parent,preview.complete_book_hash)
        self.assertEqual(mime,'audio/wav');self.assertEqual(hashlib.sha256(content).hexdigest(),preview.audio.audio_id)
        with self.assertRaises(PermissionError):case.service.audio(case.run,case.parent,preview.complete_book_hash,final=True)
        done=self.decide();case.service.audio(case.run,case.parent,done['complete_preview_hash'],final=True)
        self.assertEqual(self.audio_calls,1)
    def test_audio_tampering_and_source_withdrawal_block_approval(self):
        case=self.case;self.start(narration=True,evidence=True);case.scenes()
        state=case.service.status(case.run,case.parent)['values'];preview=case.service.adapters.checked_preview(state,case.service.repository,case.parent)
        path=case.service.adapters.media_path(case.run,preview.audio.audio_id);data=path.read_bytes();path.write_bytes(b'tampered')
        with self.assertRaises(PermissionError):self.decide()
        path.write_bytes(data);case.fixture.eligible=False
        with self.assertRaises(PermissionError):self.decide()
    def test_reject_prevents_outputs_and_releases_uncompleted_hold(self):
        case=self.case;self.start();state=case.scenes();self.decide('reject')
        self.assertEqual(case.service.status(case.run,case.parent)['values']['status'],'cancelled')
        self.assertEqual(case.ledger.balance(case.parent)['starter_available'],3)
        with self.assertRaises(PermissionError):case.service.approved_output(case.run,case.parent,state['complete_preview_hash'])
    def test_image_replacement_requires_new_complete_book_consent(self):
        case=self.case;self.start();case.scenes();old=self.decide();asset=case.asset('blue')
        case.registry.inspect_scene(case.admin,case.draft,case.context.characters,1,asset,matches=True,notes='Synthetic replacement review.')
        state=case.service.refresh_assets(case.run,case.parent)
        self.assertEqual(state['status'],'awaiting_parent_approval')
        self.assertNotEqual(old['complete_preview_hash'],state['complete_preview_hash'])
        with self.assertRaises(ValueError):self.decide(book_hash=old['complete_preview_hash'])
        self.decide(key='approve-new-version');self.assertEqual(case.ledger.balance(case.parent)['starter_completed'],1)
    def test_parent_revision_creates_new_draft_and_reruns_qa(self):
        case=self.case;self.start();before=case.scenes()
        with patch.object(case.service,'submit',return_value=None):self.decide('request_changes',notes='Make the ending more reassuring.')
        state=case.service.execute(case.run,case.parent)
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertNotEqual(state['draft_hash'],before['draft_hash'])
        self.assertEqual(state['parent_revision_count'],1);self.assertEqual(state['correction_count'],0)
        self.assertEqual(len(case.fixture.calls),4)
        for call in case.fixture.calls[-2:]:self.assertEqual(call[2]['parent_revision_request'],'Make the ending more reassuring.')
        case.draft=c.parse_payload(c.StoryDraft,case.service.repository.load(case.run,state['draft_id']))
        after=case.scenes();self.assertNotEqual(before['complete_preview_hash'],after['complete_preview_hash'])
        with self.assertRaises(ValueError):self.decide(book_hash=before['complete_preview_hash'])
        self.decide(book_hash=after['complete_preview_hash'],key='revision-approval')
    def test_failed_qa_and_other_owner_cannot_reach_approval(self):
        case=self.case;case.fixture.failures=99;state=self.start()
        self.assertEqual(state['status'],'evidence_blocked');self.assertFalse(case.service.review_book(case.run,case.parent)['approval_ready'])
        other=case.accounts.register('other-parent','Synthetic-password-123')['account_id']
        with self.assertRaises(PermissionError):case.service.review_book(case.run,other)
    def test_voice_and_audio_are_in_complete_version_hash(self):
        case=self.case;self.start(narration=True);case.scenes();review=case.service.review_book(case.run,case.parent)
        preview=c.parse_payload(CompleteBookPreview,review['preview'])
        audio=preview.audio.model_copy(update={'voice_id':'different'})
        changed=preview.model_copy(update={'audio':audio,'complete_book_hash':None})
        self.assertNotEqual(changed.complete_book_hash,preview.complete_book_hash)
    def test_initial_audio_permission_and_stale_parent_decisions_are_rejected(self):
        case=self.case;self.start();case.scenes()
        with self.assertRaises(ValueError):self.decide(book_hash='a'*64)
        req=request(child_profile_id=case.child,narration_requested=True)
        with self.assertRaises(ValueError):case.service.start(case.parent,'bad-audio',req.model_dump(mode='json'),generation_mode='provider_synthetic')

    def test_complete_api_exposes_single_review_and_rejects_approval_overrides(self):
        case=self.case;self.start();state=case.scenes();api=AccountAPI(case.accounts,case.ledger,case.registry,case.service)
        actor=dict(account_id=case.parent,role='parent');path=['api','accounts','runs',case.run]
        r,status=api.handle('GET',path+['complete-review'],actor)
        self.assertEqual(status,200);self.assertTrue(r['approval_ready'])
        with self.assertRaises(ValueError):api.handle('POST',path+['approve-text'],actor,dict(text_hash='a'*64))
        with self.assertRaises(ValueError):api.handle('POST',path+['book-decision'],actor,dict(book_hash=state['complete_preview_hash'],action='approve',request_key='api1',owner='other'))
        result,status=api.handle('POST',path+['book-decision'],actor,dict(book_hash=state['complete_preview_hash'],action='approve',request_key='api1'))
        self.assertEqual(result['values']['status'],'complete')

    def test_narrator_uncertain_failure_is_not_retried_by_execute(self):
        case=self.case;self.start(narration=True)
        with patch.object(case.service.adapters,'narrator',side_effect=TimeoutError('Synthetic uncertain audio outcome')) as narrator:
            state=case.scenes();self.assertEqual(state['status'],'outcome_unknown')
            case.service.execute(case.run,case.parent);self.assertEqual(narrator.call_count,1)
        self.assertFalse(case.service.review_book(case.run,case.parent)['approval_ready'])

    def test_selected_upload_cannot_silently_bypass_internal_resolver(self):
        case=self.case;self.start()
        req=request(child_profile_id=case.child)
        run=case.service.start(case.parent,'unreviewed-upload',req.model_dump(mode='json'),options=dict(selected_source_ids=['new-upload']),generation_mode='provider_synthetic')
        before=len(case.fixture.calls);state=case.service.execute(run,case.parent)
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(len(case.fixture.calls),before)

    def test_withdrawn_scene_or_audio_marks_review_incomplete(self):
        case=self.case;self.start();case.scenes()
        case.registry.review(case.admin,case.asset_id,approve=False,rights_basis='Withdrawn fixture',gentle=True,notes='Synthetic withdrawal.')
        review=case.service.review_book(case.run,case.parent)
        self.assertEqual(review['status'],'incomplete');self.assertFalse(review['approval_ready'])

    def test_noop_asset_refresh_does_not_create_another_parent_review(self):
        case=self.case;self.start();case.scenes();done=self.decide()
        refreshed=case.service.refresh_assets(case.run,case.parent)
        self.assertEqual(refreshed['status'],'complete')
        self.assertEqual(refreshed['complete_preview_hash'],done['complete_preview_hash'])
        self.assertEqual(refreshed['approval_id'],done['approval_id'])

    @unittest.skipUnless(os.environ.get('BALA_TEST_PDF')=='1','Set BALA_TEST_PDF=1 for actual local PDF checks')
    def test_real_pdf_uses_the_complete_approved_version(self):
        from pypdf import PdfReader
        case=self.case;self.start();case.scenes();done=self.decide()
        data=case.service.pdf(case.run,case.parent,done['complete_preview_hash'])
        reader=PdfReader(BytesIO(data));text=' '.join(page.extract_text() for page in reader.pages)
        self.assertIn('Parent-approved complete book',text)
        self.assertIn(done['complete_preview_hash'],text)
        self.assertGreaterEqual(sum(len(page.images) for page in reader.pages),6)

if __name__=='__main__':unittest.main()
