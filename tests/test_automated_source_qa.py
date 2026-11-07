"""Automated source approval is distinct from human records; no live calls."""
import json,tempfile,unittest
from pathlib import Path
import httpx
import tests.test_hosted_source_review as fixtures
from backend.ingestion.hosted_review import HostedSourceReview
from backend.ingestion.source_qa import SourceQAAgent,RoutedSourceJudge,InternalSourceReview,subject_hash
from backend.contracts.source_qa import SourceQAReport,SourceQACheck,SourceQuote,SOURCE_CHECKS
from backend.providers.router import ProviderRouter
from backend.providers.attempt_ledger import AttemptLedger
from tests.test_stage7_providers import SETTINGS


class AutomatedSourceQATests(unittest.TestCase):
    def setUp(self):
        self.h=fixtures.HostedSourceReviewTests();self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.extraction,_,_=self.h.capture();self.command=self.h.command(self.extraction)
        self.registry=HostedSourceReview(self.h.folder/'reviews',self.h.workspace,self.h.settings,
            can_manage=lambda *args:False,automated_authorize=lambda *args:True,rights_authorize=lambda *args:True)
        self.document=self.registry._extraction('owner1',self.extraction)[2]

    def report(self,status='PASS',quote=None):
        check=SourceQACheck(status=status,reason='Synthetic automated source QA only.',quotes=(SourceQuote(pdf_page=1,quote=quote or self.h.text),))
        return SourceQAReport(subject_hash=subject_hash(self.extraction,self.command),reviewer_version='source-qa-v1',**dict.fromkeys(SOURCE_CHECKS,check))

    def test_automated_pass_creates_no_human_review(self):
        key=self.registry.qualify_automated('owner1',self.command,self.report())
        manifest=self.registry.eligible('owner1',self.extraction)
        self.assertEqual(manifest.passages[0].reference.review_record_ids,('automated-qa-'+key,))
        with self.registry.db() as db:
            self.assertEqual(db.execute('select count(*) from qualifications').fetchone()[0],0)
            report=json.loads(db.execute('select report from automated_reviews').fetchone()[0])
            self.assertEqual(report['review_origin'],'automated_qa')
        self.registry.qualify_automated('owner1',self.command,self.report())
        self.registry.assert_current(manifest)

    def test_fail_or_pending_blocks_eligibility(self):
        for verdict in ('FAIL','PENDING'):
            with self.assertRaises(PermissionError):self.registry.qualify_automated('owner1',self.command,self.report(verdict))
        with self.assertRaises(PermissionError):self.registry.eligible('owner1',self.extraction)

    def test_fabricated_quote_and_wrong_version_rejected(self):
        with self.assertRaises(ValueError):self.registry.qualify_automated('owner1',self.command,self.report(quote='Invented page text'))
        wrong=self.report().model_copy(update={'reviewer_version':'other-version'})
        with self.assertRaises(ValueError):self.registry.qualify_automated('owner1',self.command,wrong)

    def test_rights_and_internal_authority_are_separate_and_rechecked(self):
        self.registry.rights_authorize=lambda *args:False
        with self.assertRaises(PermissionError):self.registry.qualify_automated('owner1',self.command,self.report())
        self.registry.rights_authorize=lambda *args:True
        self.registry.qualify_automated('owner1',self.command,self.report())
        manifest=self.registry.eligible('owner1',self.extraction)
        self.registry.automated_authorize=lambda *args:False
        with self.assertRaises(PermissionError):self.registry.assert_current(manifest)

    def test_agent_uses_distinct_prompt_and_source_judge_role(self):
        calls=[]
        def handler(req):
            data=json.loads(req.content);calls.append(data)
            self.assertIn('internal Source QA Agent',data['messages'][0]['content'])
            return httpx.Response(200,json=dict(model=data['model'],choices=[dict(message=dict(content=self.report().model_dump_json()),finish_reason='stop')],usage=dict(prompt_tokens=20,completion_tokens=20)))
        clients={name:httpx.Client(transport=httpx.MockTransport(handler)) for name in SETTINGS}
        for client in clients.values():self.addCleanup(client.close)
        router=ProviderRouter(SETTINGS,AttemptLedger(self.h.folder/'source-qa-ledger'),enabled=True,clients=clients,sleep=lambda _:None)
        self.addCleanup(router.close)
        judge=RoutedSourceJudge(router,owner='owner1',authorize=lambda *args:True)
        agent=SourceQAAgent(judge=judge,authorize=lambda *args:True)
        result=InternalSourceReview(agent,candidates=lambda *args:(self.command,))('owner1',self.extraction,self.registry)
        self.assertEqual(result[0]['status'],'PASS');self.assertEqual(result[0]['review_origin'],'automated_qa')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['model'],SETTINGS['groq'].model('reviewer'))

    def test_batch_is_bounded_before_judge_call(self):
        calls=[]
        agent=SourceQAAgent(judge=lambda *args:calls.append(args),authorize=lambda *args:True)
        batch=InternalSourceReview(agent,candidates=lambda *args:(self.command,)*6)
        with self.assertRaises(ValueError):batch('owner1',self.extraction,self.registry)
        self.assertEqual(calls,[])

    def test_failed_and_pending_reports_are_preserved_without_eligibility(self):
        for status in ('FAIL','PENDING'):
            with self.assertRaises(PermissionError):self.registry.qualify_automated('owner1',self.command,self.report(status))
        with self.registry.db() as db:
            self.assertEqual(db.execute('select count(*) from automated_review_attempts').fetchone()[0],2)
            self.assertEqual(db.execute('select count(*) from automated_reviews').fetchone()[0],0)
