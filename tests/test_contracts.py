"""Stage 2 synthetic data/compatibility checks. Never runs a model or held dataset."""
import copy
from datetime import datetime, timedelta, timezone
import itertools
import json
import math
import unittest

from pydantic import ValidationError
from backend import contracts as c
from backend.contracts import common
from backend.contracts.legacy import (LegacyOriginalRequest, LegacyFestivalRequest, LegacyStoryContent,
    LegacyGenerationEnvelope, legacy_review_to_contract, project_story_content)


def request(**updates):
    data={'child_profile_id':'child_mira','age':5,'required_character_ids':('krishna','mira'),
          'objective':'Kindness','target_minutes':3,'mood':'Calm bedtime','story_type':'original_inspired'}
    return c.StoryRequest(**(data|updates))


def characters():
    return c.CharacterManifest(manifest_id='cast1',characters=tuple(
        c.CharacterDesign(character_id=id,design_version='v1',appearance='Synthetic design.',clothing='Approved outfit.',
                          identifying_details=('Fixture only.',)) for id in ('krishna','mira')))


def evidence(req=None, **updates):
    req=req or request()
    text='Mira shares paper flowers with a friend.'
    ref=c.SourceReference(source_id='source1',snapshot_id='snapshot1',edition_id='edition1',chunk_id='chunk1',
        parent_section_id='section1',pdf_pages=(4,),content_hash=common.text_hash(text),review_record_ids=('review1',),
        eligibility_epoch=1,review_status='approved',eligibility_status='eligible',rights_status='permitted')
    passage=c.RankedPassage(reference=ref,text=text,index_version='index1',fused_rank=1,token_count=8)
    return c.EvidenceBundle(**({'bundle_id':'bundle1','request_hash':c.content_hash(req),'passages':(passage,),
        'index_version':'index1','retrieval_config_hash':'a'*64,'eligibility_epoch':1}|updates))


def draft(req=None, bundle=None, **updates):
    req=req or request()
    citation=c.ClaimCitation(chunk_id='chunk1',claim_text='share paper flowers',source_quote='Mira shares paper flowers',claim_kind='factual')
    pages=tuple(c.StoryPage(page_number=i,text=f'Krishna and Mira share paper flowers. Page {i}.',character_ids=('krishna','mira'),
        scene_description='Two friends share paper flowers.',accessible_description='Friends at a table.',
        citations=(citation,) if bundle else ()) for i in range(1,7))
    values={'draft_id':'draft1','request_hash':c.content_hash(req),'classification':'original_inspired',
        'fiction_disclosure':'These scenes are imagined fiction.','title':'Paper flowers','pages':pages,'moral':'Make room for others.',
        'parent_question':'What helped the friends?','evidence_bundle_id':bundle.bundle_id if bundle else None,
        'character_manifest_id':'cast1','prompt_version':'writer-v1'}
    return c.StoryDraft(**(values|updates))


def check(status='PASS'):
    return c.ReviewCheck(status=status,reason='Synthetic contract fixture, not actual content evaluation.',
        evidence=(c.EvidencePointer(artifact_id='book',pointer='/pages/0',quote='Synthetic text.'),) if status=='PASS' else ())


def review(**updates):
    values=dict.fromkeys(['factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety'],check())
    return c.ReviewReport(**({'draft_hash':'a'*64,'reviewer_version':'reviewer-v1'}|values|updates))


def book(**updates):
    return c.BookVersion(**({'book_id':'book1','version':1,'draft_id':'draft1','draft_hash':'a'*64,
        'character_manifest_id':'cast1','character_manifest_hash':'b'*64,'asset_manifest_id':'assets1',
        'asset_manifest_hash':'c'*64,'playbook_version':'playbook-v1'}|updates))


