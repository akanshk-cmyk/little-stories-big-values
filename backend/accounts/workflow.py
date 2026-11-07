"""Account-bound approval, reviewed assets and one-time completion accounting."""
import base64
import html
import hashlib
from backend.contracts import (AssetManifest,BookVersion,StoryDraft,StoryRequest,ReviewReport,
    content_hash,parse_payload)
from backend.workflows.service import WorkflowService
from backend.workflows.story_graph import PreparedContext
from backend.workflows.checkpoints import checkpoint_store,run_lock
from backend.contracts.operations import validate_transition
from langsmith import tracing_context
from .entitlements import Entitlements
from .consent import Consents
from backend.contracts.consent import story_text
from langgraph.types import Command


class AccountAdapters:
    """Wraps the same Stage 8 adapters; no replacement writer or Reviewer."""
    def __init__(self,base,accounts,registry):
        self.base=base;self.accounts=accounts;self.registry=registry
        self.version='account-workflow-v3'
        self.entitlements=Entitlements(accounts)
        self.supported_modes=getattr(base,'supported_modes',('replay',))
        self.playbook_version=getattr(base,'playbook_version','stage3-playbook-v1')
        self.reviewer_version=getattr(base,'reviewer_version','stage3-reviewer-v1')

    def __getattr__(self,name):return getattr(self.base,name)

    def runtime_identity(self):
        identity=getattr(self.base,'runtime_identity',lambda:self.base.version)()
        return content_hash([self.version,identity,self.registry.policy_version,
            self.entitlements.max_generation_operations_per_day,self.entitlements.max_generation_operations_per_run])

    def approval_runtime_identity(self):
        identity=getattr(self.base,'runtime_identity',lambda:self.base.version)()
        return content_hash([self.version,identity,self.registry.policy_version])

    def check_operation(self,key,state,args,owner):
        self.accounts.require(owner)
        if key=='prepare':self.entitlements.require_generation(owner,state['run_id'],state['request_hash'])
        if key.startswith(('write-','review-')):
            self.entitlements.claim_generation_operation(owner,state['run_id'],state['request_hash'],key)
        callback=getattr(self.base,'check_operation',None)
        if callback:callback(key,state,args,owner)

    def operation_key(self,key,state,args):
        return key+'-'+self.registry.revision(args[0].draft_hash) if key.startswith('assets-') else key

    def validate_assets(self,state,draft,context,manifest):
        self.registry.validate(draft,context.characters,manifest,style=state['request'].get('illustration_style'))

    def check_text(self,state,repository,owner):
        self.accounts.require(owner)
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        context=parse_payload(PreparedContext,repository.load(state['run_id'],state.get('context_id','context')))
        report=parse_payload(ReviewReport,repository.load(state['run_id'],'review-'+draft.draft_hash))
        from backend.contracts import validate_story_context
        validate_story_context(draft,parse_payload(StoryRequest,state['request']),context.characters,context.evidence)
        if (draft.draft_hash!=state['draft_hash'] or report.overall_result!='PASS' or
            report.draft_hash!=draft.draft_hash or report.reviewer_version!=state['reviewer_version'] or
            report.evidence_bundle_hash!=(context.evidence.bundle_hash if context.evidence else None) or
            report.model_dump(mode='json')!=state['review']):raise PermissionError('Current exact-text QA PASS is required.')
        if context.evidence and self.base.eligibility(context.evidence) is not True:raise PermissionError('Source approval changed.')
        callback=getattr(self.base,'check_operation',None)
        if callback:callback('review-'+draft.draft_hash,state,(draft,context,state['correction_count']),owner)
        return draft

    def text_approval_prompt(self,state,repository):
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        return dict(kind='story_text_approval',run_id=state['run_id'],text_hash=content_hash(story_text(draft)),
            parent_action='Approve the story text',approval_scope='story_text')

    @staticmethod
    def output_key(book):return 'output-'+book.book_version_hash

    def authorize_output(self,state,repository,owner):
        draft=self.check_text(state,repository,owner)
        consent=self.consents.current(owner,state['run_id'],state['request_hash'],content_hash(story_text(draft)))
        if not consent:return None
        self.check_book(state,repository,owner)
        book=parse_payload(BookVersion,repository.load(state['run_id'],'book-'+state['book_version_hash']))
        if not any(b['run_id']==state['run_id'] for b in self.entitlements.library(owner)):
            self.entitlements.reserve(owner,state['run_id'],book.book_version_hash)
        return self.consents.authorize(owner,state,book,consent).release_id

    def check_release(self,state,repository,owner,release_id):
        draft=self.check_text(state,repository,owner)
        consent=self.consents.current(owner,state['run_id'],state['request_hash'],content_hash(story_text(draft)))
        if not consent:raise PermissionError('Approve the current story text first.')
        book=parse_payload(BookVersion,repository.load(state['run_id'],'book-'+state['book_version_hash']))
        return self.consents.verify(release_id,owner,state,book,consent)

    def check_book(self,state,repository,owner):
        self.accounts.require(owner)
        context=parse_payload(PreparedContext,repository.load(state['run_id'],state.get('context_id','context')))
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        manifest=parse_payload(AssetManifest,repository.load(state['run_id'],state['asset_manifest_id']))
        book=parse_payload(BookVersion,repository.load(state['run_id'],'book-'+state['book_version_hash']))
        report=parse_payload(ReviewReport,repository.load(state['run_id'],'review-'+draft.draft_hash))
        if (book.book_version_hash!=state['book_version_hash'] or book.draft_hash!=draft.draft_hash or
            book.character_manifest_hash!=context.characters.manifest_hash or
            book.asset_manifest_hash!=manifest.manifest_hash or
            book.evidence_bundle_hash!=(context.evidence.bundle_hash if context.evidence else None) or
            report.overall_result!='PASS' or report.draft_hash!=draft.draft_hash or
            report.model_dump(mode='json')!=state['review']):raise PermissionError('Book, review or asset version changed.')
        self.validate_assets(state,draft,context,manifest)
        if context.evidence and self.base.eligibility(context.evidence) is not True:raise PermissionError('Source approval changed.')
        callback=getattr(self.base,'check_operation',None)
        if callback:callback('review-'+draft.draft_hash,state,(draft,context,state['correction_count']),owner)

    def invoke_with_state(self,key,state,args,repository,owner):
        if key.startswith('assets-'):
            context=parse_payload(PreparedContext,repository.load(state['run_id'],state.get('context_id','context')))
            return self.registry.manifest(args[0],context.characters,
                style=state['request'].get('illustration_style')).model_dump(mode='json')
        if key.startswith('render-'):
            self.check_book(state,repository,owner)
            self.check_release(state,repository,owner,state['approval_id'])
            return dict(kind='approved_html',book_hash=args[0].book_version_hash,
                html=self.document(state,repository))
        callback=getattr(self.base,'invoke_with_state',None)
        if callback:
            from backend.workflows.provider_content import ProviderContentAdapters
            if isinstance(self.base,ProviderContentAdapters):
                return callback(key,state,args,repository,owner,execution_guard=lambda _:
                    self.entitlements.require_generation(owner,state['run_id'],state['request_hash']))
            return callback(key,state,args,repository,owner)
        return getattr(self.base,key.split('-')[0])(*args)

    def document(self,state,repository):
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        assets=parse_payload(AssetManifest,repository.load(state['run_id'],state['asset_manifest_id']))
        esc=html.escape;images={p.page_number:p.asset for p in assets.pages};pages=[]
        for page in draft.pages:
            image=images[page.page_number]
            data=base64.b64encode(self.registry.image_bytes(image)).decode('ascii')
            pages.append(f'<section><img src="data:{image.media_type};base64,{data}" alt="{esc(page.accessible_description)}">'
                f'<p>{esc(page.text)}</p><footer>Page {page.page_number} of 6</footer></section>')
        extras=''.join(f'<h2>{label}</h2><p>{esc(value)}</p>' for label,value in (
            ('Moral',draft.moral),('Parent discussion',draft.parent_question),('Did You Know?',draft.cultural_fact),
            ('Family activity',draft.family_activity)) if value)
        context=parse_payload(PreparedContext,repository.load(state['run_id'],state.get('context_id','context')))
        source_notes=''
        if context.evidence:
            by_chunk={p.reference.chunk_id:p for p in context.evidence.passages}
            items=[];cited_chunks=set()
            for page in draft.pages:
                for citation in page.citations:
                    passage=by_chunk[citation.chunk_id];ref=passage.reference
                    cited_chunks.add(citation.chunk_id)
                    printed=', '.join(ref.printed_pages) if ref.printed_pages else 'Not recorded'
                    items.append(f'<li>Story page {page.page_number}: {esc(citation.claim_text)}'
                        f'<p>Source {esc(ref.source_id)}; edition {esc(ref.edition_id)}; snapshot {esc(ref.snapshot_id)}; '
                        f'PDF pages {esc(", ".join(map(str,ref.pdf_pages)))}; printed pages {esc(printed)}.</p>'
                        f'<p>Evidence kind: {esc(citation.claim_kind)}. Source quotation: {esc(citation.source_quote)}</p></li>')
            for passage in context.evidence.passages:
                ref=passage.reference
                if ref.chunk_id in cited_chunks:continue
                printed=', '.join(ref.printed_pages) if ref.printed_pages else 'Not recorded'
                items.append(f'<li>Supplied evidence (no page-level citation recorded): Source {esc(ref.source_id)}; edition {esc(ref.edition_id)}; '
                    f'snapshot {esc(ref.snapshot_id)}; PDF pages {esc(", ".join(map(str,ref.pdf_pages)))}; printed pages {esc(printed)}.'
                    f'<p>{esc(passage.text)}</p></li>')
            source_notes='<aside><h2>Source notes for parents</h2><p>These references preserve the supplied source edition. '+\
                'They do not turn imagined scenes or dialogue into traditional events.</p><ol>'+''.join(items)+'</ol></aside>'
        return '<!doctype html><html lang="en"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '+\
            'content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'+\
            '<style>@page{size:A4;margin:18mm}body{font:18px/1.6 sans-serif}section{break-after:page}img{max-width:100%;max-height:155mm;display:block;margin:auto}p{white-space:pre-wrap}footer{font-size:12px}</style>'+\
            f'<h1>{esc(draft.title)}</h1><p>{esc(draft.fiction_disclosure or draft.classification)}</p>'+\
            f'<p>{"Parent-approved text; application-validated book version" if state.get("approval_id") else "Book version for parent review"}: {esc(state["book_version_hash"])}</p>'+''.join(pages)+extras+source_notes+'</html>'


