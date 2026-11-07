"""Single-parent loopback server. Start with: python -m backend.server"""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
from io import BytesIO
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import posixpath
from pathlib import Path
import re
import secrets
from pydantic import ValidationError
from .contracts.legacy import LegacyGenerationEnvelope
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlparse, unquote
from uuid import uuid4

from .festival_engine import ROOT, CATALOG, UserError, make_book, provider_json, spoken_tracks, validate_story
from .pdf_export_service import render_pdf
from .original_story_engine import make_original_book
from .reviewer_agent import configuration as reviewer_configuration
from .qa_workflow import run_qa, revise_book, blocked_result, POLICY_VERSION, MAX_CORRECTIONS, WRITABLE
from .source_ingestion import document as extract_document, web_source, MAX_BYTES as SOURCE_MAX_BYTES
from .env_config import load_local_env
from .source_grounding import review_source, retrieve

load_local_env()

DATA=Path(os.environ.get('BALA_DATA_DIR',ROOT/'.local')).resolve()
DATA.mkdir(parents=True,exist_ok=True)
SECRET_FILE=DATA/'session-secret'
if not SECRET_FILE.exists():SECRET_FILE.write_bytes(secrets.token_bytes(32))
SECRET=SECRET_FILE.read_bytes()
LOCKS={};LOCK_GUARD=threading.Lock()
SOURCE_WORKSPACE_GUARD=threading.Lock()

@contextmanager
def database():
    connection=sqlite3.connect(DATA/'stories.sqlite',timeout=20);connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA journal_mode=WAL')
        with connection:yield connection
    finally:connection.close()

with database() as connection:
    connection.executescript('''CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY,owner TEXT NOT NULL,current_version INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS versions(book_id TEXT,version INTEGER,payload TEXT,digest TEXT,created REAL,PRIMARY KEY(book_id,version));
      CREATE TABLE IF NOT EXISTS approvals(book_id TEXT,version INTEGER,digest TEXT,owner TEXT,created REAL,PRIMARY KEY(book_id,version));
      CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,book_id TEXT,version INTEGER,digest TEXT,payload TEXT,created REAL);
      CREATE TABLE IF NOT EXISTS qa_cycles(book_id TEXT PRIMARY KEY,used INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,owner TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);''')

def canonical(book):return json.dumps(book,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(book):return hashlib.sha256(canonical(book).encode()).hexdigest()
def csrf(owner):return hmac.new(SECRET,('csrf:'+owner).encode(),hashlib.sha256).hexdigest()
def book_lock(book_id):
    with LOCK_GUARD:return LOCKS.setdefault(book_id,threading.Lock())

def banner_manifest(slot='appearance'):
    manifest=DATA/slot/'active.json'
    if not manifest.is_file():return None
    record=json.loads(manifest.read_text(encoding='utf-8'))
    if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp)',record.get('file','')):return None
    return record if (manifest.parent/record['file']).is_file() else None

def save_banner(content,slot='appearance'):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(BytesIO(content)) as image:
            extension={'PNG':'png','JPEG':'jpg','WEBP':'webp'}.get(image.format)
            if not extension or image.width*image.height>25000000 or getattr(image,'is_animated',False):raise UserError('Choose a still PNG, JPG or WebP under 25 megapixels.')
            width,height=image.size;image.verify()
    except (UnidentifiedImageError,OSError,ValueError):raise UserError('The selected picture is not a valid PNG, JPG or WebP.') from None
    folder=DATA/slot;folder.mkdir(parents=True,exist_ok=True)
    checksum=hashlib.sha256(content).hexdigest();filename=checksum+'.'+extension
    (folder/filename).write_bytes(content)
    record={'file':filename,'digest':checksum,'mime':{'png':'image/png','jpg':'image/jpeg','webp':'image/webp'}[extension],'width':width,'height':height}
    pending=folder/(uuid4().hex+'.json');pending.write_text(json.dumps(record),encoding='utf-8');pending.replace(folder/'active.json')
    return record

