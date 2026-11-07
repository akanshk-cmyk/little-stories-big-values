"""Regression checks for the reproduced review findings; fake models only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
import test_stage9_workflow as fixtures
from backend import contracts as c
from backend.contracts.common import text_hash
from test_contracts import request


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=1000000.
        self.accounts=Accounts(Store(self.temp.name),clock=lambda:self.now)
        self.owner=self.accounts.register('parent','Synthetic-password-123')['account_id']
        self.ledger=Entitlements(self.accounts,clock=lambda:self.now)

    def test_atomic_last_generation_slot_and_release(self):
        for run in ('one','two'):self.ledger.authorize_generation(self.owner,run,'a'*64)
        def claim(run):
            try:self.ledger.authorize_generation(self.owner,run,'a'*64);return True
            except PermissionError:return False
        with ThreadPoolExecutor(2) as pool:outcomes=list(pool.map(claim,('three','four')))
        self.assertEqual(sum(outcomes),1)
        self.assertEqual(self.ledger.balance(self.owner)['starter_available'],0)
        self.ledger.release(self.owner,'one')
        self.assertEqual(self.ledger.balance(self.owner)['starter_available'],1)

    def test_generation_hold_moves_to_book_without_counting_twice(self):
        self.ledger.authorize_generation(self.owner,'run','a'*64)
        before=self.ledger.balance(self.owner)
        self.ledger.reserve(self.owner,'run','b'*64)
        self.assertEqual(self.ledger.balance(self.owner),before)
        self.ledger.complete(self.owner,'run','b'*64,dict(synthetic=True))
        self.assertEqual(self.ledger.balance(self.owner)['starter_completed'],1)
        self.assertEqual(self.ledger.balance(self.owner)['starter_available'],2)

    def test_released_or_expired_uncompleted_reservation_can_bind_new_version(self):
        self.ledger.reserve(self.owner,'run','a'*64);self.ledger.release(self.owner,'run')
        self.assertEqual(self.ledger.reserve(self.owner,'run','b'*64)['book_hash'],'b'*64)
        self.now+=1801
        self.assertEqual(self.ledger.reserve(self.owner,'run','c'*64)['book_hash'],'c'*64)

    def test_active_and_completed_reservations_never_rebind(self):
        self.ledger.reserve(self.owner,'run','a'*64)
        with self.assertRaises(ValueError):self.ledger.reserve(self.owner,'run','b'*64)
        self.ledger.complete(self.owner,'run','a'*64,dict(synthetic=True))
        with self.assertRaises(ValueError):self.ledger.reserve(self.owner,'run','b'*64)

    def test_daily_budget_is_durable_idempotent_and_not_reset_by_release(self):
        ledger=Entitlements(self.accounts,clock=lambda:self.now,max_generation_operations_per_day=2)
        ledger.authorize_generation(self.owner,'run','a'*64)
        ledger.claim_generation_operation(self.owner,'run','a'*64,'write-0')
        ledger.claim_generation_operation(self.owner,'run','a'*64,'write-0')
        ledger.claim_generation_operation(self.owner,'run','a'*64,'review-0')
        fresh=Entitlements(Accounts(Store(self.temp.name),clock=lambda:self.now),clock=lambda:self.now,max_generation_operations_per_day=2)
        with self.assertRaises(PermissionError):fresh.claim_generation_operation(self.owner,'run','a'*64,'write-1')
        ledger.release(self.owner,'run');ledger.authorize_generation(self.owner,'other','b'*64)
        with self.assertRaises(PermissionError):ledger.claim_generation_operation(self.owner,'other','b'*64,'write-0')

    def test_run_budget_blocks_a_seventh_operation_and_day_reset_is_explicit(self):
        self.ledger.authorize_generation(self.owner,'run','a'*64)
        for i in range(6):self.ledger.claim_generation_operation(self.owner,'run','a'*64,'op'+str(i))
        with self.assertRaises(PermissionError):self.ledger.claim_generation_operation(self.owner,'run','a'*64,'op6')
        self.now+=86400
        self.ledger.authorize_generation(self.owner,'another','b'*64)
        self.ledger.claim_generation_operation(self.owner,'another','b'*64,'write-0')

    def test_expired_lease_and_expired_weekly_pass_cannot_authorize_model_use(self):
        for i in range(3):
            run='done'+str(i);self.ledger.reserve(self.owner,run,'a'*64)
            self.ledger.complete(self.owner,run,'a'*64,{})
        pass_record=self.ledger.checkout(self.owner,'pass','success')
        self.now=pass_record['expires_at']-2
        self.ledger.authorize_generation(self.owner,'run','a'*64)
        self.now+=3
        with self.assertRaises(PermissionError):self.ledger.require_generation(self.owner,'run','a'*64)
        with self.assertRaises(PermissionError):self.ledger.authorize_generation(self.owner,'new','b'*64)


class IntegratedFixTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.ManagedWorkflowTests('test_exact_book_review_approval_and_single_debit')
        self.case.setUp();self.addCleanup(self.case.doCleanups)

    def test_zero_credits_stops_before_writer_or_reviewer(self):
        case=self.case
        for index in range(3):
            run='used'+str(index);case.ledger.reserve(case.parent,run,'a'*64)
            case.ledger.complete(case.parent,run,'a'*64,{})
        with self.assertRaises(PermissionError):case.workflow()
        self.assertEqual(case.fixture.calls,[])

    def test_cancel_releases_slot_without_claiming_a_completed_book(self):
        case=self.case;case.workflow()
        self.assertEqual(case.ledger.balance(case.parent)['starter_held'],1)
        state=case.service.cancel(case.run,case.parent)
        self.assertEqual(state['status'],'cancelled')
        self.assertEqual(case.ledger.balance(case.parent)['starter_available'],3)
        self.assertEqual(case.ledger.library(case.parent),[])
        self.assertEqual(case.service.execute(case.run,case.parent)['status'],'cancelled')

    def test_queued_cancel_never_calls_a_model(self):
        case=self.case;case.workflow();before=len(case.fixture.calls)
        run=case.service.start(case.parent,'queued',request(child_profile_id=case.child).model_dump(mode='json'),generation_mode='provider_synthetic')
        self.assertEqual(case.service.cancel(run,case.parent)['status'],'cancelled')
        self.assertEqual(case.service.execute(run,case.parent)['status'],'cancelled')
        self.assertEqual(len(case.fixture.calls),before)

    def test_expired_hold_stops_provider_retry_and_fallback(self):
        case=self.case
        case.fixture.unavailable=2
        original_guard=case.ledger.require_generation
        def require(*args):
            if case.fixture.calls:raise PermissionError('Synthetic expired generation hold.')
            return original_guard(*args)
        with patch.object(case.ledger,'require_generation',side_effect=require):
            with self.assertRaises(AssertionError):case.workflow()
        self.assertEqual(len(case.fixture.calls),1)
        self.assertEqual(case.fixture.calls[0][0],'groq')
        self.assertEqual(case.service.status(case.run,case.parent)['values']['status'],'evidence_blocked')

    def test_daily_limit_stops_before_reviewer_and_releases_story_slot(self):
        case=self.case
        case.ledger.max_generation_operations_per_day=1
        with self.assertRaises(AssertionError):case.workflow()
        self.assertEqual(len(case.fixture.calls),1)
        self.assertEqual(case.ledger.balance(case.parent)['starter_available'],3)
        self.assertEqual(case.ledger.library(case.parent),[])
        state=case.service.status(case.run,case.parent)['values']
        self.assertEqual(state['errors'][0]['code'],'generation_limit_reached')
        self.assertIn('AI request limit',state['errors'][0]['parent_message'])

    def test_failed_approval_then_picture_replacement_can_complete_once(self):
        case=self.case;case.workflow();before=case.scenes()
        with patch('backend.accounts.workflow.AccountWorkflowService._advance_output',side_effect=RuntimeError('Synthetic pre-approval interruption')):
            with self.assertRaises(RuntimeError):case.service.approve_text(case.run,case.parent,case.service.text_review(case.run,case.parent)['text_hash'])
        asset=case.asset('blue')
        case.registry.inspect_scene(case.admin,case.draft,case.context.characters,1,asset,matches=True,notes='Synthetic replacement review.')
        after=case.service.refresh_assets(case.run,case.parent)
        self.assertEqual(case.service.approve_text(case.run,case.parent,case.service.text_review(case.run,case.parent)['text_hash'])['status'],'complete')
        self.assertEqual(case.ledger.balance(case.parent)['starter_completed'],1)
        self.assertEqual(len(case.ledger.library(case.parent)),1)

    def test_source_notes_are_present_in_preview_and_saved_export(self):
        case=self.case;case.workflow(evidence=True);state=case.scenes()
        preview=case.service.preview(case.run,case.parent)
        for phrase in ('Source notes for parents','source1','edition1','snapshot1','PDF pages 4','Source quotation:','original teaching-inspired fiction'):
            self.assertIn(phrase,preview)
        case.service.approve_text(case.run,case.parent,case.service.text_review(case.run,case.parent)['text_hash'])
        saved=case.service.approved_output(case.run,case.parent,state['book_version_hash'])['result']['html']
        self.assertIn('PDF pages 4',saved)
        self.assertEqual(preview.count('<img '),saved.count('<img '))

    def test_additional_supplied_evidence_is_not_dropped_and_text_is_escaped(self):
        case=self.case;case.workflow(evidence=True);state=case.scenes()
        bundle=case.context.evidence;old=bundle.passages[0]
        text='Additional approved excerpt <script>not executable</script>'
        ref=old.reference.model_copy(update={'chunk_id':'chunk2','source_id':'additional-source',
            'content_hash':text_hash(text),'pdf_pages':(7,),'printed_pages':('vii',)})
        extra=c.RankedPassage(reference=ref,text=text,index_version=old.index_version,fused_rank=2,token_count=8)
        expanded=bundle.model_copy(update={'passages':(*bundle.passages,extra),'bundle_hash':None})
        context=case.context.model_copy(update={'evidence':expanded})
        load=case.service.repository.load
        def renderer_fixture(run,key):return context.model_dump(mode='json') if key=='context' else load(run,key)
        with patch.object(case.service.repository,'load',side_effect=renderer_fixture):
            document=case.service.adapters.document(state,case.service.repository)
        self.assertIn('additional-source',document);self.assertIn('printed pages vii',document)
        self.assertIn('no page-level citation recorded',document)
        self.assertIn('&lt;script&gt;',document);self.assertNotIn('<script>',document)


if __name__=='__main__':unittest.main()
