import unittest,json
from backend.contracts.compact_story import CompactStory,CompactStoryPage,CompactStoryCitation,expand_story
from pydantic import ValidationError

class CompactStoryTests(unittest.TestCase):
    def make(self,claim='Rama listened.'):
        page=CompactStoryPage(text='Rama listened. He heard the message with care and gave the visitor time to finish. His kind attention helped the visitor explain the offer of help clearly and calmly, without hurrying him.',character_ids=('rama',))
        citation=CompactStoryCitation(page_number=1,claim_text=claim,source_quote_id='q-source1')
        pages=tuple(page.model_copy(update={'text':page.text+' Moment '+str(i+1)+'.'}) for i in range(6))
        raw=CompactStory(title='Kind words',pages=pages,citations=(citation,),moral='Listen kindly.',parent_question='How can you listen kindly?')
        payload=dict(draft_id='draft1',request_hash='a'*64,request={'story_type':'adapted_retelling'},evidence_bundle_id='bundle1',character_manifest_id='cast1',prompt_version='writer-v4',source_quote_bank=[dict(source_quote_id='q-source1',chunk_id='source1',source_quote='heard his words')])
        return raw,payload

    def test_app_supplies_identity_without_changing_model_prose(self):
        raw,payload=self.make();story=expand_story(raw,payload)
        self.assertEqual(story.draft_id,'draft1');self.assertEqual(len(story.pages),6)
        self.assertEqual(story.pages[0].text,raw.pages[0].text)
        self.assertEqual(story.pages[0].accessible_description,raw.pages[0].text)

    def test_invalid_claim_is_not_repaired_or_approved_by_expansion(self):
        raw,payload=self.make('Absent words')
        with self.assertRaises(ValidationError):expand_story(raw,payload)

    def test_one_line_per_page_is_not_a_completed_story(self):
        raw,_=self.make();payload=raw.model_dump(mode='json')
        for index,page in enumerate(payload['pages']):page['text']='Rama listened kindly. '+str(index)
        with self.assertRaisesRegex(ValidationError,'One-line captions'):CompactStory.model_validate_json(json.dumps(payload))

    def test_repeated_page_text_is_rejected(self):
        raw,_=self.make();payload=raw.model_dump(mode='json')
        payload['pages'][1]['text']=payload['pages'][0]['text'].upper()
        with self.assertRaisesRegex(ValidationError,'Repeated page'):CompactStory.model_validate_json(json.dumps(payload))

    def test_line_wrapping_resolves_to_the_exact_existing_source(self):
        raw,payload=self.make();payload['source_quote_bank'][0]['source_quote']='heard\nhis words'
        story=expand_story(raw,payload)
        self.assertEqual(story.pages[0].citations[0].source_quote,'heard\nhis words')

    def test_unknown_source_quote_id_is_rejected(self):
        raw,payload=self.make();raw=raw.model_copy(update={'citations':(raw.citations[0].model_copy(update={'source_quote_id':'invented-id'}),)})
        with self.assertRaisesRegex(ValueError,'not in the frozen evidence'):expand_story(raw,payload)
