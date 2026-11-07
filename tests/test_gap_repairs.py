import tempfile,unittest
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
from backend.accounts.http_api import AccountAPI
from types import SimpleNamespace
from test_contracts import request

class GapRepairs(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.accounts=Accounts(Store(self.temp.name))
        self.owner=self.accounts.register('owner','Synthetic-password-123')['account_id']
        self.other=self.accounts.register('other','Synthetic-password-456')['account_id']
        self.child=self.accounts.add_child(self.owner,'Reader',5)['child_id']
        self.ledger=Entitlements(self.accounts,clock=lambda:100000)
        self.api=AccountAPI(self.accounts,self.ledger,None,workflow=SimpleNamespace(source_mode='local_ramayan_text_only'))
        self.actor={'account_id':self.owner,'role':'parent'}

    def test_edit_and_delete_are_owner_scoped(self):
        with self.assertRaises(LookupError):self.accounts.update_child(self.other,self.child,'Wrong',6)
        self.accounts.update_child(self.owner,self.child,'Updated',6)
        self.assertEqual(self.accounts.children(self.owner)[0]['age'],6)
        with self.assertRaises(LookupError):self.accounts.delete_child(self.other,self.child)
        self.accounts.delete_child(self.owner,self.child)
        self.assertEqual(self.accounts.children(self.owner),[])

    def test_delete_requires_explicit_confirmation(self):
        with self.assertRaises(ValueError):self.api.handle('POST',['api','accounts','children',self.child,'delete'],self.actor,{})
        self.assertEqual(len(self.accounts.children(self.owner)),1)

    def test_preflight_no_operations_or_credit_consumption(self):
        req=request().model_copy(update={'child_profile_id':self.child,'age':5,'idea':'Rama building a bridge','required_character_ids':('rama',)})
        before=self.ledger.balance(self.owner)
        result,code=self.api.handle('POST',['api','accounts','preflight'],self.actor,req.model_dump(mode='json'))
        self.assertEqual(code,200);self.assertEqual(result['status'],'UNSUPPORTED')
        self.assertEqual(before,self.ledger.balance(self.owner))
        self.assertEqual(self.ledger.operation_usage(self.owner)['used'],0)

    def test_preflight_rejects_another_accounts_profile(self):
        req=request().model_copy(update={'child_profile_id':self.child,'age':5})
        with self.assertRaises(ValueError):self.api.handle('POST',['api','accounts','preflight'],{'account_id':self.other,'role':'parent'},req.model_dump(mode='json'))

    def test_operation_reset_is_next_utc_midnight(self):
        usage=self.ledger.operation_usage(self.owner)
        self.assertEqual(usage['resets_at'],172800)
        self.assertEqual(usage['timezone'],'UTC')
