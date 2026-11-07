"""Text consent and application release checks; synthetic providers only."""
import json
import unittest
from pydantic import ValidationError
from backend.contracts import content_hash
from backend.contracts.consent import story_text, TextApprovalRequest
from backend.accounts.http_api import AccountAPI
import test_stage9_workflow as fixtures

class TextConsentTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.ManagedWorkflowTests('test_exact_book_review_approval_and_single_debit')
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.case.workflow()

    def approve(self):
        c=self.case
        return c.service.approve_text(c.run,c.parent,c.service.text_review(c.run,c.parent)['text_hash'])

    def test_text_can_be_approved_before_images_without_releasing_output(self):
        c=self.case;state=self.approve()
        self.assertEqual(state['status'],'assets_pending')
        self.assertTrue(c.service.text_review(c.run,c.parent)['text_approved'])
        self.assertEqual(c.ledger.balance(c.parent)['starter_completed'],0)
        with self.assertRaises(PermissionError):c.service.approved_output(c.run,c.parent,'a'*64)
        done=c.scenes();self.assertEqual(done['status'],'complete')
        self.assertEqual(c.ledger.balance(c.parent)['starter_completed'],1)

    def test_picture_replacement_reuses_consent_and_debits_once(self):
        c=self.case;c.scenes();before=self.approve()
        consent=c.service.text_review(c.run,c.parent)['consent_id']
        asset=c.asset('blue')
        c.registry.inspect_scene(c.admin,c.draft,c.context.characters,1,asset,matches=True,notes='Synthetic replacement.')
        with self.assertRaises(PermissionError):c.service.approved_output(c.run,c.parent,before['book_version_hash'])
        after=c.service.refresh_assets(c.run,c.parent)
        self.assertEqual(after['status'],'complete')
        self.assertNotEqual(before['book_version_hash'],after['book_version_hash'])
        self.assertNotEqual(before['approval_id'],after['approval_id'])
        self.assertEqual(c.service.text_review(c.run,c.parent)['consent_id'],consent)
        self.assertEqual(c.ledger.balance(c.parent)['starter_completed'],1)
        self.assertEqual(len(c.fixture.calls),2)
        with c.accounts.store.db() as db:
            rows=db.execute('SELECT payload FROM library_versions WHERE account_id=? AND run_id=?',(c.parent,c.run)).fetchall()
        self.assertEqual(sorted(json.loads(row[0])['book']['version'] for row in rows),[1,2])
        c.service.approved_output(c.run,c.parent,after['book_version_hash'])

    def test_text_projection_excludes_scene_metadata_and_detects_changed_words(self):
        c=self.case;original=content_hash(story_text(c.draft))
        page=c.draft.pages[0].model_copy(update={'scene_description':'New approved scene specification.'})
        metadata=c.draft.model_copy(update={'pages':(page,*c.draft.pages[1:]),'draft_hash':None})
        self.assertEqual(original,content_hash(story_text(metadata)))
        changed=c.draft.model_copy(update={'title':'Changed story words','draft_hash':None})
        self.assertNotEqual(original,content_hash(story_text(changed)))
        self.approve();state=c.service.status(c.run,c.parent)['values']
        self.assertIsNone(c.service.adapters.consents.current(c.parent,c.run,state['request_hash'],content_hash(story_text(changed))))
        with self.assertRaises(ValueError):c.service.adapters.consents.approve(c.parent,state,changed,original)

    def test_stale_hash_and_full_book_consent_are_rejected(self):
        c=self.case
        with self.assertRaises(ValueError):c.service.approve_text(c.run,c.parent,'a'*64)
        with self.assertRaises(ValueError):c.service.approve(c.run,c.parent,'a'*64)
        self.assertFalse(c.service.text_review(c.run,c.parent)['text_approved'])
        with self.assertRaises(ValidationError):TextApprovalRequest(text_hash='a'*64,book_hash='b'*64)

    def test_api_exposes_text_scope_and_rejects_owner_override(self):
        c=self.case;api=AccountAPI(c.accounts,c.ledger,c.registry,c.service)
        principal=dict(account_id=c.parent,role='parent');path=['api','accounts','runs',c.run]
        review,status=api.handle('GET',path+['text-review'],principal)
        self.assertEqual(status,200);self.assertEqual(review['approval_scope'],'story_text')
        result,status=api.handle('POST',path+['approve-text'],principal,dict(text_hash=review['text_hash']))
        self.assertTrue(result['text_approval']['text_approved'])
        with self.assertRaises(ValidationError):api.handle('POST',path+['approve-text'],principal,dict(text_hash=review['text_hash'],owner='other'))
        with self.assertRaises(ValueError):api.handle('POST',path+['approve'],principal,dict(book_hash='a'*64))

    def test_legacy_receipt_cannot_replace_typed_application_release(self):
        c=self.case;c.scenes();done=self.approve()
        with c.service.repository.db() as db:db.execute('DELETE FROM application_releases WHERE id=?',(done['approval_id'],))
        with self.assertRaises(PermissionError):c.service.approved_output(c.run,c.parent,done['book_version_hash'])

if __name__=='__main__':unittest.main()
