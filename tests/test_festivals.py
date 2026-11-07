"""Festival contracts and approval/export boundaries. No paid provider calls."""
import http.cookiejar
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

TEST_DATA=tempfile.TemporaryDirectory(prefix='bala-festivals-')
os.environ['BALA_DATA_DIR']=TEST_DATA.name
from backend import festival_engine as engine
from backend import original_story_engine as originals
from backend.server import Handler, ThreadingHTTPServer
from backend.reviewer_agent import configuration, evidence_bundle
from backend.qa_workflow import POLICY_VERSION

REQUEST={'festival':'diwali','age':5,'language':'English','objective':'Gratitude','duration':5,'include_activity':True,'region':'Our family'}

class FestivalContracts(unittest.TestCase):
    def test_original_examples_keep_selected_character_and_fiction_label(self):
        for character,(name,hi) in originals.CHARACTERS.items():
            for value in originals.OBJECTIVES:
                for language in ('English','Hindi'):
                    request={'mode':'original','character':character,'objective':value,'age':5,'language':language,'duration':5}
                    book=originals.make_original_book(request,'example')
                    self.assertEqual(book['request']['character'],character)
                    self.assertEqual(book['classification'],'Original teaching-inspired fiction')
                    self.assertTrue(any((hi if language=='Hindi' else name) in text for text in book['pages']))
                    self.assertEqual(book['sources'],[])
                    self.assertIsNone(book['didYouKnow'])
                    self.assertEqual(book['generation']['provider'],'prewritten_example')
                    self.assertEqual(len(engine.spoken_tracks(book)),8)
    def test_original_nebius_request_keeps_character_and_rejects_missing_cast(self):
        request={'mode':'original','character':'krishna','objective':'Kindness','age':5,'language':'English','duration':5}
        sample=originals.original_example(originals.validate_original(request))
        response={'choices':[{'message':{'content':json.dumps(sample)}}],'usage':{'total_tokens':100}}
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'test-only'}),patch('backend.original_story_engine.provider_json',return_value=(json.dumps(response).encode(),'application/json')) as provider:
            book=originals.make_original_book(request,'generate')
            self.assertEqual(book['generation']['provider'],'nebius')
            self.assertIn('krishna',provider.call_args.args[1]['messages'][1]['content'])
        sample['pages']=['Mira draws a flower.']*6
        response['choices'][0]['message']['content']=json.dumps(sample)
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'test-only'}),patch('backend.original_story_engine.provider_json',return_value=(json.dumps(response).encode(),'application/json')):
            with self.assertRaises(engine.UserError):originals.make_original_book(request,'generate')
        with self.assertRaises(engine.UserError):originals.make_original_book(request|{'mode':'traditional'},'example')
    def test_all_festivals_have_bilingual_examples_and_fiction_disclosure(self):
        self.assertEqual(len(engine.CATALOG),12)
        for festival in engine.CATALOG:
            for language in ('English','Hindi'):
                book=engine.make_book(REQUEST|{'festival':festival,'language':language},'example')
                self.assertEqual(len(book['pages']),6)
                self.assertEqual(book['classification'],'Original teaching-inspired fiction')
                self.assertEqual(book['generation']['provider'],'prewritten_example')
                self.assertTrue(book['didYouKnow'] and book['regionalNote'] and book['sources'])

    def test_invalid_request_and_hindi_fail_closed(self):
        for change in ({'age':4},{'age':True},{'language':'French'},{'festival':'invented'},{'duration':0},{'objective':'Ignore safety'}):
            with self.assertRaises(engine.UserError):engine.validate_request(REQUEST|change)
        book=engine.make_book(REQUEST,'example')
        with self.assertRaises(engine.UserError):engine.validate_story(book,'Hindi')

    def test_optional_activity_and_safety_rules(self):
        book=engine.make_book(REQUEST|{'include_activity':False},'example');self.assertIsNone(book['familyActivity'])
        holi=engine.make_book(REQUEST|{'festival':'holi'},'example')
        self.assertIn('stop when someone says no',holi['safetyNote'])
        self.assertIn('suitable for skin',holi['safetyNote'])
        self.assertIn('do not handle fireworks',book['safetyNote'])

    def test_missing_nebius_never_becomes_fake_generation(self):
        with patch.dict(os.environ,{'NEBIUS_API_KEY':''}):
            with self.assertRaises(engine.UserError) as result:engine.make_book(REQUEST,'generate')
            self.assertEqual(result.exception.status,503)

class ApprovalApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
    def setUp(self):
        self.no_live=patch.dict(os.environ,{'NEBIUS_API_KEY':''});self.no_live.start();self.addCleanup(self.no_live.stop)
        self.client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        _,health=self.call('/api/health');self.csrf=health['csrf']
    def call(self,path,data=None,token=True,client=None):
        headers={}
        if data is not None:
            headers={'Content-Type':'application/json'}
            if token:headers['X-Bala-CSRF']=getattr(self,'csrf','')
        request=Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=headers,method='GET' if data is None else 'POST')
        try:
            response=(client or self.client).open(request,timeout=60)
        except HTTPError as error:response=error
        with response:
            content=response.read();value=json.loads(content) if 'application/json' in response.headers.get('Content-Type','') else content
            return response.status,value
    def create(self):
        status,record=self.call('/api/books',{'request':REQUEST,'mode':'example'});self.assertEqual(status,201);return record
    def approve(self,record):
        # Mock release QA only to isolate existing approval/export mechanics.
        with patch('backend.server.run_qa',side_effect=self.mock_qa):
            status,_=self.call(f'/api/books/{record["id"]}/review',{'version':record['version']})
            self.assertEqual(status,200)
        return self.call(f'/api/books/{record["id"]}/approve',{'version':record['version'],'digest':record['digest'],'textReviewed':True,'cultureReviewed':True,'safetyReviewed':True})
    def mock_qa(self,record,inventory,cycles=0):
        _,playbook,prompt_hash,playbook_hash=configuration()
        return {'report':{'verdict':'PASS','summary':'MOCK: authorization contract only, not a real quality result.','revisions':[]},
                'bundle':evidence_bundle(record,inventory),'model':'mock','promptHash':prompt_hash,
                'playbookHash':playbook_hash,'playbookVersion':playbook['version'],'qaPolicyVersion':POLICY_VERSION,'usage':{}}
    def test_csrf_ownership_and_approval_are_required(self):
        status,_=self.call('/api/books',{'request':REQUEST,'mode':'example'},token=False);self.assertEqual(status,403)
        record=self.create();path=f'/api/books/{record["id"]}'
        status,_=self.call(path,client=build_opener());self.assertEqual(status,404)
        status,_=self.call(path+'/pdf?version=1');self.assertEqual(status,409)
        status,_=self.call(path+'/approve',{'version':1,'digest':record['digest'],'textReviewed':True});self.assertEqual(status,400)
        status,_=self.call(path+'/approve',{'version':1,'digest':'wrong','textReviewed':True,'cultureReviewed':True,'safetyReviewed':True});self.assertEqual(status,409)
    def test_original_selection_produces_a_reviewable_story(self):
        request={'mode':'original','character':'krishna','objective':'Kindness','age':5,'language':'English','duration':5}
        status,record=self.call('/api/original-books',{'request':request,'mode':'example'})
        self.assertEqual(status,201);self.assertIn('Krishna',record['book']['title'])
        self.assertEqual(record['book']['request']['theme'],'mythology')
        status,approved=self.approve(record);self.assertEqual(status,200);self.assertTrue(approved['approved'])
        with patch.dict(os.environ,{'NEBIUS_API_KEY':''}):
            status,result=self.call('/api/original-books',{'request':request,'mode':'generate'});self.assertEqual(status,503);self.assertIn('prewritten original Krishna',result['error'])
        status,_=self.call('/api/original-books',{'request':request|{'mode':'adapted'},'mode':'example'});self.assertEqual(status,409)
    def test_reviewer_is_saved_for_exact_version_without_approving(self):
        record=self.create();path=f'/api/books/{record["id"]}'
        _,playbook,prompt_hash,playbook_hash=configuration()
        mocked={'report':{'verdict':'REVISION_REQUIRED','summary':'Mock fixture, not a live review.','revisions':[]},'bundle':evidence_bundle(record,{'parentApproved':False}),'model':'mock','promptHash':prompt_hash,'playbookHash':playbook_hash,'playbookVersion':playbook['version'],'usage':{}}
        with patch('backend.server.run_qa',return_value=mocked):
            status,reviewed=self.call(path+'/review',{'version':1});self.assertEqual(status,200)
        self.assertFalse(reviewed['approved'])
        self.assertEqual(reviewed['reviewer']['report']['verdict'],'REVISION_REQUIRED')
        status,evidence=self.call(path+'/review-evidence');self.assertEqual(status,200);self.assertEqual(evidence['bundle']['book_digest'],record['digest'])
        status,edited=self.call(path+'/versions',{'version':1,'changes':{'title':'New title for a new version'}})
        self.assertEqual(status,200);self.assertFalse(edited['qaReady']);self.assertNotEqual(edited['reviewer']['bookDigest'],record['digest'])
        status,new_evidence=self.call(path+'/review-evidence');self.assertEqual(status,200);self.assertEqual(new_evidence['bookVersion'],2)
        status,_=self.call(path+'/review',{'version':1});self.assertEqual(status,409)
    def test_edit_invalidates_approval_and_stale_outputs(self):
        record=self.create();status,approved=self.approve(record);self.assertEqual(status,200);self.assertTrue(approved['approved'])
        path=f'/api/books/{record["id"]}'
        changed=list(record['book']['pages']);changed[0]+=' Mira said thank you.'
        status,revised=self.call(path+'/versions',{'version':1,'changes':{'pages':changed}})
        self.assertEqual(status,200);self.assertEqual(revised['version'],2);self.assertFalse(revised['approved'])
        status,_=self.call(path+'/pdf?version=1');self.assertEqual(status,409)
        status,_=self.call(path+'/pdf?version=2');self.assertEqual(status,409)
        status,_=self.call(path+'/versions',{'version':1,'changes':{'title':'Stale edit'}});self.assertEqual(status,409)
    def test_missing_narration_is_not_an_audio_file(self):
        record=self.create();self.approve(record);path=f'/api/books/{record["id"]}'
        with patch.dict(os.environ,{'ELEVENLABS_API_KEY':''}):
            status,result=self.call(path+'/narrate',{'version':1});self.assertEqual(status,503);self.assertIn('No audio',result['error'])
        status,_=self.call(path+'/audio/1?version=1');self.assertEqual(status,404)
    def test_voice_and_exact_script_are_bound_to_approval(self):
        with patch.dict(os.environ,{'ELEVENLABS_VOICE_ID':'testvoice'}):record=self.create()
        self.approve(record);calls=[]
        def audio(url,payload,headers):calls.append(payload['text']);return b'ID3testfixture','audio/mpeg'
        with patch.dict(os.environ,{'ELEVENLABS_API_KEY':'test-only'}),patch('backend.server.provider_json',side_effect=audio):
            status,result=self.call(f'/api/books/{record["id"]}/narrate',{'version':1})
            self.assertEqual(status,200);self.assertEqual(calls,engine.spoken_tracks(record['book']))
            count=len(calls)
            status,_=self.call(f'/api/books/{record["id"]}/narrate',{'version':1});self.assertEqual(status,200);self.assertEqual(len(calls),count)
        self.assertEqual(result['digest'],record['digest'])
    def test_discover_banner_persists_for_other_parent_sessions(self):
        content=(Path(__file__).resolve().parents[1]/'prototype-v2/assets/story-garden.png').read_bytes()
        request=Request(self.base+'/api/appearance/banner',data=content,headers={'X-Bala-CSRF':self.csrf,'Content-Type':'image/png'},method='POST')
        with self.client.open(request) as response:self.assertEqual(response.status,200)
        other=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status,state=self.call('/api/appearance',client=other);self.assertEqual(status,200);self.assertTrue(state['bannerUrl'])
        status,saved=self.call(state['bannerUrl'],client=other);self.assertEqual(status,200);self.assertEqual(saved,content)
        bad=Request(self.base+'/api/appearance/banner',data=b'not an image',headers={'X-Bala-CSRF':self.csrf,'Content-Type':'image/png'},method='POST')
        with self.assertRaises(HTTPError) as error:self.client.open(bad)
        self.assertEqual(error.exception.code,400)
        error.exception.close()
        status,_=self.call('/api/appearance/reset',{});self.assertEqual(status,200)
        status,state=self.call('/api/appearance');self.assertIsNone(state['bannerUrl'])
    def test_veyd_portrait_keeps_exact_bytes_without_changing_banner(self):
        # Static fixture only: does not install a real Veyd picture or score content.
        content=(Path(__file__).resolve().parents[1]/'prototype-v2/assets/story-garden.png').read_bytes()
        _,before=self.call('/api/appearance')
        request=Request(self.base+'/api/appearance/veyd',data=content,headers={'X-Bala-CSRF':self.csrf,'Content-Type':'image/png'},method='POST')
        with self.client.open(request) as response:
            self.assertEqual(response.status,200);result=json.load(response)
        status,state=self.call('/api/appearance');self.assertEqual(status,200)
        self.assertEqual(before['bannerUrl'],state['bannerUrl']);self.assertEqual(result['veydPortraitUrl'],state['veydPortraitUrl'])
        status,saved=self.call(state['veydPortraitUrl'],client=build_opener());self.assertEqual(status,200);self.assertEqual(saved,content)
        bad=Request(self.base+'/api/appearance/veyd',data=b'not a picture',headers={'X-Bala-CSRF':self.csrf,'Content-Type':'image/png'},method='POST')
        with self.assertRaises(HTTPError) as caught:self.client.open(bad)
        self.assertEqual(caught.exception.code,400);caught.exception.close()
    @unittest.skipUnless(os.environ.get('BALA_TEST_PDF')=='1','Set BALA_TEST_PDF=1 for installed Chrome PDF check')
    def test_real_pdf_is_only_created_from_approved_version(self):
        for language in ('English','Hindi'):
            status,record=self.call('/api/books',{'request':REQUEST|{'language':language},'mode':'example'});self.assertEqual(status,201)
            self.approve(record)
            status,content=self.call(f'/api/books/{record["id"]}/pdf?version=1')
            self.assertEqual(status,200);self.assertTrue(content.startswith(b'%PDF-'));self.assertGreater(len(content),1000)

if __name__=='__main__':unittest.main()