def load_book(book_id,owner,approved=False,expected=None):
    if not re.fullmatch('[a-f0-9]{32}',book_id):raise UserError('Story not found.',404)
    with database() as connection:
        row=connection.execute('SELECT v.* FROM books b JOIN versions v ON v.book_id=b.id AND v.version=b.current_version WHERE b.id=? AND b.owner=?',(book_id,owner)).fetchone()
        if not row:raise UserError('Story not found in this parent session.',404)
        approval=connection.execute('SELECT 1 FROM approvals WHERE book_id=? AND version=? AND digest=? AND owner=?',(book_id,row['version'],row['digest'],owner)).fetchone()
        review=connection.execute('SELECT payload FROM reviews WHERE book_id=? AND version=? AND digest=? ORDER BY created DESC LIMIT 1',(book_id,row['version'],row['digest'])).fetchone()
        cycles=connection.execute('SELECT used FROM qa_cycles WHERE book_id=?',(book_id,)).fetchone()
    if expected is not None and row['version']!=expected:raise UserError('This story changed. Reload and review the current version.',409)
    if approved and not approval:raise UserError('Review and approve this exact story version before export or narration.',409)
    result={'id':book_id,'version':row['version'],'digest':row['digest'],'approved':bool(approval),'book':json.loads(row['payload'])}
    if review:
        saved=json.loads(review['payload']);_,_,prompt_hash,playbook_hash=reviewer_configuration()
        result['reviewer']={key:value for key,value in saved.items() if key!='bundle'}
        result['reviewer']['configurationCurrent']=saved['promptHash']==prompt_hash and saved['playbookHash']==playbook_hash
    saved=result.get('reviewer',{})
    result['qaReady']=bool(saved.get('configurationCurrent') and saved.get('qaPolicyVersion')==POLICY_VERSION
                           and saved.get('bookVersion')==result['version'] and saved.get('bookDigest')==result['digest']
                           and saved.get('report',{}).get('verdict')=='PASS')
    result['correctionCyclesUsed']=cycles['used'] if cycles else 0
    result['maxCorrectionCycles']=MAX_CORRECTIONS
    if approved:require_qa(result)
    return result

def require_qa(record):
    if not record.get('qaReady'):
        raise UserError('QA must PASS for this exact version and current Playbook before parent approval, PDF or narration. Resolve the routed QA findings first.',409)

def save_qa(record,result):
    result.update({'id':uuid4().hex,'bookVersion':record['version'],'bookDigest':record['digest'],'createdAt':time.time()})
    with database() as connection:
        connection.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?)',(result['id'],record['id'],record['version'],record['digest'],canonical(result),result['createdAt']))

def check_qa(record):
    result=run_qa(record,artifact_inventory(record),record['correctionCyclesUsed'])
    save_qa(record,result)

def consume_correction(record):
    if record['correctionCyclesUsed']>=MAX_CORRECTIONS:
        raise UserError('Two correction cycles have been used. Stop and resolve the remaining requirements with the parent. This draft is not ready for approval.',409)
    with database() as connection:
        connection.execute('INSERT INTO qa_cycles VALUES(?,?) ON CONFLICT(book_id) DO UPDATE SET used=excluded.used',
                           (record['id'],record['correctionCyclesUsed']+1))

def save_version(record,book):
    version=record['version']+1
    with database() as connection:
        connection.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(record['id'],version,canonical(book),digest(book),time.time()))
        connection.execute('UPDATE books SET current_version=? WHERE id=?',(version,record['id']))

def artifact_inventory(record):
    folder=DATA/'artifacts'/record['id']/record['digest']
    def inspect(path):
        if not path.is_file():return {'exists':False,'contentInspectionPerformed':False}
        content=path.read_bytes()
        return {'exists':True,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'contentInspectionPerformed':False}
    return {'bookVersion':record['version'],'bookDigest':record['digest'],'parentApproved':record['approved'],
      'generationProvenance':record['book']['generation'],'textOnly':True,
      'estimatedTextMinutes':round(len(' '.join(record['book']['pages']).split())/100,1),
      'audioDurationMeasured':False,'expertCulturalApprovalRecorded':False,
      'pdf':inspect(folder/'book.pdf'),'audio':[inspect(folder/f'track-{i}.mp3') for i in range(len(spoken_tracks(record['book'])))],
      'limitations':'File existence and hashes are verified; PDF content and audio fidelity/pronunciation have not been inspected by this Reviewer.'}

def create_book(book,owner):
    book_id=uuid4().hex
    with database() as connection:
        connection.execute('INSERT INTO books VALUES(?,?,1)',(book_id,owner))
        connection.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(book_id,1,canonical(book),digest(book),time.time()))
    record=load_book(book_id,owner)
    check_qa(record)
    return load_book(book_id,owner)

def save_source(source,owner):
    source={**source,'id':uuid4().hex,'createdAt':time.time()}
    with database() as connection:
        connection.execute('INSERT INTO sources VALUES(?,?,?,?)',(source['id'],owner,canonical(source),source['createdAt']))
    return source

def load_source(source_id,owner):
    if not re.fullmatch('[a-f0-9]{32}',source_id):raise UserError('Source not found.',404)
    with database() as connection:
        row=connection.execute('SELECT payload FROM sources WHERE id=? AND owner=?',(source_id,owner)).fetchone()
    if not row:raise UserError('Source not found in this parent session.',404)
    return json.loads(row['payload'])

def story_source_context(data,owner):
    ids=data.get('sourceIds',[])
    if not isinstance(ids,list) or len(ids)>3 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
        raise UserError('Select up to three distinct source records.')
    if not ids:return None
    if data.get('mode')!='generate':raise UserError('Uploaded sources cannot be used by prewritten examples.',409)
    request=data.get('request')
    if not isinstance(request,dict):raise UserError('Confirm the story request first.')
    if request.get('language')!='English':raise UserError('Source-grounded generation currently supports English only.',409)
    return retrieve([load_source(i,owner) for i in ids],request)

