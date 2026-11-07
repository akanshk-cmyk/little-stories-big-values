"""Stage 2 HTTP compatibility checks using isolated local test sessions and stubs."""
import copy
import json
import unittest
from unittest.mock import patch
import test_festivals as fixtures
from backend import original_story_engine as original, festival_engine as festival
from backend.server import canonical, digest

REQUEST={'mode':'original','character':'krishna','objective':'Sharing','age':5,'language':'English','duration':3}


class ContractApiCompatibility(unittest.TestCase):
    setUpClass=fixtures.ApprovalApi.__dict__['setUpClass']
    tearDownClass=fixtures.ApprovalApi.__dict__['tearDownClass']
    setUp=fixtures.ApprovalApi.setUp
    call=fixtures.ApprovalApi.call

    def test_unknown_outer_fields_stop_before_generation(self):
        for endpoint in ('original-books','books'):
            with patch('backend.server.make_original_book') as original_call,patch('backend.server.make_book') as festival_call:
                status,result=self.call('/api/'+endpoint,{'request':REQUEST,'mode':'generate','grantApproval':True})
            self.assertEqual(status,400)
            self.assertIn('unknown or invalid',result['error'])
            original_call.assert_not_called();festival_call.assert_not_called()

    def test_valid_legacy_creation_and_reopen_keep_exact_json_and_digest(self):
        expected=original.make_original_book(REQUEST,'example')
        before=copy.deepcopy(expected)
        with patch('backend.server.check_qa'):
            status,record=self.call('/api/original-books',{'request':REQUEST,'mode':'example'})
        self.assertEqual(status,201)
        book=record['book'];self.assertEqual(book['pages'],before['pages'])
        self.assertEqual(book['classification'],before['classification'])
        self.assertFalse(record['approved']);self.assertFalse(record['qaReady'])
        self.assertEqual(record['digest'],digest(book))
        status,reopened=self.call('/api/books/'+record['id'])
        self.assertEqual(status,200);self.assertEqual(reopened['digest'],record['digest'])
        self.assertEqual(canonical(reopened['book']),canonical(book))

    def test_malformed_duration_and_unsupported_character_block_provider(self):
        for change in [{'duration':True},{'duration':'3'},{'character':'rama'},{'unknown':'ignored?'}]:
            with patch.object(original,'provider_json') as provider:
                status,_=self.call('/api/original-books',{'request':REQUEST|change,'mode':'generate'})
            self.assertEqual(status,400);provider.assert_not_called()

    def test_invalid_provider_story_returns_422_without_saving_a_book(self):
        invalid={'title':'Invalid','pages':['Only one page.'],'moral':'Share.','discussionQuestion':'What helped?'}
        response=json.dumps({'choices':[{'message':{'content':json.dumps(invalid)}}]}).encode()
        with patch.dict('os.environ',{'NEBIUS_API_KEY':'SYNTHETIC'}), \
             patch.object(original,'provider_json',return_value=(response,'application/json')), \
             patch('backend.server.create_book') as save:
            status,_=self.call('/api/original-books',{'request':REQUEST,'mode':'generate'})
        self.assertEqual(status,422);save.assert_not_called()

    def test_existing_csrf_and_approval_export_gates_are_not_bypassed(self):
        self.assertEqual(self.call('/api/original-books',{'request':REQUEST,'mode':'example'},token=False)[0],403)
        with patch('backend.server.check_qa'):
            _,record=self.call('/api/original-books',{'request':REQUEST,'mode':'example'})
        status,_=self.call('/api/books/'+record['id']+'/approve',{'version':record['version'],'digest':record['digest'],
            'textReviewed':True,'cultureReviewed':True,'safetyReviewed':True})
        self.assertEqual(status,409)
        self.assertEqual(self.call('/api/books/'+record['id']+'/pdf?version=1')[0],409)


if __name__=='__main__':unittest.main()
