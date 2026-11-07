"""Real loopback HTTP authentication checks; no model or payment requests."""
import http.cookiejar
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,build_opener,HTTPCookieProcessor
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from backend.server import Handler
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
from backend.accounts.assets import AssetRegistry
from backend.accounts.http_api import AccountAPI
import test_festivals as legacy_fixtures
from test_stage9_workflow import picture

PASSWORD='Synthetic-password-123'


class SecureAccountAPI(unittest.TestCase):
    call=legacy_fixtures.ApprovalApi.call

    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.accounts=Accounts(Store(self.temp.name));self.ledger=Entitlements(self.accounts)
        self.registry=AssetRegistry(self.accounts)
        self.api=AccountAPI(self.accounts,self.ledger,self.registry)
        injection=patch.object(self.server,'account_api',self.api,create=True)
        injection.start();self.addCleanup(injection.stop)
        self.client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        _,health=self.call('/api/health');self.csrf=health['csrf']

    def sign_in(self,login='parent1',admin=False):
        _,health=self.call('/api/health');self.csrf=health['csrf']
        if admin:self.accounts.bootstrap_admin(login,PASSWORD)
        else:
            status,data=self.call('/api/accounts/register',dict(login=login,password=PASSWORD))
            self.assertEqual(status,201,data)
        _,health=self.call('/api/health');self.csrf=health['csrf']
        status,data=self.call('/api/accounts/login',dict(login=login,password=PASSWORD))
        self.assertEqual(status,200,data);self.csrf=data['csrf']
        return data

    def test_admin_pages_encoded_paths_and_head_require_sign_in(self):
        for path in ('/admin/','/ADMIN/','/Admin/','/admin./','/admin%20/',
                     '/%61dmin/','/%41dmin/','/api/accounts/admin/summary'):
            self.assertEqual(self.call(path)[0],401)
        for path in ('/admin/','/ADMIN/','/admin./'):
            req=Request(self.base+path,method='HEAD')
            with self.assertRaises(HTTPError) as error:self.client.open(req)
            self.assertEqual(error.exception.code,401);error.exception.close()

    def test_signed_in_new_tab_can_bootstrap_csrf_without_invalidating_original(self):
        self.assertEqual(self.call('/api/accounts/csrf')[0],401)
        self.sign_in();original=self.csrf
        status,new=self.call('/api/accounts/csrf')
        self.assertEqual(status,200,new)
        self.csrf=new['csrf']
        self.assertEqual(self.call('/api/accounts/children',dict(name='New tab reader',age=5))[0],201)
        self.csrf=original
        self.assertEqual(self.call('/api/accounts/children',dict(name='Original tab reader',age=6))[0],201)
        self.csrf='untrusted-token'
        self.assertEqual(self.call('/api/accounts/children',dict(name='Blocked',age=5))[0],403)

    def test_csrf_bootstrap_rejects_cross_origin_reads(self):
        self.sign_in()
        req=Request(self.base+'/api/accounts/csrf',headers={'Origin':'https://untrusted.example','Sec-Fetch-Site':'cross-site'})
        with self.assertRaises(HTTPError) as error:self.client.open(req)
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_invalid_sign_in_is_not_reported_as_account_permission_failure(self):
        self.accounts.register('parent-login',PASSWORD)
        for login in ('parent-login','unknown-login'):
            status,value=self.call('/api/accounts/login',dict(login=login,password='Wrong-password-123'))
            self.assertEqual(status,401,value)
            self.assertIn('Login or password',value['error'])
            self.assertNotIn('cannot perform',value['error'])
        self.assertEqual(self.call('/api/accounts/me')[0],401)
        status,session=self.call('/api/accounts/login',dict(login='parent-login',password=PASSWORD))
        self.assertEqual(status,200,session)

    def test_sign_in_lockout_has_distinct_safe_message(self):
        self.accounts.register('rate-parent',PASSWORD)
        for _ in range(5):
            self.assertEqual(self.call('/api/accounts/login',dict(login='rate-parent',password='Wrong-password-123'))[0],401)
        status,value=self.call('/api/accounts/login',dict(login='rate-parent',password=PASSWORD))
        self.assertEqual(status,429,value)
        self.assertIn('wait 15 minutes',value['error'])

    def test_parent_cannot_use_admin_page_or_metrics(self):
        self.sign_in()
        for path in ('/admin/','/ADMIN/','/Admin/','/admin./','/admin%20/'):
            self.assertEqual(self.call(path)[0],403)
        self.assertEqual(self.call('/api/accounts/admin/summary')[0],403)

    def test_development_directory_aliases_are_never_served(self):
        for path in ('/qa/','/QA/','/qa./','/qa%20/'):
            self.assertEqual(self.call(path)[0],404)

    def test_windows_stream_and_backslash_paths_are_rejected(self):
        for path in ('/admin::$INDEX_ALLOCATION/','/admin%3a%3a$DATA','/admin%5cindex.html'):
            self.assertEqual(self.call(path)[0],404)

    def test_resolved_file_outside_public_root_is_not_served(self):
        private=Path(self.temp.name)/'private.html';private.write_text('Restricted fixture',encoding='utf-8')
        with patch('http.server.SimpleHTTPRequestHandler.translate_path',return_value=str(private)):
            self.assertEqual(self.call('/public-alias')[0],404)

    def test_admin_can_open_existing_portal_and_measured_summary(self):
        self.sign_in('admin1',admin=True)
        self.assertEqual(self.call('/admin/')[0],200)
        status,summary=self.call('/api/accounts/admin/summary')
        self.assertEqual(status,200);self.assertEqual(summary['telemetry'],'measured_local_records')

    def test_registration_forbids_role_and_login_cookie_is_hardened(self):
        self.assertEqual(self.call('/api/accounts/register',dict(login='badadmin',password=PASSWORD,role='admin'))[0],400)
        session=self.sign_in()
        self.assertNotIn('token',session);self.assertNotIn('password',session)
        status,me=self.call('/api/accounts/me')
        self.assertEqual(status,200);self.assertEqual(me['account_id'],session['account_id'])
        self.assertEqual(self.call('/api/accounts/balance')[1]['starter_available'],3)

    def test_session_csrf_required_for_mutations(self):
        self.sign_in()
        self.assertEqual(self.call('/api/accounts/children',dict(name='Veyd',age=5),token=False)[0],403)
        self.assertEqual(self.call('/api/accounts/children',dict(name='Veyd',age=5))[0],201)

    def test_children_are_not_shared_between_signed_in_accounts(self):
        self.sign_in();self.call('/api/accounts/children',dict(name='Veyd',age=5))
        self.assertEqual(len(self.call('/api/accounts/children')[1]['children']),1)
        self.sign_in('parent2')
        self.assertEqual(self.call('/api/accounts/children')[1]['children'],[])

    def test_checkout_is_simulated_idempotent_and_rejects_card_fields(self):
        self.sign_in()
        status,receipt=self.call('/api/accounts/checkout',dict(request_key='key',outcome='success'))
        self.assertEqual(status,200);self.assertTrue(receipt['is_simulated'])
        self.assertEqual(self.call('/api/accounts/checkout',dict(request_key='key',outcome='success'))[1],receipt)
        self.assertEqual(self.call('/api/accounts/checkout',dict(request_key='other',outcome='success',card_number='4111111111111111'))[0],400)

    def test_legacy_generation_and_replay_cannot_bypass_account_workflow(self):
        self.sign_in()
        for path in ('/api/books','/api/original-books','/api/workflow-runs'):
            self.assertEqual(self.call(path,{})[0],409)

    def test_logout_revokes_session_and_starter_credits_do_not_reset(self):
        session=self.sign_in()
        self.ledger.reserve(session['account_id'],'fixture','a'*64)
        self.ledger.complete(session['account_id'],'fixture','a'*64,{'fixture':True})
        self.assertEqual(self.call('/api/accounts/logout',{})[0],200)
        self.assertEqual(self.call('/api/accounts/me')[0],401)
        _,health=self.call('/api/health');self.csrf=health['csrf']
        status,login=self.call('/api/accounts/login',dict(login='parent1',password=PASSWORD))
        self.assertEqual(status,200);self.csrf=login['csrf']
        self.assertEqual(self.call('/api/accounts/balance')[1]['starter_available'],2)

    def test_parent_cannot_approve_or_upload_shared_story_assets(self):
        self.sign_in()
        self.assertEqual(self.call('/api/accounts/assets/no-such-asset/review',dict(approve=True,
            rights_basis='Claim',gentle=True,notes='Claim'))[0],403)
        req=Request(self.base+'/api/accounts/assets/upload',data=picture(),headers={
            'X-Bala-CSRF':self.csrf,'X-Asset-Characters':'["krishna"]',
            'X-Asset-Style':'Watercolor','X-Asset-Description':'Fixture'},method='POST')
        with self.assertRaises(HTTPError) as error:self.client.open(req)
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_admin_static_upload_starts_pending_then_can_be_reviewed(self):
        self.sign_in('admin1',admin=True)
        req=Request(self.base+'/api/accounts/assets/upload',data=picture(),headers={
            'X-Bala-CSRF':self.csrf,'X-Asset-Characters':'["krishna"]',
            'X-Asset-Style':'Watercolor','X-Asset-Description':'Synthetic tile'},method='POST')
        with self.client.open(req) as response:
            self.assertEqual(response.status,201);record=json.load(response)
        self.assertEqual(record['status'],'pending')
        status,review=self.call('/api/accounts/assets/'+record['asset_id']+'/review',dict(
            approve=True,rights_basis='Synthetic generated fixture bytes only.',gentle=True,notes='Fixture inspection only.'))
        self.assertEqual(status,200);self.assertTrue(review['approve'])

    def test_cross_origin_account_write_is_blocked(self):
        self.sign_in()
        req=Request(self.base+'/api/accounts/children',data=b'{"name":"Veyd","age":5}',headers={
            'Origin':'https://example.invalid','X-Bala-CSRF':self.csrf,'Content-Type':'application/json'},method='POST')
        with self.assertRaises(HTTPError) as error:self.client.open(req)
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_password_change_revokes_http_session(self):
        self.sign_in()
        status,data=self.call('/api/accounts/password',dict(current_password=PASSWORD,new_password='Updated-synthetic-password-123'))
        self.assertEqual(status,200,data)
        self.assertEqual(self.call('/api/accounts/me')[0],401)

    def test_recovery_requires_code_and_can_be_used_after_logout(self):
        self.sign_in()
        status,data=self.call('/api/accounts/recovery-code',dict(current_password=PASSWORD))
        self.assertEqual(status,200,data);code=data['recovery_code']
        self.call('/api/accounts/logout',{})
        self.csrf=self.call('/api/health')[1]['csrf']
        status,data=self.call('/api/accounts/recover',dict(login='parent1',recovery_code=code,new_password='Recovered-password-123'))
        self.assertEqual(status,200,data)
        self.csrf=self.call('/api/health')[1]['csrf']
        status,data=self.call('/api/accounts/login',dict(login='parent1',password='Recovered-password-123'))
        self.assertEqual(status,200,data)

    def test_password_settings_require_tab_csrf(self):
        self.sign_in();self.csrf='wrong'
        self.assertEqual(self.call('/api/accounts/recovery-code',dict(current_password=PASSWORD))[0],403)


    def test_story_validation_identifies_field_without_echoing_input(self):
        self.sign_in()
        status,data=self.call('/api/accounts/preflight',{'language':'secret-invalid-input'})
        self.assertEqual(status,400,data)
        self.assertIn('language',data['error'])
        self.assertNotIn('secret-invalid-input',data['error'])

    def test_no_story_credits_has_actionable_message(self):
        self.sign_in()
        with patch.object(self.api,'handle',side_effect=PermissionError('No story credits are available.')):
            status,data=self.call('/api/accounts/runs',{})
        self.assertEqual(status,403,data)
        self.assertEqual(data['error'],'No story credits are available.')

    def test_reused_story_key_is_not_reported_as_generic_account_error(self):
        self.sign_in()
        message='This request key was already used with different preferences.'
        with patch.object(self.api,'handle',side_effect=ValueError(message)):
            status,data=self.call('/api/accounts/runs',{})
        self.assertEqual(status,400,data);self.assertEqual(data['error'],message)

if __name__=='__main__':unittest.main()
