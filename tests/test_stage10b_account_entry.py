"""Account entry and real HTTP account controls; no paid services."""
import unittest
from urllib.request import Request
import test_stage9_account_api as fixture

class AccountEntryTests(unittest.TestCase):
    call=fixture.SecureAccountAPI.call
    setUpClass=fixture.SecureAccountAPI.__dict__['setUpClass']
    tearDownClass=fixture.SecureAccountAPI.__dict__['tearDownClass']
    setUp=fixture.SecureAccountAPI.setUp
    sign_in=fixture.SecureAccountAPI.sign_in

    def test_ui_assets_are_fresh_even_with_old_browser_conditional_headers(self):
        for path in ('index.html','signin-nav.js','managed-story.css'):
            with self.subTest(path=path):
                request=Request(self.base+'/'+path,headers={'If-Modified-Since':'Wed, 01 Jan 2037 00:00:00 GMT'})
                with self.client.open(request) as response:
                    self.assertEqual(response.status,200)
                    self.assertIn('no-store',response.headers.get('Cache-Control',''))
                    content=response.read().decode('utf-8')
                if path=='index.html':
                    self.assertNotIn('data-nav="family"',content)
                    self.assertEqual(content.count('id="main-sign-in"'),1)

    def test_capabilities_do_not_claim_managed_story_is_connected(self):
        status,data=self.call('/api/health')
        self.assertEqual(status,200);self.assertTrue(data['accountsEnabled'])
        self.assertFalse(data['managedWorkflowEnabled'])

    def test_account_page_is_public_and_billing_data_is_private(self):
        with self.client.open(self.base+'/') as response:
            self.assertTrue(response.url.endswith('/account.html'))
            content=response.read().decode('utf-8')
        self.assertIn('No money is charged',content)
        self.assertNotIn('type="password" value=',content)
        self.assertEqual(self.call('/api/accounts/balance')[0],401)
        self.sign_in()
        self.assertEqual(self.call('/api/accounts/balance')[1]['starter_available'],3)
        self.assertEqual(self.call('/api/accounts/checkout',dict(request_key='declined1',outcome='declined'))[0],200)
        self.assertIsNone(self.call('/api/accounts/balance')[1]['weekly_expires_at'])
        self.assertEqual(self.call('/api/accounts/checkout',dict(request_key='success1',outcome='success'))[0],200)
        self.assertEqual(self.call('/api/accounts/balance')[1]['weekly_available'],10)

    def test_logout_revokes_account_access(self):
        self.sign_in();self.assertEqual(self.call('/api/accounts/logout',{})[0],200)
        self.assertEqual(self.call('/api/accounts/children')[0],401)

if __name__=='__main__':unittest.main()
