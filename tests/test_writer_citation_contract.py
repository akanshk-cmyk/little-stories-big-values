"""Citation shape regression; these examples do not assert cultural accuracy."""
import unittest
from pydantic import ValidationError
from backend.contracts.stories import ClaimCitation, StoryPage


class WriterCitationContractTests(unittest.TestCase):
    def page(self, claim):
        return StoryPage(page_number=1,text='Guha welcomed Rama.',character_ids=('guha','rama'),
            scene_description='A welcome.',accessible_description='A welcome.',
            citations=(ClaimCitation(chunk_id='excerpt',claim_text=claim,
                source_quote='Welcome, dear Prince',claim_kind='factual'),))

    def test_adapted_claim_is_separate_from_source_quote(self):
        page=self.page('Guha welcomed Rama.')
        self.assertNotEqual(page.citations[0].claim_text,page.citations[0].source_quote)

    def test_source_text_in_claim_field_is_still_rejected(self):
        with self.assertRaises(ValidationError):self.page('Welcome, dear Prince')

    def test_provider_schema_explains_both_quote_origins(self):
        fields=ClaimCitation.model_json_schema()['properties']
        self.assertIn('page.text',fields['claim_text']['description'])
        self.assertIn('evidence passage',fields['source_quote']['description'])
