"""Explicit user-authorized text-only v5 policy, not missing illustrated assets."""
import html
from backend.contracts import AssetManifest,StoryDraft,parse_payload
from backend.workflows.story_graph import PreparedContext
from .script_first import ScriptFirstAdapters,ScriptFirstWorkflowService


class TextOnlyAdapters(ScriptFirstAdapters):
    text_only=True

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.version='account-text-only-v1'

    def invoke_with_state(self,key,state,args,repository,owner):
        if key.startswith('assets-'):
            return AssetManifest(manifest_id='text-only-'+args[0].draft_hash,pages=()).model_dump(mode='json')
        return super().invoke_with_state(key,state,args,repository,owner)

    def validate_assets(self,state,draft,context,manifest):
        if manifest.pages:raise ValueError('Text-only policy cannot claim reviewed scene images.')

    def text_approval_prompt(self,state,repository):
        return dict(super().text_approval_prompt(state,repository),parent_action='Save Book')

    def document(self,state,repository):
        draft=parse_payload(StoryDraft,repository.load(state['run_id'],state['draft_id']))
        context=parse_payload(PreparedContext,repository.load(state['run_id'],state.get('context_id','context')))
        esc=html.escape
        pages=''.join('<section><p>'+esc(p.text)+'</p><footer>Page '+str(p.page_number)+' of 6</footer></section>' for p in draft.pages)
        notes=''
        if getattr(self.base,'strict_duration',True) is False:
            notes='<aside><p>Reading time may be shorter than your selected target. Story quality checks and parent approval are still required.</p></aside>'
        if context.evidence:
            notes+='<aside><h2>Source and adaptation note</h2><p>Text-only adaptation of the uploaded Ramayan edition. Local PDF retrieval; no LlamaCloud retrieval or illustrations are claimed. Edition metadata is unverified. This is an adaptation of the cited passage, not a newly verified scriptural quotation.</p><ol>'+''.join('<li>Source '+esc(p.reference.source_id)+'; edition '+esc(p.reference.edition_id)+'; PDF pages '+esc(', '.join(map(str,p.reference.pdf_pages)))+'.<p>'+esc(p.text)+'</p></li>' for p in context.evidence.passages)+'</ol></aside>'
        label='Original teaching-inspired fiction · Text edition' if draft.classification=='original_inspired' else 'Ramayan story · Text edition'
        if draft.classification=='original_inspired':notes+='<aside><h2>About this story</h2><p>'+esc(draft.fiction_disclosure or '')+' Created from your description. No Ramayan passages were used.</p></aside>'
        return '<!doctype html><html lang="en"><meta charset="utf-8"><style>@page{size:A4;margin:18mm}body{font:18px/1.6 sans-serif}section{break-after:page}p{white-space:pre-wrap}footer{font-size:12px}</style><h1>'+esc(draft.title)+'</h1><p>'+esc(label)+'</p>'+pages+'<h2>Moral</h2><p>'+esc(draft.moral)+'</p><h2>Parent question</h2><p>'+esc(draft.parent_question)+'</p>'+notes+'</html>'


class TextOnlyWorkflowService(ScriptFirstWorkflowService):
    source_mode='local_ramayan_text_only'

    def review_book(self,run,owner):
        result=super().review_book(run,owner)  # Includes authenticated ownership checks.
        if result.get('draft') and getattr(self.adapters.base,'strict_duration',True) is False:
            result['duration_notice']='Reading time may be shorter than your selected target. Quality checks and your approval remain required.'
        if result.get('preview') or result.get('status') not in ('failed','evidence_blocked','assets_pending'):
            return result
        state=self.status(run,owner)['values']
        if not state.get('draft_id'):return result
        if state.get('review',{} ) and state['review']['critical_safety']['status']=='FAIL':return result
        draft=parse_payload(StoryDraft,self.repository.load(run,state['draft_id']))
        result.update(draft_preview=draft.model_dump(mode='json'),approval_ready=False,
            draft_warning='Unapproved draft for parent inspection only. QA has not passed. Saving, PDF download and narration are unavailable.',
            draft_issues=[e.get('parent_message',e.get('code','Check incomplete')) for e in state.get('errors',[])])
        if state.get('review'):
            result['draft_issues'].extend(check['reason'] for name,check in state['review'].items()
                if isinstance(check,dict) and check.get('status') in ('FAIL','PENDING'))
        return result

    def start(self,owner,key,request,**kwargs):
        from backend.contracts import StoryRequest
        req=parse_payload(StoryRequest,request)
        if req.story_type=='original_inspired':
            from backend.original_fiction import resolve_story_intent
            intent=resolve_story_intent(req)
            if intent.status!='READY':raise ValueError(intent.message)
            if 'original_inspired' not in self.adapters.capabilities.story_types:raise ValueError('Original story creation is unavailable in this workflow.')
            return super().start(owner,key,req.model_dump(mode='json'),**kwargs)
        from backend.contracts.local_intent import resolve_local_intent
        intent=resolve_local_intent(req)
        if intent.status!='READY':raise ValueError(intent.message)
        req=intent.request
        ids=req.required_character_ids
        if req.story_type!='adapted_retelling' or not set(ids)<={'rama','hanuman','guha','lakshmana'} or not set(ids)&{'rama','hanuman'} or req.objective not in ('Kindness','Sharing') or req.target_minutes!=3:
            raise ValueError('Choose Rama or Hanuman, Adapted retelling, Kindness or Sharing, and 3 minutes.')
        return super().start(owner,key,req.model_dump(mode='json'),**kwargs)