class RequestContracts(unittest.TestCase):
    def test_strict_request_json_roundtrip(self):
        req=request()
        self.assertEqual(c.StoryRequest.model_validate_json(req.model_dump_json()),req)
        payload=req.model_dump(mode='json');self.assertIsInstance(payload['required_character_ids'],list)
        self.assertEqual(c.parse_payload(c.StoryRequest,payload),req)

    def test_unknown_fields_and_scalar_coercion_rejected(self):
        for change in [{'unknown':'ignored?'},{'age':'5'},{'age':True},{'target_minutes':True},
                       {'narration_requested':'false'},{'age':2},{'age':9},{'target_minutes':4},{'language':'Hindi'}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):request(**change)

    def test_duplicate_empty_or_invalid_characters_rejected(self):
        for ids in [(),('krishna','krishna'),('has spaces',)]:
            with self.subTest(ids=ids),self.assertRaises(ValidationError):request(required_character_ids=ids)

    def test_unsupported_capabilities_not_silently_substituted(self):
        caps=c.PlaybookCapabilities(character_ids=('krishna','mira'),objectives=('Kindness',),story_types=('original_inspired',))
        self.assertEqual(c.validate_capabilities(request(),caps),request())
        for changed in [request(required_character_ids=('rama',)),request(objective='Courage'),request(story_type='traditional_retelling')]:
            with self.assertRaises(ValueError):c.validate_capabilities(changed,caps)

    def test_frozen_nested_fields_and_validated_replacement(self):
        req=request()
        with self.assertRaises(ValidationError):req.age=6
        with self.assertRaises(TypeError):req.required_character_ids[0]='rama'
        with self.assertRaises(ValidationError):req.model_copy(update={'age':'six'})
        self.assertEqual(req.model_copy(update={'age':6}).age,6)
        self.assertEqual(req.age,5)


class EvidenceContracts(unittest.TestCase):
    def test_bundle_seals_and_roundtrips_without_fabricated_scores(self):
        bundle=evidence()
        self.assertEqual(len(bundle.bundle_hash),64)
        self.assertIsNone(bundle.passages[0].rerank_score)
        self.assertEqual(c.EvidenceBundle.model_validate_json(bundle.model_dump_json()),bundle)

    def test_missing_rights_review_or_status_prevents_eligible_claim(self):
        payload=evidence().passages[0].reference.model_dump(mode='json')
        for change in [{'rights_status':'unknown'},{'review_status':'pending'},{'review_record_ids':[]}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):c.parse_payload(c.SourceReference,payload|change)

    def test_pdf_pages_and_printed_labels_are_not_invented(self):
        payload=evidence().passages[0].reference.model_dump(mode='json')
        self.assertIsNone(c.parse_payload(c.SourceReference,payload).printed_pages)
        for change in [{'pdf_pages':[0]},{'pdf_pages':[4,4]},{'pdf_pages':[5,4]},{'printed_pages':['iv','5']}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):c.parse_payload(c.SourceReference,payload|change)

    def test_edited_passage_hash_and_bundle_hash_rejected(self):
        payload=evidence().model_dump(mode='json')
        payload['passages'][0]['text']+=' Changed.'
        with self.assertRaises(ValidationError):c.parse_payload(c.EvidenceBundle,payload)
        with self.assertRaises(ValidationError):evidence().model_copy(update={'bundle_id':'another'})

    def test_mixed_index_or_eligibility_epoch_rejected(self):
        for change in [{'index_version':'index2'},{'eligibility_epoch':2}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):evidence(**change)

    def test_duplicate_context_and_passage_ids_rejected(self):
        b=evidence()
        for change in [{'passages':(b.passages[0],b.passages[0])},{'context_chunk_ids':('missing',)}]:
            with self.assertRaises(ValidationError):evidence(**change)

    def test_nonfinite_search_scores_rejected(self):
        for score in [math.nan,math.inf,-math.inf]:
            with self.assertRaises(ValidationError):c.SearchHit(reference=evidence().passages[0].reference,index_version='index1',retriever='dense',rank=1,score=score)


