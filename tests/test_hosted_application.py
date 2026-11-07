"""Composed real HTTP/graph/account journey; all external services are fixtures."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import time
import unittest

import httpx

import test_hosted_source_api as source_fixture
import test_stage8_content as content_fixture
from test_contracts import request, characters
from test_stage9_workflow import picture
from backend.contracts import content_hash
from backend.retrieval.evidence_resolver import SufficiencyAssessment, SupportQuote
from backend.accounts.hosted_application import (
    HostedApplicationConfig, HostedPolicyPorts, build_hosted_application)


class HostedApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_fixture.HostedSourceAPI.setUpClass()

    @classmethod
    def tearDownClass(cls):
        source_fixture.HostedSourceAPI.tearDownClass()

    def setUp(self):
        self.h = source_fixture.HostedSourceAPI()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.child = self.h.accounts.add_child(self.h.parent, 'Fixture reader', 5)['child_id']
        self.content = content_fixture.ContentGraphTests()
        self.content.setUp()
        self.addCleanup(self.content.doCleanups)
        self.original = self.content.service()
        self.content.mutate = self.mutate
        self.verdict, self.search_calls = 'PASS', 0
        self.config = HostedApplicationConfig(parser_version='2026-09-28', parse_config_id='fixture-config',
            policy_version='fixture-policy-v1', sufficiency_version='fixture-sufficiency-v1',
            character_version='fixture-characters-v1', token_counter_version='synthetic-counter-v1',
            media_policy_version='fixture-media-v1', worker_steps=1, poll_interval=0.0)
        self.policies = HostedPolicyPorts(qualified_review=lambda *args: True,
            parse_usage=lambda perm, dest: perm.usage_authorization_id == 'fixture-parse-usage',
            publication_usage=lambda *args: True, index_usage_reference=lambda *args: 'fixture-index-usage',
            query_usage_reference=lambda *args: 'fixture-query-usage', query_authorize=lambda *args: True,
            story_authorize=lambda *args: True, provider_authorize=lambda *args: True,
            provider_consent=lambda *args: True, characters=lambda *args: characters(),
            token_counter=lambda text: 12, assess=self.assess)
        self.runtime = self.build()
        self.addCleanup(self.runtime.close)
        self.runtime.attach(self.h.server)
        self.h.api, self.h.service = self.runtime.api, self.runtime.sources
        self.h.workspace, self.h.reviews = self.runtime.workspace, self.runtime.reviews
        self.h.parser, self.h.publisher = self.runtime.parser, self.runtime.publisher

    def build(self, **updates):
        values = dict(accounts=self.h.accounts, entitlements=self.h.ledger, registry=self.h.registry,
            base=self.original.adapters.base, router=self.original.adapters.router, settings=self.h.settings,
            config=self.config, policies=self.policies, enabled=True, synthetic=True,
            transport=httpx.MockTransport(self.handler))
        folder = updates.pop('folder', Path(self.h.temp.name) / 'composed')
        values.update(updates)
        return build_hosted_application(folder, **values)

    def handler(self, req):
        if req.url.path == '/api/v1/retrieval/retrieve':
            self.search_calls += 1
            return httpx.Response(200, json={'results': [dict(content=self.h.text,
                metadata=self.h.metadata, static_fields={'parsed_directory_file_id': 'parsed1'},
                score=0.8, rerank_score=0.9)]})
        return self.h.handler(req)

    def assess(self, owner, req, selection):
        passage = selection.passages[0]
        return SufficiencyAssessment(request_hash=content_hash(req), selection_hash=selection.selection_hash,
            policy_version=self.config.sufficiency_version, verdict=self.verdict,
            reason='Synthetic composition gate only; not actual cultural review.',
            references=(SupportQuote(passage_id=passage.reference.chunk_id, quote=passage.text),))

    def mutate(self, raw, payload):
        if 'pages' in raw:
            passage = payload['context']['evidence']['passages'][0]
            raw['pages'][0]['text'] = passage['text'] + ' ' + raw['pages'][0]['text']
            raw['pages'][0]['citations'] = [dict(chunk_id=passage['reference']['chunk_id'],
                claim_text=passage['text'], source_quote=passage['text'], claim_kind='factual')]
            for page in raw['pages'][1:]:
                page['citations'] = []  # Remove the older fixture's unrelated chunk1 references.
            raw['fiction_disclosure'] = None
            raw['draft_hash'] = None
        return raw

    def publish(self):
        source, _, parsed = self.h.parsed()
        self.assertEqual(parsed['details']['phase'], 'awaiting_source_review', parsed)
        self.h.login('admin1')
        from backend.contracts.common import text_hash
        extraction = parsed['details']['extraction_id']
        command = dict(extraction_id=extraction, spans=[dict(pdf_page=1, start=0, end=len(self.h.text))],
            expected_text_hash=text_hash(self.h.text), scope='traditional_claims', extraction_checked=True,
            boundaries_checked=True, edition_verified=True, cultural_context_checked=True,
            child_suitability_checked=True, rights_permitted=True, index_text_permitted=True,
            rights_basis='Synthetic rights.', edition_reference='Fixture edition.',
            cultural_review_basis='Synthetic qualification.', notes='Not real cultural certification.')
        status, qualification = self.h.call('/api/accounts/hosted-sources/admin/qualify',
            dict(owner_id=self.h.parent, qualification=command))
        self.assertEqual(status, 201, qualification)
        self.qualification = qualification['qualification_id']
        status, publication = self.h.call('/api/accounts/hosted-sources/admin/publish',
            dict(owner_id=self.h.parent, extraction_id=extraction, purpose='traditional_claims', request_key='pub1'))
        self.assertEqual(status, 202, publication)
        self.h.login('parent1')
        value = self.h.wait(publication['job_id'])
        self.assertEqual(value['details']['phase'], 'awaiting_activation', value)
        self.h.login('admin1')
        status, value = self.h.call('/api/accounts/hosted-sources/admin/activate',
                                    dict(owner_id=self.h.parent, record_id=publication['job_id']))
        self.assertEqual(status, 200, value)
        self.h.login('parent1')
        return source

    def start(self, source, key='story1', **updates):
        req = request(child_profile_id=self.child, story_type='traditional_retelling', **updates)
        status, result = self.h.call('/api/accounts/runs', dict(request_key=key,
            request=req.model_dump(mode='json'), options={'selected_source_ids': [source]}))
        self.assertEqual(status, 202, result)
        run = result['run_id']
        until = time.monotonic() + 12
        while time.monotonic() < until:
            status, result = self.h.call('/api/accounts/runs/' + run + '/status')
            self.assertEqual(status, 200, result)
            values = result['values']
            if values.get('status') and values['status'] not in ('queued', 'validating', 'retrieving', 'writing', 'reviewing', 'correcting', 'preparing_audio'):
                return run, values
            time.sleep(.02)
        self.fail('Composed offline story did not settle.')

    def scenes(self, run, state):
        self.h.login('admin1')
        asset = self.h.registry.add(self.h.admin, picture(), characters=('krishna', 'mira'),
            style='Synthetic fixture', description='Synthetic color tile, not an actual scene.')['asset_id']
        self.h.registry.review(self.h.admin, asset, approve=True, rights_basis='Synthetic generated fixture tile.',
                               gentle=True, notes='Fixture only.')
        for page in range(1, 7):
            status, result = self.h.call('/api/accounts/scene-review', dict(run_id=run,
                draft_hash=state['draft_hash'], page_number=page, asset_id=asset,
                matches=True, notes='Synthetic scene match; no real visual quality claim.'))
            self.assertEqual(status, 200, result)
        self.h.login('parent1')
        status, result = self.h.call('/api/accounts/runs/' + run + '/refresh-assets', {})
        self.assertEqual(status, 200, result)
        return result['values']

    def test_complete_composed_http_book_flow_has_one_final_approval(self):
        source = self.publish()
        status, catalog = self.h.call('/api/accounts/hosted-sources/catalog')
        self.assertEqual(status, 200, catalog)
        self.assertEqual(catalog['sources'][0]['source_id'], source)
        run, state = self.start(source)
        self.assertEqual(state['status'], 'assets_pending', state)
        self.assertEqual(state['dense_hits'], [])
        self.assertEqual(state['sparse_hits'], [])
        self.assertEqual(self.search_calls, 1)
        self.assertEqual(len(self.content.calls), 2)
        self.assertEqual(self.h.ledger.balance(self.h.parent)['starter_completed'], 0)
        state = self.scenes(run, state)
        self.assertEqual(state['status'], 'awaiting_parent_approval', state)
        status, review = self.h.call('/api/accounts/runs/' + run + '/complete-review')
        self.assertEqual(status, 200, review)
        self.assertTrue(review['approval_ready'])
        self.assertEqual(review['approval_scope'], 'complete_book')
        self.assertIn('Source', review['html'])
        decision = dict(action='approve', book_hash=review['book_hash'], request_key='approve1', notes='')
        status, result = self.h.call('/api/accounts/runs/' + run + '/book-decision', decision)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['values']['status'], 'complete')
        self.assertEqual(self.h.ledger.balance(self.h.parent)['starter_completed'], 1)
        self.assertEqual(len(self.h.call('/api/accounts/library')[1]['books']), 1)
        self.assertEqual(self.h.call('/api/accounts/runs/' + run + '/book-decision', decision)[0], 200)
        self.assertEqual(self.h.ledger.balance(self.h.parent)['starter_completed'], 1)

    def test_pending_sufficiency_stops_composed_writer_and_releases_hold(self):
        source = self.publish()
        self.verdict = 'PENDING'
        _, state = self.start(source)
        self.assertEqual(state['status'], 'evidence_blocked', state)
        self.assertEqual(self.content.calls, [])
        self.assertEqual(self.h.ledger.balance(self.h.parent)['starter_available'], 3)

    def test_withdrawn_evidence_blocks_final_approval_and_completion_charge(self):
        source = self.publish()
        run, state = self.start(source)
        self.assertEqual(state['status'], 'assets_pending', state)
        self.scenes(run, state)
        _, review = self.h.call('/api/accounts/runs/' + run + '/complete-review')
        self.h.reviews.withdraw_passage(self.h.admin, self.h.parent, self.qualification)
        status, _ = self.h.call('/api/accounts/runs/' + run + '/book-decision', dict(
            action='approve', book_hash=review['book_hash'], request_key='stale-approve', notes=''))
        self.assertEqual(status, 403)
        self.assertEqual(self.h.ledger.balance(self.h.parent)['starter_completed'], 0)
        self.assertEqual(self.h.call('/api/accounts/library')[1]['books'], [])

    def test_catalog_hides_withdrawn_or_other_account_sources_without_cloud_calls(self):
        self.publish()
        count = len(self.h.calls)
        self.assertEqual(len(self.runtime.catalog.list(self.h.parent)), 1)
        self.assertEqual(len(self.h.calls), count)
        self.h.reviews.withdraw_passage(self.h.admin, self.h.parent, self.qualification)
        self.assertEqual(self.runtime.catalog.list(self.h.parent), [])
        other = self.h.accounts.register('parent2', source_fixture.secure.PASSWORD)['account_id']
        self.assertEqual(self.runtime.catalog.list(other), [])

    def test_borrowed_child_profile_cannot_start_story(self):
        other = self.h.accounts.register('parent2', source_fixture.secure.PASSWORD)['account_id']
        child = self.h.accounts.add_child(other, 'Other reader', 5)['child_id']
        req = request(child_profile_id=child)
        self.assertEqual(self.h.call('/api/accounts/runs', dict(request_key='wrong-child', request=req.model_dump(mode='json')))[0], 403)
        self.assertEqual(self.content.calls, [])

    def test_unrecorded_request_hash_cannot_authorize_cloud_query(self):
        source = self.publish()
        from backend.retrieval.hosted_search import HostedQuery
        publication = self.runtime.catalog.list(self.h.parent)[0]['publication_id']
        query = HostedQuery(operation_id='forged-request', publication_id=publication,
            story_request_hash='e' * 64, query='Unowned request', usage_authorization_id='fixture-query-usage')
        with self.assertRaises(PermissionError):
            self.runtime.search.search(self.h.parent, query)
        self.assertEqual(self.search_calls, 0)

    def test_catalog_does_not_arbitrarily_pick_between_sources(self):
        from backend.contracts.complete_book import CreationOptions
        with unittest.mock.patch.object(self.runtime.catalog, 'list', return_value=[
                dict(source_id='one', purpose='traditional_claims'),
                dict(source_id='two', purpose='traditional_claims')]):
            self.assertIsNone(self.runtime.catalog.select(self.h.parent,
                request(child_profile_id=self.child, story_type='traditional_retelling'), CreationOptions()))

    def test_v5_episode_planner_composes_and_has_separate_workflow_store(self):
        from backend.workflows.episode_planner import EpisodePlanner
        planner=EpisodePlanner(retrieve=lambda *args:[],eligible=lambda *args:False)
        config=self.config.model_copy(update={'script_first':True})
        folder=Path(self.h.temp.name)/'v5-composition'
        runtime=self.build(folder=folder,config=config,policies=replace(self.policies,episode_planner=planner))
        self.addCleanup(runtime.close)
        self.assertEqual(runtime.api.workflow.approval_policy,'script-first-review-v1')
        self.assertIs(runtime.api.workflow.adapters.episode_planner,planner)
        self.assertEqual(runtime.api.workflow.folder,folder/'workflow-v5')
        self.assertEqual(self.content.calls,[])

    def test_planner_requires_type_and_v5_before_creating_runtime(self):
        from backend.workflows.episode_planner import EpisodePlanner
        folder=Path(self.h.temp.name)/'bad-planner'
        for value in (lambda *args:[], EpisodePlanner(retrieve=lambda *args:[],eligible=lambda *args:False)):
            with self.assertRaises(ValueError):
                self.build(folder=folder,policies=replace(self.policies,episode_planner=value))
            self.assertFalse(folder.exists())

    def test_local_env_can_load_hosted_settings_without_enabling_runtime(self):
        import os
        from backend.env_config import load_local_env
        path = Path(self.h.temp.name) / 'fixture.env'
        path.write_text('LLAMA_CLOUD_API_KEY=fixture-only\nLLAMACLOUD_ENABLED=false\nUNSUPPORTED_SETTING=no\n')
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            load_local_env(path)
            self.assertEqual(os.environ['LLAMA_CLOUD_API_KEY'], 'fixture-only')
            self.assertEqual(os.environ['LLAMACLOUD_ENABLED'], 'false')
            self.assertNotIn('UNSUPPORTED_SETTING', os.environ)

    def test_disabled_or_unsafe_configuration_has_no_new_runtime_folder(self):
        folder = Path(self.h.temp.name) / 'disabled-composition'
        with self.assertRaises(PermissionError):
            self.build(folder=folder, enabled=False)
        self.assertFalse(folder.exists())
        with self.assertRaises(ValueError):
            self.build(folder=folder, transport=None)
        self.assertFalse(folder.exists())
        with self.assertRaises(ValueError):
            self.build(folder=folder, policies=replace(self.policies, token_counter=None))
        self.assertFalse(folder.exists())

    def test_attach_rejects_public_listener_or_different_account_authority(self):
        with self.assertRaises(PermissionError):
            self.runtime.attach(SimpleNamespace(server_address=('0.0.0.0', 9000)))
        with self.assertRaises(ValueError):
            self.runtime.attach(SimpleNamespace(server_address=('127.0.0.1', 9000),
                account_api=SimpleNamespace(accounts=object(), workflow=None)))

    def test_readiness_has_no_secrets_and_makes_no_provider_requests(self):
        before = len(self.h.calls)
        status, result = self.h.call('/api/accounts/hosted-sources/readiness')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['mode'], 'offline_synthetic')
        self.assertEqual(result['live_authentication'], 'not_verified')
        self.assertNotIn('fixture-key', json.dumps(result))
        self.assertEqual(len(self.h.calls), before)


if __name__ == '__main__':
    unittest.main()
