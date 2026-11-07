"""One parent checkpoint for an exact complete book, including requested audio.

Opt-in v4 service: earlier text-consent runs/receipts are never migrated silently.
Narration and source qualification are trusted injections; absence blocks progress.
"""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from langgraph.types import Command
from langsmith import tracing_context
from backend.contracts import BookVersion,StoryDraft,StoryRequest,content_hash,parse_payload
from backend.contracts.complete_book import (CreationOptions,AudioArtifact,CompleteBookPreview,
    CompleteBookApproval,CompleteBookDecision)
from backend.contracts.common import canonical_json
from backend.workflows.checkpoints import run_lock,checkpoint_store
from backend.workflows.repository import UnknownOutcome
from .workflow import AccountAdapters,AccountWorkflowService


class CompleteApprovals:
    record_type=CompleteBookApproval
    def __init__(self,repository):
        self.repository=repository
        with repository.db() as db:db.execute('CREATE TABLE IF NOT EXISTS complete_book_approvals(owner TEXT,run_id TEXT,book_hash TEXT,payload TEXT,PRIMARY KEY(owner,run_id,book_hash))')

    def find(self,owner,run,book_hash):
        with self.repository.db() as db:r=db.execute('SELECT payload FROM complete_book_approvals WHERE owner=? AND run_id=? AND book_hash=?',(owner,run,book_hash)).fetchone()
        return parse_payload(self.record_type,json.loads(r[0])) if r else None

    def approve(self,owner,state,preview):
        with self.repository.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT payload FROM complete_book_approvals WHERE owner=? AND run_id=? AND book_hash=?',(owner,state['run_id'],preview.complete_book_hash)).fetchone()
            if old:return parse_payload(self.record_type,json.loads(old[0]))
            record=self.record_type(approval_id=uuid4().hex,owner_id=owner,run_id=state['run_id'],
                complete_book_hash=preview.complete_book_hash,base_book_hash=preview.book.book_version_hash,
                review_hash=preview.review_hash,approved_at=datetime.now(timezone.utc))
            db.execute('INSERT INTO workflow_approvals VALUES(?,?,?,?,?,?,?)',(record.approval_id,record.run_id,owner,
                record.base_book_hash,record.review_hash,state['playbook_version'],record.approved_at.isoformat()))
            db.execute('INSERT INTO complete_book_approvals VALUES(?,?,?,?)',(owner,record.run_id,record.complete_book_hash,record.model_dump_json()))
        return record


