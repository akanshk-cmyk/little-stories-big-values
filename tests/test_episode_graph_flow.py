"""Criteria-first selection through real writer/QA/account graph using fixtures."""
import unittest
from unittest.mock import patch
import test_complete_book_flow as fixture
from test_contracts import evidence,characters
from backend.contracts import content_hash
from backend.contracts.episode_selection import EpisodeCandidate
from backend.workflows.episode_planner import EpisodePlanner
from backend.accounts.script_first import ScriptFirstAdapters,ScriptFirstWorkflowService


class EpisodeGraphTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.CompleteBookTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.retrieval_calls=0;self.allowed=True

    def retrieve(self,owner,request,options):
        self.retrieval_calls+=1;items=[]
        for i in range(2):
            bundle=evidence(request)
            passage=bundle.passages[0]
            ref=passage.reference.model_copy(update={'parent_section_id':'episode'+str(i)})
            passage=passage.model_copy(update={'reference':ref})
            bundle=bundle.model_copy(update={'bundle_id':'bundle'+str(i),'passages':(passage,),'bundle_hash':None})
            c=EpisodeCandidate(episode_id='episode'+str(i),references=(ref,),minimum_age=3,maximum_age=8,character_ids=('krishna','mira'),relevance=1.0-i*0.1,age_fit=1.0,reliability=1.0,adaptability=1.0,assessment='PASS',assessment_reason='Synthetic fixture only.',faithful_adaptation=True,policy_version='episode-selection-v1')
            items.append(dict(candidate=c.model_dump(mode='json'),context=dict(characters=characters().model_dump(mode='json'),evidence=bundle.model_dump(mode='json'))))
        return items

    def start(self,fail_first=True):
        self.case.case.ledger.max_generation_operations_per_run=8
        planner=EpisodePlanner(retrieve=self.retrieve,eligible=lambda *args:self.allowed)
        class Adapters(ScriptFirstAdapters):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);self.episode_planner=planner
        def mutate(raw,payload):
            if fail_first and 'draft' in payload and 'episode-0' in payload['draft']['draft_id']:
                raw['clarity']=dict(status='FAIL',reason='Synthetic first candidate failure.',requested_correction='Use another suitable narrative.',evidence=[])
            return raw
        self.case.case.fixture.mutate=mutate
        with patch.object(fixture,'CompleteBookAdapters',Adapters),patch.object(fixture,'CompleteBookWorkflowService',ScriptFirstWorkflowService):
            return self.case.start(narration=True,evidence=True)

    def test_fallback_reaches_review_then_approval_and_audio(self):
        state=self.start();inner=self.case.case
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertEqual(state['episode_index'],1)
        self.assertEqual(self.retrieval_calls,1)
        self.assertEqual(state['correction_count'],0)
        self.assertEqual(len(inner.fixture.calls),8)
        self.assertEqual(self.case.audio_calls,0)
        state=inner.scenes()
        review=inner.service.review_book(inner.run,inner.parent)
        self.assertTrue(review['approval_ready'])
        self.assertEqual(review['context']['evidence']['bundle_id'],'bundle1')
        done=self.case.decide()
        self.assertEqual(done['status'],'complete');self.assertEqual(self.case.audio_calls,1)
        inner.service.audio(inner.run,inner.parent,done['complete_preview_hash'])
        self.assertEqual(inner.ledger.balance(inner.parent)['starter_completed'],1)
        import os
        if os.environ.get('BALA_TEST_PDF')=='1':
            pdf=inner.service.pdf(inner.run,inner.parent,done['complete_preview_hash'])
            self.assertTrue(pdf.startswith(b'%PDF-'))
            from pathlib import Path
            import json
            output=Path(__file__).resolve().parents[1]/'docs/verification'
            (output/'full-backend-synthetic.pdf').write_bytes(pdf)
            report=dict(status='PASS',scope='Synthetic reviewed episode evidence, model replies, static tiles and audio. Real graph/account/PDF mechanics.',selected_episode=done['episode_id'],retrieval_calls=self.retrieval_calls,model_calls=len(inner.fixture.calls),audio_calls=self.case.audio_calls,parent_approval_recorded=True,qa_result=done['review'],starter_completed=inner.ledger.balance(inner.parent)['starter_completed'],pdf_bytes=len(pdf),pdf_file='full-backend-synthetic.pdf',live_calls=0,held_evaluation_executed=False)
            (output/'full-backend-e2e.json').write_text(json.dumps(report,indent=2)+'\n')

    def test_best_candidate_does_not_require_parent_episode_choice(self):
        state=self.start(fail_first=False)
        self.assertEqual(state['episode_index'],0);self.assertEqual(self.retrieval_calls,1)
        self.assertEqual(len(self.case.case.fixture.calls),2)

    def test_missing_eligibility_blocks_before_writer(self):
        self.allowed=False;state=self.start()
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(self.case.case.fixture.calls,[])
        self.assertEqual(self.case.audio_calls,0)

    def test_all_candidates_fail_without_exposing_a_draft(self):
        self.case.case.ledger.max_generation_operations_per_day=30
        def fail(raw,payload):
            if 'draft' in payload:raw['clarity']=dict(status='FAIL',reason='Synthetic failure.',requested_correction='Revise.',evidence=[])
            return raw
        # Raise only the fixture's explicit per-run bound for twelve calls.
        planner=EpisodePlanner(retrieve=self.retrieve,eligible=lambda *args:True)
        class Adapters(ScriptFirstAdapters):
            def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.episode_planner=planner
        self.case.case.fixture.mutate=fail
        self.case.case.ledger.max_generation_operations_per_run=12
        with patch.object(fixture,'CompleteBookAdapters',Adapters),patch.object(fixture,'CompleteBookWorkflowService',ScriptFirstWorkflowService):
            state=self.case.start(evidence=True)
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertFalse(self.case.case.service.review_book(self.case.case.run,self.case.case.parent).get('preview'))
        self.assertEqual(self.retrieval_calls,1)
