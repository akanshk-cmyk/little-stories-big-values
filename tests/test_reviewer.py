"""Reviewer mechanics only; not story-quality validation or the user's eval set."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from backend.original_story_engine import make_original_book
from backend.reviewer_agent import configuration, evidence_bundle, run_review, validate_report
from backend.festival_engine import UserError

class ReviewerMechanics(unittest.TestCase):
    def setUp(self):
        book=make_original_book({'mode':'original','character':'krishna','objective':'Kindness','age':5,'language':'English','duration':3},'example')
        self.record={'id':'test-artifact','version':1,'digest':'test-version-hash','approved':False,'book':book}
        self.inventory={'parentApproved':False,'pdf':{'exists':False},'audioDurationMeasured':False}
        self.bundle=evidence_bundle(self.record,self.inventory)
        self.prompt,self.playbook,self.prompt_hash,self.playbook_hash=configuration()
    def fixture_report(self):
        # Structurally conforming mocked report; this is not a real model PASS.
        ref={'artifact_id':'book','pointer':'/fictionDisclosure','quote':self.record['book']['fictionDisclosure']}
        return {'verdict':'PASS','summary':'Mocked structure for mechanical checks only.','coverage':self.bundle['required_coverage'],
          'dimensions':[{'name':name,'status':'PASS','note':'Mock finding.','evidence':[ref]} for name in self.playbook['dimensions']],
          'rules':[{'rule_id':rule['id'],'status':'PASS','note':'Mock finding.','evidence':[ref]} for rule in self.playbook['rules']],
          'claims':[{'artifact_pointer':f'/pages/{index}','claim':text,'kind':'FICTIONAL','decision':'FICTIONAL','note':'Labeled imagined narrative.','evidence':[ref]} for index,text in enumerate(self.record['book']['pages'])],
          'revisions':[]}
    def test_complete_mocked_structure_can_pass_mechanical_checks(self):
        self.assertEqual(validate_report(self.fixture_report(),self.bundle,self.playbook)['verdict'],'PASS')
    def test_invented_quote_or_pointer_cannot_pass(self):
        for ref in ({'artifact_id':'book','pointer':'/fictionDisclosure','quote':'fabricated quote'}, {'artifact_id':'made-up-source','pointer':'/fact','quote':'anything'}):
            report=self.fixture_report();report['claims'][0]['evidence']=[ref]
            result=validate_report(report,self.bundle,self.playbook);self.assertEqual(result['verdict'],'REVISION_REQUIRED')
    def test_missing_coverage_rule_or_dimension_cannot_pass(self):
        for key in ('coverage','rules','dimensions'):
            report=self.fixture_report();report[key]=report[key][:-1]
            self.assertEqual(validate_report(report,self.bundle,self.playbook)['verdict'],'REVISION_REQUIRED')
    def test_self_citation_and_unknown_claims_cannot_pass(self):
        report=self.fixture_report();claim=report['claims'][0];claim['kind']='FACTUAL';claim['decision']='SUPPORTED'
        self.assertEqual(validate_report(report,self.bundle,self.playbook)['verdict'],'REVISION_REQUIRED')
        claim['decision']='NOT_CHECKABLE';claim['evidence']=[]
        self.assertEqual(validate_report(report,self.bundle,self.playbook)['verdict'],'REVISION_REQUIRED')
    def test_failed_or_unresolved_dimension_cannot_be_overruled(self):
        for state in ('FAIL','UNRESOLVED'):
            report=self.fixture_report();report['dimensions'][0]['status']=state
            self.assertEqual(validate_report(report,self.bundle,self.playbook)['verdict'],'REVISION_REQUIRED')
    def test_separate_prompt_and_saved_evidence_not_writer_conversation(self):
        response={'choices':[{'message':{'content':json.dumps(self.fixture_report())}}],'usage':{'total_tokens':123}}
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'test-only','NEBIUS_REVIEW_MODEL':'review-model-fixture'}),patch('backend.reviewer_agent.provider_json',return_value=(json.dumps(response).encode(),'application/json')) as provider:
            result=run_review(self.record,self.inventory)
        payload=provider.call_args.args[1]
        self.assertEqual(len(payload['messages']),2)
        self.assertEqual(payload['messages'][0]['content'],self.prompt)
        self.assertEqual(payload['model'],'review-model-fixture')
        self.assertIn('playbook',json.loads(payload['messages'][1]['content']))
        self.assertEqual(result['bundle']['book_digest'],'test-version-hash')
        self.assertEqual(result['promptHash'],self.prompt_hash)
        self.assertNotIn('NEBIUS_API_KEY',payload['messages'][1]['content'])
    def test_missing_connection_does_not_fabricate_a_report(self):
        with patch.dict(os.environ,{'NEBIUS_API_KEY':''}):
            with self.assertRaises(UserError) as caught:run_review(self.record,self.inventory)
        self.assertEqual(caught.exception.status,503)
