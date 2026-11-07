"""Synthetic evidence-gate and actual LangGraph routing tests, no live quality claims."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import tests.test_hosted_search as fixtures
from test_contracts import request, characters, draft
from test_stage3_workflows import adapters
from backend.contracts import content_hash, parse_payload, EvidenceBundle, ClaimCitation
from backend.contracts.complete_book import CreationOptions
from backend.providers.retry_policy import ProviderFailure
from backend.retrieval.evidence_resolver import (
    EvidenceResolutions, EvidenceSourcePlan, HostedEvidenceResolver,
    SufficiencyAssessment, SupportQuote)
from backend.workflows.story_graph import PreparedContext
from backend.workflows.service import WorkflowService
from backend.accounts.assembly import assemble_hosted_complete_accounts


class EvidenceResolverTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HostedSearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.search = self.fixture.searcher
        self.folder = self.fixture.fixture.source.folder / 'resolutions'
        self.store = EvidenceResolutions(self.folder)
        self.req = request(story_type='traditional_retelling')
        self.options = CreationOptions(selected_source_ids=(self.fixture.fixture.manifest.source_id,))
        self.allowed, self.verdict, self.policy_calls = True, 'PASS', 0
        self.bad_quote, self.bad_request, self.bad_selection = False, False, False
        self.plan = EvidenceSourcePlan(publication_id=self.fixture.key, query='Synthetic sharing example',
                                       usage_authorization_id='fixture-search-usage')
        self.on_write = lambda: None
        self.resolver = self.make_resolver()

    def assess(self, owner, req, selection):
        self.policy_calls += 1
        return SufficiencyAssessment(request_hash='e' * 64 if self.bad_request else content_hash(req),
            selection_hash='f' * 64 if self.bad_selection else selection.selection_hash,
            policy_version='synthetic-sufficiency-v1', verdict=self.verdict,
            reason='Synthetic gate decision, not an actual content judgment.',
            references=(SupportQuote(passage_id=selection.passages[0].reference.chunk_id,
                quote='Unsupported quote' if self.bad_quote else selection.passages[0].text),))

    def make_resolver(self, **updates):
        options = dict(characters=lambda owner, req: characters(), source_selector=lambda *args: self.plan,
            authorize=lambda owner, req, options, operation: self.allowed and owner == 'owner1',
            assess=self.assess, policy_version='synthetic-sufficiency-v1',
            selector_version='synthetic-selector-v1')
        options.update(updates)
        return HostedEvidenceResolver(self.search, self.store, **options)

    def context(self):
        return parse_payload(PreparedContext, self.resolver('owner1', self.req, self.options))

    def test_pass_freezes_exact_saved_evidence_and_remains_current(self):
        context = self.context()
        bundle = context.evidence
        self.assertIsInstance(bundle, EvidenceBundle)
        self.assertEqual(bundle.request_hash, content_hash(self.req))
        self.assertEqual(bundle.passages[0].text, self.fixture.fixture.manifest.passages[0].text)
        record = self.store.for_bundle(bundle.bundle_id)
        self.assertEqual(record.assessment.verdict, 'PASS')
        self.assertEqual(record.selection.sufficiency, 'PENDING')
        self.assertTrue(self.resolver.eligibility(bundle))
        self.assertEqual(self.resolver.sufficiency(self.req, bundle), 'PASS')

    def test_default_semantic_policy_is_pending_and_saved_without_bundle(self):
        self.resolver = self.make_resolver(assess=None)
        with self.assertRaises(ProviderFailure) as error:
            self.context()
        self.assertEqual(error.exception.code, 'evidence_sufficiency_pending')
        with self.store.db() as db:
            row = db.execute('SELECT bundle_id,payload FROM resolutions').fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(json.loads(row[1])['assessment']['verdict'], 'PENDING')

    def test_fail_and_pending_cannot_release_a_bundle(self):
        for verdict in ('FAIL', 'PENDING'):
            self.verdict = verdict
            with self.subTest(verdict=verdict), self.assertRaises(ProviderFailure):
                self.context()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM resolutions WHERE bundle_id IS NOT NULL').fetchone()[0], 0)

    def test_empty_search_fails_before_semantic_policy(self):
        self.fixture.results = []
        with self.assertRaises(ProviderFailure) as error:
            self.context()
        self.assertEqual(error.exception.code, 'evidence_missing')
        self.assertEqual(self.policy_calls, 0)

    def test_wrong_request_selection_or_unsupported_quote_blocks_pass(self):
        for name in ('bad_request', 'bad_selection', 'bad_quote'):
            setattr(self, name, True)
            with self.subTest(name=name), self.assertRaises(ProviderFailure):
                self.context()
            setattr(self, name, False)

    def test_no_permission_stops_before_retrieval(self):
        self.allowed = False
        with self.assertRaises(ProviderFailure):
            self.context()
        self.assertEqual(self.fixture.query_calls, 0)

    def test_wrong_selected_source_never_silently_replaced(self):
        self.options = CreationOptions(selected_source_ids=('other-source',))
        with self.assertRaises(ProviderFailure):
            self.context()
        self.assertEqual(self.fixture.query_calls, 0)

    def test_multiple_or_duplicate_selected_sources_block(self):
        source = self.fixture.fixture.manifest.source_id
        for sources in ((source, 'other'), (source, source)):
            self.options = CreationOptions(selected_source_ids=sources)
            with self.subTest(sources=sources), self.assertRaises(ProviderFailure):
                self.context()
        self.assertEqual(self.fixture.query_calls, 0)

    def test_missing_catalog_choice_blocks_traditional_request(self):
        self.resolver = self.make_resolver(source_selector=None)
        with self.assertRaises(ProviderFailure):
            self.context()

    def test_original_without_cultural_claims_does_not_search(self):
        self.req = request()
        self.options = CreationOptions()
        self.assertIsNone(self.context().evidence)
        self.assertEqual(self.fixture.query_calls, 0)

    def test_original_with_cultural_fact_still_needs_evidence(self):
        self.req = request(required_elements=('moral', 'parent_question', 'cultural_fact'))
        self.options = CreationOptions()
        self.assertIsNotNone(self.context().evidence)
        self.assertEqual(self.fixture.query_calls, 1)

    def test_wrong_corpus_purpose_is_not_upgraded(self):
        self.req = request()  # With a selected source, this requires inspiration scope.
        with self.assertRaises(ProviderFailure):
            self.context()
        self.assertEqual(self.fixture.query_calls, 0)

    def test_missing_character_is_not_substituted(self):
        self.req = request(story_type='traditional_retelling', required_character_ids=('hanuman',))
        with self.assertRaises(ProviderFailure):
            self.context()
        self.assertEqual(self.fixture.query_calls, 0)

    def test_withdrawal_and_changed_request_invalidate_bundle(self):
        bundle = self.context().evidence
        self.assertEqual(self.resolver.sufficiency(self.req.model_copy(update={'age': 6}), bundle), 'PENDING')
        f = self.fixture.fixture
        f.source.registry.withdraw_passage('reviewer1', 'owner1', f.review)
        self.assertFalse(self.resolver.eligibility(bundle))

    def test_changed_sufficiency_policy_and_resolver_permission_block(self):
        bundle = self.context().evidence
        self.assertFalse(self.make_resolver(policy_version='new-policy-v2').eligibility(bundle))
        self.allowed = False
        self.assertFalse(self.resolver.eligibility(bundle))

    def test_restart_preserves_bundle_identity_and_does_not_repeat_search(self):
        first = self.context().evidence
        reopened = self.make_resolver()
        reopened.store = EvidenceResolutions(self.folder)
        result = parse_payload(PreparedContext, reopened('owner1', self.req, self.options))
        self.assertEqual(result.evidence, first)
        self.assertEqual(self.fixture.query_calls, 1)

    def test_changed_or_unregistered_bundle_rejected(self):
        bundle = self.context().evidence
        changed = bundle.model_copy(update={'bundle_id': 'unregistered', 'bundle_hash': None})
        self.assertFalse(self.resolver.eligibility(changed))
        self.assertFalse(self.resolver.eligibility(None))
        self.assertEqual(self.resolver.sufficiency(self.req, None), 'PENDING')

    def test_explicit_assembly_binds_resolver_checks(self):
        base = adapters()
        with patch('backend.accounts.assembly.assemble_complete_accounts', return_value='fixture-api') as build:
            result = assemble_hosted_complete_accounts(self.folder, resolver=self.resolver, base=base)
        self.assertEqual(result, 'fixture-api')
        args = build.call_args.kwargs
        self.assertTrue(args['base'].uses_prepared_evidence)
        self.assertIs(args['evidence_resolver'], self.resolver)
        self.assertEqual(args['evidence_sufficiency'], self.resolver.sufficiency)

    def graph(self):
        writes = []
        def write(req, context, number, previous):
            writes.append(context.evidence.bundle_id)
            # Structural synthetic story only; no actual cultural fidelity claim.
            value = draft(req, None)
            passage = context.evidence.passages[0]
            first = value.pages[0].model_copy(update={
                'text': passage.text + ' Krishna and Mira watch.',
                'citations': (ClaimCitation(chunk_id=passage.reference.chunk_id,
                    claim_text=passage.text, source_quote=passage.text, claim_kind='factual'),)})
            self.on_write()
            return value.model_copy(update={'classification': req.story_type,
                'pages': (first, *value.pages[1:]),
                'evidence_bundle_id': context.evidence.bundle_id, 'fiction_disclosure': None,
                'draft_hash': None}).model_dump(mode='json')
        def forbidden(*args):
            raise AssertionError('Hosted results must not be labeled dense/sparse local hits.')
        base = replace(adapters(assets_missing=True),
            prepare=lambda req: self.resolver('owner1', req, self.options),
            dense=forbidden, sparse=forbidden, write=write)
        service = WorkflowService(self.folder / 'graph', self.resolver.bind_adapters(base), enabled=True)
        self.addCleanup(service.close)
        run = service.start('owner1', 'request1', self.req.model_dump(mode='json'))
        return service, run, writes

    def test_actual_graph_uses_frozen_evidence_without_fake_dense_sparse(self):
        service, run, writes = self.graph()
        state = service.execute(run, 'owner1')
        self.assertEqual(state['status'], 'assets_pending', state)
        self.assertEqual(len(writes), 1)
        self.assertEqual(state['dense_hits'], [])
        self.assertEqual(state['sparse_hits'], [])
        self.assertEqual(len(state['ranked_hits']), 1)
        self.assertEqual(state['evidence_bundle_id'], writes[0])

    def test_pending_sufficiency_graph_never_calls_writer(self):
        self.verdict = 'PENDING'
        service, run, writes = self.graph()
        state = service.execute(run, 'owner1')
        self.assertEqual(state['status'], 'evidence_blocked', state)
        self.assertEqual(writes, [])

    def test_withdrawal_during_writer_blocks_graph_before_release(self):
        f = self.fixture.fixture
        self.on_write = lambda: f.source.registry.withdraw_passage('reviewer1', 'owner1', f.review)
        service, run, writes = self.graph()
        state = service.execute(run, 'owner1')
        self.assertEqual(state['status'], 'evidence_blocked', state)
        self.assertEqual(len(writes), 1)
        self.assertIsNone(state['approval_id'])

    def test_policy_change_requires_explicit_checkpoint_recovery(self):
        service, run, _ = self.graph()
        self.resolver.policy_version = 'new-policy-v2'
        with self.assertRaises(ValueError):
            service.execute(run, 'owner1')


if __name__ == '__main__':
    unittest.main()
