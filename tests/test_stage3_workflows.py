"""Synthetic orchestration checks only. No model or held dataset execution."""
from dataclasses import replace
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from backend import contracts as c
from backend.workflows.checkpoints import checkpoint_store, run_lock
from backend.workflows.repository import RunRepository, UnknownOutcome
from backend.workflows.service import WorkflowService
from backend.workflows.state import merge_attempts, merge_errors
from backend.workflows.story_graph import ReplayAdapters
from test_contracts import request, characters, evidence, draft


def adapters(*, with_evidence=False, failures=0, safety='PASS', assets_missing=False, log=None):
    def record(name):
        if log:
            with open(log,'a',encoding='utf-8') as file:
                file.write(name+'\n')

    def prepare(req):
        record('prepare')
        return {'characters':characters().model_dump(mode='json'),
                'evidence':evidence(req).model_dump(mode='json') if with_evidence else None}

    def search(kind):
        def action(req, context):
            record(kind)
            return [c.SearchHit(reference=context.evidence.passages[0].reference,
                index_version='index1',retriever=kind,rank=1,score=1.0).model_dump(mode='json')]
        return action

    def write(req, context, number, previous):
        record('write-'+str(number))
        return draft(req,context.evidence,draft_id='draft'+str(number)).model_dump(mode='json')

    def review(book, context, number):
        record('review-'+str(number))
        pointer = c.EvidencePointer(artifact_id=book.draft_id,pointer='/title',quote=book.title)
        def check(status):
            return c.ReviewCheck(status=status,reason='Synthetic routing fixture; no content quality claim.',
                                 evidence=(pointer,) if status=='PASS' else ())
        return c.ReviewReport(draft_hash=book.draft_hash,
            evidence_bundle_hash=context.evidence.bundle_hash if context.evidence else None,
            reviewer_version='stage3-reviewer-v1',factual_correctness=check('PASS'),clarity=check('PASS'),
            value_insight=check('FAIL' if number < failures else 'PASS'),engagement=check('PASS'),
            tone_fit=check('PASS'),critical_safety=check(safety)).model_dump(mode='json')

    def assets(book):
        record('assets')
        return c.AssetManifest(manifest_id='assets-'+book.draft_id,pages=() if assets_missing else tuple(
            c.PageAsset(page_number=i,scene_inspection='PASS',asset=c.StaticAssetReference(
                asset_id='asset'+str(i),version='fixture-v1',file_hash='a'*64,media_type='image/png',
                origin='approved_static_library',review_status='approved',rights_status='permitted',
                character_ids=('krishna','mira'),style='Synthetic fixture',accessible_description='Synthetic fixture'))
            for i in range(1,7))).model_dump(mode='json')

    def render(book):
        record('render')
        return {'artifact':'synthetic-output','book_hash':book.book_version_hash}

    return ReplayAdapters(version='fixture-v1',capabilities=c.PlaybookCapabilities(
        character_ids=('krishna','mira'),objectives=('Kindness',),
        story_types=('original_inspired','traditional_retelling')),
        prepare=prepare,dense=search('dense'),sparse=search('sparse'),write=write,review=review,
        assets=assets,render=render,eligibility=lambda bundle:True)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/'runs'
        self.log = Path(self.temp.name)/'calls.txt'

    def service(self, **kwargs):
        service = WorkflowService(self.folder,adapters(log=self.log,**kwargs),enabled=True)
        self.addCleanup(service.close)
        return service

    def start(self, service, req=None):
        return service.start('parent1','request1',(req or request()).model_dump(mode='json'))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_default_disabled_has_no_disk_side_effect(self):
        service = WorkflowService(self.folder,adapters())
        self.addCleanup(service.close)
        with self.assertRaises(PermissionError):self.start(service)
        self.assertFalse(self.folder.exists())

    def test_live_mode_is_not_silently_replayed(self):
        service=self.service()
        with self.assertRaises(ValueError):service.start('parent1','key',request().model_dump(mode='json'),generation_mode='live')

    def test_duplicate_start_is_idempotent_and_conflicting_request_rejected(self):
        service=self.service();run=self.start(service)
        self.assertEqual(run,self.start(service))
        with self.assertRaises(ValueError):self.start(service,request(age=6))
        with self.assertRaises(PermissionError):service.execute(run,'another_parent')

    def test_parent_pause_restart_and_exact_version_approval(self):
        service=self.service();run=self.start(service)
        state=service.submit(run,'parent1').result(timeout=20)
        self.assertEqual(state['status'],'awaiting_parent_approval')
        self.assertNotIn('render',self.calls())
        other=self.service()
        self.assertEqual(other.execute(run,'parent1')['status'],'awaiting_parent_approval')
        with self.assertRaises(ValueError):other.approve(run,'parent1','b'*64)
        with self.assertRaises(PermissionError):other.approve(run,'parent2',state['book_version_hash'])
        complete=other.approve(run,'parent1',state['book_version_hash'])
        self.assertEqual(complete['status'],'complete')
        self.assertEqual(other.execute(run,'parent1')['status'],'complete')
        self.assertEqual(self.calls().count('render'),1)
        self.assertEqual(self.calls().count('write-0'),1)
        output=other.repository.load(run,'output')
        self.assertEqual(output['provenance'],'offline_replay')
        self.assertEqual(output['book_version_hash'],state['book_version_hash'])

    def test_status_reader_does_not_take_the_worker_lease(self):
        import backend.workflows.service as workflow_service
        service=self.service();run=self.start(service)
        entered=threading.Event();release=threading.Event();original=workflow_service.checkpoint_store
        @contextmanager
        def paused_reader(path):
            with original(path) as saver:
                if threading.current_thread().name.startswith('poll-reader'):
                    entered.set()
                    if not release.wait(timeout=10):raise RuntimeError('Synthetic reader timeout')
                yield saver
        with patch.object(workflow_service,'checkpoint_store',paused_reader),ThreadPoolExecutor(max_workers=1,thread_name_prefix='poll-reader') as pool:
            reader=pool.submit(service.status,run,'parent1')
            try:
                self.assertTrue(entered.wait(timeout=5))
                state=service.submit(run,'parent1').result(timeout=5)
                self.assertEqual(state['status'],'awaiting_parent_approval')
            finally:release.set()
            self.assertIn('values',reader.result(timeout=5))

    def test_traditional_without_evidence_blocks_before_writer(self):
        service=self.service();run=self.start(service,request(story_type='traditional_retelling'))
        self.assertEqual(service.execute(run,'parent1')['status'],'evidence_blocked')
        self.assertNotIn('write-0',self.calls())

    def test_ineligible_evidence_never_reaches_search_or_writer(self):
        service=self.service(with_evidence=True)
        service.adapters=replace(service.adapters,eligibility=lambda bundle:False)
        run=self.start(service)
        self.assertEqual(service.execute(run,'parent1')['status'],'evidence_blocked')
        self.assertEqual(self.calls(),['prepare'])

    def test_parallel_searches_join_and_preserve_both_channels(self):
        service=self.service(with_evidence=True)
        barrier=threading.Barrier(2)
        def wrap(action):
            def inner(*args):
                barrier.wait(timeout=10)
                return action(*args)
            return inner
        service.adapters=replace(service.adapters,dense=wrap(service.adapters.dense),sparse=wrap(service.adapters.sparse))
        run=self.start(service);state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'awaiting_parent_approval')
        self.assertEqual(len(state['dense_hits']),1)
        self.assertEqual(len(state['sparse_hits']),1)
        self.assertEqual(state['fused_hits'],[])  # No invented hybrid/reranker results.

    def test_unknown_chunk_from_search_blocks(self):
        service=self.service(with_evidence=True)
        service.adapters=replace(service.adapters,dense=lambda *args:[{}])
        run=self.start(service);state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'failed')
        self.assertNotIn('write-0',self.calls())

    def test_two_corrections_then_block(self):
        service=self.service(failures=3);run=self.start(service)
        state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(state['correction_count'],2)
        self.assertEqual([v for v in self.calls() if v.startswith('write')],['write-0','write-1','write-2'])
        self.assertNotIn('assets',self.calls())

    def test_correction_success_is_fully_rereviewed(self):
        service=self.service(failures=1);run=self.start(service)
        state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'awaiting_parent_approval')
        self.assertEqual(state['correction_count'],1)
        self.assertIn('review-1',self.calls())
        self.assertEqual(state['review']['draft_hash'],state['draft_hash'])

    def test_critical_safety_blocks_without_automatic_rewrite(self):
        service=self.service(safety='FAIL');run=self.start(service)
        state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(state['correction_count'],0)
        with self.assertRaises(ValueError):service.approve(run,'parent1','a'*64)

    def test_pending_check_does_not_pass(self):
        service=self.service(safety='PENDING',failures=3);run=self.start(service)
        state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(state['correction_count'],0)

    def test_missing_assets_cannot_be_approved(self):
        service=self.service(assets_missing=True);run=self.start(service)
        self.assertEqual(service.execute(run,'parent1')['status'],'assets_pending')
        with self.assertRaises(ValueError):service.approve(run,'parent1','a'*64)

    def test_narration_request_does_not_fake_audio(self):
        service=self.service();run=self.start(service,request(narration_requested=True))
        self.assertEqual(service.execute(run,'parent1')['status'],'awaiting_clarification')
        self.assertEqual(self.calls(),[])

    def test_source_withdrawn_during_parent_pause_blocks_render(self):
        service=self.service(with_evidence=True);run=self.start(service)
        state=service.execute(run,'parent1')
        service.adapters=replace(service.adapters,eligibility=lambda bundle:False)
        self.assertEqual(service.approve(run,'parent1',state['book_version_hash'])['status'],'failed')
        self.assertNotIn('render',self.calls())

    def test_forged_resume_id_cannot_bypass_parent_approval(self):
        from langgraph.types import Command
        service=self.service();run=self.start(service);service.execute(run,'parent1')
        with checkpoint_store(self.folder/'checkpoints.sqlite') as saver:
            with self.assertRaises(PermissionError):
                service._graph('parent1',saver).invoke(Command(resume=True),service._config(run))
        self.assertNotIn('render',self.calls())

    def test_wrong_reviewer_draft_is_rejected(self):
        service=self.service();original=service.adapters.review
        def bad(*args):return original(*args)|{'draft_hash':'f'*64}
        service.adapters=replace(service.adapters,review=bad)
        run=self.start(service)
        self.assertEqual(service.execute(run,'parent1')['status'],'failed')

    def test_resume_requires_original_adapter_version(self):
        service=self.service();run=self.start(service);service.execute(run,'parent1')
        service.adapters=replace(service.adapters,version='different')
        with self.assertRaises(ValueError):service.execute(run,'parent1')

    def test_unconfirmed_writer_operation_stops_without_duplicate_call(self):
        service=self.service();run=self.start(service)
        with service.repository.db() as db:
            db.execute('INSERT INTO operations VALUES(?,?,?,NULL)',(run,'write-0','started'))
        state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'outcome_unknown')
        self.assertNotIn('write-0',self.calls())
        self.assertEqual(service.execute(run,'parent1')['status'],'outcome_unknown')

    def test_bad_reviewer_quote_cannot_pass(self):
        service=self.service();original=service.adapters.review
        def bad(*args):
            report=original(*args)
            report['clarity']['evidence'][0]['quote']='This sentence was never written.'
            return report
        service.adapters=replace(service.adapters,review=bad)
        run=self.start(service)
        self.assertEqual(service.execute(run,'parent1')['status'],'failed')
        self.assertNotIn('assets',self.calls())

    def test_changed_page_image_identity_blocks_parent_review(self):
        service=self.service();original=service.adapters.assets
        def bad(*args):
            manifest=original(*args)
            manifest['manifest_hash']=None
            manifest['pages'][0]['asset']['character_ids']=['rama']
            return manifest
        service.adapters=replace(service.adapters,assets=bad)
        run=self.start(service)
        self.assertEqual(service.execute(run,'parent1')['status'],'failed')

    def test_exception_records_error_without_leaking_message(self):
        service=self.service()
        def fail(*args):raise RuntimeError('SECRET-CREDENTIAL-MUST-NOT-APPEAR')
        service.adapters=replace(service.adapters,write=fail)
        run=self.start(service);state=service.execute(run,'parent1')
        self.assertEqual(state['status'],'failed')
        self.assertNotIn('SECRET-CREDENTIAL',json.dumps(state))

    def test_process_death_reuses_confirmed_writer_result(self):
        script = '''
import os, sys
from pathlib import Path
from backend.workflows.service import WorkflowService
from test_stage3_workflows import adapters, request
folder, log = sys.argv[1:]
service=WorkflowService(folder,adapters(log=log),enabled=True)
run=service.start('parent1','request1',request().model_dump(mode='json'))
original=service.repository.save
def save(run_id, artifact_id, payload):
    result=original(run_id,artifact_id,payload)
    if artifact_id == 'draft0': os._exit(78)
    return result
service.repository.save=save
service.execute(run,'parent1')
'''
        env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parent)
        proc=subprocess.run([sys.executable,'-c',script,str(self.folder),str(self.log)],
                            cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,78,proc.stderr.decode(errors='replace'))
        service=self.service();run=self.start(service)
        self.assertEqual(service.execute(run,'parent1')['status'],'awaiting_parent_approval')
        self.assertEqual(self.calls().count('write-0'),1)


