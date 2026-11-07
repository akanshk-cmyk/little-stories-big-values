import unittest,tempfile
from pydantic import ValidationError
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
from backend.accounts.http_api import AccountAPI,ParentProfile

class ParentProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.accounts=Accounts(Store(self.temp.name))
        self.owner=self.accounts.register('parent1','Synthetic-password-123')['account_id']
        self.other=self.accounts.register('parent2','Synthetic-password-456')['account_id']
        self.api=AccountAPI(self.accounts,Entitlements(self.accounts),None)

    def test_existing_account_has_no_invented_details_or_secrets(self):
        data,_=self.api.handle('GET',['api','accounts','me'],dict(account_id=self.owner,role='parent'))
        self.assertIsNone(data['profile']['display_name']);self.assertIsNone(data['profile']['email'])
        self.assertEqual(data['profile']['login'],'parent1')
        self.assertNotIn('password',str(data));self.assertNotIn('salt',str(data))

    def test_profile_persists_without_changing_login_or_password(self):
        self.accounts.update_profile(self.owner,dict(display_name=' Parent One ',email='hello@Example.COM'))
        reopened=Accounts(Store(self.temp.name));profile=reopened.profile(self.owner)
        self.assertEqual(profile['display_name'],'Parent One');self.assertEqual(profile['email'],'hello@example.com')
        self.assertFalse(profile['email_verified'])
        self.assertEqual(reopened.login('parent1','Synthetic-password-123')['account_id'],self.owner)
        self.assertIsNone(reopened.profile(self.other)['email'])

    def test_api_cannot_overwrite_other_owner_via_body(self):
        with self.assertRaises(ValidationError):self.api.handle('POST',['api','accounts','profile'],dict(account_id=self.owner,role='parent'),dict(display_name='Other',email=None,account_id=self.other))
        self.assertIsNone(self.accounts.profile(self.other)['display_name'])

    def test_invalid_email_and_blank_name_rejected(self):
        for email in ('bad','a@@example.com','a@example.com\r\nBcc:x@y.com','.a@example.com','a..b@example.com'):
            with self.subTest(email=email),self.assertRaises(ValidationError):ParentProfile(display_name='Parent',email=email)
        with self.assertRaises(ValidationError):ParentProfile(display_name='  ')

    def test_email_can_be_removed(self):
        self.accounts.update_profile(self.owner,dict(display_name='Parent',email='a@example.com'))
        self.accounts.update_profile(self.owner,dict(display_name='Parent',email=None))
        self.assertIsNone(self.accounts.profile(self.owner)['email'])