class StoryContracts(unittest.TestCase):
    def test_complete_draft_context_roundtrip(self):
        req=request();b=evidence(req);d=draft(req,b)
        self.assertEqual(c.validate_story_context(d,req,characters(),b),d)
        self.assertEqual(c.StoryDraft.model_validate_json(d.model_dump_json()),d)

    def test_missing_duplicate_and_reordered_pages_rejected(self):
        d=draft()
        for pages in [d.pages[:-1],(d.pages[0],)*6,tuple(reversed(d.pages))]:
            with self.assertRaises(ValidationError):draft(pages=pages)

    def test_blank_text_disclosure_and_fabricated_traditional_label_rejected(self):
        for change in [{'title':'  '},{'moral':''},{'fiction_disclosure':None},{'fiction_disclosure':'A story.'},
                       {'classification':'traditional_retelling'}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):draft(**change)

    def test_unknown_or_missing_declared_characters_rejected(self):
        req=request();d=draft()
        with self.assertRaises(ValueError):c.validate_story_context(d,req,c.CharacterManifest(manifest_id='cast1',characters=characters().characters[:1]))
        d=draft(pages=tuple(p.model_copy(update={'character_ids':('krishna',)}) for p in d.pages))
        with self.assertRaises(ValueError):c.validate_story_context(d,req,characters())

    def test_missing_requested_question_fact_or_activity_rejected(self):
        for required,change in [('parent_question',{'parent_question':None}),('cultural_fact',{}),('family_activity',{})]:
            req=request(required_elements=('moral',required))
            d=draft(req,**change)
            with self.assertRaises(ValueError):c.validate_story_context(d,req,characters())

    def test_claim_quote_and_evidence_binding_checked(self):
        req=request();b=evidence(req);d=draft(req,b)
        self.assertEqual(c.validate_story_context(d,req,characters(),b),d)
        bad=c.ClaimCitation(chunk_id='chunk1',claim_text='share paper flowers',source_quote='An invented quote.',claim_kind='factual')
        changed=draft(req,b,pages=(d.pages[0].model_copy(update={'citations':(bad,)}),)+d.pages[1:])
        with self.assertRaises(ValueError):c.validate_story_context(changed,req,characters(),b)
        with self.assertRaises(ValueError):c.validate_story_context(d,req,characters())
        with self.assertRaises(ValueError):c.validate_story_context(d,request(age=6),characters(),b)

    def test_claim_must_exist_on_its_page(self):
        with self.assertRaises(ValidationError):c.StoryPage(page_number=1,text='A different scene.',character_ids=(),
            scene_description='Scene.',accessible_description='Description.',citations=(c.ClaimCitation(chunk_id='c1',claim_text='not present',source_quote='source quote',claim_kind='factual'),))

    def test_static_assets_cannot_accept_runtime_generation_or_urls(self):
        data={'asset_id':'a1','version':'v1','file_hash':'a'*64,'media_type':'image/png',
              'origin':'approved_static_library','review_status':'approved','rights_status':'permitted',
              'character_ids':['krishna','mira'],'style':'Watercolor','accessible_description':'Friends share.'}
        asset=c.parse_payload(c.StaticAssetReference,data)
        for change in [{'url':'https://example.org/image.png'},{'origin':'generated'},{'review_status':'pending'}]:
            with self.assertRaises(ValidationError):c.parse_payload(c.StaticAssetReference,data|change)
        self.assertEqual(asset.file_hash,'a'*64)

    def test_page_asset_binding_rejects_wrong_image(self):
        d=draft();p=d.pages[0].model_copy(update={'selected_asset_id':'a1'});d=draft(pages=(p,)+d.pages[1:])
        with self.assertRaises(ValueError):c.validate_story_context(d,request(),characters())

    def test_text_and_asset_changes_invalidate_approval_hash(self):
        b=book();a=c.ParentApproval(approval_id='a1',authenticated_parent_id='parent1',approval_kind='final_book',
            book_version_hash=b.book_version_hash,review_report_id='review1',policy_version='policy1',approved_at=datetime.now(timezone.utc))
        self.assertEqual(c.validate_approval_binding(a,b,parent_id='parent1',policy_version='policy1'),a)
        for changed in [book(version=2,draft_hash='d'*64),book(asset_manifest_hash='e'*64),book(voice_settings_hash='f'*64)]:
            with self.assertRaises(ValueError):c.validate_approval_binding(a,changed,parent_id='parent1',policy_version='policy1')
        with self.assertRaises(ValueError):c.validate_approval_binding(a,b,parent_id='other',policy_version='policy1')
        with self.assertRaises(ValueError):c.validate_approval_binding(a,b,parent_id='parent1',policy_version='new-policy')