class CompleteBookAdapters(AccountAdapters):
    preview_type=CompleteBookPreview
    supports_complete_review=True
    approval_policy='complete-book-review-v1'

    def __init__(self,base,accounts,registry,*,media_folder,narrator=None,audio_checker=None,audio_authorize=None,evidence_resolver=None,media_policy_version='media-unconfigured-v1'):
        super().__init__(base,accounts,registry)
        self.version='account-complete-book-v4';self.media_folder=Path(media_folder)
        self.narrator=narrator;self.audio_checker=audio_checker;self.audio_authorize=audio_authorize or (lambda *_:False)
        self.media_policy_version=media_policy_version
        self.evidence_resolver=evidence_resolver

    def invoke_with_state(self,key,state,args,repository,owner):
        if key=='prepare':
            options=parse_payload(CreationOptions,state['creation_options'])
            if self.evidence_resolver:return self.evidence_resolver(owner,args[0],options)
            if options.selected_source_ids:
                from backend.providers.retry_policy import ProviderFailure
                raise ProviderFailure('evidence_missing')
        return super().invoke_with_state(key,state,args,repository,owner)

    def runtime_identity(self):return content_hash([super().runtime_identity(),self.approval_policy,self.media_policy_version])

    def approval_runtime_identity(self):return content_hash([super().approval_runtime_identity(),self.approval_policy,self.media_policy_version])

    def save_approval_runtime(self,state,repository,owner):
        repository.save(state['run_id'],'approval-runtime-'+state['complete_preview_hash'],dict(
            identity=self.approval_runtime_identity(),run_runtime=repository.get(state['run_id'],owner)['runtime_identity']))

    def operation_key(self,key,state,args):
        result=super().operation_key(key,state,args)
        if state.get('revision_epoch'):result+='-'+state['revision_epoch']
        if key.startswith('render-'):result+='-'+str(state.get('complete_preview_hash'))
        return result

    def media_path(self,run,audio_id):
        # Both names originate from sealed server records, never filesystem input.
        return self.media_folder/run/(audio_id+'.audio')

    def checked_audio(self,owner,state,preview):
        if not preview.audio:return
        req=parse_payload(StoryRequest,state['request']);options=parse_payload(CreationOptions,state['creation_options'])
        if self.audio_authorize(owner,req,options) is not True:raise PermissionError('Audio processing permission is unavailable.')
        audio=preview.audio;data=self.media_path(state['run_id'],audio.audio_id).read_bytes()
        if hashlib.sha256(data).hexdigest()!=audio.audio_id:raise PermissionError('Saved narration changed.')
        if audio.script_hash!=content_hash(preview.narration_script) or (audio.voice_id,audio.model_id)!=(options.voice_id,options.voice_model):raise PermissionError('Narration settings changed.')
        return data

    def require_audio_funding(self,owner,state):
        self.entitlements.require_generation(owner,state['run_id'],state['request_hash'])

    def assemble_complete_preview(self,state,repository,owner):
        self.check_text(state,repository,owner);self.check_book(state,repository,owner)
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        book=parse_payload(BookVersion,repository.load(state['run_id'],'book-'+state['book_version_hash']))
        req=parse_payload(StoryRequest,state['request']);options=parse_payload(CreationOptions,state['creation_options'])
        script=tuple([draft.title,*[p.text for p in draft.pages],draft.moral])
        audio=None
        if req.narration_requested:
            reason=None
            if not self.narrator or not self.audio_checker:reason='Requested narration is not available yet.'
            elif self.audio_authorize(owner,req,options) is not True:reason='Initial audio preparation authorization is not available.'
            if reason:return dict(status='assets_pending',asset_manifest_id=state['asset_manifest_id'],book_version_hash=book.book_version_hash,
                complete_preview_hash=None,incomplete_reasons=[reason])
            def prepare():
                # Recheck immediately before the external operation, not just at request creation.
                self.check_text(state,repository,owner)
                self.require_audio_funding(owner,state)
                if self.audio_authorize(owner,req,options) is not True:raise PermissionError('Audio permission changed.')
                import httpx
                try:result=self.narrator(script,options)
                except (TimeoutError,ConnectionError,httpx.TimeoutException):
                    raise UnknownOutcome('Narration submission needs confirmation before retrying.') from None
                data=result['content'];mime=result['media_type']
                if not isinstance(data,bytes) or not 16<=len(data)<=20*1024*1024:raise ValueError('Narration bytes are missing or oversized.')
                if mime=='audio/wav':valid=data[:4]==b'RIFF' and data[8:12]==b'WAVE'
                elif mime=='audio/mpeg':valid=data[:3]==b'ID3' or (data[0]==255 and data[1]&224==224)
                else:valid=False
                if not valid:raise ValueError('Narration format is invalid.')
                verdict=self.audio_checker(script,options,data)
                artifact=parse_payload(AudioArtifact,verdict)
                if (artifact.audio_id!=hashlib.sha256(data).hexdigest() or artifact.media_type!=mime or
                    artifact.script_hash!=content_hash(script) or artifact.voice_id!=options.voice_id or artifact.model_id!=options.voice_model):raise ValueError('Audio validation used different bytes, script or voice.')
                path=self.media_path(state['run_id'],artifact.audio_id);path.parent.mkdir(parents=True,exist_ok=True)
                pending=path.with_suffix('.pending');pending.write_bytes(data);pending.replace(path)
                return artifact.model_dump(mode='json')
            key='complete-audio-'+content_hash([script,options.model_dump(mode='json'),self.media_policy_version])
            audio=parse_payload(AudioArtifact,repository.operation(state['run_id'],key,prepare))
        preview=self.preview_type(book=book,request_hash=state['request_hash'],review_hash=content_hash(state['review']),
            options_hash=content_hash(options),narration_requested=req.narration_requested,narration_script=script,audio=audio)
        self.checked_audio(owner,state,preview)
        repository.save(state['run_id'],'complete-preview-'+preview.complete_book_hash,preview.model_dump(mode='json'))
        self.save_approval_runtime(dict(state,complete_preview_hash=preview.complete_book_hash),repository,owner)
        return dict(status='awaiting_parent_approval',asset_manifest_id=state['asset_manifest_id'],book_version_hash=book.book_version_hash,
            complete_preview_hash=preview.complete_book_hash,incomplete_reasons=[])

    def checked_preview(self,state,repository,owner):
        self.check_text(state,repository,owner);self.check_book(state,repository,owner)
        if not state.get('complete_preview_hash'):raise PermissionError('The requested complete book is not ready.')
        preview=parse_payload(self.preview_type,repository.load(state['run_id'],'complete-preview-'+state['complete_preview_hash']))
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        options=parse_payload(CreationOptions,state['creation_options'])
        if (preview.complete_book_hash!=state['complete_preview_hash'] or preview.book.book_version_hash!=state['book_version_hash'] or
            preview.review_hash!=content_hash(state['review']) or preview.request_hash!=state['request_hash'] or
            preview.options_hash!=content_hash(options) or preview.narration_requested!=state['request']['narration_requested'] or
            preview.narration_script!=tuple([draft.title,*[p.text for p in draft.pages],draft.moral])):raise PermissionError('Complete preview is stale.')
        self.checked_audio(owner,state,preview)
        return preview

    def text_approval_prompt(self,state,repository):
        return dict(kind='complete_book_approval',run_id=state['run_id'],book_hash=state['complete_preview_hash'],
            parent_action='Approve Book',approval_scope='complete_book')

    def authorize_output(self,state,repository,owner):
        preview=self.checked_preview(state,repository,owner)
        record=self.complete_approvals.find(owner,state['run_id'],preview.complete_book_hash)
        return record.approval_id if record else None

    def check_release(self,state,repository,owner,release_id):
        preview=self.checked_preview(state,repository,owner)
        record=self.complete_approvals.find(owner,state['run_id'],preview.complete_book_hash)
        if not record or (record.approval_id,record.owner_id,record.run_id,record.complete_book_hash,record.base_book_hash,record.review_hash)!=(
                release_id,owner,state['run_id'],preview.complete_book_hash,state['book_version_hash'],content_hash(state['review'])):raise PermissionError('Approve this exact complete book first.')
        repository.check_approval(release_id,state['run_id'],owner,state['book_version_hash'],record.review_hash,state['playbook_version'])
        return record

    @staticmethod
    def output_key_for_state(state,book):return 'output-'+state['complete_preview_hash']

    def document(self,state,repository):
        result=super().document(state,repository)
        result=result.replace('Parent-approved text; application-validated book version','Parent-approved complete book')
        return result.replace(state['book_version_hash'],state.get('complete_preview_hash') or state['book_version_hash'])


