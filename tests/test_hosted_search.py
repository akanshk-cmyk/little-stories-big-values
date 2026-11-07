"""Synthetic hybrid search/provenance tests; not a content golden-dataset run."""
import json
import unittest

import httpx
from pydantic import ValidationError

import tests.test_llamacloud_publication as fixtures
from backend.retrieval.hosted_search import (
    HostedQuery, HostedSearches, HybridSearchOptions, LlamaCloudSearch, SearchBlocked)


class HostedSearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.publisher = self.fixture.publisher
        self.key = self.publisher.stage('owner1', self.fixture.plan)
        self.publisher.check('owner1', self.key)
        self.publisher.activate('owner1', self.key)
        self.query = HostedQuery(operation_id='search1', publication_id=self.key,
            story_request_hash='d' * 64, query='A synthetic sharing example',
            usage_authorization_id='fixture-search-usage')
        self.store = HostedSearches(self.fixture.source.folder / 'searches')
        self.allowed, self.query_calls = True, 0
        self.after_query = lambda: None
        self.raw = dict(content='shares a paper flower.', score=0.8, rerank_score=0.9,
            metadata=dict(self.fixture.metadata), static_fields=dict(parsed_directory_file_id='parsed1',
                page_range_start=999, page_range_end=999, chunk_index=0))
        self.results = [self.raw]
        self.publisher.transport = httpx.MockTransport(self.handler)
        self.searcher = LlamaCloudSearch(self.publisher, self.store,
            authorize=lambda *args: self.allowed, token_counter=lambda text: 12,
            token_counter_version='synthetic-test-counter-v1')

    def handler(self, request):
        if request.url.path != '/api/v1/retrieval/retrieve':
            return self.fixture.handler(request)
        self.query_calls += 1
        payload = json.loads(request.content)
        self.assertEqual(payload['static_filters']['parsed_directory_file_id'],
                         {'operator': 'in', 'value': ['parsed1']})
        self.assertEqual(payload['custom_filters']['publication_id']['value'], self.key)
        self.assertEqual(payload['custom_filters']['purpose']['value'], 'traditional_claims')
        self.assertEqual(payload['rerank'], {'enabled': True, 'top_n': 20})
        self.after_query()
        return httpx.Response(200, json={'results': self.results})

    def test_search_maps_original_pages_and_expands_reviewed_context(self):
        result = self.searcher.search('owner1', self.query)
        parent = self.fixture.manifest.passages[0]
        self.assertEqual(result.passages[0].reference, parent.reference)
        self.assertEqual(result.passages[0].text, parent.text)
        self.assertEqual(result.matches[0].hit.pdf_pages, (1,))
        self.assertEqual(result.matches[0].hit.text, self.raw['content'])
        self.assertEqual(result.passages[0].token_count, 12)
        self.assertEqual(result.sufficiency, 'PENDING')
        self.assertTrue(self.searcher.assert_current('owner1', self.query, result))

    def test_saved_result_reuse_revalidates_but_does_not_query_twice(self):
        first = self.searcher.search('owner1', self.query)
        reopened = LlamaCloudSearch(self.publisher, HostedSearches(self.store.folder),
            authorize=lambda *args: True, token_counter=lambda text: 12,
            token_counter_version='synthetic-test-counter-v1')
        self.assertEqual(reopened.search('owner1', self.query), first)
        self.assertEqual(self.query_calls, 1)

    def test_empty_search_is_not_evidence(self):
        self.results = []
        result = self.searcher.search('owner1', self.query)
        self.assertEqual(result.passages, ())
        self.assertEqual(result.matches, ())
        self.assertEqual(result.sufficiency, 'PENDING')

    def test_default_denial_and_missing_token_counter_stop_before_network(self):
        calls = len(self.fixture.calls)
        denied = LlamaCloudSearch(self.publisher, self.store)
        with self.assertRaises(PermissionError):
            denied.search('owner1', self.query)
        denied = LlamaCloudSearch(self.publisher, self.store, authorize=lambda *args: True)
        with self.assertRaises(SearchBlocked):
            denied.search('owner1', self.query)
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertEqual(self.query_calls, 0)

    def test_owner_and_query_permission_are_enforced(self):
        with self.assertRaises(PermissionError):
            self.searcher.search('other', self.query)
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.searcher.search('owner1', self.query)
        self.assertEqual(self.query_calls, 0)

    def test_remote_file_or_metadata_injection_blocks_entire_response(self):
        self.raw['static_fields']['parsed_directory_file_id'] = 'outside-file'
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_changed_source_hash_blocks(self):
        self.raw['metadata']['text_hash'] = 'a' * 64
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_changed_or_missing_text_is_rejected(self):
        self.raw['content'] = 'An unsupported invented event.'
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_ambiguous_repeated_text_is_not_given_invented_offsets(self):
        self.raw['content'] = 'a'
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_unknown_result_after_valid_result_still_blocks_all(self):
        changed = dict(self.raw, static_fields={'parsed_directory_file_id': 'unknown'})
        self.results.append(changed)
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_overlapping_chunks_expand_to_one_parent(self):
        second = dict(self.raw, content='paper flower.')
        self.results.append(second)
        result = self.searcher.search('owner1', self.query)
        self.assertEqual(len(result.matches), 2)
        self.assertEqual(len(result.passages), 1)

    def test_duplicate_exact_chunks_rejected(self):
        self.results.append(dict(self.raw))
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_bad_scores_do_not_pass_schema(self):
        self.raw['score'] = 'high'
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_absent_scores_stay_absent(self):
        self.raw.pop('score')
        self.raw.pop('rerank_score')
        result = self.searcher.search('owner1', self.query)
        self.assertIsNone(result.matches[0].hit.score)
        self.assertIsNone(result.passages[0].rerank_score)

    def test_token_budget_blocks_without_truncation(self):
        query = self.query.model_copy(update={'options': HybridSearchOptions(max_context_tokens=1)})
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', query)

    def test_invalid_token_count_rejected(self):
        self.searcher.token_counter = lambda text: True
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_timeout_never_automatically_repeats_query(self):
        original = self.handler
        def handler(request):
            if request.url.path == '/api/v1/retrieval/retrieve':
                self.query_calls += 1
                raise httpx.ReadTimeout('secret-provider-detail')
            return original(request)
        self.publisher.transport = httpx.MockTransport(handler)
        for _ in range(2):
            with self.assertRaises(SearchBlocked) as error:
                self.searcher.search('owner1', self.query)
            self.assertNotIn('secret-provider-detail', str(error.exception))
        self.assertEqual(self.query_calls, 1)

    def test_withdrawal_during_search_releases_no_selection(self):
        self.after_query = lambda: self.fixture.source.registry.withdraw_passage(
            'reviewer1', 'owner1', self.fixture.review)
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_remote_sync_change_during_search_blocks(self):
        self.after_query = lambda: setattr(self.fixture, 'synced', '2026-11-05T12:00:00Z')
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', self.query)

    def test_changed_request_cannot_reuse_saved_selection(self):
        result = self.searcher.search('owner1', self.query)
        changed = self.query.model_copy(update={'story_request_hash': 'e' * 64})
        with self.assertRaises(SearchBlocked):
            self.searcher.assert_current('owner1', changed, result)
        with self.assertRaises(SearchBlocked):
            self.searcher.search('owner1', changed)
        self.assertEqual(self.query_calls, 1)

    def test_cached_result_still_requires_current_permissions(self):
        self.searcher.search('owner1', self.query)
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.searcher.search('owner1', self.query)
        self.assertEqual(self.query_calls, 1)

    def test_saved_selection_rechecks_remote_publication_before_use(self):
        result = self.searcher.search('owner1', self.query)
        self.fixture.remote_state = 'syncing'
        with self.assertRaises(SearchBlocked):
            self.searcher.assert_current('owner1', self.query, result)

    def test_concurrent_identical_search_is_not_duplicated(self):
        def competing():
            with self.assertRaises(RuntimeError):
                self.searcher.search('owner1', self.query)
        self.after_query = competing
        self.searcher.search('owner1', self.query)
        self.assertEqual(self.query_calls, 1)

    def test_forged_selection_and_sufficiency_pass_are_rejected(self):
        result = self.searcher.search('owner1', self.query)
        with self.assertRaises(ValidationError):
            result.model_copy(update={'sufficiency': 'PASS', 'selection_hash': None})
        changed = result.model_copy(update={'matches': (), 'passages': (), 'selection_hash': None})
        with self.assertRaises(SearchBlocked):
            self.searcher.assert_current('owner1', self.query, changed)

    def test_invalid_options_fail_locally(self):
        for data in ({'top_k': 0}, {'top_k': 51}, {'vector_weight': float('nan')},
                     {'vector_weight': 0.2}, {'max_context_tokens': True}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                HybridSearchOptions(**data)


if __name__ == '__main__':
    unittest.main()