class ReviewContracts(unittest.TestCase):
    def test_all_review_status_combinations_and_missing_safety(self):
        fields=['factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety']
        for statuses in itertools.product(('PASS','FAIL','PENDING'),repeat=6):
            r=review(**dict(zip(fields,map(check,statuses))))
            expected='FAIL' if 'FAIL' in statuses else 'PENDING' if 'PENDING' in statuses else 'PASS'
            self.assertEqual(r.overall_result,expected)
        data=review().model_dump(mode='json');del data['critical_safety']
        with self.assertRaises(ValidationError):c.parse_payload(c.ReviewReport,data)
        self.assertEqual(review(critical_safety=check('FAIL')).overall_result,'FAIL')

    def test_model_cannot_supply_its_own_overall_pass(self):
        with self.assertRaises(ValidationError):c.parse_payload(c.ReviewReport,review().model_dump(mode='json')|{'overall_result':'PASS'})
        with self.assertRaises(ValidationError):c.ReviewCheck(status='PASS',reason='No evidence.')
        self.assertEqual(review(validation_issues=('Unresolved schema check.',)).overall_result,'PENDING')

    def test_actual_pointer_and_quote_resolution(self):
        self.assertEqual(c.validate_review_evidence(review(),{'book':{'pages':['Synthetic text.']}}).overall_result,'PASS')
        for artifacts in [{},{'book':{'pages':[]}},{'book':{'pages':['Different text.']}}]:
            with self.assertRaises(ValueError):c.validate_review_evidence(review(),artifacts)

    def test_legacy_unresolved_and_missing_checks_never_become_pass(self):
        ref={'artifact_id':'book','pointer':'/pages/0','quote':'Synthetic text.'}
        data={'verdict':'PASS','dimensions':[{'name':name,'status':'PASS','note':'Fixture.','evidence':[ref]} for name in ('Factual Correctness','Clarity','Value / Insight','Engagement','Tone Fit')],
              'rules':[{'rule_id':'gentle_content','status':'PASS','note':'Old generic rule.','evidence':[ref]}]}
        preserved=copy.deepcopy(data)
        converted=legacy_review_to_contract(data,draft_hash='a'*64,reviewer_version='v1')
        self.assertEqual(converted.critical_safety.status,'PENDING')
        self.assertEqual(converted.overall_result,'PENDING');self.assertEqual(data,preserved)
        data['rules'].append({'rule_id':'critical_safety','status':'PASS','note':'Explicit fixture.','evidence':[ref]})
        self.assertEqual(legacy_review_to_contract(data,draft_hash='a'*64,reviewer_version='v1').overall_result,'PASS')
        data['dimensions'][0]['status']='UNRESOLVED'
        self.assertEqual(legacy_review_to_contract(data,draft_hash='a'*64,reviewer_version='v1').factual_correctness.status,'PENDING')


