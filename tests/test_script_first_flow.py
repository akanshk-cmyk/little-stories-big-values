"""Real graph/account regression, synthetic model and audio callbacks only."""
import unittest
from unittest.mock import patch
import test_complete_book_flow as fixture
from backend.accounts.script_first import ScriptFirstAdapters,ScriptFirstWorkflowService


class ScriptFirstFlowTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.CompleteBookTests();self.case.setUp();self.addCleanup(self.case.doCleanups)

    def start(self,**kwargs):
        with patch.object(fixture,'CompleteBookAdapters',ScriptFirstAdapters),patch.object(fixture,'CompleteBookWorkflowService',ScriptFirstWorkflowService):
            return self.case.start(**kwargs)

    def test_actual_graph_calls_audio_only_after_approval_and_once(self):
        self.start(narration=True);inner=self.case.case;state=inner.scenes()
        self.assertEqual(state['status'],'awaiting_parent_approval')
        self.assertEqual(self.case.audio_calls,0)
        review=inner.service.review_book(inner.run,inner.parent)
        self.assertTrue(review['approval_ready']);self.assertFalse(review['audio_available'])
        with self.assertRaises(PermissionError):inner.service.audio(inner.run,inner.parent,state['complete_preview_hash'])
        done=self.case.decide()
        self.assertEqual(done['status'],'complete');self.assertEqual(self.case.audio_calls,1)
        data,mime=inner.service.audio(inner.run,inner.parent,done['complete_preview_hash'])
        self.assertEqual(mime,'audio/wav');self.assertTrue(data)
        self.case.decide();self.assertEqual(self.case.audio_calls,1)
        self.assertEqual(inner.ledger.balance(inner.parent)['starter_completed'],1)

    def test_missing_narrator_does_not_block_script_review_or_readable_book(self):
        self.start(narration=True,configured=False);inner=self.case.case;inner.scenes()
        self.assertTrue(inner.service.review_book(inner.run,inner.parent)['approval_ready'])
        done=self.case.decide();self.assertEqual(done['status'],'complete')
        self.assertFalse(inner.service.review_book(inner.run,inner.parent)['audio_available'])
        inner.service.approved_output(inner.run,inner.parent,done['complete_preview_hash'])
        self.assertEqual(self.case.audio_calls,0)

    def test_reject_and_revision_do_not_generate_audio(self):
        self.start(narration=True);inner=self.case.case;inner.scenes()
        self.case.decide('reject')
        self.assertEqual(self.case.audio_calls,0)
        self.assertEqual(inner.service.status(inner.run,inner.parent)['values']['status'],'cancelled')

    def test_source_withdrawal_blocks_approval_and_playback(self):
        self.start(narration=True,evidence=True);inner=self.case.case;inner.scenes()
        inner.fixture.eligible=False
        with self.assertRaises(PermissionError):self.case.decide()
        self.assertEqual(self.case.audio_calls,0)

    def test_revision_needs_fresh_approval(self):
        self.start(narration=True);inner=self.case.case;before=inner.scenes()
        with patch.object(inner.service,'submit',return_value=None):self.case.decide('request_changes',notes='Make ending gentle.')
        inner.service.execute(inner.run,inner.parent)
        self.assertEqual(self.case.audio_calls,0)
        with self.assertRaises(PermissionError):inner.service.audio(inner.run,inner.parent,before['complete_preview_hash'])

    def test_capabilities_advertise_script_first_without_adding_actions(self):
        self.start();inner=self.case.case
        from backend.accounts.http_api import AccountAPI
        api=AccountAPI(inner.accounts,inner.ledger,inner.registry,inner.service)
        self.assertEqual(inner.service.approval_policy,'script-first-review-v1')
        self.assertEqual(api._complete_workflow(),inner.service)

    def test_revocation_after_approval_blocks_playback(self):
        self.start(narration=True,evidence=True);inner=self.case.case;inner.scenes()
        done=self.case.decide();self.assertEqual(self.case.audio_calls,1)
        inner.fixture.eligible=False
        with self.assertRaises(PermissionError):inner.service.audio(inner.run,inner.parent,done['complete_preview_hash'])

    def test_pdf_only_release_rejects_audio_requests_before_generation(self):
        from pathlib import Path
        from test_contracts import request
        inner=self.case.case
        base=inner.fixture.service()
        adapter=ScriptFirstAdapters(base.adapters,inner.accounts,inner.registry,media_folder=Path(inner.temp.name)/'pdf-media')
        service=ScriptFirstWorkflowService(Path(inner.temp.name)/'pdf-only',adapter,inner.accounts,inner.ledger,enabled=True,narration_enabled=False,public_generation_mode='provider_synthetic')
        self.addCleanup(service.close)
        with self.assertRaises(ValueError):service.start(inner.parent,'no-audio',request(child_profile_id=inner.child,narration_requested=True).model_dump(mode='json'),options=dict(voice_id='voice1',voice_model='model1',audio_processing_consent=True))
        self.assertFalse(service.voice_options)
        self.assertEqual(inner.fixture.calls,[])

    def test_stopped_status_reconciles_credit_before_return(self):
        from backend.accounts.workflow import AccountWorkflowService
        from backend.workflows.service import WorkflowService
        from unittest.mock import Mock
        service=object.__new__(AccountWorkflowService);service.entitlements=Mock()
        with patch.object(WorkflowService,'status',return_value={'values':{'status':'evidence_blocked'}}):
            result=service.status('run1','owner1')
        self.assertEqual(result['values']['status'],'evidence_blocked')
        service.entitlements.release.assert_called_once_with('owner1','run1')
        service.entitlements.reset_mock()
        with patch.object(WorkflowService,'status',return_value={'values':{'status':'awaiting_parent_approval'}}):service.status('run1','owner1')
        service.entitlements.release.assert_not_called()

    def test_parent_actions_unavailable_when_draft_qa_is_pending(self):
        self.case.case.fixture.review_status={'clarity':'PENDING'}
        state=self.start()
        inner=self.case.case
        self.assertEqual(state['status'],'evidence_blocked')
        review=inner.service.review_book(inner.run,inner.parent)
        self.assertFalse(review['approval_ready'])
        self.assertNotIn('preview',review)
        self.assertEqual(self.case.audio_calls,0)
