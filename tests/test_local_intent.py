import unittest
from test_contracts import request
from backend.contracts.local_intent import resolve_local_intent
from backend.ingestion.local_ramayan_catalog import LocalRamayanCatalog
from unittest.mock import Mock

class LocalIntentTests(unittest.TestCase):
    def resolve(self,idea,characters=('rama',),exclusions=()):
        return resolve_local_intent(request().model_copy(update=dict(idea=idea,required_character_ids=characters,exclusions=exclusions)))

    def test_bridge_does_not_silently_become_guha(self):
        result=self.resolve('Rama building a bridge')
        self.assertEqual(result.status,'UNSUPPORTED')
        catalog=LocalRamayanCatalog(None,None,'Guha',None,Mock(),None)
        with self.assertRaisesRegex(ValueError,'do not cover'):catalog.select(result.request)
        catalog.router.assert_not_called()

    def test_negated_hanuman_does_not_become_required(self):
        result=self.resolve('Rama story without Hanuman')
        self.assertEqual(result.status,'READY')
        self.assertEqual(result.request.required_character_ids,('rama',))
        self.assertEqual(result.candidate_ids,('rama-guha-welcome-v1',))
        self.assertIn('hanuman',result.request.exclusions)

    def test_selected_character_conflict_requires_clarification(self):
        self.assertEqual(self.resolve('Rama without Hanuman',('rama','hanuman')).status,'CLARIFICATION_REQUIRED')

    def test_explicit_exclusion_conflicts_with_positive_request(self):
        self.assertEqual(self.resolve('Rama and Hanuman',exclusions=('No Hanuman',)).status,'CLARIFICATION_REQUIRED')

    def test_positive_hanuman_preserved(self):
        result=self.resolve('Hanuman and Ram story')
        self.assertEqual(result.request.required_character_ids,('rama','hanuman'))
        self.assertEqual(result.candidate_ids,('rama-hanuman-first-meeting-v1',))

    def test_two_candidates_only_no_claim_of_five_results(self):
        self.assertEqual(len(self.resolve('A kind story').candidate_ids),2)

    def test_unknown_character_is_not_substituted(self):
        self.assertEqual(self.resolve('Rama and Krishna story').status,'UNSUPPORTED')
