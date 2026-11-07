import tempfile
import unittest
from pydantic import ValidationError
from tests.test_contracts import request,evidence
from backend.contracts.common import content_hash
from backend.contracts.episode_selection import SelectionPolicy,EpisodeCandidate,ScriptBinding
from backend.workflows.episode_selection import EpisodeSelector
from backend.accounts.script_approval import ScriptApprovalGate


DIMENSIONS=('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety')


def candidate(id='episode1',**changes):
    return EpisodeCandidate(**(dict(episode_id=id,references=(evidence().passages[0].reference,),minimum_age=3,maximum_age=8,character_ids=('krishna','mira'),relevance=0.8,age_fit=1.0,reliability=1.0,adaptability=1.0,assessment='PASS',assessment_reason='Synthetic fixture',faithful_adaptation=True,policy_version='episode-selection-v1')|changes))


class CriteriaFlowTests(unittest.TestCase):
    def test_policy_rejects_invalid_weights_and_limits(self):
        for changes in ({'top_k':0},{'top_k':10,'retrieval_pool_size':5},{'max_corrections':3},{'relevance_weight':0.9}):
            with self.assertRaises(ValidationError):SelectionPolicy(**changes)

    def test_filter_before_topk_and_deterministic_ties(self):
        selector=EpisodeSelector(eligible=lambda owner,req,c:c.episode_id!='bad',policy=SelectionPolicy(top_k=1))
        result=selector.shortlist('owner',request(),[candidate('bad',relevance=1.0),candidate('b'),candidate('a')])
        self.assertEqual(result[0].episode_id,'a')

    def test_pending_unfaithful_wrong_character_and_revoked_are_excluded(self):
        selector=EpisodeSelector(eligible=lambda *args:True)
        for changes in ({'assessment':'PENDING'},{'faithful_adaptation':False},{'character_ids':('krishna',)},{'minimum_age':6}):
            self.assertFalse(selector.shortlist('owner',request(),[candidate(**changes)]))
        ref=evidence().passages[0].reference.model_copy(update={'review_status':'withdrawn','eligibility_status':'blocked'})
        self.assertFalse(selector.shortlist('owner',request(),[candidate(references=(ref,))]))

    def test_mixed_episodes_rejected(self):
        ref=evidence().passages[0].reference.model_copy(update={'parent_section_id':'other'})
        with self.assertRaises(ValidationError):candidate(references=(evidence().passages[0].reference,ref))

    def test_two_corrections_then_next_candidate(self):
        selector=EpisodeSelector(eligible=lambda *args:True)
        calls=[]
        def write(c,feedback):calls.append(c.episode_id);return c.episode_id
        def review(c,draft):return dict.fromkeys(DIMENSIONS,'FAIL' if c.episode_id=='a' else 'PASS')
        result=selector.run('owner',request(),[candidate('a'),candidate('b')],write=write,review=review)
        self.assertEqual(calls,['a','a','a','b'])
        self.assertEqual(result['status'],'awaiting_parent_approval')
        self.assertEqual(result['draft'],'b')

    def test_pending_review_does_not_surface_draft(self):
        result=EpisodeSelector(eligible=lambda *args:True).run('owner',request(),[candidate()],write=lambda *args:'hidden',review=lambda *args:{})
        self.assertEqual(result['status'],'blocked');self.assertNotIn('draft',result)

    def test_provider_exception_does_not_silently_switch_episode(self):
        def fail(*args):raise TimeoutError()
        with self.assertRaises(TimeoutError):EpisodeSelector(eligible=lambda *args:True).run('owner',request(),[candidate()],write=fail,review=lambda *args:{})

    def test_duplicate_and_excessive_pool_rejected(self):
        selector=EpisodeSelector(eligible=lambda *args:True)
        with self.assertRaises(ValueError):selector.shortlist('owner',request(),[candidate(),candidate()])
        with self.assertRaises(ValueError):selector.shortlist('owner',request(),[candidate(str(i)) for i in range(21)])


class ScriptApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.fresh=True;self.calls=[]
        self.guard=ScriptApprovalGate(self.temp.name,authorize=lambda owner,run:owner=='owner',current=lambda *args:self.fresh)
        self.script=('Exact story.',)
        self.binding=ScriptBinding(run_id='run1',request_hash='a'*64,script_hash=content_hash(self.script),evidence_hash='b'*64,review_hash='c'*64,asset_hash='d'*64,selection_hash='e'*64)

    def narrate(self,binding=None,script=None):
        return self.guard.narrate('owner',binding or self.binding,script=script or self.script,submit=lambda value:self.calls.append(value))

    def test_no_narration_before_approval(self):
        with self.assertRaises(PermissionError):self.narrate()
        self.assertEqual(self.calls,[])
        self.guard.approve('owner',self.binding);self.narrate();self.assertEqual(self.calls,[self.script])

    def test_changed_script_or_review_requires_new_approval(self):
        self.guard.approve('owner',self.binding)
        with self.assertRaises(PermissionError):self.narrate(script=('Edited.',))
        updated=self.binding.model_copy(update={'review_hash':'f'*64,'binding_hash':None})
        with self.assertRaises(PermissionError):self.narrate(binding=updated)
        self.assertFalse(self.calls)

    def test_revocation_cancel_and_cross_owner_block(self):
        self.guard.approve('owner',self.binding);self.fresh=False
        with self.assertRaises(PermissionError):self.narrate()
        self.fresh=True;self.guard.invalidate('owner','run1')
        with self.assertRaises(PermissionError):self.narrate()
        with self.assertRaises(PermissionError):self.guard.approve('other',self.binding)

    def test_approval_survives_restart(self):
        self.guard.approve('owner',self.binding)
        reopened=ScriptApprovalGate(self.temp.name,authorize=lambda *args:True,current=lambda *args:True)
        self.assertEqual(reopened.require('owner',self.binding),self.binding.binding_hash)
