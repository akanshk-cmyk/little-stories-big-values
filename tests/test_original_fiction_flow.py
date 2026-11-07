"""Current account/LangGraph/provider path with controlled HTTP, not live quality scores."""
import json,tempfile,unittest,os
from types import SimpleNamespace
from unittest.mock import Mock,patch
import httpx
from evaluation.hhh_v1.offline import OfflineApp
from backend import contracts as c
from backend.original_fiction import FICTION_IDS,SACRED_IDS,ORIGINAL_OBJECTIVES,prepare_original,resolve_story_intent
from backend.contracts.compact_story import CompactStory,expand_story


class OriginalApp(OfflineApp):
    def __init__(self,folder,fault='none'):
        self.requests=[]
        super().__init__(folder,fault)
        bridge=self.service.adapters.base
        caps=c.PlaybookCapabilities(character_ids=(*FICTION_IDS,*SACRED_IDS),objectives=ORIGINAL_OBJECTIVES,story_types=('original_inspired',))
        bridge.capabilities=bridge.base.capabilities=caps
        bridge.base.version='original-fiction-controlled-v1'
        bridge.base.prepare=prepare_original
        self.service.adapters.evidence_resolver=lambda owner,req,options:prepare_original(req)

    def respond(self,http_request):
        self.fixture_exchanges+=1
        envelope=json.loads(http_request.content);payload=json.loads(envelope['messages'][1]['content'])
        self.requests.append((envelope,payload))
        if 'draft' in payload:
            draft=payload['draft'];quote=draft['pages'][0]['text'].split('.')[0]+'.'
            checks={name:dict(status='FAIL' if self.fault=='safety' and name=='critical_safety' else 'PASS',reason='Controlled protocol fixture, not a real content judgment.',page=1,quote=quote,source_quote=None) for name in ('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety')}
            result=dict(draft_hash=draft['draft_hash'],evidence_bundle_hash=None,reviewer_version=payload['reviewer_version'],**checks)
        else:
            moments=[
                'Bun the rabbit carried a basket of apples into the garden. Pip the bird sat beside an empty bowl. Bun noticed the little bowl and wondered how they could enjoy a snack together.',
                'Bun set the basket on the grass and asked Pip to join him. Pip smiled and hopped closer to the apples. Together they looked for a clean place to put their afternoon snack.',
                'Pip brought a broad green leaf to use as a little plate. Bun placed an apple beside the leaf and waited for his friend. They took their time and made a cheerful picnic spot.',
                'Bun offered Pip the first apple and kept another for himself. Pip thanked Bun for remembering his empty bowl. The friends discovered that sharing could make their small picnic feel even more special.',
                'After their snack, Pip offered to help Bun carry the empty basket home. Bun held one side while Pip carefully guided the other. They moved slowly together and talked about their lovely garden picnic.',
                'At home, Bun placed the basket ready for another day. Pip rested nearby and remembered how warmly his friend had welcomed him. They smiled, glad that a simple shared snack had brought them closer.'
            ]
            result=dict(title='Bun and Pip Share a Picnic',pages=[dict(text=t,character_ids=['original-main','original-friend-1']) for t in moments],citations=[],moral='Sharing a little can bring friends closer.',parent_question='What could we share with a friend?')
        return httpx.Response(200,json=dict(model=envelope['model'],choices=[dict(message=dict(content=json.dumps(result)),finish_reason='stop')]))

    def request(self,**updates):
        return dict(child_profile_id=self.child,age=5,required_character_ids=['original-main'],objective='Sharing',language='English',target_minutes=3,mood='Calm bedtime',story_type='original_inspired',idea='Bun the rabbit and Pip the bird share apples in a garden.',**updates)


class OriginalFictionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=OriginalApp(self.temp.name);self.addCleanup(self.app.close)

    def start(self):
        app=self.app
        run=app.service.start(app.owner,'original1',app.request(),generation_mode='provider_synthetic',provider_mode='groq')
        return run,app.service.execute(run,app.owner)

    def test_source_free_generation_qa_approval_and_library(self):
        app=self.app
        with patch('backend.local_ramayan_test.source',side_effect=AssertionError('No Ramayan retrieval for fiction')):
            run,state=self.start()
        self.assertEqual(state['status'],'awaiting_parent_approval',state)
        review=app.service.review_book(run,app.owner)
        self.assertTrue(review['approval_ready'])
        self.assertEqual(review['draft']['fiction_disclosure'],'original teaching-inspired fiction')
        self.assertIsNone(review['context']['evidence'])
        self.assertIsNone(state['evidence_bundle_id'])
        self.assertIn('No Ramayan passages were used.',review['html'])
        self.assertIn('Original teaching-inspired fiction',review['html'])
        self.assertNotIn('Ramayan story · Text edition',review['html'])
        self.assertFalse(app.ledger.library(app.owner))
        with self.assertRaises(PermissionError):app.service.pdf(run,app.owner,review['book_hash'])
        done=app.service.decide(run,app.owner,dict(action='approve',book_hash=review['book_hash'],request_key='save1'))
        self.assertEqual(done['status'],'complete')
        self.assertEqual(len(app.ledger.library(app.owner)),1)
        self.assertEqual(app.ledger.balance(app.owner)['starter_completed'],1)
        self.assertEqual(len(app.requests),2)
        self.assertTrue(all(p['context']['evidence'] is None for _,p in app.requests))
        self.assertIn('original teaching-inspired fiction',app.requests[0][0]['messages'][0]['content'])
        self.assertEqual(app.requests[0][1]['source_quote_bank'],[])
        self.assertNotIn('Provide 2-3 citations',app.requests[0][1]['compact_response_instruction'])

    def test_correction_requires_new_qa_and_new_parent_approval(self):
        run,state=self.start();app=self.app
        first=app.service.review_book(run,app.owner)
        app.service.decide(run,app.owner,dict(action='request_changes',book_hash=first['book_hash'],request_key='change1',notes='Keep the ending warm and gentle.'))
        app.service.submit(run,app.owner).result(timeout=20)
        revised=app.service.review_book(run,app.owner)
        self.assertTrue(revised['approval_ready']);self.assertNotEqual(first['book_hash'],revised['book_hash'])
        with self.assertRaises(ValueError):app.service.decide(run,app.owner,dict(action='approve',book_hash=first['book_hash'],request_key='stale1'))
        self.assertFalse(app.ledger.library(app.owner))

    def test_critical_safety_still_blocks(self):
        self.app.fault='safety'
        run,state=self.start()
        review=self.app.service.review_book(run,self.app.owner)
        self.assertFalse(review['approval_ready']);self.assertNotIn('draft_preview',review)
        self.assertFalse(self.app.ledger.library(self.app.owner))

    def test_blank_description_is_rejected_without_model_calls(self):
        req=self.app.request();req['idea']=' '
        with self.assertRaisesRegex(ValueError,'Describe the original story'):self.app.service.start(self.app.owner,'empty',req,generation_mode='provider_synthetic')
        self.assertEqual(self.app.fixture_exchanges,0)

    def test_sacred_selection_is_fiction_not_source_review(self):
        req=self.app.request();req['required_character_ids']=['krishna'];req=c.parse_payload(c.StoryRequest,req)
        result=prepare_original(req)
        self.assertIsNone(result['evidence']);self.assertEqual(len(result['characters']['characters']),8)
        self.assertEqual(result['characters']['characters'][0]['character_id'],'krishna')

    def test_missing_evidence_never_allows_a_retelling(self):
        req=self.app.request();req['story_type']='traditional_retelling'
        with self.assertRaises(ValueError):self.app.service.start(self.app.owner,'traditional',req,generation_mode='provider_synthetic')
        self.assertEqual(self.app.fixture_exchanges,0)

    def test_preflight_and_capability_advertise_original_fiction(self):
        actor=dict(account_id=self.app.owner)
        caps,_=self.app.api.handle('GET',['api','accounts','capabilities'],actor)
        self.assertTrue(caps['original_fiction_enabled'])
        result,status=self.app.api.handle('POST',['api','accounts','preflight'],actor,self.app.request())
        self.assertEqual((status,result['status']),(200,'READY'));self.assertFalse(result['candidate_ids'])

    @unittest.skipUnless(os.environ.get('BALA_TEST_PDF')=='1','Optional installed browser PDF export')
    def test_actual_pdf_after_parent_approval(self):
        run,state=self.start();app=self.app
        review=app.service.review_book(run,app.owner)
        app.service.decide(run,app.owner,dict(action='approve',book_hash=review['book_hash'],request_key='pdf-save'))
        result=app.service.pdf(run,app.owner,review['book_hash'])
        self.assertTrue(result.startswith(b'%PDF-'))