class RepositoryTests(unittest.TestCase):
    def test_immutable_artifact_and_unknown_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=RunRepository(folder)
            repo.save('run','artifact',{'text':'Original'})
            with self.assertRaises(ValueError):repo.save('run','artifact',{'text':'Changed'})
            with repo.db() as db:db.execute('INSERT INTO operations VALUES(?,?,?,NULL)',('run','op','started'))
            with self.assertRaises(UnknownOutcome):repo.operation('run','op',lambda:self.fail('Must not retry'))

    def test_duplicate_workers_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            with run_lock(folder,'run'):
                with self.assertRaises(RuntimeError):
                    with run_lock(folder,'run'):pass

    def test_error_reducer_deduplicates_and_rejects_conflicts(self):
        error=dict(error_id='e1',node='review',code='missing',parent_message='Missing evidence',retryable=False,trace_reference=None)
        self.assertEqual(merge_errors([error],[error]),[error])
        with self.assertRaises(ValueError):merge_errors([error],[error|{'retryable':True}])

    def test_attempt_reducer_versions_and_terminal_invariant(self):
        started=c.ProviderAttempt(attempt_id='a1',record_version=1,operation_id='o1',requested_mode='auto',
            role='writer',provider='groq',model_id='fixture',status='started').model_dump(mode='json')
        done=started|{'record_version':2,'status':'succeeded'}
        self.assertEqual(merge_attempts([started],[done]),[done])
        self.assertEqual(merge_attempts([done],[started]),[done])
        with self.assertRaises(ValueError):merge_attempts([done],[done|{'status':'failed'}])
        with self.assertRaises(ValueError):merge_attempts([done],[done|{'record_version':3,'status':'failed'}])


if __name__=='__main__':unittest.main()
