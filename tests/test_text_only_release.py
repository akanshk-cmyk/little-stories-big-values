import unittest
from unittest.mock import patch
import test_complete_book_flow as fixture
from backend.accounts.text_only import TextOnlyAdapters
from backend.accounts.script_first import ScriptFirstWorkflowService
from backend.accounts.text_only import TextOnlyWorkflowService
from unittest.mock import Mock
from test_contracts import draft,request


class TextOnlyReleaseTests(unittest.TestCase):
    def test_matched_supporting_characters_reach_story_start(self):
        service=object.__new__(TextOnlyWorkflowService)
        for idea in ('Rama and Guha welcome', 'Rama and Hanuman meeting Lakshmana'):
            req=request().model_copy(update=dict(idea=idea,required_character_ids=('rama',),story_type='adapted_retelling',objective='Kindness',target_minutes=3))
            with patch.object(ScriptFirstWorkflowService,'start',return_value='saved-run') as start:
                self.assertEqual(service.start('parent','key',req.model_dump(mode='json')),'saved-run')
                ids=start.call_args.args[2]['required_character_ids']
                self.assertIn('guha' if 'Guha' in idea else 'lakshmana',ids)

    def test_hanuman_topic_preserves_both_required_characters(self):
        service=object.__new__(TextOnlyWorkflowService)
        req=request().model_copy(update={'required_character_ids':('rama',),'story_type':'adapted_retelling','idea':'Hanuman and Ram story'})
        with patch.object(ScriptFirstWorkflowService,'start') as start:
            service.start('parent1','new-story',req.model_dump(mode='json'))
            self.assertEqual(start.call_args.args[2]['required_character_ids'],['rama','hanuman'])

    def test_unapproved_draft_preview_never_grants_release(self):
        service=object.__new__(TextOnlyWorkflowService)
        story=draft(request());service.repository=Mock();service.repository.load.return_value=story.model_dump(mode='json')
        service.status=Mock(return_value={'values':dict(draft_id=story.draft_id,review=None,errors=[])})
        with patch.object(ScriptFirstWorkflowService,'review_book',return_value=dict(status='failed',approval_ready=False)):
            result=service.review_book('run1','parent1')
        self.assertEqual(result['draft_preview']['draft_hash'],story.draft_hash)
        self.assertFalse(result['approval_ready']);self.assertNotIn('preview',result);self.assertNotIn('book_hash',result)

    def test_critical_safety_failed_draft_is_not_exposed(self):
        service=object.__new__(TextOnlyWorkflowService);service.repository=Mock()
        service.status=Mock(return_value={'values':dict(draft_id='draft1',review={'critical_safety':{'status':'FAIL'}})})
        with patch.object(ScriptFirstWorkflowService,'review_book',return_value=dict(status='failed',approval_ready=False)):
            result=service.review_book('run1','parent1')
        self.assertNotIn('draft_preview',result);service.repository.load.assert_not_called()

    def test_explicit_text_only_flow_needs_no_scene_approval_but_needs_parent_approval(self):
        case=fixture.CompleteBookTests();case.setUp();self.addCleanup(case.doCleanups)
        with patch.object(fixture,'CompleteBookAdapters',TextOnlyAdapters),patch.object(fixture,'CompleteBookWorkflowService',ScriptFirstWorkflowService):state=case.start()
        inner=case.case
        self.assertEqual(state['status'],'awaiting_parent_approval')
        review=inner.service.review_book(inner.run,inner.parent)
        self.assertTrue(review['approval_ready']);self.assertNotIn('<img',review['html'])
        with self.assertRaises(PermissionError):inner.service.approved_output(inner.run,inner.parent,state['complete_preview_hash'])
        done=case.decide();self.assertEqual(done['status'],'complete')
        self.assertEqual(inner.ledger.balance(inner.parent)['starter_completed'],1)