class CompleteBookWorkflowService(AccountWorkflowService):
    approval_store_type=CompleteApprovals
    approval_policy='complete-book-review-v1'

    def _approval_payload(self,run,owner):
        try:return self._payload(run,owner)
        except ValueError as original:
            payload=self.read_payload(run,owner)
            if payload['adapter_version']!=self.adapters.version or payload['capabilities_hash']!=content_hash(self.adapters.capabilities):raise original
            state=self.status(run,owner)['values'];key=state.get('complete_preview_hash')
            if not key:raise original
            try:record=self.repository.load(run,'approval-runtime-'+key)
            except ValueError:raise original
            if record!=dict(identity=self.adapters.approval_runtime_identity(),run_runtime=payload['runtime_identity']):raise original
            return payload

    def __init__(self,*args,public_generation_mode='provider_live',voice_options=(),**kwargs):
        super().__init__(*args,**kwargs);self.public_generation_mode=public_generation_mode;self.voice_options=tuple(voice_options)
        if self.enabled:self.adapters.complete_approvals=self.approval_store_type(self.repository)

    def start(self,owner,key,request,*,options=None,**kwargs):
        req=parse_payload(StoryRequest,request);opts=parse_payload(CreationOptions,options or {})
        opts.validate_request(req)
        return super().start(owner,key,request,creation_options=opts.model_dump(mode='json'),**kwargs)

    def approve_text(self,*_):raise ValueError('There is one complete-book review. Separate text approval is not supported.')
    def text_review(self,*_):raise ValueError('Use the complete-book review.')
    def authorize_narration(self,*_):raise ValueError('Narration is included in initial creation options and final book review.')

    def _refresh_assets(self,run_id,owner):
        state=self.status(run_id,owner)['values']
        if state.get('status') in ('awaiting_parent_approval','complete') and state.get('complete_preview_hash'):
            try:
                self.adapters.checked_preview(state,self.repository,owner)
                return state  # No replacement: do not invent another version/review gate.
            except (PermissionError,ValueError,OSError):pass
        return super()._refresh_assets(run_id,owner)

    def review_book(self,run,owner):
        self.read_payload(run,owner);state=self.status(run,owner)['values']
        result=dict(run_id=run,status=state.get('status','queued'),approval_scope='complete_book',approval_ready=False,
            book_hash=state.get('complete_preview_hash'),incomplete_reasons=state.get('incomplete_reasons',[]),
            provenance=state.get('generation_mode'),errors=state.get('errors',[]))
        if state.get('status') not in ('awaiting_parent_approval','complete'):return result
        if state.get('status')=='awaiting_parent_approval':
            try:self._approval_payload(run,owner)
            except ValueError:return dict(result,status='incomplete',approval_ready=False,incomplete_reasons=['The creation settings changed. This draft is retained, but must be revalidated before approval.'])
        try:preview=self.adapters.checked_preview(state,self.repository,owner)
        except (PermissionError,ValueError,OSError):
            return dict(result,status='incomplete',incomplete_reasons=['A required source, scene image, QA record or narration is no longer valid.'],approval_ready=False)
        result.update(approval_ready=state['status']=='awaiting_parent_approval',preview=preview.model_dump(mode='json'),
            draft=self.repository.load(run,state['draft_id']),review=state['review'],
            context=self.repository.load(run,state.get('context_id','context')),html=self.adapters.document(state,self.repository),
            audio_available=preview.audio is not None)
        return result

    def decide(self,run,owner,raw):
        command=parse_payload(CompleteBookDecision,raw)
        if command.action=='approve':self._approval_payload(run,owner)
        else:self._payload(run,owner)
        with run_lock(self.folder,'completion-'+run):
            if command.action=='approve':
                with run_lock(self.folder,run):
                    state=self.status(run,owner)['values']
                    if state['status'] not in ('awaiting_parent_approval','approved','rendering','complete'):raise PermissionError('The requested book is incomplete or not available for approval.')
                    preview=self.adapters.checked_preview(state,self.repository,owner)
                    if command.book_hash!=preview.complete_book_hash:raise ValueError('Review the current complete book version.')
                    if not any(b['run_id']==run for b in self.entitlements.library(owner)):self.entitlements.reserve(owner,run,preview.complete_book_hash)
                    self.adapters.complete_approvals.approve(owner,state,preview)
                try:return self._advance_output(run,owner)
                except Exception:self.entitlements.release(owner,run);raise
            def apply():
                with run_lock(self.folder,run),checkpoint_store(self.folder/'checkpoints.sqlite') as saver,tracing_context(enabled=False):
                    graph=self._graph(owner,saver);config=self._config(run);state=dict(graph.get_state(config).values)
                    if state.get('status')!='awaiting_parent_approval' or state.get('complete_preview_hash')!=command.book_hash:raise ValueError('This complete book version is not awaiting review.')
                    self.adapters.checked_preview(state,self.repository,owner)
                    if command.action=='reject':
                        graph.update_state(config,dict(status='cancelled',approval_id=None,incomplete_reasons=['The parent rejected this book.']),as_node='blocked')
                        self.entitlements.release(owner,run)
                    else:
                        self.entitlements.require_generation(owner,run,state['request_hash'])
                        epoch='revision-'+content_hash([command.request_key,command.book_hash,command.notes])[:20]
                        graph.update_state(config,dict(status='writing',revision_epoch=epoch,revision_notes=command.notes,
                            parent_revision_count=state.get('parent_revision_count',0)+1,correction_count=0,
                            complete_preview_hash=None,approval_id=None,book_version_hash=None,incomplete_reasons=[]),as_node='correct')
                    return dict(accepted=True,action=command.action,command_hash=content_hash(command))
            receipt=self.repository.operation(run,'parent-decision-'+command.request_key,apply)
            if receipt['command_hash']!=content_hash(command):raise ValueError('This decision key was reused with different content.')
        if command.action=='request_changes':self.submit(run,owner)
        return self.status(run,owner)['values']

    def _publish(self,state,owner):
        approval=self.adapters.check_release(state,self.repository,owner,state['approval_id'])
        preview=self.adapters.checked_preview(state,self.repository,owner)
        if not any(b['run_id']==state['run_id'] for b in self.entitlements.library(owner)):
            self.entitlements.reserve(owner,state['run_id'],preview.complete_book_hash)
        self.entitlements.publish_version(owner,state['run_id'],preview.complete_book_hash,dict(
            book=preview.book.model_dump(mode='json'),complete_preview=preview.model_dump(mode='json'),
            approval_id=approval.approval_id,provenance=state['generation_mode']))

    def approved_output(self,run,owner,book_hash):
        self.read_payload(run,owner);state=self.status(run,owner)['values']
        if state.get('status')!='complete' or state.get('complete_preview_hash')!=book_hash:raise PermissionError('Approve the current complete book first.')
        self.adapters.check_release(state,self.repository,owner,state['approval_id'])
        if not any(b['run_id']==run and b['book_hash']==book_hash for b in self.entitlements.library(owner)):raise PermissionError('Completion accounting needs reconciliation.')
        result=self.repository.load(run,'output-'+book_hash)
        if result['book_version_hash']!=state['book_version_hash']:raise PermissionError('Saved output belongs to another base book.')
        return dict(result,complete_book_hash=book_hash)

    def audio(self,run,owner,book_hash,*,final=False):
        self._payload(run,owner);state=self.status(run,owner)['values']
        if state.get('status') not in ('awaiting_parent_approval','complete') or state.get('complete_preview_hash')!=book_hash:raise PermissionError('This narration preview is not available.')
        if final:self.approved_output(run,owner,book_hash)
        preview=self.adapters.checked_preview(state,self.repository,owner)
        if preview.audio is None:raise LookupError('Narration was not requested.')
        return self.adapters.checked_audio(owner,state,preview),preview.audio.media_type