class AccountWorkflowService(WorkflowService):
    def __init__(self,folder,adapters,accounts,entitlements,*,enabled=False):
        self.accounts=accounts;self.entitlements=entitlements
        adapters.entitlements=entitlements
        super().__init__(folder,adapters,enabled=enabled)
        if enabled:adapters.consents=Consents(self.repository)

    def _payload(self,run_id,owner):
        self.accounts.require(owner)
        return super()._payload(run_id,owner)

    def read_payload(self,run_id,owner):
        self.accounts.require(owner)
        return super().read_payload(run_id,owner)

    def recovery_options(self,run_id,owner):
        result=super().recovery_options(run_id,owner)
        if result['can_resume']:
            from .entitlements import GenerationBlocked
            try:self.entitlements.require_generation(owner,run_id,self.read_payload(run_id,owner)['request_hash'])
            except GenerationBlocked:return dict(result,can_resume=False,requires_attention=True,reason='This request no longer has an active story slot. It will not restart automatically.')
        return result

    def start(self,owner,key,request,**kwargs):
        self.accounts.require(owner)
        req=parse_payload(StoryRequest,request)
        children=self.accounts.children(owner)
        if not any(c['id']==req.child_profile_id and c['age']==req.age for c in children):raise PermissionError('Choose a child profile belonging to this account and confirm its age.')
        run_id=super().start(owner,key,req.model_dump(mode='json'),**kwargs)
        self.entitlements.authorize_generation(owner,run_id,content_hash(req))
        return run_id

    def status(self,run_id,owner):
        result=super().status(run_id,owner)
        # A committed graph checkpoint can be observed before execute() finishes
        # its accounting cleanup. Reconcile idempotently before exposing a stop.
        if result.get('values',{}).get('status') in ('failed','cancelled','evidence_blocked','awaiting_clarification','outcome_unknown'):
            self.entitlements.release(owner,run_id)
        return result

    def execute(self,run_id,owner):
        payload=self._payload(run_id,owner)
        state=self.status(run_id,owner)['values']
        if not state or state['status'] in ('queued','validating','retrieving','writing','reviewing','correcting'):
            self.entitlements.require_generation(owner,run_id,payload['request_hash'])
        result=super().execute(run_id,owner)
        if result['status']=='complete':self._publish(result,owner)
        if result['status'] in ('failed','cancelled','evidence_blocked','awaiting_clarification','outcome_unknown'):
            self.entitlements.release(owner,run_id)
        return result

    def cancel(self,run_id,owner):
        self._payload(run_id,owner)
        with run_lock(self.folder,run_id),checkpoint_store(self.folder/'checkpoints.sqlite') as saver,tracing_context(enabled=False):
            graph=self._graph(owner,saver);config=self._config(run_id)
            state=dict(graph.get_state(config).values)
            if not state:
                state=self._initial(run_id,self._payload(run_id,owner))
            if state['status']=='cancelled':return state
            if state['status'] in ('approved','rendering','complete','failed'):
                raise ValueError('This story cannot be cancelled at this step.')
            validate_transition(state['status'],'cancelled')
            graph.update_state(config,dict(state,status='cancelled'),as_node='blocked')
            self.entitlements.release(owner,run_id)
            return dict(graph.get_state(config).values)

    def refresh_assets(self,run_id,owner):
        with run_lock(self.folder,'completion-'+run_id):
            return self._refresh_assets(run_id,owner)

    def _refresh_assets(self,run_id,owner):
        self._payload(run_id,owner)
        with run_lock(self.folder,run_id),checkpoint_store(self.folder/'checkpoints.sqlite') as saver,tracing_context(enabled=False):
            graph=self._graph(owner,saver);config=self._config(run_id);state=dict(graph.get_state(config).values)
            if state.get('status') not in ('assets_pending','awaiting_parent_approval','complete'):raise ValueError('This story cannot refresh its scene images.')
            self.adapters.check_text(state,self.repository,owner)
            report=parse_payload(ReviewReport,self.repository.load(run_id,'review-'+state['draft_hash']))
            if report.overall_result!='PASS' or report.model_dump(mode='json')!=state['review']:raise PermissionError('A current content PASS is required.')
            validate_transition(state['status'],'reviewing',allow_output_revision=state['status']=='complete')
            graph.update_state(config,dict(status='reviewing',approval_id=None),as_node='review')
            graph.invoke(None,config,durability='sync')
            result=dict(graph.get_state(config).values)
        if result['status']=='complete':self._publish(result,owner)
        return result

    def approve(self,run_id,owner,expected_book_hash):
        raise ValueError('Full-book parent consent is retired. Use approve-text with the exact text hash.')

    def text_review(self,run_id,owner):
        self._payload(run_id,owner)
        state=self.status(run_id,owner)['values']
        draft=self.adapters.check_text(state,self.repository,owner)
        text=story_text(draft);digest=content_hash(text)
        consent=self.adapters.consents.current(owner,run_id,state['request_hash'],digest)
        return dict(run_id=run_id,text=text.model_dump(mode='json'),text_hash=digest,
            approval_scope='story_text',parent_action='Approve the story text',
            consent_id=consent.consent_id if consent else None,text_approved=bool(consent),
            output_status=state['status'])

    def approve_text(self,run_id,owner,expected_text_hash):
        self._payload(run_id,owner)
        with run_lock(self.folder,'completion-'+run_id):
            with run_lock(self.folder,run_id):
                state=self.status(run_id,owner)['values']
                if state.get('status') not in ('assets_pending','awaiting_parent_approval','approved','rendering','complete'):
                    raise ValueError('This story text is not ready for parent approval.')
                draft=self.adapters.check_text(state,self.repository,owner)
                self.adapters.consents.approve(owner,state,draft,expected_text_hash)
            try:
                return self._advance_output(run_id,owner)
            except Exception:
                self.entitlements.release(owner,run_id)
                raise

    def _advance_output(self,run_id,owner):
        with run_lock(self.folder,run_id),checkpoint_store(self.folder/'checkpoints.sqlite') as saver,tracing_context(enabled=False):
            graph=self._graph(owner,saver);config=self._config(run_id);snapshot=graph.get_state(config)
            state=dict(snapshot.values)
            if state['status']=='assets_pending':return state
            if state['status']=='awaiting_parent_approval':
                graph.invoke(Command(resume='text-consent-recorded') if snapshot.interrupts else None,config,durability='sync')
            elif state['status'] in ('approved','rendering'):graph.invoke(None,config,durability='sync')
            result=dict(graph.get_state(config).values)
        if result['status']!='complete':raise RuntimeError('Application output validation did not complete.')
        self._publish(result,owner)
        return result

    def _publish(self,state,owner):
        self.adapters.check_book(state,self.repository,owner)
        release=self.adapters.check_release(state,self.repository,owner,state['approval_id'])
        book=self.repository.load(state['run_id'],'book-'+state['book_version_hash'])
        if not any(b['run_id']==state['run_id'] for b in self.entitlements.library(owner)):
            self.entitlements.reserve(owner,state['run_id'],state['book_version_hash'])
        self.entitlements.publish_version(owner,state['run_id'],state['book_version_hash'],
            dict(book=book,provenance=state['generation_mode'],text_hash=release.text_hash,
                text_consent_id=release.consent_id,application_release_id=release.release_id))

    def approved_output(self,run_id,owner,book_hash):
        self._payload(run_id,owner)
        state=self.status(run_id,owner)['values']
        if state.get('status')!='complete' or state.get('book_version_hash')!=book_hash:raise PermissionError('Approve this exact complete book first.')
        if not any(b['run_id']==run_id and b['book_hash']==book_hash for b in self.entitlements.library(owner)):raise PermissionError('Completion accounting needs reconciliation.')
        self.adapters.check_book(state,self.repository,owner)
        self.adapters.check_release(state,self.repository,owner,state['approval_id'])
        output=self.repository.load(run_id,'output-'+book_hash)
        if output['book_version_hash']!=book_hash:raise ValueError('Output version mismatch.')
        return output

    def preview(self,run_id,owner):
        self._payload(run_id,owner)
        state=self.status(run_id,owner)['values']
        if state.get('status') not in ('awaiting_parent_approval','complete'):raise PermissionError('The complete book is not ready for review.')
        self.adapters.check_book(state,self.repository,owner)
        return self.adapters.document(state,self.repository)

    def authorize_narration(self,run_id,owner,book_hash,voice):
        self.approved_output(run_id,owner,book_hash)
        if not isinstance(voice,dict) or set(voice)!={'voice_id','model_id'} or any(
            not isinstance(v,str) or not v or len(v)>100 for v in voice.values()):raise ValueError('Confirm one voice and narration model.')
        state=self.status(run_id,owner)['values'];draft=self.repository.load(run_id,state['draft_id'])
        script=[draft['title'],*[p['text'] for p in draft['pages']],draft['moral']]
        payload=dict(book_hash=book_hash,draft_hash=draft['draft_hash'],voice=voice,script=script,script_hash=content_hash(script),
            owner=owner,status='authorized_not_generated')
        identity=content_hash(payload)
        self.repository.save(run_id,'narration-'+identity,payload)
        with self.accounts.store.db(write=True) as db:
            db.execute('INSERT INTO narration_authorizations VALUES(?,?,?) ON CONFLICT(account_id,run_id) DO UPDATE SET authorization_id=excluded.authorization_id',
                (owner,run_id,identity))
        return dict(authorization_id=identity,**payload)

    def narration_payload(self,run_id,owner,authorization_id):
        payload=self.repository.load(run_id,'narration-'+authorization_id)
        self.approved_output(run_id,owner,payload['book_hash'])
        with self.accounts.store.db() as db:
            row=db.execute('SELECT authorization_id FROM narration_authorizations WHERE account_id=? AND run_id=?',(owner,run_id)).fetchone()
        if (not row or row[0]!=authorization_id or payload['owner']!=owner or
            content_hash(payload)!=authorization_id or content_hash(payload['script'])!=payload['script_hash']):
            raise PermissionError('This narration authorization is stale.')
        return payload

    def pdf(self,run_id,owner,book_hash):
        output=self.approved_output(run_id,owner,book_hash)
        from backend.pdf_export_service import render_html_pdf
        folder=self.folder/'exports'/run_id/book_hash;folder.mkdir(parents=True,exist_ok=True)
        destination=folder/'book.pdf'
        with run_lock(self.folder,'pdf-'+run_id):
            # Revalidation is required even for a previously rendered PDF.
            self.approved_output(run_id,owner,book_hash)
            if not destination.exists():
                pending=folder/'book.pending.pdf'
                with self.repository.db() as db:
                    confirmed=db.execute('SELECT 1 FROM artifacts WHERE run_id=? AND id=?',(run_id,'pdf-'+book_hash)).fetchone()
                if confirmed:
                    receipt=self.repository.load(run_id,'pdf-'+book_hash)
                    if not pending.exists() or hashlib.sha256(pending.read_bytes()).hexdigest()!=receipt['file_hash']:
                        raise PermissionError('Confirmed PDF bytes are missing; reconcile before recreating the export.')
                else:
                    render_html_pdf(output['result']['html'],pending,folder)
                    content=pending.read_bytes()
                    if not content.startswith(b'%PDF-'):raise ValueError('PDF generation did not produce a PDF.')
                    self.repository.save(run_id,'pdf-'+book_hash,dict(book_hash=book_hash,
                        file_hash=hashlib.sha256(content).hexdigest()))
                pending.replace(destination)
            self.approved_output(run_id,owner,book_hash)
            content=destination.read_bytes()
            receipt=self.repository.load(run_id,'pdf-'+book_hash)
            if receipt['file_hash']!=hashlib.sha256(content).hexdigest():raise PermissionError('The saved PDF changed; it cannot be exported.')
            return content
