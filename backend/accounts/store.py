from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Store:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'accounts.sqlite'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,login TEXT UNIQUE,
                    password_hash TEXT,salt TEXT,role TEXT,active INTEGER,created REAL);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,account_id TEXT,
                    csrf_hash TEXT,expires REAL);
                CREATE TABLE IF NOT EXISTS login_failures(login TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS recovery_codes(account_id TEXT PRIMARY KEY,code_hash TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS parent_profiles(account_id TEXT PRIMARY KEY,display_name TEXT NOT NULL,email TEXT);
                CREATE TABLE IF NOT EXISTS session_csrf_tokens(token_hash TEXT,csrf_hash TEXT,expires REAL,
                    PRIMARY KEY(token_hash,csrf_hash));
                CREATE TABLE IF NOT EXISTS children(id TEXT PRIMARY KEY,account_id TEXT,name TEXT,age INTEGER);
                CREATE TABLE IF NOT EXISTS passes(id TEXT PRIMARY KEY,account_id TEXT,starts REAL,expires REAL);
                CREATE TABLE IF NOT EXISTS checkouts(account_id TEXT,request_key TEXT,fingerprint TEXT,
                    payload TEXT,PRIMARY KEY(account_id,request_key));
                CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,account_id TEXT,run_id TEXT,
                    book_hash TEXT,fund TEXT,state TEXT,expires REAL,UNIQUE(account_id,run_id));
                CREATE TABLE IF NOT EXISTS library(account_id TEXT,run_id TEXT,book_hash TEXT,
                    payload TEXT,created REAL,PRIMARY KEY(account_id,run_id));
                CREATE TABLE IF NOT EXISTS imports(account_id TEXT,request_key TEXT,digest TEXT,
                    payload TEXT,created REAL,PRIMARY KEY(account_id,request_key));
                CREATE TABLE IF NOT EXISTS narration_authorizations(account_id TEXT,run_id TEXT,
                    authorization_id TEXT,PRIMARY KEY(account_id,run_id));
                CREATE TABLE IF NOT EXISTS generation_holds(account_id TEXT,run_id TEXT,request_hash TEXT,
                    fund TEXT,state TEXT,expires REAL,PRIMARY KEY(account_id,run_id));
                CREATE TABLE IF NOT EXISTS generation_operations(account_id TEXT,run_id TEXT,operation_key TEXT,
                    created REAL,PRIMARY KEY(account_id,run_id,operation_key));
                CREATE TABLE IF NOT EXISTS library_versions(account_id TEXT,run_id TEXT,book_hash TEXT,
                    payload TEXT,created REAL,PRIMARY KEY(account_id,run_id,book_hash));
                INSERT OR IGNORE INTO library_versions SELECT account_id,run_id,book_hash,payload,created FROM library;
            ''')

    @contextmanager
    def db(self,*,write=False):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            if write:db.execute('BEGIN IMMEDIATE')
            with db:yield db
        finally:db.close()
