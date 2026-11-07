"""Local account/ledger verification with synthetic identities; no payments."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from backend.accounts.store import Store
from backend.accounts.identity import Accounts,sha
from backend.accounts.entitlements import Entitlements
from backend.accounts.http_api import Checkout,Credentials
from backend.contracts import parse_payload

PASSWORD='Synthetic-password-123'


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=1000000.
        self.accounts=Accounts(Store(self.temp.name),clock=lambda:self.now)
        self.parent=self.accounts.register('parent1',PASSWORD)['account_id']
        self.ledger=Entitlements(self.accounts,clock=lambda:self.now)

    def complete(self,run,hash='a'*64):
        self.ledger.reserve(self.parent,run,hash)
        self.ledger.complete(self.parent,run,hash,{'fixture':run})

    def test_passwords_and_session_secrets_not_stored_in_plaintext(self):
        session=self.accounts.login('parent1',PASSWORD)
        with self.accounts.store.db() as db:
            account=dict(db.execute('SELECT * FROM accounts').fetchone())
            record=dict(db.execute('SELECT * FROM sessions').fetchone())
        self.assertNotIn(PASSWORD,json.dumps(account));self.assertEqual(len(account['password_hash']),64)
        self.assertEqual(record['token_hash'],sha(session['token']))
        self.assertNotIn(session['csrf'],json.dumps(record))

    def test_login_csrf_logout_and_expiry(self):
        session=self.accounts.login('parent1',PASSWORD)
        self.assertEqual(self.accounts.authenticate(session['token'],csrf=session['csrf'])['account_id'],self.parent)
        with self.assertRaises(PermissionError):self.accounts.authenticate(session['token'],csrf='wrong')
        self.accounts.logout(session['token'])
        with self.assertRaises(PermissionError):self.accounts.authenticate(session['token'])
        session=self.accounts.login('parent1',PASSWORD);self.now+=43201
        with self.assertRaises(PermissionError):self.accounts.authenticate(session['token'])

    def test_no_public_admin_role_and_duplicate_registration_does_not_reset_credits(self):
        with self.assertRaises(ValueError):parse_payload(Credentials,dict(login='badadmin',password=PASSWORD,role='admin'))
        self.complete('run1')
        with self.assertRaises(ValueError):self.accounts.register('PARENT1',PASSWORD)
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)

    def test_failed_login_rate_limit_survives_new_instance(self):
        for i in range(5):
            with self.assertRaises(PermissionError):self.accounts.login('parent1','Wrong-password-123')
        fresh=Accounts(self.accounts.store,clock=lambda:self.now)
        with self.assertRaises(PermissionError):fresh.login('parent1',PASSWORD)
        self.now+=901;self.assertEqual(fresh.login('parent1',PASSWORD)['account_id'],self.parent)

    def test_roles_and_disabling_account_revoke_existing_sessions(self):
        session=self.accounts.login('parent1',PASSWORD)
        with self.assertRaises(PermissionError):self.accounts.require(self.parent,'admin')
        admin=self.accounts.bootstrap_admin('admin1',PASSWORD)['account_id']
        self.accounts.disable(admin,self.parent)
        with self.assertRaises(PermissionError):self.accounts.authenticate(session['token'])
        with self.assertRaises(PermissionError):self.ledger.balance(self.parent)

    def test_children_are_account_scoped_and_age_checked(self):
        self.accounts.add_child(self.parent,'Veyd',5)
        other=self.accounts.register('parent2',PASSWORD)['account_id']
        self.assertEqual(self.accounts.children(other),[])
        with self.assertRaises(ValueError):self.accounts.add_child(self.parent,'Veyd',True)

    def test_three_starter_stories_and_no_negative_balance(self):
        for i in range(3):self.complete('run'+str(i))
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],0)
        with self.assertRaises(PermissionError):self.ledger.reserve(self.parent,'fourth','b'*64)

    def test_weekly_price_cap_and_exact_expiration(self):
        for i in range(3):self.complete('free'+str(i))
        receipt=self.ledger.checkout(self.parent,'checkout1','success')
        self.assertEqual((receipt['price_cents'],receipt['story_limit'],receipt['expires_at']-receipt['starts_at']),(500,10,604800))
        for i in range(10):self.complete('weekly'+str(i))
        with self.assertRaises(PermissionError):self.ledger.reserve(self.parent,'eleventh','b'*64)
        self.now=receipt['expires_at']
        self.assertEqual(self.ledger.balance(self.parent)['weekly_available'],0)
        self.assertTrue(receipt['is_simulated'])

    def test_declined_cancelled_and_idempotent_checkout(self):
        for result in ('declined','cancelled'):
            receipt=self.ledger.checkout(self.parent,result,result)
            self.assertNotIn('pass_id',receipt)
        success=self.ledger.checkout(self.parent,'paid','success')
        self.assertEqual(self.ledger.checkout(self.parent,'paid','success'),success)
        with self.assertRaises(ValueError):self.ledger.checkout(self.parent,'paid','declined')
        with self.assertRaises(ValueError):self.ledger.checkout(self.parent,'another','success')

    def test_no_financial_capture_fields(self):
        with self.assertRaises(ValueError):parse_payload(Checkout,dict(request_key='key',outcome='success',card_number='4111111111111111'))

    def test_reservations_are_idempotent_releasable_and_expire(self):
        first=self.ledger.reserve(self.parent,'run1','a'*64)
        self.assertEqual(self.ledger.reserve(self.parent,'run1','a'*64),first)
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)
        self.ledger.release(self.parent,'run1')
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],3)
        self.ledger.reserve(self.parent,'run1','a'*64);self.now+=1801
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],3)
        with self.assertRaises(PermissionError):self.ledger.complete(self.parent,'run1','a'*64,{})

    def test_two_concurrent_reservations_cannot_spend_last_credit_twice(self):
        self.complete('first');self.complete('second')
        def reserve(run):
            try:self.ledger.reserve(self.parent,run,'a'*64);return True
            except PermissionError:return False
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(reserve,('third','fourth')))
        self.assertEqual(sum(results),1)

    def test_completion_debits_once_and_library_is_account_scoped(self):
        self.complete('run1');self.complete('run1')
        self.assertEqual(self.ledger.balance(self.parent)['starter_available'],2)
        self.assertEqual(len(self.ledger.library(self.parent)),1)
        other=self.accounts.register('parent2',PASSWORD)['account_id']
        self.assertEqual(self.ledger.library(other),[])
        with self.assertRaises(ValueError):self.ledger.reserve(self.parent,'run1','b'*64)

    def test_history_import_needs_consent_and_cannot_grant_entitlements(self):
        with self.assertRaises(ValueError):self.ledger.import_history(self.parent,'key',[{'title':'Old book'}],consent=False)
        result=self.ledger.import_history(self.parent,'key',[{'title':'Old book'}],consent=True)
        self.assertFalse(result['verified']);self.assertEqual(self.ledger.balance(self.parent)['starter_available'],3)
        self.assertEqual(self.ledger.library(self.parent),[])
        with self.assertRaises(ValueError):self.ledger.import_history(self.parent,'other',[{'approved':'true'}],consent=True)


if __name__=='__main__':unittest.main()
