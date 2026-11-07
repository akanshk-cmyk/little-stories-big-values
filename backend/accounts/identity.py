"""Password accounts; no public admin registration or trusted browser roles."""
import hashlib
import hmac
import re
import secrets
import time
from uuid import uuid4


def sha(value):return hashlib.sha256(value.encode()).hexdigest()


class LoginFailure(PermissionError):
    """Public-safe sign-in category, never an account existence disclosure."""
    def __init__(self,code):
        self.code=code
        super().__init__('Please wait before trying to sign in again.' if code=='rate_limited' else 'Login or password is incorrect.')


class Accounts:
    def __init__(self,store,*,clock=time.time):self.store=store;self.clock=clock

    @staticmethod
    def credentials(login,password):
        if not isinstance(login,str) or not re.fullmatch(r'[a-z0-9][a-z0-9@._+-]{2,119}',login.strip().lower()):
            raise ValueError('Use a login with 3 to 120 letters, numbers or email characters.')
        if not isinstance(password,str) or not 12<=len(password)<=128:
            raise ValueError('Use a password containing 12 to 128 characters.')
        return login.strip().lower(),password

    @staticmethod
    def password_hash(password,salt):
        return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()

    def register(self,login,password):return self._create(login,password,'parent')

    def bootstrap_admin(self,login,password):
        # Internal/offline entry point only; never accepted by registration JSON.
        return self._create(login,password,'admin')

    def _create(self,login,password,role):
        login,password=self.credentials(login,password);salt=secrets.token_hex(16);account=uuid4().hex
        encoded=self.password_hash(password,salt)
        import sqlite3
        try:
            with self.store.db(write=True) as db:
                db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?)',(account,login,encoded,salt,role,1,self.clock()))
        except sqlite3.IntegrityError:raise ValueError('This login is already registered.') from None
        return dict(account_id=account,role=role)

    def login(self,login,password):
        login,password=self.credentials(login,password);now=self.clock()
        # Serialize attempts so parallel failed requests cannot bypass the limit.
        with self.store.db(write=True) as db:
            attempts=db.execute('SELECT count(*) FROM login_failures WHERE login=? AND created>?',(login,now-900)).fetchone()[0]
            if attempts>=5:raise LoginFailure('rate_limited')
            row=db.execute('SELECT * FROM accounts WHERE login=?',(login,)).fetchone()
            salt=row['salt'] if row else '00'*16
            encoded=self.password_hash(password,salt)
            valid=bool(row and row['active'] and hmac.compare_digest(encoded,row['password_hash']))
            if not valid:db.execute('INSERT INTO login_failures VALUES(?,?)',(login,now))
            else:
                db.execute('DELETE FROM login_failures WHERE login=?',(login,))
                token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(sha(token),row['id'],sha(csrf),now+12*3600))
        # Raise after commit so failed attempt records survive.
        if not valid:raise LoginFailure('invalid_credentials')
        return dict(token=token,csrf=csrf,account_id=row['id'],role=row['role'],expires_at=now+12*3600)

    def authenticate(self,token,*,csrf=None,role=None):
        if not isinstance(token,str) or not 20<=len(token)<=100:raise PermissionError('Please sign in.')
        with self.store.db() as db:
            row=db.execute('SELECT a.id,a.role,s.csrf_hash FROM sessions s JOIN accounts a ON a.id=s.account_id '
                'WHERE s.token_hash=? AND s.expires>? AND a.active=1',(sha(token),self.clock())).fetchone()
        if not row or (role and row['role']!=role):raise PermissionError('This account cannot access this page.')
        if csrf is not None:
            valid=isinstance(csrf,str) and hmac.compare_digest(sha(csrf),row['csrf_hash'])
            if not valid and isinstance(csrf,str):
                with self.store.db() as db:
                    valid=bool(db.execute('SELECT 1 FROM session_csrf_tokens WHERE token_hash=? AND csrf_hash=? AND expires>?',(sha(token),sha(csrf),self.clock())).fetchone())
            if not valid:raise PermissionError('Refresh the account page before making changes.')
        return dict(account_id=row['id'],role=row['role'])

    def session_csrf(self,token):
        """Issue a tab token for an existing cookie session without breaking other tabs."""
        self.authenticate(token)
        value=secrets.token_urlsafe(32);now=self.clock();digest=sha(token)
        with self.store.db(write=True) as db:
            session=db.execute('SELECT expires FROM sessions WHERE token_hash=? AND expires>?',(digest,now)).fetchone()
            if not session:raise PermissionError('Please sign in.')
            db.execute('DELETE FROM session_csrf_tokens WHERE expires<=?',(now,))
            db.execute('INSERT INTO session_csrf_tokens VALUES(?,?,?)',(digest,sha(value),session['expires']))
            db.execute('DELETE FROM session_csrf_tokens WHERE token_hash=? AND rowid NOT IN (SELECT rowid FROM session_csrf_tokens WHERE token_hash=? ORDER BY rowid DESC LIMIT 8)',(digest,digest))
        return value

    def require(self,account,role='parent'):
        with self.store.db() as db:row=db.execute('SELECT role,active FROM accounts WHERE id=?',(account,)).fetchone()
        if not row or not row['active'] or (role and row['role']!=role):raise PermissionError('Account access denied.')
        return True

    def logout(self,token):
        with self.store.db(write=True) as db:
            db.execute('DELETE FROM session_csrf_tokens WHERE token_hash=?',(sha(token),))
            db.execute('DELETE FROM sessions WHERE token_hash=?',(sha(token),))

    def disable(self,admin,account):
        self.require(admin,'admin')
        with self.store.db(write=True) as db:
            db.execute('UPDATE accounts SET active=0 WHERE id=?',(account,))
            db.execute('DELETE FROM sessions WHERE account_id=?',(account,))

    def add_child(self,account,name,age):
        self.require(account)
        if not isinstance(name,str) or not name.strip() or len(name)>80 or type(age) is not int or not 3<=age<=8:
            raise ValueError('Provide a child name and an age from 3 to 8.')
        child=uuid4().hex
        with self.store.db(write=True) as db:db.execute('INSERT INTO children VALUES(?,?,?,?)',(child,account,name.strip(),age))
        return dict(child_id=child,name=name.strip(),age=age)

    def children(self,account):
        self.require(account)
        with self.store.db() as db:return [dict(r) for r in db.execute('SELECT id,name,age FROM children WHERE account_id=?',(account,))]

    def profile(self,account):
        self.require(account)
        with self.store.db() as db:
            row=db.execute('SELECT a.login,a.created,p.display_name,p.email FROM accounts a LEFT JOIN parent_profiles p ON p.account_id=a.id WHERE a.id=?',(account,)).fetchone()
        return dict(login=row['login'],created_at=row['created'],display_name=row['display_name'],email=row['email'],email_verified=False)

    def update_profile(self,account,profile):
        self.require(account)
        from .http_api import ParentProfile
        from backend.contracts.common import parse_payload
        command=parse_payload(ParentProfile,profile)
        with self.store.db(write=True) as db:
            db.execute('INSERT INTO parent_profiles VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET display_name=excluded.display_name,email=excluded.email',(account,command.display_name,command.email))
        return self.profile(account)

    def update_child(self,account,child,name,age):
        self.require(account)
        if not isinstance(name,str) or not name.strip() or len(name)>80 or type(age) is not int or not 3<=age<=8:
            raise ValueError('Provide a child name and an age from 3 to 8.')
        with self.store.db(write=True) as db:
            result=db.execute('UPDATE children SET name=?,age=? WHERE id=? AND account_id=?',(name.strip(),age,child,account))
            if result.rowcount!=1:raise LookupError('Child profile not found.')
        return dict(child_id=child,name=name.strip(),age=age)

    def delete_child(self,account,child):
        self.require(account)
        with self.store.db(write=True) as db:
            result=db.execute('DELETE FROM children WHERE id=? AND account_id=?',(child,account))
            if result.rowcount!=1:raise LookupError('Child profile not found.')
        # Saved books and immutable request records are intentionally retained.
        return dict(deleted=True)

    def _password_check(self,account,password):
        self.require(account)
        if not isinstance(password,str) or not 12<=len(password)<=128:raise LoginFailure('invalid_credentials')
        now=self.clock();key='settings:'+account
        with self.store.db(write=True) as db:
            if db.execute('SELECT count(*) FROM login_failures WHERE login=? AND created>?',(key,now-900)).fetchone()[0]>=5:
                raise LoginFailure('rate_limited')
            row=db.execute('SELECT * FROM accounts WHERE id=? AND active=1',(account,)).fetchone()
            valid=bool(row and hmac.compare_digest(self.password_hash(password,row['salt']),row['password_hash']))
            if not valid:db.execute('INSERT INTO login_failures VALUES(?,?)',(key,now))
        if not valid:raise LoginFailure('invalid_credentials')
        return row['password_hash']

    @staticmethod
    def _revoke_sessions(db,account):
        db.execute('DELETE FROM session_csrf_tokens WHERE token_hash IN (SELECT token_hash FROM sessions WHERE account_id=?)',(account,))
        db.execute('DELETE FROM sessions WHERE account_id=?',(account,))

    def change_password(self,account,current_password,new_password):
        expected=self._password_check(account,current_password)
        self.credentials('validation',new_password)
        if current_password==new_password:raise ValueError('Choose a different new password.')
        salt=secrets.token_hex(16);encoded=self.password_hash(new_password,salt)
        with self.store.db(write=True) as db:
            result=db.execute('UPDATE accounts SET password_hash=?,salt=? WHERE id=? AND password_hash=? AND active=1',(encoded,salt,account,expected))
            if result.rowcount!=1:raise PermissionError('Account changed. Sign in again.')
            self._revoke_sessions(db,account)
            db.execute('DELETE FROM recovery_codes WHERE account_id=?',(account,))
        return dict(password_changed=True,signed_out=True,recovery_code_invalidated=True)

    def create_recovery_code(self,account,current_password):
        expected=self._password_check(account,current_password)
        code=secrets.token_urlsafe(32)
        with self.store.db(write=True) as db:
            if not db.execute('SELECT 1 FROM accounts WHERE id=? AND password_hash=? AND active=1',(account,expected)).fetchone():raise PermissionError('Account changed. Sign in again.')
            db.execute('INSERT OR REPLACE INTO recovery_codes VALUES(?,?,?)',(account,sha(code),self.clock()))
        return dict(recovery_code=code,one_time=True)

    def recover_password(self,login,code,new_password):
        login,new_password=self.credentials(login,new_password)
        if not isinstance(code,str) or not 32<=len(code)<=128:raise LoginFailure('invalid_credentials')
        now=self.clock();key='recovery:'+login
        with self.store.db(write=True) as db:
            if db.execute('SELECT count(*) FROM login_failures WHERE login=? AND created>?',(key,now-900)).fetchone()[0]>=5:raise LoginFailure('rate_limited')
            row=db.execute('SELECT a.id,r.code_hash FROM accounts a LEFT JOIN recovery_codes r ON r.account_id=a.id WHERE a.login=? AND a.active=1',(login,)).fetchone()
            valid=bool(hmac.compare_digest(sha(code),row['code_hash'] if row and row['code_hash'] else '0'*64) and row)
            if not valid:db.execute('INSERT INTO login_failures VALUES(?,?)',(key,now))
            else:
                salt=secrets.token_hex(16)
                db.execute('UPDATE accounts SET password_hash=?,salt=? WHERE id=?',(self.password_hash(new_password,salt),salt,row['id']))
                db.execute('DELETE FROM recovery_codes WHERE account_id=?',(row['id'],))
                self._revoke_sessions(db,row['id'])
                db.execute('DELETE FROM login_failures WHERE login IN (?,?)',(key,login))
        if not valid:raise LoginFailure('invalid_credentials')
        return dict(password_changed=True,signed_out=True,recovery_code_consumed=True)
