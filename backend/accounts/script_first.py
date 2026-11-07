"""V5 account workflow: review first, narration only after exact approval."""
from backend.contracts import BookVersion,StoryDraft,StoryRequest,content_hash,parse_payload
from backend.contracts.complete_book import CreationOptions
from backend.contracts.script_first import ScriptFirstPreview,ScriptFirstApproval
from backend.workflows.checkpoints import run_lock
from .complete_book import CompleteApprovals,CompleteBookAdapters,CompleteBookWorkflowService


class ScriptFirstApprovals(CompleteApprovals):
    record_type=ScriptFirstApproval


class ScriptFirstAdapters(CompleteBookAdapters):
    preview_type=ScriptFirstPreview
    approval_policy='script-first-review-v1'
    episode_planner=None

    def runtime_identity(self):
        return content_hash([super().runtime_identity(),self.episode_planner.identity() if self.episode_planner else None])

    def approval_runtime_identity(self):
        return content_hash([super().approval_runtime_identity(),self.episode_planner.identity() if self.episode_planner else None])

    def route_review(self,state):
        from backend.workflows.routing import after_review
        from backend.contracts import ReviewReport
        route=after_review(state)
        if self.episode_planner:
            verdict=parse_payload(ReviewReport,state['review']).overall_result
            if route=='correct' and state['correction_count']>=self.episode_planner.policy.max_corrections:route='blocked'
            if route=='blocked' and verdict=='FAIL' and state.get('episode_index',0)+1<state.get('episode_count',0):return 'next_episode'
        return route

    def assemble_complete_preview(self,state,repository,owner):
        self.check_text(state,repository,owner);self.check_book(state,repository,owner)
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        book=parse_payload(BookVersion,repository.load(state['run_id'],'book-'+state['book_version_hash']))
        options=parse_payload(CreationOptions,state['creation_options'])
        preview=ScriptFirstPreview(book=book,request_hash=state['request_hash'],review_hash=content_hash(state['review']),options_hash=content_hash(options),narration_requested=state['request']['narration_requested'],narration_script=tuple([draft.title,*[p.text for p in draft.pages],draft.moral]))
        repository.save(state['run_id'],'complete-preview-'+preview.complete_book_hash,preview.model_dump(mode='json'))
        self.save_approval_runtime(dict(state,complete_preview_hash=preview.complete_book_hash),repository,owner)
        return dict(status='awaiting_parent_approval',asset_manifest_id=state['asset_manifest_id'],book_version_hash=book.book_version_hash,complete_preview_hash=preview.complete_book_hash,incomplete_reasons=[])

    def require_audio_funding(self,owner,state):
        if not any(b['run_id']==state['run_id'] and b['book_hash']==state['complete_preview_hash'] for b in self.entitlements.library(owner)):
            return super().require_audio_funding(owner,state)

    def prepare_approved_audio(self,state,repository,owner):
        self.check_release(state,repository,owner,state['approval_id'])
        approved=self.checked_preview(state,repository,owner)
        if not approved.narration_requested:return None
        result=super().assemble_complete_preview(state,repository,owner)
        if result['status']!='awaiting_parent_approval':raise PermissionError(result['incomplete_reasons'][0])
        audio_preview=parse_payload(ScriptFirstPreview,repository.load(state['run_id'],'complete-preview-'+result['complete_preview_hash']))
        if audio_preview.narration_script!=approved.narration_script:raise PermissionError('Approved script changed.')
        self.check_release(state,repository,owner,state['approval_id'])
        repository.save(state['run_id'],'approved-audio-'+approved.complete_book_hash,audio_preview.model_dump(mode='json'))
        return audio_preview

    def saved_audio(self,state,repository,owner):
        self.check_release(state,repository,owner,state['approval_id'])
        approved=self.checked_preview(state,repository,owner)
        preview=parse_payload(ScriptFirstPreview,repository.load(state['run_id'],'approved-audio-'+approved.complete_book_hash))
        if (preview.book!=approved.book or preview.request_hash!=approved.request_hash or preview.review_hash!=approved.review_hash or preview.options_hash!=approved.options_hash or preview.narration_script!=approved.narration_script):raise PermissionError('Saved audio belongs to another approved version.')
        return preview,self.checked_audio(owner,state,preview)


class ScriptFirstWorkflowService(CompleteBookWorkflowService):
    approval_policy='script-first-review-v1'
    approval_store_type=ScriptFirstApprovals

    def __init__(self,*args,narration_enabled=True,**kwargs):
        if type(narration_enabled) is not bool:raise ValueError('Narration capability must be explicit.')
        self.narration_enabled=narration_enabled
        super().__init__(*args,**kwargs)
        if not narration_enabled:self.voice_options=()

    def start(self,owner,key,request,**kwargs):
        req=parse_payload(StoryRequest,request)
        if req.narration_requested and not self.narration_enabled:
            raise ValueError('This release creates readable books and PDFs. Narration is not enabled.')
        return super().start(owner,key,request,**kwargs)

    def decide(self,run,owner,raw):
        result=super().decide(run,owner,raw)
        if raw.get('action')=='approve' and result['status']=='complete' and result['request']['narration_requested']:
            try:
                with run_lock(self.folder,'approved-audio-'+run):
                    self.adapters.prepare_approved_audio(result,self.repository,owner)
            except Exception:
                # Story approval/output survive audio errors. Durable operations retain
                # uncertain submission states and prevent blind paid resubmission.
                self.repository.save(run,'audio-status-'+result['complete_preview_hash'],dict(status='unavailable',reason='Narration could not be confirmed. The approved story is saved.'))
        return result

    def review_book(self,run,owner):
        result=super().review_book(run,owner)
        result['narration_after_approval']=True
        if result.get('status')=='complete' and result.get('preview',{}).get('narration_requested'):
            try:
                state=self.status(run,owner)['values'];self.adapters.saved_audio(state,self.repository,owner)
                result['audio_available']=True
            except (PermissionError,ValueError,OSError,KeyError,LookupError):
                result['audio_available']=False
                result['incomplete_reasons']=['Narration is not available. Your approved readable story is saved.']
        return result

    def audio(self,run,owner,book_hash,*,final=False):
        self.approved_output(run,owner,book_hash)
        state=self.status(run,owner)['values']
        preview,data=self.adapters.saved_audio(state,self.repository,owner)
        return data,preview.audio.media_type