class OperationAndBillingContracts(unittest.TestCase):
    def test_all_public_models_have_closed_json_schemas(self):
        for model in c.PUBLIC_MODELS:
            with self.subTest(model=model.__name__):
                schema=model.model_json_schema()
                self.assertFalse(schema['additionalProperties'])
        self.assertEqual(len(c.PUBLIC_MODELS),21)

    def test_provider_unknown_usage_is_not_zero_and_bad_counts_rejected(self):
        data={'attempt_id':'attempt1','record_version':1,'operation_id':'op1','requested_mode':'auto','role':'writer',
              'provider':'groq','model_id':'configured-model','status':'outcome_unknown'}
        attempt=c.ProviderAttempt(**data)
        self.assertIsNone(attempt.input_tokens);self.assertIsNone(attempt.latency_ms)
        for change in [{'input_tokens':-1},{'latency_ms':'20'},{'provider':'other'},{'api_key':'forbidden'}]:
            with self.assertRaises(ValidationError):c.ProviderAttempt(**(data|change))

    def test_workflow_status_validation_is_not_graph_execution(self):
        self.assertEqual(c.validate_transition('queued','validating'),'validating')
        for a,b in [('queued','approved'),('complete','writing'),('reviewing','complete'),('bogus','writing')]:
            with self.assertRaises(ValueError):c.validate_transition(a,b)
        self.assertEqual(c.validate_transition('writing','outcome_unknown'),'outcome_unknown')

    def test_simulated_pass_exact_price_cap_duration_and_no_real_financial_fields(self):
        start=datetime(2026,11,3,tzinfo=timezone.utc)
        data={'pass_id':'pass1','account_id':'account1','is_simulated':True,'price_cents':500,'currency':'USD',
              'story_limit':10,'stories_used':0,'starts_at':start,'expires_at':start+timedelta(days=7)}
        good=c.SimulatedWeeklyPass(**data)
        self.assertEqual(c.SimulatedWeeklyPass.model_validate_json(good.model_dump_json()),good)
        for change in [{'stories_used':11},{'price_cents':500.0},{'story_limit':1},{'auto_renew':True},{'is_simulated':1},
                       {'card_number':'never accept'},{'expires_at':start+timedelta(days=8)},{'starts_at':start.replace(tzinfo=None)}]:
            with self.subTest(change=change),self.assertRaises(ValidationError):c.SimulatedWeeklyPass(**(data|change))

    def test_canonical_hash_is_order_independent_and_newline_sensitive(self):
        self.assertEqual(c.content_hash({'a':1,'b':2}),c.content_hash({'b':2,'a':1}))
        self.assertNotEqual(c.content_hash({'a':'one\ntwo'}),c.content_hash({'a':'one two'}))
        with self.assertRaises(ValueError):c.canonical_json({'score':math.nan})


class LegacyCompatibility(unittest.TestCase):
    def test_original_json_roundtrip_keeps_current_languages_and_field_names(self):
        from backend import original_story_engine as engine
        for character in engine.CHARACTERS:
            for objective in engine.OBJECTIVES:
                for language in ('English','Hindi'):
                    value={'mode':'original','character':character,'objective':objective,'age':5,'language':language,'duration':3}
                    normalized=engine.validate_original(value)
                    self.assertEqual(engine.validate_original(normalized),normalized)
                    self.assertEqual(normalized['character'],character)
                    self.assertEqual(normalized['language'],language)

    def test_festival_roundtrip_all_current_catalog_entries(self):
        from backend import festival_engine as engine
        for festival in engine.CATALOG:
            for objective in engine.VALUES:
                req={'festival':festival,'objective':objective,'age':5,'language':'English','duration':3}
                normalized=engine.validate_request(req)
                self.assertEqual(engine.validate_request(normalized),normalized)

    def test_saved_books_metadata_and_hash_bytes_are_not_rewritten(self):
        from backend.original_story_engine import make_original_book
        from backend.festival_engine import validate_story
        book=make_original_book({'mode':'original','character':'krishna','objective':'Sharing','age':5,'language':'English','duration':3},'example')
        before=c.canonical_json(book);before_hash=c.content_hash(book)
        projected=validate_story(book,'English')
        self.assertEqual(set(projected),{'title','pages','moral','discussionQuestion'})
        self.assertEqual(c.canonical_json(book),before);self.assertEqual(c.content_hash(book),before_hash)
        self.assertEqual(projected['pages'],book['pages'])

    def test_new_unknown_request_fields_and_unsupported_characters_rejected(self):
        from backend.original_story_engine import validate_original
        from backend.festival_engine import UserError,validate_request
        req={'mode':'original','character':'krishna','objective':'Sharing','age':5,'language':'English','duration':3}
        for change in [{'character':'rama'},{'unsupported':'not silently dropped'}]:
            with self.assertRaises(UserError):validate_original(req|change)
        f={'festival':'diwali','objective':'Sharing','age':5,'language':'English','duration':3}
        with self.assertRaises(UserError):validate_request(f|{'unknown':True})
        with self.assertRaises(ValidationError):LegacyGenerationEnvelope(mode='example',request=f,grantApproval=True)


if __name__=='__main__':unittest.main()
