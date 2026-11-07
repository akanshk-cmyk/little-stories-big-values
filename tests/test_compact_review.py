import unittest
from tests.test_contracts import draft,evidence,request
from backend.contracts.compact_review import CompactCheck,CompactReview,expand_review
from backend.contracts.reviews import validate_review_evidence


class CompactReviewTests(unittest.TestCase):
    def make(self,quote=None,source_quote=None):
        req=request();bundle=evidence(req);story=draft(req,bundle)
        check=CompactCheck(status='PASS',reason='Supported synthetic check.',page=1,quote=quote or story.pages[0].text,source_quote=None)
        factual=check.model_copy(update={'source_quote':source_quote or bundle.passages[0].text})
        report=CompactReview(draft_hash=story.draft_hash,evidence_bundle_hash=bundle.bundle_hash,reviewer_version='review-v1',factual_correctness=factual,clarity=check,value_insight=check,engagement=check,tone_fit=check,critical_safety=check)
        return report,story,bundle

    def test_exact_quotes_expand_to_valid_strict_evidence_without_changing_verdict(self):
        compact,story,bundle=self.make();report=expand_review(compact,story,bundle)
        self.assertEqual(report.overall_result,'PASS')
        validate_review_evidence(report,{story.draft_id:story.model_dump(mode='json'),bundle.bundle_id:bundle.model_dump(mode='json')})

    def test_fabricated_quotes_are_rejected(self):
        for changes in ({'quote':'Invented story quote'},{'source_quote':'Invented source quote'}):
            compact,story,bundle=self.make(**changes)
            with self.assertRaises(ValueError):expand_review(compact,story,bundle)

    def test_wrong_page_is_resolved_only_from_unique_exact_quote(self):
        compact,story,bundle=self.make()
        changed=compact.model_copy(update={'clarity':compact.clarity.model_copy(update={'page':6})})
        report=expand_review(changed,story,bundle)
        self.assertEqual(report.clarity.evidence[0].pointer,'/pages/0/text')
        validate_review_evidence(report,{story.draft_id:story.model_dump(mode='json'),bundle.bundle_id:bundle.model_dump(mode='json')})

    def test_moral_quote_resolves_to_moral_not_an_invented_page(self):
        compact,story,bundle=self.make()
        compact=compact.model_copy(update={'value_insight':compact.value_insight.model_copy(update={'quote':story.moral})})
        report=expand_review(compact,story,bundle)
        self.assertEqual(report.value_insight.evidence[0].pointer,'/moral')
        validate_review_evidence(report,{story.draft_id:story.model_dump(mode='json'),bundle.bundle_id:bundle.model_dump(mode='json')})
