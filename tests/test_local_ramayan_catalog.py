import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.ingestion.local_ramayan_catalog import LocalRamayanCatalog,requested_characters
from test_contracts import request

class LocalCatalogTests(unittest.TestCase):
    def test_hanuman_identity_is_required_even_when_only_rama_card_selected(self):
        req=request().model_copy(update={'required_character_ids':('rama',),'idea':'Hanuman and Ram story'})
        self.assertEqual(requested_characters(req),('rama','hanuman'))

    def test_hanuman_source_failure_does_not_substitute_guha(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog=LocalRamayanCatalog(SimpleNamespace(source_id='source1'),None,'Guha source',None,None,Path(folder))
            req=request().model_copy(update={'required_character_ids':('rama',),'idea':'Hanuman and Ram story'})
            command=SimpleNamespace(expected_text_hash='a'*64)
            report=SimpleNamespace(overall_result='FAIL',model_dump_json=lambda **kwargs:'{}')
            with patch('backend.ingestion.local_ramayan_catalog.hanuman_candidate',return_value=(command,'Hanuman source')),patch('backend.ingestion.local_ramayan_catalog.SourceQAAgent') as agent:
                agent.return_value.review.return_value=report
                with self.assertRaisesRegex(ValueError,'No different episode'):catalog.select(req)
            self.assertNotIn('rama-hanuman-first-meeting-v1',catalog.records)

    def test_unrequested_hanuman_is_not_added(self):
        req=request().model_copy(update={'required_character_ids':('rama',),'idea':'A kind welcome'})
        self.assertEqual(requested_characters(req),('rama',))
