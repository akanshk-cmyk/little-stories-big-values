"""Strict account APIs. Actor identity comes exclusively from server sessions."""
from typing import Literal
from pydantic import Field,field_validator
from backend.contracts.common import Contract,Identifier,Digest,Text,parse_payload


class Credentials(Contract):
    login:str=Field(min_length=3,max_length=120)
    password:str=Field(min_length=12,max_length=128,repr=False)

class PasswordConfirmation(Contract):
    current_password:str=Field(min_length=12,max_length=128,repr=False)

class PasswordChange(PasswordConfirmation):
    new_password:str=Field(min_length=12,max_length=128,repr=False)

class PasswordRecovery(Contract):
    login:str=Field(min_length=3,max_length=120)
    recovery_code:str=Field(min_length=32,max_length=128,repr=False)
    new_password:str=Field(min_length=12,max_length=128,repr=False)

class DeleteChild(Contract):
    confirm:bool

class ParentProfile(Contract):
    display_name:str=Field(min_length=1,max_length=80)
    email:str|None=Field(default=None,max_length=254)

    @field_validator('display_name')
    @classmethod
    def clean_name(cls,value):
        if not value.strip() or any(ord(c)<32 for c in value):raise ValueError('Enter your name.')
        return value.strip()

    @field_validator('email')
    @classmethod
    def clean_email(cls,value):
        import re
        if value is None or not value.strip():return None
        value=value.strip()
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+",value):
            raise ValueError('Enter a valid email address.')
        local,domain=value.rsplit('@',1)
        if len(local)>64 or local.startswith('.') or local.endswith('.') or '..' in local:raise ValueError('Enter a valid email address.')
        return local+'@'+domain.lower()


class Child(Contract):
    name:str=Field(min_length=1,max_length=80)
    age:int=Field(ge=3,le=8)


class Checkout(Contract):
    request_key:Identifier
    outcome:Literal['success','declined','cancelled']


class Import(Contract):
    request_key:Identifier
    consent:bool
    items:list[dict[str,str]]=Field(max_length=100)


class AssetReview(Contract):
    approve:bool
    rights_basis:Text
    gentle:bool
    notes:Text


class SceneReview(Contract):
    run_id:Identifier
    draft_hash:Digest
    page_number:int=Field(ge=1,le=6)
    asset_id:Identifier
    matches:bool
    notes:Text


class OutputVersionRequest(Contract):
    book_hash:Digest


class Narration(OutputVersionRequest):
    voice_id:Identifier
    model_id:Identifier


