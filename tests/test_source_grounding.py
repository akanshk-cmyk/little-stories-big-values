"""Source-to-writer integration tests. Synthetic fixtures, mocked model calls."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from urllib.request import build_opener

import test_source_ingestion as source_api
from backend import source_grounding as g, source_ingestion as intake
from backend import original_story_engine as original, festival_engine as festival
from backend.qa_workflow import deterministic_findings


REQUEST={'mode':'original','character':'krishna','objective':'Sharing','age':5,'language':'English',
         'duration':3,'idea':'Share paper flowers at the garden table.','mood':'Gentle daytime','exclusions':'No frightening content.'}


def source(text='Krishna and Veyd share paper flowers at the garden table.', id='1'*32):
    s=intake.document(text.encode(),'sharing.txt')|{'id':id,'createdAt':1}
    return g.review_source(s,{'action':'approve','textHash':s['textHash'],'textReviewed':True,'cultureReviewed':True},'test-parent',2)


def response(context,festival_case=False):
    quote=context['excerpts'][0]['text']
    story={'title':'Sharing flowers','pages':['Krishna and Veyd share paper flowers at the garden table.']*6,
           'moral':'Share kindly.','discussionQuestion':'How can we make room?',
           'sourceCitations':[{'page':1,'excerptId':context['excerpts'][0]['id'],
                                'claim':'share paper flowers','sourceQuote':quote}]}
    if festival_case:
        story['didYouKnow']=quote
        story['sourceCitations'].append({'page':0,'excerptId':context['excerpts'][0]['id'],'claim':quote,'sourceQuote':quote})
    return story


def provider(story):
    return json.dumps({'model':'synthetic-test','choices':[{'message':{'content':json.dumps(story)}}]}).encode(),'application/json'


class GroundingContracts(unittest.TestCase):
    def test_review_requires_exact_hash_and_explicit_checks(self):
        s=intake.document(b'A paper flower.','notes.txt')|{'id':'1'*32}
        for data in [{'action':'approve','textHash':'stale','textReviewed':True,'cultureReviewed':True},
                     {'action':'approve','textHash':s['textHash'],'textReviewed':True}]:
            with self.assertRaises(festival.UserError):g.review_source(s,data,'owner',2)
        approved=source()
        self.assertEqual(approved['reviewStatus'],'approved')
        self.assertFalse(approved['review']['expertCertification'])

    def test_pending_revoked_and_changed_sources_cannot_be_used(self):
        s=source()
        for changed in [dict(s,reviewStatus='pending_review'),
                        dict(s,sections=[{'location':'Paragraph 1','text':'Changed source text.'}]),
                        g.review_source(s,{'action':'revoke','textHash':s['textHash']},'owner',3)]:
            with self.assertRaises(festival.UserError):g.retrieve([changed],REQUEST)

    def test_chunks_have_300_word_targets_and_50_word_overlap(self):
        s=source(' '.join('flowers'+str(i) for i in range(600)))
        context=g.retrieve([s],dict(REQUEST,idea='flowers0 flowers250 flowers500'))
        pieces=sorted(context['excerpts'],key=lambda e:e['startWord'])
        self.assertEqual([p['startWord'] for p in pieces],[0,250,500])
        self.assertEqual([len(p['text'].split()) for p in pieces],[300,300,100])
        self.assertEqual(pieces[0]['text'].split()[-50:],pieces[1]['text'].split()[:50])
        self.assertEqual(pieces[0]['location'],'Paragraph 1')

    def test_irrelevant_source_is_an_error_not_a_silent_fallback(self):
        with self.assertRaisesRegex(festival.UserError,'No matching'):
            g.retrieve([source('Astronomy planets galaxies telescopes.')],REQUEST)

    def test_retrieval_is_bounded_and_preserves_provenance(self):
        context=g.retrieve([source(('Krishna paper flowers.\n\n')*9)],REQUEST)
        self.assertLessEqual(len(context['excerpts']),5)
        for e in context['excerpts']:
            self.assertEqual(e['sourceId'],'1'*32)
            self.assertEqual(e['reviewStatus'],'approved')
            self.assertIn('textHash',e)

    def test_actual_original_writer_receives_selected_text(self):
        context=g.retrieve([source()],REQUEST)
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'SYNTHETIC'}),patch.object(original,'provider_json',return_value=provider(response(context))) as call:
            book=original.make_original_book(REQUEST,'generate',context)
        payload=call.call_args.args[1]
        user=json.loads(payload['messages'][1]['content'])
        self.assertEqual(user['reviewedSourceExcerpts'],context['excerpts'])
        self.assertIn('untrusted reference DATA',payload['messages'][0]['content'])
        self.assertEqual(book['sourceEvidence'],context['excerpts'])
        self.assertEqual(book['sourceRetrieval']['method'],g.VERSION)
        self.assertEqual(book['classification'],'Original teaching-inspired fiction')
        self.assertIn('not expert',book['sourceReview'])

    def test_forged_missing_and_wrong_page_citations_are_rejected(self):
        context=g.retrieve([source()],REQUEST)
        for mutation in ('no_citations','made_up_id','false_quote','false_claim','wrong_page','bad_id_type'):
            output=response(context)
            if mutation=='no_citations':output.pop('sourceCitations')
            else:
                citation=output['sourceCitations'][0]
                citation.update({'made_up_id':{'excerptId':'fake'},'false_quote':{'sourceQuote':'invented quote'},
                                 'false_claim':{'claim':'not in this story'},'wrong_page':{'page':99},
                                 'bad_id_type':{'excerptId':[]}}[mutation])
            with patch.dict(os.environ,{'NEBIUS_API_KEY':'SYNTHETIC'}),patch.object(original,'provider_json',return_value=provider(output)):
                with self.assertRaises(festival.UserError):original.make_original_book(REQUEST,'generate',context)

    def test_samples_cannot_claim_to_use_uploaded_evidence(self):
        context=g.retrieve([source()],REQUEST)
        with self.assertRaises(festival.UserError):original.make_original_book(REQUEST,'example',context)

    def test_festival_writer_replaces_catalog_context_with_reviewed_source(self):
        req={'festival':'diwali','age':5,'language':'English','objective':'Sharing','duration':3,'include_activity':False}
        context=g.retrieve([source('Some families share paper flowers at Diwali.')],req)
        output=response(context,True)
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'SYNTHETIC'}),patch.object(festival,'provider_json',return_value=provider(output)) as call:
            book=festival.make_book(req,'generate',context)
        data=json.loads(call.call_args.args[1]['messages'][1]['content'])
        self.assertIsNone(data['festival_fact'])
        self.assertEqual(book['didYouKnow'],context['excerpts'][0]['text'])
        self.assertEqual(book['sourceEvidence'],context['excerpts'])

    def test_edits_breaking_citations_create_qa_findings(self):
        context=g.retrieve([source()],REQUEST)
        with patch.dict(os.environ,{'NEBIUS_API_KEY':'SYNTHETIC'}),patch.object(original,'provider_json',return_value=provider(response(context))):
            book=original.make_original_book(REQUEST,'generate',context)
        book['pages'][0]='A completely different scene.'
        findings=deterministic_findings(book)
        self.assertTrue(any(f['artifact_pointer']=='/sourceCitations' for f in findings))


class SourceGroundingApi(unittest.TestCase):
    setUpClass=source_api.SourceApi.__dict__['setUpClass']
    tearDownClass=source_api.SourceApi.__dict__['tearDownClass']
    setUp=source_api.SourceApi.setUp
    call=source_api.SourceApi.call
    upload=source_api.SourceApi.upload

    def test_review_csrf_ownership_and_stale_snapshot(self):
        _,s=self.upload()
        endpoint='/api/sources/'+s['id']+'/review'
        data={'action':'approve','textHash':s['textHash'],'textReviewed':True,'cultureReviewed':True}
        self.assertEqual(self.call(endpoint,data,token=False)[0],403)
        other=build_opener();_,health=self.call('/api/health',client=other)
        # A fresh cookie-aware client is needed for a second parent session.
        from urllib.request import HTTPCookieProcessor
        import http.cookiejar
        other=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        _,health=self.call('/api/health',client=other)
        old=self.csrf;self.csrf=health['csrf']
        self.assertEqual(self.call(endpoint,data,client=other)[0],404)
        self.csrf=old
        self.assertEqual(self.call(endpoint,dict(data,textHash='old'))[0],409)
        status,approved=self.call(endpoint,data)
        self.assertEqual(status,200);self.assertEqual(approved['review']['authority'],'parent')

    def test_pending_selection_stops_before_provider_call(self):
        _,s=self.upload(b'Krishna shares paper flowers.','source.txt')
        with patch('backend.server.make_original_book') as writer:
            status,_=self.call('/api/original-books',{'request':REQUEST,'mode':'generate','sourceIds':[s['id']]})
        self.assertEqual(status,409);writer.assert_not_called()

    def test_api_passes_owned_reviewed_context_to_same_writer(self):
        _,s=self.upload(b'Krishna shares paper flowers.','source.txt')
        self.call('/api/sources/'+s['id']+'/review',{'action':'approve','textHash':s['textHash'],'textReviewed':True,'cultureReviewed':True})
        with patch('backend.server.make_original_book') as writer,patch('backend.server.create_book',return_value={'id':'b'*32,'synthetic':True}):
            writer.return_value={'generation':{}}
            status,_=self.call('/api/original-books',{'request':REQUEST,'mode':'generate','sourceIds':[s['id']]})
        self.assertEqual(status,201)
        context=writer.call_args.args[2]
        self.assertEqual(context['sourceIds'],[s['id']])
        self.assertIn('Krishna shares paper flowers',context['excerpts'][0]['text'])
        self.assertTrue(self.call('/api/sources/'+s['id'])[1]['usedForGeneration'])

    def test_duplicate_and_other_parent_source_ids_are_rejected(self):
        _,s=self.upload()
        self.assertEqual(self.call('/api/original-books',{'request':REQUEST,'mode':'generate','sourceIds':[s['id'],s['id']]})[0],400)
        self.assertEqual(self.call('/api/original-books',{'request':REQUEST,'mode':'generate','sourceIds':['f'*32]})[0],404)
