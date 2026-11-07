"""Offline hosted boundary tests; no account credentials or evaluation dataset."""
import unittest
from unittest.mock import patch

from pydantic import SecretStr, ValidationError

from backend.contracts.common import text_hash
from backend.contracts.hosted_retrieval import (
    HostedIndexBinding, HostedSearchHit, HostedSearchResult, ParsedDocument, ParsedPage)
from backend.retrieval.hosted_config import HostedSettings, from_environment


def binding():
    return HostedIndexBinding(owner_id='owner1', source_id='source1', snapshot_id='snap1',
        edition_id='edition1', organization_id='org1', project_id='project1',
        hosted_index_id='index1', index_version='manifest1', parse_version='parse1',
        retrieval_config_hash='a' * 64)


def hit(**updates):
    return HostedSearchHit(**(dict(hosted_file_id='file1', hosted_chunk_id='chunk1',
        text='Synthetic passage.', pdf_pages=(3,), content_hash=text_hash('Synthetic passage.'),
        rank=1) | updates))


class HostedBoundaryTests(unittest.TestCase):
    def setUp(self):
        guard = patch('socket.socket', side_effect=AssertionError('Network forbidden in offline tests'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_disabled_configuration_does_not_claim_readiness(self):
        value = from_environment({})
        self.assertFalse(value.enabled)
        self.assertIn('index_id', value.missing())
        self.assertFalse(value.status()['configured'])
        self.assertFalse(value.status()['live_execution_allowed'])

    def test_complete_configuration_is_not_authorization(self):
        value = from_environment(dict(LLAMACLOUD_ENABLED='true', LLAMA_CLOUD_API_KEY='secret-fixture',
            LLAMACLOUD_ORGANIZATION_ID='org1', LLAMACLOUD_PROJECT_ID='project1',
            LLAMACLOUD_INDEX_ID='index1', LLAMACLOUD_API_GENERATION='v2'))
        self.assertTrue(value.status()['configured'])
        self.assertFalse(value.status()['live_execution_allowed'])
        self.assertEqual(value.status()['authentication'], 'not_checked')
        self.assertNotIn('secret-fixture', repr(value))
        self.assertNotIn('secret-fixture', str(value.status()))

    def test_invalid_settings_fail_closed(self):
        for env in ({'LLAMACLOUD_ENABLED': 'yes'}, {'LLAMACLOUD_READ_SECONDS': 'nan'},
                    {'LLAMACLOUD_READ_SECONDS': '999'}, {'LLAMACLOUD_REGION': 'elsewhere'},
                    {'LLAMACLOUD_API_GENERATION': 'latest'}, {'LLAMACLOUD_INDEX_ID': '../other'}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                from_environment(env)
        with self.assertRaises(ValueError):
            HostedSettings(api_key='unwrapped-secret')
        with self.assertRaises(ValueError):
            HostedSettings(enabled=1, api_key=SecretStr('fixture'))

    def test_project_does_not_substitute_for_index(self):
        self.assertIn('index_id', from_environment({'LLAMACLOUD_PROJECT_ID': 'project1'}).missing())

    def test_parsed_document_preserves_blank_page(self):
        pages = tuple(ParsedPage(pdf_page=i, text=t, content_hash=text_hash(t))
                      for i, t in enumerate(('Extracted text.', ''), 1))
        document = ParsedDocument(source_id='source1', snapshot_id='snap1',
            original_file_hash='b' * 64, parse_version='parse1', expected_page_count=2, pages=pages)
        self.assertEqual(document.pages[1].text, '')
        for invalid in ((pages[0],), (pages[0], pages[0]), tuple(reversed(pages))):
            with self.subTest(pages=invalid), self.assertRaises(ValidationError):
                document.model_copy(update={'pages': invalid})

    def test_page_and_candidate_hashes_cannot_lie(self):
        with self.assertRaises(ValidationError):
            ParsedPage(pdf_page=1, text='Changed', content_hash='a' * 64)
        with self.assertRaises(ValidationError):
            hit(text='Changed')
        with self.assertRaises(ValidationError):
            hit().model_copy(update={'text': 'Changed'})

    def test_candidates_are_not_cultural_approval(self):
        candidate = hit()
        self.assertNotIn('eligibility_status', candidate.model_dump())
        with self.assertRaises(ValidationError):
            HostedSearchHit(**candidate.model_dump(), eligibility_status='eligible')

    def test_bad_citations_and_nonfinite_scores_rejected(self):
        for updates in ({'pdf_pages': (3, 2)}, {'pdf_pages': (3, 3)}, {'pdf_pages': ()},
                        {'score': float('nan')}, {'rerank_score': float('inf')}, {'rank': True}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                hit(**updates)

    def test_no_answer_is_empty_not_fake_evidence(self):
        result = HostedSearchResult(binding=binding(), query_hash='c' * 64)
        self.assertEqual(result.hits, ())
        self.assertEqual(result.retrieval_kind, 'hosted_hybrid')

    def test_unique_ordered_candidates_and_immutable_binding(self):
        for hits in ((hit(), hit(rank=2)), (hit(rank=2),)):
            with self.subTest(hits=hits), self.assertRaises(ValidationError):
                HostedSearchResult(binding=binding(), query_hash='c' * 64, hits=hits)
        value = binding()
        with self.assertRaises(ValidationError):
            value.owner_id = 'other'
        with self.assertRaises(ValidationError):
            value.model_copy(update={'index_version': ''})


if __name__ == '__main__':
    unittest.main()
