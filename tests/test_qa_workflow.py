"""Release QA mechanics with mock providers only. Not the pending evaluation set."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from backend import qa_workflow as qa
from backend.festival_engine import make_book, UserError
from backend.original_story_engine import make_original_book
from backend.reviewer_agent import configuration, evidence_bundle
from test_festivals import ApprovalApi, REQUEST


class QAContracts(unittest.TestCase):
    def setUp(self):
        book=make_original_book({'mode':'original','character':'krishna','objective':'Kindness','age':5,'language':'English','duration':3},'example')
        # 300 words to isolate other checks, not a quality example or evaluation case.
        book['pages']=[' '.join(['Krishna and Mira share paper flowers.']*8)]*6
        self.record={'id':'fixture','version':1,'digest':'fixture-digest','approved':False,'book':book}
        self.inventory={'parentApproved':False}
    def response(self,verdict='PASS',revisions=None):
        _,pb,ph,bh=configuration()
        return {'report':{'verdict':verdict,'summary':'Mocked mechanics only.','revisions':revisions or [],'claims':[], 'rules':[], 'dimensions':[], 'validationNotes':[]},
                'bundle':evidence_bundle(self.record,self.inventory),'promptHash':ph,'playbookHash':bh,'playbookVersion':pb['version'],'usage':{},'model':'mock'}
    def test_missing_credentials_blocks_without_false_pass(self):
        with patch.dict(os.environ,{'NEBIUS_API_KEY':''}):result=qa.run_qa(self.record,self.inventory)
        self.assertEqual(result['report']['verdict'],'BLOCKED');self.assertFalse(result['report']['parentReviewReady'])
    def test_pending_festival_sources_block_without_calling_reviewer(self):
        self.record['book']=make_book(REQUEST,'example')
        with patch('backend.qa_workflow.run_review') as provider:result=qa.run_qa(self.record,self.inventory)
        provider.assert_not_called();self.assertEqual(result['report']['verdict'],'BLOCKED')
        self.assertIn('Cultural Knowledge Agent',result['report']['routes'])
    def test_duration_failure_cannot_be_overruled_by_model_pass(self):
        self.record['book']['pages']=['Mira smiles.']*6
        with patch('backend.qa_workflow.run_review',return_value=self.response()):result=qa.run_qa(self.record,self.inventory)
        self.assertEqual(result['report']['verdict'],'REVISION_REQUIRED')
        self.assertIn('Story Generation Agent',result['report']['routes'])
    def test_missing_preference_routes_to_intake(self):
        del self.record['book']['request']['age']
        with patch('backend.qa_workflow.run_review') as provider:result=qa.run_qa(self.record,self.inventory)
        provider.assert_not_called();self.assertIn('Story Intake Agent',result['report']['routes'])
    def test_correctable_failure_routes_and_stops_after_two_cycles(self):
        notes=[{'rule_id':'gentle_content','issue':'Mock safety issue','requested_change':'Remove unsafe action.'}]
        with patch('backend.qa_workflow.run_review',return_value=self.response('REVISION_REQUIRED',notes)):
            result=qa.run_qa(self.record,self.inventory,2)
        self.assertEqual(result['report']['verdict'],'BLOCKED')
        self.assertIn('Child Safety & Cultural Review Agent',result['report']['routes'])
        self.assertTrue(any('Two correction' in note for note in result['report']['validationNotes']))
    def test_routed_rewrite_uses_separate_role_and_preserves_evidence(self):
        fields={key:self.record['book'][key] for key in ('title','pages','moral','discussionQuestion')}
        response={'choices':[{'message':{'content':json.dumps(fields)}}],'usage':{'total_tokens':20}}
        report={'routes':['Child Safety & Cultural Review Agent'],'revisions':[{'route_to':'Child Safety & Cultural Review Agent','issue':'Mock check'}]}
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'fixture-only'}),patch('backend.qa_workflow.provider_json',return_value=(json.dumps(response).encode(),'application/json')) as provider:
            book=qa.revise_book(self.record,report)
        payload=provider.call_args.args[1];self.assertEqual(len(payload['messages']),2)
        self.assertIn('Child Safety & Cultural Review Agent',payload['messages'][0]['content'])
        self.assertEqual(book['request'],self.record['book']['request']);self.assertEqual(book.get('sourceEvidence'),self.record['book'].get('sourceEvidence'))
    def test_evidence_or_intake_gaps_do_not_trigger_writing(self):
        for agent in ('Story Intake Agent','Cultural Knowledge Agent'):
            with patch('backend.qa_workflow.provider_json') as provider:
                with self.assertRaises(UserError):qa.revise_book(self.record,{'routes':[agent]})
                provider.assert_not_called()


class QAGateApi(ApprovalApi):
    # Inherits transport setup; only run new tests here, not duplicate inherited ones.
    def test_unreviewed_and_revision_drafts_cannot_approve(self):
        record=self.create();path=f'/api/books/{record["id"]}'
        data={'version':1,'digest':record['digest'],'textReviewed':True,'cultureReviewed':True,'safetyReviewed':True}
        status,result=self.call(path+'/approve',data);self.assertEqual(status,409);self.assertIn('QA must PASS',result['error'])
        self.assertFalse(record['qaReady']);self.assertEqual(record['reviewer']['report']['verdict'],'BLOCKED')
    def test_previous_approval_cannot_bypass_failed_current_qa(self):
        record=self.create();status,_=self.approve(record);self.assertEqual(status,200)
        path=f'/api/books/{record["id"]}'
        status,_=self.call(path+'/review',{'version':1});self.assertEqual(status,200)
        for suffix in ('/pdf?version=1','/audio/0?version=1'):
            status,_=self.call(path+suffix);self.assertEqual(status,409)
        status,_=self.call(path+'/narrate',{'version':1});self.assertEqual(status,409)
    def test_new_version_has_its_own_qa_and_no_parent_approval(self):
        record=self.create();self.approve(record);path=f'/api/books/{record["id"]}'
        status,revised=self.call(path+'/versions',{'version':1,'changes':{'title':'Changed fixture'}})
        self.assertEqual(status,200);self.assertFalse(revised['qaReady']);self.assertFalse(revised['approved'])
        self.assertEqual(revised['reviewer']['bookVersion'],2);self.assertEqual(revised['reviewer']['bookDigest'],revised['digest'])
    def test_manual_corrections_cannot_reset_two_cycle_limit(self):
        record=self.create();path=f'/api/books/{record["id"]}'
        for version in (1,2):
            status,record=self.call(path+'/versions',{'version':version,'changes':{'title':f'Correction {version}'}})
            self.assertEqual(status,200)
        self.assertEqual(record['correctionCyclesUsed'],2)
        status,_=self.call(path+'/versions',{'version':3,'changes':{'title':'Third attempt'}});self.assertEqual(status,409)
        with patch('backend.server.run_qa') as provider:
            status,_=self.call(path+'/review',{'version':3});self.assertEqual(status,409);provider.assert_not_called()
    def test_correction_dispatch_saves_revision_and_reruns_full_qa(self):
        record=self.create();path=f'/api/books/{record["id"]}'
        def needs_fix(record,inventory,cycles=0):
            result=self.mock_qa(record,inventory,cycles)
            result['report'].update(verdict='REVISION_REQUIRED',routes=['Story Generation Agent'],revisions=[{'route_to':'Story Generation Agent','issue':'Fixture only'}])
            return result
        with patch('backend.server.run_qa',side_effect=needs_fix):self.call(path+'/review',{'version':1})
        revised=copy.deepcopy(record['book']);revised['title']='Corrected fixture'
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'fixture-only'}),patch('backend.server.revise_book',return_value=revised) as rewrite,patch('backend.server.run_qa',side_effect=self.mock_qa) as review:
            status,result=self.call(path+'/correct',{'version':1,'confirmPaid':True})
        self.assertEqual(status,200);self.assertEqual(result['version'],2);self.assertTrue(result['qaReady']);self.assertFalse(result['approved'])
        self.assertEqual(result['correctionCyclesUsed'],1);rewrite.assert_called_once();review.assert_called_once()


# Avoid unittest collecting inherited ApprovalApi checks a second time in this module.
for _name in list(ApprovalApi.__dict__):
    if _name.startswith('test_') and _name not in QAGateApi.__dict__:
        setattr(QAGateApi,_name,None)
del ApprovalApi