def record_source_use(context,record,owner):
    if not context:return
    for source_id in context['sourceIds']:
        with book_lock('source-'+source_id):
            source=load_source(source_id,owner)
            source['usedForGeneration']=True
            source['lastUsedBookId']=record['id']
            source['lastUsedAt']=time.time()
            with database() as connection:
                connection.execute('UPDATE sources SET payload=? WHERE id=? AND owner=?',(canonical(source),source_id,owner))

class Handler(SimpleHTTPRequestHandler):
    server_version='BalaKathaLocal/1'
    def is_ui_asset(self):
        path=urlparse(self.path).path.lower()
        return not path.startswith('/api/') and (path=='/' or path.endswith(('.html','.js','.css')))
    def end_headers(self):
        if self.is_ui_asset():
            self.send_header('Cache-Control','no-store, max-age=0')
            self.send_header('Pragma','no-cache')
            self.send_header('Expires','0')
        super().end_headers()
    def send_head(self):
        if self.is_ui_asset() and 'If-Modified-Since' in self.headers:
            del self.headers['If-Modified-Since']
        return super().send_head()
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'prototype-v2'),**kwargs)
    def log_message(self,fmt,*args):pass
    def session(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:pass
        value=cookie.get('bala_parent');value=value.value if value else ''
        parts=value.split('.')
        if len(parts)==2 and re.fullmatch('[a-f0-9]{48}',parts[0]) and hmac.compare_digest(parts[1],hmac.new(SECRET,parts[0].encode(),hashlib.sha256).hexdigest()):return parts[0],None
        owner=secrets.token_hex(24);signature=hmac.new(SECRET,owner.encode(),hashlib.sha256).hexdigest()
        return owner,f'bala_parent={owner}.{signature}; HttpOnly; SameSite=Strict; Path=/'
    def send_bytes(self,content,mime,status=200,filename=None):
        self.send_response(status)
        self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if getattr(self,'new_cookie',None):self.send_header('Set-Cookie',self.new_cookie)
        if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(content)
    def send_json(self,value,status=200):self.send_bytes(json.dumps(value,ensure_ascii=False).encode(),'application/json; charset=utf-8',status)
    def api_path(self):
        parsed=urlparse(self.path)
        return parsed.path.rstrip('/').split('/')[1:],parse_qs(parsed.query)
    def source_workspace(self):
        with SOURCE_WORKSPACE_GUARD:
            workspace=getattr(self.server,'source_workspace',None)
            if workspace is None:
                from .ingestion.workspace import SourceWorkspace
                workspace=SourceWorkspace(DATA/'source-workspace')
                self.server.source_workspace=workspace
            return workspace
    def workspace_request(self,method,parts,query,data=None):
        from .ingestion.http_api import handle
        try:
            workspace=self.source_workspace()
            if method=='GET' and len(parts)==4 and parts[3]=='original':
                return self.send_bytes(workspace.original(parts[2],self.owner).read_bytes(),'application/pdf')
            payload,status=handle(workspace,method,parts,query,self.owner,data)
        except PermissionError:raise UserError('Source not found in this parent session.',404) from None
        except LookupError:raise UserError('Source endpoint not found.',404) from None
        except ValidationError:raise UserError('Complete the review fields using the current passage version.',400) from None
        except ValueError as error:raise UserError(str(error),400) from None
        except (TypeError,KeyError):raise UserError('Check the page, version, review fields and source permissions.',400) from None
        except RuntimeError:raise UserError('This source is being processed. Please try again shortly.',409) from None
        return self.send_json(payload,status)
    def workflow_request(self,method,parts,data=None):
        service=getattr(self.server,'workflow_service',None)
        if service is None or not service.enabled:raise UserError('Endpoint not found.',404)
        from .workflows.http_api import handle
        try:
            payload,status=handle(service,method,parts,self.owner,data)
        except PermissionError:raise UserError('Story run not found in this parent session.',404) from None
        except LookupError:raise UserError('Endpoint not found.',404) from None
        except (ValueError,TypeError):raise UserError('Invalid workflow request or book version.',400) from None
        except RuntimeError:raise UserError('This story run is already being processed.',409) from None
        return self.send_json(payload,status)
    def initialize_request(self,write=False):
        self._authorized_static_target=None
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed:raise UserError('This local server accepts loopback hostnames only.',403)
        self.owner,self.new_cookie=self.session()
        api=getattr(self.server,'account_api',None)
        self.account_actor=None;self.account_token=None
        decoded_path=unquote(urlparse(self.path).path)
        if any(character in decoded_path for character in ('\\',':','\x00')):
            raise UserError('Page not found.',404)
        normalized=posixpath.normpath(decoded_path)
        # Authorize the same resolved destination that GET/HEAD will serve.
        # Windows can resolve case, trailing dots/spaces and junction aliases
        # to one directory despite different URL spellings.
        root=Path(self.directory).resolve()
        target=Path(super().translate_path(self.path)).resolve()
        if not target.is_relative_to(root):raise UserError('Page not found.',404)
        relative=target.relative_to(root).parts
        area=relative[0].rstrip(' .').casefold() if relative else ''
        self._authorized_static_target=target
        if area=='qa':
            raise UserError('Development checks are not exposed by the app server.',404)
        if api:
            public=normalized in ('/api/health','/api/accounts/register','/api/accounts/login','/api/accounts/recover')
            cookies=SimpleCookie()
            try:cookies.load(self.headers.get('Cookie',''))
            except Exception:pass
            token=cookies.get('bala_account')
            self.account_token=token.value if token else None
            if self.account_token:
                try:self.account_actor=api.accounts.authenticate(self.account_token)
                except PermissionError:
                    if not public:raise UserError('Please sign in again.',401) from None
                    self.account_token=None
                if self.account_actor:
                    self.owner=self.account_actor['account_id'];self.new_cookie=None
            if (normalized.startswith('/api/') and not public) or area=='admin':
                if not self.account_actor:raise UserError('Sign in to access your account.',401)
            if area=='admin':
                if self.account_actor['role']!='admin':raise UserError('Administrator access is required.',403)
            if normalized.startswith(('/api/books','/api/original-books','/api/workflow-runs')):
                raise UserError('Use the account story workflow for approved books.',409)
            if write and normalized.startswith('/api/appearance/') and self.account_actor['role']!='admin':
                raise UserError('Administrator access is required for shared artwork.',403)
        if write:
            origin=self.headers.get('Origin')
            if origin and origin not in {f'http://{host}' for host in allowed}:raise UserError('Cross-origin writes are not allowed.',403)
            if api and normalized.startswith('/api/accounts/') and not public:
                try:api.accounts.authenticate(self.account_token,csrf=self.headers.get('X-Bala-CSRF',''))
                except PermissionError:raise UserError('Refresh your account session before making changes.',403) from None
            elif not hmac.compare_digest(self.headers.get('X-Bala-CSRF',''),csrf(self.owner)):raise UserError('Refresh the app before changing a story.',403)

    def translate_path(self,path):
        target=getattr(self,'_authorized_static_target',None)
        if path==self.path and target is not None:return str(target)
        return super().translate_path(path)

    def account_request(self,method,parts,data=None):
        from .ingestion.hosted_service import HostedServiceBusy
        from .accounts.identity import LoginFailure
        api=getattr(self.server,'account_api',None)
        if api is None:raise UserError('Endpoint not found.',404)
        try:
            if method=='POST' and parts[2:]==['recover']:
                from .accounts.http_api import PasswordRecovery
                from .contracts.common import parse_payload
                command=parse_payload(PasswordRecovery,data)
                return self.send_json(api.accounts.recover_password(command.login,command.recovery_code,command.new_password))
            if method=='GET' and parts[2:]==['csrf']:
                if self.headers.get('Origin') not in (None,'http://'+self.headers.get('Host','')) or self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'):
                    raise UserError('Use the account app to refresh your session.',403)
                return self.send_json({'csrf':api.accounts.session_csrf(self.account_token)})
            if method=='POST' and parts[2:] in (['register'],['login']):
                from .accounts.http_api import Credentials
                from .contracts.common import parse_payload
                command=parse_payload(Credentials,data)
                if parts[2]=='register':return self.send_json(api.accounts.register(command.login,command.password),201)
                session=api.accounts.login(command.login,command.password)
                self.new_cookie=f'bala_account={session.pop("token")}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'
                return self.send_json(session)
            if method=='POST' and parts[2:]==['logout']:
                if data!={}:raise ValueError('Logout accepts no fields.')
                api.accounts.logout(self.account_token)
                self.new_cookie='bala_account=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
                return self.send_json({'signed_out':True})
            payload,status=api.handle(method,parts,self.account_actor,data)
            return self.send_json(payload,status)
        except LoginFailure as error:
            if error.code=='rate_limited':raise UserError('Too many unsuccessful attempts. Please wait 15 minutes before trying again.',429) from None
            if parts[2:]==['recover']:raise UserError('Recovery details were not accepted. Check your login and one-time code.',401) from None
            raise UserError('Login or password was not accepted. Check your sign-in details. If you have not registered, choose Create parent account.',401) from None
        except PermissionError as error:
            message=str(error)
            if message in ('No story credits are available.','Choose a child profile belonging to this account and confirm its age.'):
                raise UserError(message,403) from None
            raise UserError('This account cannot perform that action.',403) from None
        except LookupError:raise UserError('Account record or endpoint not found.',404) from None
        except HostedServiceBusy:raise UserError('Source processing is busy. Please try again shortly.',409) from None
        except ValidationError as error:
            if method=='POST' and parts[2:] in (['preflight'],['runs']):
                fields=sorted({str(part) for issue in error.errors(include_input=False) for part in issue['loc'] if isinstance(part,str)})
                raise UserError('Check these story fields: '+', '.join(fields or ['story preferences'])+'. Your story has not started.',400) from None
            raise UserError('Check the account fields, story version and review status.',400) from None
        except (ValueError,TypeError) as error:
            message=str(error)
            if method=='POST' and parts[2:] in (['preflight'],['runs']) and message.startswith(('Choose Rama or Hanuman,','This request key was already used with different preferences.','Choose a child profile and its matching age.','The available Ramayan passages do not cover','No available episode supports','Should ')):
                raise UserError(message,400) from None
            raise UserError('Check the account fields, story version and review status.',400) from None

    def do_HEAD(self):
        try:
            self.initialize_request()
            if urlparse(self.path).path.startswith('/api/'):raise UserError('Use GET for this account request.',405)
            return super().do_HEAD()
        except UserError as error:self.send_json({'error':str(error)},error.status)
    def do_GET(self):
        try:
            self.initialize_request()
            if not self.path.startswith('/api/'):
                if urlparse(self.path).path=='/' and getattr(self.server,'account_api',None):
                    self.send_response(302);self.send_header('Location','/account.html');self.send_header('Content-Length','0');self.end_headers()
                    return
                if self.path.split('?')[0].startswith('/qa'):raise UserError('Development checks are not exposed by the app server.',404)
                return super().do_GET()
            parts,query=self.api_path()
            if parts[:3]==['api','accounts','runs'] and len(parts)==5 and parts[4] in ('audio-preview','audio'):
                api=getattr(self.server,'account_api',None)
                if not api:raise UserError('Endpoint not found.',404)
                try:
                    workflow=api._complete_workflow()
                    content,mime=workflow.audio(parts[3],self.owner,query.get('book_hash',[''])[0],final=parts[4]=='audio')
                    return self.send_bytes(content,mime)
                except (PermissionError,ValueError):raise UserError('This narration version is not available.',403) from None
                except LookupError:raise UserError('Narration is not available.',404) from None
            if parts[:2]==['api','accounts'] and len(parts)==5 and parts[2]=='runs' and parts[4] in ('preview','pdf'):
                api=getattr(self.server,'account_api',None)
                if not api:raise UserError('Endpoint not found.',404)
                try:
                    workflow=api._workflow()
                    if parts[4]=='preview':return self.send_bytes(workflow.preview(parts[3],self.owner).encode(),'text/html; charset=utf-8')
                    return self.send_bytes(workflow.pdf(parts[3],self.owner,query.get('book_hash',[''])[0]),'application/pdf',filename='storybook.pdf')
                except PermissionError:raise UserError('This exact book is not available for this account.',403) from None
                except LookupError:raise UserError('The managed story workflow is not enabled.',404) from None
            if parts[:3]==['api','accounts','assets'] and len(parts)==4:
                api=getattr(self.server,'account_api',None)
                if not api:raise UserError('Endpoint not found.',404)
                try:
                    api.accounts.require(self.owner,'admin')
                    with api.accounts.store.db() as db:row=db.execute('SELECT payload FROM image_assets WHERE id=?',(parts[3],)).fetchone()
                    if not row:raise UserError('Image not found.',404)
                    payload=json.loads(row[0])
                    return self.send_bytes(api.registry._bytes(payload),payload['reference']['media_type'])
                except PermissionError:raise UserError('Administrator access is required.',403) from None
            if parts[:2]==['api','accounts']:return self.account_request('GET',parts)
            if parts[:2]==['api','source-workspace']:return self.workspace_request('GET',parts,query)
            if parts[:2]==['api','workflow-runs']:return self.workflow_request('GET',parts)
            if parts==['api','health']:
                account_api=getattr(self.server,'account_api',None)
                return self.send_json({'csrf':csrf(self.owner),'accountsEnabled':account_api is not None,
                    'managedWorkflowEnabled':bool(account_api and account_api.workflow and account_api.workflow.enabled),
                    'singleCompleteReviewEnabled':bool(account_api and account_api.workflow and account_api.workflow.enabled and getattr(account_api.workflow,'approval_policy',None) in ('complete-book-review-v1','script-first-review-v1')),
                    'nebiusConfigured':bool(os.environ.get('NEBIUS_API_KEY')),'reviewerConfigured':bool(os.environ.get('NEBIUS_API_KEY')),'narrationConfigured':bool(os.environ.get('ELEVENLABS_API_KEY') and os.environ.get('ELEVENLABS_VOICE_ID')),'model':os.environ.get('NEBIUS_MODEL','Qwen/Qwen3-30B-A3B-Instruct-2507'),'mode':'local_single_parent'})
            if parts==['api','provider-readiness']:
                from .providers.config import readiness
                try:return self.send_json(readiness())
                except ValueError:raise UserError('Check the configured provider mode.',400) from None
            if parts==['api','festivals']:return self.send_json(list(CATALOG.values()))
            if parts==['api','sources']:
                with database() as connection:
                    rows=connection.execute('SELECT payload FROM sources WHERE owner=? ORDER BY created DESC LIMIT 100',(self.owner,)).fetchall()
                return self.send_json([{key:value for key,value in json.loads(row['payload']).items() if key not in ('text','sections')} for row in rows])
            if len(parts)==3 and parts[:2]==['api','sources']:
                return self.send_json(load_source(parts[2],self.owner))
            if parts==['api','appearance']:
                record=banner_manifest()
                portrait=banner_manifest('appearance/characters/veyd')
                return self.send_json({'bannerUrl':f'/api/appearance/banner?v={record["digest"]}' if record else None,
                                       'veydPortraitUrl':f'/api/appearance/veyd?v={portrait["digest"]}' if portrait else None})
            if parts==['api','appearance','veyd']:
                record=banner_manifest('appearance/characters/veyd')
                if not record:raise UserError('Veyd illustration has not been added. Choose or paste the supplied picture on his card.',404)
                return self.send_bytes((DATA/'appearance/characters/veyd'/record['file']).read_bytes(),record['mime'])
            if parts==['api','appearance','banner']:
                record=banner_manifest()
                if not record:raise UserError('No custom Discover banner is saved.',404)
                return self.send_bytes((DATA/'appearance'/record['file']).read_bytes(),record['mime'])
            if len(parts)<3 or parts[:2]!=['api','books']:raise UserError('Endpoint not found.',404)
            book_id=parts[2]
            if len(parts)==3:return self.send_json(load_book(book_id,self.owner))
            if len(parts)==4 and parts[3]=='review-evidence':
                record=load_book(book_id,self.owner)
                with database() as connection:row=connection.execute('SELECT payload FROM reviews WHERE book_id=? AND version=? AND digest=? ORDER BY created DESC LIMIT 1',(book_id,record['version'],record['digest'])).fetchone()
                if not row:raise UserError('No independent review exists for this version.',404)
                return self.send_json(json.loads(row['payload']))
            version=int(query.get('version',['0'])[0]);record=load_book(book_id,self.owner,approved=True,expected=version)
            folder=DATA/'artifacts'/book_id/record['digest'];folder.mkdir(parents=True,exist_ok=True)
            if len(parts)==4 and parts[3]=='pdf':
                with book_lock(book_id):
                    output=folder/'book.pdf'
                    if not output.is_file():
                        try:render_pdf(record['book'],record['version'],record['digest'],output,DATA/'browser')
                        except (RuntimeError,ImportError) as error:raise UserError(str(error),503) from None
                load_book(book_id,self.owner,approved=True,expected=version)
                return self.send_bytes(output.read_bytes(),'application/pdf',filename=f'festival-story-v{version}.pdf')
            if len(parts)==5 and parts[3]=='audio':
                index=int(parts[4]);count=len(spoken_tracks(record['book']))
                if not 0<=index<count:raise UserError('Audio page not found.',404)
                output=folder/f'track-{index}.mp3'
                if not output.is_file():raise UserError('Narration has not been generated for this version.',404)
                return self.send_bytes(output.read_bytes(),'audio/mpeg')
            raise UserError('Endpoint not found.',404)
        except UserError as error:self.send_json({'error':str(error)},error.status)
        except (ValueError,TypeError):self.send_json({'error':'Invalid request parameter.'},400)
        except (BrokenPipeError,ConnectionResetError):pass
        except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):return
        except Exception:self.send_json({'error':'The local operation failed. Your saved story is retained.'},500)
    def do_POST(self):
        try:
            self.initialize_request(write=True)
            length=int(self.headers.get('Content-Length','0'))
            parts,_=self.api_path()
            if parts==['api','accounts','hosted-sources','upload']:
                api=getattr(self.server,'account_api',None)
                service=getattr(api,'hosted_sources',None)
                if not service or not service.enabled:raise UserError('Endpoint not found.',404)
                if length<=0 or length>50*1024*1024:raise UserError('Choose a PDF up to 50 MB.',413)
                if self.headers.get('Content-Type','').split(';')[0].strip()!='application/pdf':
                    raise UserError('Choose a PDF document.',415)
                try:
                    result=service.upload(self.owner,self.rfile,length,
                        title=unquote(self.headers.get('X-Document-Title','')),
                        edition=unquote(self.headers.get('X-Document-Edition','')))
                except PermissionError:raise UserError('This account cannot upload that source.',403) from None
                except ValueError:raise UserError('Check the PDF, title and edition details.',422) from None
                return self.send_json(result,201)
            if parts==['api','accounts','assets','upload']:
                api=getattr(self.server,'account_api',None)
                if not api:raise UserError('Endpoint not found.',404)
                try:api.accounts.require(self.owner,'admin')
                except PermissionError:raise UserError('Administrator access is required.',403) from None
                if length<=0 or length>15*1024*1024:raise UserError('Choose a static image up to 15 MB.',413)
                result=api.registry.add(self.owner,self.rfile.read(length),
                    characters=json.loads(self.headers.get('X-Asset-Characters','[]')),
                    style=unquote(self.headers.get('X-Asset-Style','')),description=unquote(self.headers.get('X-Asset-Description','')))
                return self.send_json(result,201)
            if parts==['api','source-workspace','upload']:
                from .ingestion.workspace import MAX_PDF_BYTES
                if length<=0 or length>MAX_PDF_BYTES:raise UserError('Choose a PDF up to 50 MB.',413)
                try:
                    workspace=self.source_workspace()
                    source_id=workspace.create(self.rfile,length,owner=self.owner,
                        title=unquote(self.headers.get('X-Document-Title','')),
                        edition=unquote(self.headers.get('X-Document-Edition','')))
                    workspace.submit(source_id,self.owner)
                except ValueError as error:raise UserError(str(error),422) from None
                return self.send_json({'id':source_id,'statusUrl':'/api/source-workspace/'+source_id},202)
            if parts==['api','appearance','veyd']:
                if length<=0 or length>15*1024*1024:raise UserError('Choose a PNG, JPG or WebP picture up to 15 MB.',413)
                with book_lock('veyd-portrait'):
                    record=save_banner(self.rfile.read(length),'appearance/characters/veyd')
                return self.send_json({'veydPortraitUrl':f'/api/appearance/veyd?v={record["digest"]}'})
            if parts==['api','sources','upload']:
                if length<=0 or length>SOURCE_MAX_BYTES:raise UserError('Choose a document up to 10 MB.',413)
                name=unquote(self.headers.get('X-Source-Name',''))
                source=extract_document(self.rfile.read(length),name)
                return self.send_json(save_source(source,self.owner),201)
            if parts==['api','appearance','banner']:
                if length<=0 or length>15*1024*1024:raise UserError('Choose a picture up to 15 MB.',413)
                with book_lock('app-appearance'):
                    record=save_banner(self.rfile.read(length))
                return self.send_json({'bannerUrl':f'/api/appearance/banner?v={record["digest"]}'})
            if length<=0 or length>160000:raise UserError('Request size is invalid.',413)
            try:data=json.loads(self.rfile.read(length))
            except (ValueError,UnicodeError):raise UserError('Request must contain valid JSON.') from None
            if not isinstance(data,dict):raise UserError('Expected an object.')
            if parts[:2]==['api','accounts']:return self.account_request('POST',parts,data)
            if parts[:2]==['api','source-workspace']:return self.workspace_request('POST',parts,{},data)
            if parts[:2]==['api','workflow-runs']:return self.workflow_request('POST',parts,data)
            if parts==['api','sources','url']:
                return self.send_json(save_source(web_source(data.get('url')),self.owner),201)
            if len(parts)==4 and parts[:2]==['api','sources'] and parts[3]=='review':
                with book_lock('source-'+parts[2]):
                    source=review_source(load_source(parts[2],self.owner),data,self.owner,time.time())
                    with database() as connection:
                        connection.execute('UPDATE sources SET payload=? WHERE id=? AND owner=?',(canonical(source),source['id'],self.owner))
                return self.send_json(source)
            if parts==['api','appearance','reset']:
                with book_lock('app-appearance'):
                    manifest=DATA/'appearance/active.json'
                    if manifest.is_file():manifest.unlink()
                return self.send_json({'bannerUrl':None})
            if parts==['api','books']:
                try:LegacyGenerationEnvelope.model_validate(data)
                except ValidationError:raise UserError('Story creation request has unknown or invalid fields.') from None
                context=story_source_context(data,self.owner)
                started=time.monotonic();book=make_book(data.get('request'),data.get('mode'),context)
                book['generation']['latencyMs']=round((time.monotonic()-started)*1000)
                record=create_book(book,self.owner);record_source_use(context,record,self.owner)
                return self.send_json(record,201)
            if parts==['api','original-books']:
                try:LegacyGenerationEnvelope.model_validate(data)
                except ValidationError:raise UserError('Story creation request has unknown or invalid fields.') from None
                context=story_source_context(data,self.owner)
                started=time.monotonic();book=make_original_book(data.get('request'),data.get('mode'),context)
                book['generation']['latencyMs']=round((time.monotonic()-started)*1000)
                record=create_book(book,self.owner);record_source_use(context,record,self.owner)
                return self.send_json(record,201)
            if len(parts)!=4 or parts[:2]!=['api','books']:raise UserError('Endpoint not found.',404)
            book_id,action=parts[2:];expected=data.get('version')
            if type(expected) is not int:raise UserError('Supply the current version.')
            with book_lock(book_id):
                record=load_book(book_id,self.owner,expected=expected);book=record['book']
                if action=='review':
                    if record['correctionCyclesUsed']>=MAX_CORRECTIONS and record.get('reviewer') and not record['qaReady']:
                        raise UserError('Two correction cycles have been used and QA has not passed. Resolve the remaining notes with the parent; further automatic review is stopped.',409)
                    check_qa(record)
                    return self.send_json(load_book(book_id,self.owner))
                if action=='correct':
                    report=record.get('reviewer',{}).get('report',{})
                    if record['qaReady']:raise UserError('QA has already passed. No correction is needed.',409)
                    routes=set(report.get('routes',[]))
                    if not routes or routes-WRITABLE:raise UserError('Resolve the routed parent preferences or reviewed-source evidence first. No automatic rewrite was run.',409)
                    if not os.environ.get('NEBIUS_API_KEY'):raise UserError('Nebius is not connected. No correction was generated.',503)
                    if data.get('confirmPaid') is not True:raise UserError('Explicitly request the correction call before continuing.',400)
                    consume_correction(record)
                    try:book=revise_book(record,report)
                    except UserError as error:
                        result=blocked_result(record,artifact_inventory(record),str(error))
                        save_qa(record,result)
                        return self.send_json(load_book(book_id,self.owner))
                    save_version(record,book)
                    check_qa(load_book(book_id,self.owner))
                    return self.send_json(load_book(book_id,self.owner))
                if action=='versions':
                    changes=data.get('changes',{})
                    if not isinstance(changes,dict) or set(changes)-{'title','pages','moral','discussionQuestion'}:raise UserError('Only editable story text may be changed.')
                    book.update(changes);validate_story(book,book['request']['language'])
                    book['estimatedMinutes']=round(len(' '.join(book['pages']).split())/100,1)
                    if digest(book)==record['digest']:return self.send_json(record)
                    if not record['qaReady']:consume_correction(record)
                    save_version(record,book)
                    check_qa(load_book(book_id,self.owner))
                    return self.send_json(load_book(book_id,self.owner))
                if action=='approve':
                    if data.get('digest')!=record['digest']:raise UserError('Version content changed. Review it again.',409)
                    if any(data.get(key) is not True for key in ('textReviewed','cultureReviewed','safetyReviewed')):raise UserError('Review the text, cultural context and child suitability before approval.')
                    require_qa(record)
                    validate_story(book,book['request']['language'])
                    with database() as connection:connection.execute('INSERT OR REPLACE INTO approvals VALUES(?,?,?,?,?)',(book_id,expected,record['digest'],self.owner,time.time()))
                    return self.send_json(load_book(book_id,self.owner))
                if action=='narrate':
                    load_book(book_id,self.owner,approved=True,expected=expected)
                    if not os.environ.get('ELEVENLABS_API_KEY'):raise UserError('ELEVENLABS_API_KEY is not configured. No audio was generated.',503)
                    voice=book['voice']
                    if not re.fullmatch('[A-Za-z0-9_-]{1,100}',voice['id']):raise UserError('The approved version has no narrator voice. Configure ELEVENLABS_VOICE_ID and create a new version before approval.',503)
                    folder=DATA/'artifacts'/book_id/record['digest'];folder.mkdir(parents=True,exist_ok=True)
                    tracks=spoken_tracks(book)
                    for index,text in enumerate(tracks):
                        output=folder/f'track-{index}.mp3'
                        if output.is_file():continue
                        load_book(book_id,self.owner,approved=True,expected=expected)
                        content,mime=provider_json(f'https://api.elevenlabs.io/v1/text-to-speech/{voice["id"]}?output_format=mp3_44100_128',{'text':text,'model_id':voice['model']},{'xi-api-key':os.environ['ELEVENLABS_API_KEY'],'Accept':'audio/mpeg'})
                        if not content or not mime.startswith('audio/'):raise UserError('Narration provider did not return an audio file. Completed clips are retained.',502)
                        output.write_bytes(content)
                    return self.send_json({'version':expected,'digest':record['digest'],'tracks':[f'/api/books/{book_id}/audio/{i}?version={expected}' for i in range(len(tracks))]})
            raise UserError('Endpoint not found.',404)
        except UserError as error:self.send_json({'error':str(error)},error.status)
        except (ValueError,TypeError):self.send_json({'error':'Invalid request parameter.'},400)
        except (BrokenPipeError,ConnectionResetError):pass
        except (ConnectionAbortedError,ConnectionResetError,BrokenPipeError):return
        except Exception:self.send_json({'error':'The local operation failed. Your saved story is retained.'},500)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8001)
    parser.add_argument('--accounts',action='store_true',help='Require password accounts for APIs and administrator pages.')
    args=parser.parse_args()
    print(f'Bala Katha local app: http://localhost:{args.port}',flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    if args.accounts:
        from .accounts.store import Store
        from .accounts.identity import Accounts
        from .accounts.entitlements import Entitlements
        from .accounts.assets import AssetRegistry
        from .accounts.http_api import AccountAPI
        accounts=Accounts(Store(DATA/'accounts'))
        server.account_api=AccountAPI(accounts,Entitlements(accounts),AssetRegistry(accounts))
    server.serve_forever()

if __name__=='__main__':main()