class AccountAPI:
    def __init__(self,accounts,entitlements,registry,workflow=None,hosted_sources=None):
        if hosted_sources is not None and hosted_sources.accounts is not accounts:
            raise ValueError('Hosted source preparation must use the same account authority.')
        self.accounts=accounts;self.entitlements=entitlements;self.registry=registry;self.workflow=workflow
        self.hosted_sources=hosted_sources

    def handle(self,method,parts,actor,data=None):
        tail=parts[2:];owner=actor['account_id']
        self.accounts.require(owner,role=None)
        if tail[:1]==['hosted-sources']:
            if not self.hosted_sources or not self.hosted_sources.enabled:
                raise LookupError('Hosted source preparation is not enabled.')
            from backend.ingestion.hosted_http_api import handle
            return handle(self.hosted_sources,method,tail[1:],actor,data)
        if method=='GET':
            if tail==['runs']:
                workflow=self._workflow()
                with workflow.repository.db() as db:
                    rows=list(db.execute('SELECT id,payload FROM runs WHERE owner=? ORDER BY rowid DESC LIMIT 20',(owner,)))
                import json
                requests=[]
                for row in rows:
                    request=json.loads(row['payload'])['request'];state=workflow.status(row['id'],owner)['values']
                    requests.append(dict(run_id=row['id'],status=state.get('status','queued'),idea=request.get('idea',''),objective=request['objective']))
                return dict(requests=requests),200
            if len(tail)==3 and tail[0]=='runs' and tail[2]=='recovery':
                return self._workflow().recovery_options(tail[1],owner),200
            if tail==['capabilities']:
                complete=bool(self.workflow and self.workflow.enabled and getattr(self.workflow,'approval_policy',None) in ('complete-book-review-v1','script-first-review-v1'))
                return dict(single_complete_review=complete,parent_save_flow=bool(self.workflow and getattr(self.workflow,'source_mode',None)=='local_ramayan_text_only'),source_mode=getattr(self.workflow,'source_mode',None),narration_enabled=bool(self.workflow and getattr(self.workflow,'narration_enabled',True)),narration_after_approval=bool(self.workflow and getattr(self.workflow,'approval_policy',None)=='script-first-review-v1'),voice_options=list(self.workflow.voice_options) if complete else [],
                    original_fiction_enabled=bool(self.workflow and 'original_inspired' in self.workflow.adapters.capabilities.story_types),
                    hosted_sources_enabled=bool(self.hosted_sources and self.hosted_sources.enabled),
                    hosted_catalog_enabled=bool(self.hosted_sources and getattr(self.hosted_sources,'catalog',None))),200
            if len(tail)==3 and tail[0]=='runs' and tail[2]=='complete-review':
                return self._complete_workflow().review_book(tail[1],owner),200
            if len(tail)==3 and tail[0]=='runs' and tail[2]=='status':
                return self._workflow().status(tail[1],owner),200
            if len(tail)==3 and tail[0]=='runs' and tail[2]=='text-review':
                return self._workflow().text_review(tail[1],owner),200
            if tail==['me']:return dict(account_id=owner,role=actor['role'],billing_is_simulated=True,profile=self.accounts.profile(owner)),200
            if tail==['children']:return dict(children=self.accounts.children(owner)),200
            if tail==['operation-usage']:return self.entitlements.operation_usage(owner),200
            if tail==['balance']:return self.entitlements.balance(owner),200
            if tail==['billing-history']:return dict(receipts=self.entitlements.receipts(owner),is_simulated=True),200
            if tail==['library']:return dict(books=self.entitlements.library(owner)),200
            if len(tail)==3 and tail[0]=='runs' and tail[2]=='review':
                workflow=self._workflow();run=tail[1]
                state=workflow.status(run,owner)['values']
                if not state.get('draft_id'):raise LookupError('The story draft is not ready.')
                return dict(status=state['status'],book_hash=state.get('book_version_hash'),
                    request=state['request'],draft=workflow.repository.load(run,state['draft_id']),
                    review=state['review'],context=workflow.repository.load(run,'context'),
                    assets=(workflow.repository.load(run,state['asset_manifest_id']) if state.get('asset_manifest_id') else None)),200
            if tail==['admin','summary']:
                self.accounts.require(owner,'admin')
                with self.accounts.store.db() as db:
                    return dict(telemetry='measured_local_records',accounts=db.execute('SELECT count(*) FROM accounts').fetchone()[0],
                        children=db.execute('SELECT count(*) FROM children').fetchone()[0],completed_books=db.execute('SELECT count(*) FROM library').fetchone()[0],
                        simulated_checkouts=db.execute('SELECT count(*) FROM checkouts').fetchone()[0]),200
        if method=='POST':
            if tail==['profile']:
                return dict(profile=self.accounts.update_profile(owner,data)),200
            if tail==['password']:
                command=parse_payload(PasswordChange,data)
                return self.accounts.change_password(owner,command.current_password,command.new_password),200
            if tail==['recovery-code']:
                command=parse_payload(PasswordConfirmation,data)
                return self.accounts.create_recovery_code(owner,command.current_password),200
            if len(tail)==3 and tail[0]=='children' and tail[2] in ('update','delete'):
                if tail[2]=='update':
                    command=parse_payload(Child,data)
                    return self.accounts.update_child(owner,tail[1],command.name,command.age),200
                command=parse_payload(DeleteChild,data)
                if command.confirm is not True:raise ValueError('Confirm deletion of this child profile.')
                return self.accounts.delete_child(owner,tail[1]),200
            if tail==['preflight']:
                from backend.contracts import StoryRequest
                from backend.original_fiction import resolve_story_intent
                request=parse_payload(StoryRequest,data)
                profiles=self.accounts.children(owner)
                if not any(c['id']==request.child_profile_id and c['age']==request.age for c in profiles):
                    raise ValueError('Choose a child profile and its matching age.')
                if getattr(self.workflow,'source_mode',None)!='local_ramayan_text_only':
                    raise LookupError('Request preflight is unavailable in this workflow.')
                if request.story_type=='original_inspired' and 'original_inspired' not in self.workflow.adapters.capabilities.story_types:
                    return dict(status='UNSUPPORTED',message='Original story creation is unavailable in this workflow.'),200
                return resolve_story_intent(request).model_dump(mode='json'),200
            if tail==['runs']:
                from backend.contracts.complete_book import CompleteStoryStart
                command=parse_payload(CompleteStoryStart,data);workflow=self._complete_workflow()
                if command.request.narration_requested and not any(
                    voice['voice_id']==command.options.voice_id and voice['model_id']==command.options.voice_model
                    for voice in workflow.voice_options):raise ValueError('Choose an available authorized narration voice.')
                run=workflow.start(owner,command.request_key,command.request.model_dump(mode='json'),
                    options=command.options.model_dump(mode='json'),provider_mode=command.provider_mode,
                    generation_mode=workflow.public_generation_mode)
                workflow.submit(run,owner)
                return dict(run_id=run,status='accepted'),202
            if tail==['children']:
                command=parse_payload(Child,data)
                return self.accounts.add_child(owner,command.name,command.age),201
            if tail==['checkout']:
                command=parse_payload(Checkout,data)
                return self.entitlements.checkout(owner,command.request_key,command.outcome),200
            if tail==['import-history']:
                command=parse_payload(Import,data)
                return self.entitlements.import_history(owner,command.request_key,command.items,consent=command.consent),200
            if len(tail)==3 and tail[0]=='assets' and tail[2]=='review':
                command=parse_payload(AssetReview,data)
                return self.registry.review(owner,tail[1],**command.model_dump()),200
            if tail==['scene-review']:
                self.accounts.require(owner,'admin');command=parse_payload(SceneReview,data)
                workflow=self._workflow()
                # Admin review is allowed to read a selected run explicitly,
                # never to impersonate its parent for approval or billing.
                with workflow.repository.db() as db:
                    row=db.execute('SELECT owner FROM runs WHERE id=?',(command.run_id,)).fetchone()
                if not row:raise LookupError('Story not found.')
                state=workflow.status(command.run_id,row['owner'])['values']
                if state.get('draft_hash')!=command.draft_hash:raise ValueError('Review the current draft.')
                from backend.contracts import StoryDraft
                from backend.workflows.story_graph import PreparedContext
                draft=parse_payload(StoryDraft,workflow.repository.load(command.run_id,state['draft_id']))
                context=parse_payload(PreparedContext,workflow.repository.load(command.run_id,'context'))
                return self.registry.inspect_scene(owner,draft,context.characters,command.page_number,command.asset_id,
                    matches=command.matches,notes=command.notes),200
            if len(tail)==3 and tail[0]=='runs':
                workflow=self._workflow();run=tail[1];action=tail[2]
                if action=='book-decision':
                    return dict(values=self._complete_workflow().decide(run,owner,data)),200
                if action=='cancel':
                    if data!={}:raise ValueError('Cancellation accepts no overrides.')
                    return dict(values=workflow.cancel(run,owner)),200
                if action=='resume':
                    if data!={}:raise ValueError('Continuation accepts no overrides.')
                    return workflow.resume_saved(run,owner),202
                if action=='refresh-assets':
                    if data!={}:raise ValueError('This operation accepts no overrides.')
                    return dict(values=workflow.refresh_assets(run,owner)),200
                if action=='approve':
                    raise ValueError('Use approve-text; parent consent covers story text only.')
                if action=='approve-text':
                    from backend.contracts.consent import TextApprovalRequest
                    command=parse_payload(TextApprovalRequest,data)
                    return dict(values=workflow.approve_text(run,owner,command.text_hash),
                        text_approval=workflow.text_review(run,owner)),200
                if action=='authorize-narration':
                    command=parse_payload(Narration,data)
                    return workflow.authorize_narration(run,owner,command.book_hash,
                        dict(voice_id=command.voice_id,model_id=command.model_id)),200
        raise LookupError('Account endpoint not found.')

    def _workflow(self):
        if self.workflow is None or not self.workflow.enabled:raise LookupError('The managed story workflow is not enabled.')
        return self.workflow

    def _complete_workflow(self):
        workflow=self._workflow()
        if getattr(workflow,'approval_policy',None) not in ('complete-book-review-v1','script-first-review-v1'):raise LookupError('The complete-book workflow is not connected.')
        return workflow
