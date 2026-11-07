"""Injected story graph. No default models, sources or renderers.

This graph is intentionally not connected to the parent-facing live endpoints.
Its replay artifacts are not production books or approvals.
"""
from dataclasses import dataclass
from typing import Callable
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from backend.contracts import (StoryRequest, PlaybookCapabilities, CharacterManifest,
    EvidenceBundle, SearchHit, StoryDraft, ReviewReport, AssetManifest, BookVersion,
    content_hash, parse_payload, validate_capabilities, validate_story_context,
    validate_review_evidence)
from backend.contracts.common import Contract
from backend.contracts.operations import validate_transition
from .state import StoryState
from .routing import after_review
from .repository import UnknownOutcome


class PreparedContext(Contract):
    characters: CharacterManifest
    evidence: EvidenceBundle | None = None


@dataclass(frozen=True)
class ReplayAdapters:
    """Trusted developer injection only, not fields accepted from a browser.

    Each callable returns a JSON-compatible payload. All callbacks must be
    offline synthetic implementations in this milestone.
    """
    version: str
    capabilities: PlaybookCapabilities
    prepare: Callable
    dense: Callable
    sparse: Callable
    write: Callable
    review: Callable
    assets: Callable
    render: Callable
    eligibility: Callable


def build_graph(repository, adapters, *, owner, checkpointer):
    def load(state, key, model):
        return parse_payload(model, repository.load(state['run_id'],state.get('context_id','context') if key=='context' else key))

    def call(state, key, function, *args):
        invoke = getattr(adapters, 'invoke_with_state', None)
        check = getattr(adapters, 'check_operation', None)
        if check:
            check(key,state,args,owner)
        operation_key=getattr(adapters,'operation_key',lambda key,*_:key)(key,state,args)
        return repository.operation(state['run_id'],operation_key,
            lambda: invoke(key,state,args,repository,owner) if invoke else function(*args))

    def attempts(state):
        collect = getattr(adapters, 'attempts', None)
        return {'provider_attempts':collect(state,owner)} if collect else {}

    def advance(state, target, **updates):
        validate_transition(state['status'],target)
        return dict(status=target,**updates)

    def protected(name, function):
        def node(state):
            try:
                return function(state)
            except UnknownOutcome:
                return advance(state,'outcome_unknown',errors=[dict(error_id=name+'-unknown',node=name,
                    code='outcome_unknown',parent_message='A previous operation needs confirmation before continuing.',
                    retryable=False,trace_reference=None)])
            except Exception as error:
                from backend.providers.retry_policy import ProviderFailure
                from backend.accounts.entitlements import GenerationBlocked
                if isinstance(error,GenerationBlocked):
                    return advance(state,'evidence_blocked',**attempts(state),errors=[dict(
                        error_id=name+'-credit',node=name,code=error.code,parent_message=str(error),
                        retryable=False,trace_reference=None)])
                if isinstance(error, ProviderFailure):
                    target = 'outcome_unknown' if error.unknown else (
                        'evidence_blocked' if error.code in ('evidence_missing','evidence_withdrawn',
                            'evidence_sufficiency_pending','safety_refusal','safety_blocked','preconditions_failed',
                            'generation_hold_expired','generation_limit_reached') else 'failed')
                    return advance(state,target,**attempts(state),errors=[dict(
                        error_id=name+'-provider',node=name,code=error.code,
                        parent_message=GenerationBlocked.MESSAGES.get(error.code,
                            'Story creation stopped. The source, safety or provider check needs attention.'),
                        retryable=False,trace_reference=None)])
                # Never expose provider responses, filesystem paths or secrets to the parent.
                return advance(state,'failed',errors=[dict(error_id=name+'-failed',node=name,
                    code=type(error).__name__,parent_message='This story step could not be completed. Please review the run details.',
                    retryable=False,trace_reference=None)])
        return node

    def validate(state):
        request = parse_payload(StoryRequest,state['request'])
        validate_capabilities(request,adapters.capabilities)
        if content_hash(request) != state['request_hash']:
            raise ValueError('Request changed.')
        return advance(state,'validating')

    def prepare(state):
        request = parse_payload(StoryRequest,state['request'])
        if request.narration_requested and not getattr(adapters,'supports_complete_review',False):
            return advance(state,'awaiting_clarification')
        episode_updates={}
        planner=getattr(adapters,'episode_planner',None)
        if planner:
            adapters.check_operation('prepare',state,(request,),owner)
            planned=planner.prepare(owner,state,repository)
            context=parse_payload(PreparedContext,planned.pop('context'))
            episode_updates=planned
        else:
            context = parse_payload(PreparedContext,call(state,'prepare',adapters.prepare,request))
        if not set(request.required_character_ids) <= {c.character_id for c in context.characters.characters}:
            raise ValueError('Character manifest does not preserve the request.')
        bundle = context.evidence
        if bundle and bundle.request_hash != state['request_hash']:
            raise ValueError('Evidence is bound to another request.')
        # Current eligibility is an application authority, not a model/reranker verdict.
        if (request.story_type != 'original_inspired' and not bundle) or (
                bundle and adapters.eligibility(bundle) is not True):
            return advance(state,'evidence_blocked',source_eligibility_status='blocked')
        repository.save(state['run_id'],episode_updates.get('context_id',state.get('context_id','context')),context.model_dump(mode='json'))
        if bundle:
            repository.save(state['run_id'],bundle.bundle_id,bundle.model_dump(mode='json'))
        updates = dict(**episode_updates,character_manifest_id=context.characters.manifest_id,
            evidence_bundle_id=bundle.bundle_id if bundle else None,
            source_snapshot_ids=sorted({p.reference.snapshot_id for p in bundle.passages}) if bundle else [],
            source_eligibility_status='eligible' if bundle else 'not_required',
            index_version=bundle.index_version if bundle else None)
        if bundle and (planner or getattr(adapters,'uses_prepared_evidence',False)):
            updates['ranked_hits']=[p.model_dump(mode='json') for p in bundle.passages]
            return advance(state,'writing',**updates)
        return advance(state,'retrieving' if bundle else 'writing',**updates)

    def search(kind):
        def node(state):
            context = load(state,'context',PreparedContext)
            raw = call(state,kind, getattr(adapters,kind),parse_payload(StoryRequest,state['request']),context)
            hits = [parse_payload(SearchHit,item) for item in raw]
            eligible = {p.reference.chunk_id:p.reference for p in context.evidence.passages}
            for hit in hits:
                if (hit.retriever != kind or hit.index_version != state['index_version'] or
                    hit.reference != eligible.get(hit.reference.chunk_id)):
                    raise ValueError('Retrieval returned an ineligible or mismatched passage.')
            return {kind+'_hits':[hit.model_dump(mode='json') for hit in hits]}
        return node

    def join(state):
        if state['errors']:
            return advance(state,'failed')
        context = load(state,'context',PreparedContext)
        if adapters.eligibility(context.evidence) is not True:
            return advance(state,'evidence_blocked',source_eligibility_status='blocked')
        # No RRF/reranker implemented here. Keep actual hits and injected evidence distinct.
        return advance(state,'writing')

    def write(state):
        context = load(state,'context',PreparedContext)
        request = parse_payload(StoryRequest,state['request'])
        if context.evidence and adapters.eligibility(context.evidence) is not True:
            return advance(state,'evidence_blocked')
        number = state['correction_count']
        draft = parse_payload(StoryDraft,call(state,'write-'+str(number),adapters.write,
                    request,context,number,state['review']))
        validate_story_context(draft,request,context.characters,context.evidence)
        repository.save(state['run_id'],draft.draft_id,draft.model_dump(mode='json'))
        return advance(state,'reviewing',draft_id=draft.draft_id,draft_hash=draft.draft_hash,
                       review=None,approval_id=None,book_version_hash=None,asset_manifest_id=None,
                       complete_preview_hash=None,incomplete_reasons=[],**attempts(state))

    def review(state):
        context = load(state,'context',PreparedContext)
        draft = load(state,state['draft_id'],StoryDraft)
        report = parse_payload(ReviewReport,call(state,'review-'+state['draft_hash'],adapters.review,
                         draft,context,state['correction_count']))
        if (report.draft_hash != draft.draft_hash or report.reviewer_version != state['reviewer_version'] or
            report.evidence_bundle_hash != (context.evidence.bundle_hash if context.evidence else None)):
            raise ValueError('Reviewer used a different draft, evidence or rubric version.')
        artifacts = {draft.draft_id:draft.model_dump(mode='json')}
        if context.evidence:
            artifacts[context.evidence.bundle_id] = context.evidence.model_dump(mode='json')
        validate_review_evidence(report,artifacts)
        repository.save(state['run_id'],'review-'+draft.draft_hash,report.model_dump(mode='json'))
        return {'review':report.model_dump(mode='json'),**attempts(state)}

    def correct(state):
        return advance(state,'correcting',correction_count=state['correction_count']+1)

    def next_episode(state):
        return advance(state,'validating',episode_index=state.get('episode_index',0)+1,
            correction_count=0,review=None,draft_id=None,draft_hash=None,approval_id=None,
            book_version_hash=None,asset_manifest_id=None,complete_preview_hash=None)

    def blocked(state):
        return advance(state,'evidence_blocked')

    def assets(state):
        context = load(state,'context',PreparedContext)
        draft = load(state,state['draft_id'],StoryDraft)
        manifest = parse_payload(AssetManifest,call(state,'assets-'+draft.draft_hash,adapters.assets,draft))
        repository.save(state['run_id'],manifest.manifest_id,manifest.model_dump(mode='json'))
        text_only=getattr(adapters,'text_only',False) is True
        complete = text_only or (len(manifest.pages) == 6 and all(p.scene_inspection == 'PASS' for p in manifest.pages))
        if not complete:
            return advance(state,'assets_pending',asset_manifest_id=manifest.manifest_id)
        pages = {p.page_number:p for p in manifest.pages}
        for page in (() if text_only else draft.pages):
            if not set(page.character_ids) <= set(pages[page.page_number].asset.character_ids):
                raise ValueError('Page image does not preserve the declared characters.')
        validate_story_context(draft,parse_payload(StoryRequest,state['request']),context.characters,context.evidence,None if text_only else manifest)
        authority=getattr(adapters,'validate_assets',None)
        if authority:authority(state,draft,context,manifest)
        version=state['correction_count']+1+state.get('parent_revision_count',0)
        if state.get('book_version_hash'):
            previous=load(state,'book-'+state['book_version_hash'],BookVersion)
            version=max(version,previous.version+1)
        book = BookVersion(book_id=state['run_id'],version=version,
            draft_id=draft.draft_id,draft_hash=draft.draft_hash,
            evidence_bundle_id=context.evidence.bundle_id if context.evidence else None,
            evidence_bundle_hash=context.evidence.bundle_hash if context.evidence else None,
            character_manifest_id=context.characters.manifest_id,character_manifest_hash=context.characters.manifest_hash,
            asset_manifest_id=manifest.manifest_id,asset_manifest_hash=manifest.manifest_hash,
            playbook_version=state['playbook_version'])
        repository.save(state['run_id'],'book-'+book.book_version_hash,book.model_dump(mode='json'))
        assemble=getattr(adapters,'assemble_complete_preview',None)
        if assemble:
            return advance(state,'preparing_audio',asset_manifest_id=manifest.manifest_id,
                book_version_hash=book.book_version_hash)
        return advance(state,'awaiting_parent_approval',asset_manifest_id=manifest.manifest_id,
                       book_version_hash=book.book_version_hash)

    def assemble_preview(state):
        return adapters.assemble_complete_preview(state,repository,owner)

    def approval(state):
        # Interrupt must be outside exception handlers: GraphInterrupt is control flow.
        authorize=getattr(adapters,'authorize_output',None)
        if authorize:
            approval_id=authorize(state,repository,owner)
            if not approval_id:
                interrupt(adapters.text_approval_prompt(state,repository))
                approval_id=authorize(state,repository,owner)
                if not approval_id:raise PermissionError('Current parent consent is required.')
        else:
            approval_id = interrupt({'kind':'parent_approval','run_id':state['run_id'],
                'book_version_hash':state['book_version_hash'],'provenance':
                    'offline_replay' if state['generation_mode']=='replay' else state['generation_mode']})
        repository.check_approval(approval_id,state['run_id'],owner,state['book_version_hash'],
                                  content_hash(state['review']),state['playbook_version'])
        context = load(state,'context',PreparedContext)
        if context.evidence and adapters.eligibility(context.evidence) is not True:
            return advance(state,'failed')
        check=getattr(adapters,'check_book',None)
        if check:check(state,repository,owner)
        release_check=getattr(adapters,'check_release',None)
        if release_check:release_check(state,repository,owner,approval_id)
        return advance(state,'approved',approval_id=approval_id)

    def rendering(state):
        return advance(state,'rendering')

    def render(state):
        repository.check_approval(state['approval_id'],state['run_id'],owner,state['book_version_hash'],
                                  content_hash(state['review']),state['playbook_version'])
        context = load(state,'context',PreparedContext)
        if context.evidence and adapters.eligibility(context.evidence) is not True:
            return advance(state,'failed')
        check=getattr(adapters,'check_book',None)
        if check:check(state,repository,owner)
        release_check=getattr(adapters,'check_release',None)
        if release_check:release_check(state,repository,owner,state['approval_id'])
        book = load(state,'book-'+state['book_version_hash'],BookVersion)
        result = call(state,'render-'+book.book_version_hash,adapters.render,book)
        output_key=(adapters.output_key_for_state(state,book) if hasattr(adapters,'output_key_for_state')
                    else getattr(adapters,'output_key',lambda book:'output')(book))
        repository.save(state['run_id'],output_key,{'provenance':
            'offline_replay' if state['generation_mode']=='replay' else state['generation_mode'],'result':result,
                                               'book_version_hash':book.book_version_hash})
        return advance(state,'complete')

    graph = StateGraph(StoryState)
    for name, function in dict(validate=validate,prepare=prepare,dense=search('dense'),sparse=search('sparse'),
        join=join,write=write,review=review,correct=correct,blocked=blocked,next_episode=next_episode,assets=assets,
        assemble_preview=assemble_preview,rendering=rendering,render=render).items():
        # Parallel searches update disjoint channels and never compete for status.
        if name in ('dense','sparse'):
            def safe_search(state, function=function, name=name):
                try:
                    return function(state)
                except Exception:
                    return {'errors':[dict(error_id=name+'-failed',node=name,code='search_failed',
                        parent_message='A source search could not be completed.',retryable=False,trace_reference=None)]}
            graph.add_node(name,safe_search)
        else:
            graph.add_node(name,protected(name,function))
    graph.add_node('parent_approval',approval)
    graph.add_edge(START,'validate')
    graph.add_conditional_edges('validate',lambda s:'prepare' if s['status']=='validating' else END)
    graph.add_conditional_edges('prepare',lambda s: ['dense','sparse'] if s['status']=='retrieving'
                                else ('write' if s['status']=='writing' else END))
    graph.add_edge(['dense','sparse'],'join')
    graph.add_conditional_edges('join',lambda s:'write' if s['status']=='writing' else END)
    graph.add_conditional_edges('write',lambda s:'review' if s['status']=='reviewing' else END)
    graph.add_conditional_edges('review',lambda s:getattr(adapters,'route_review',after_review)(s) if s['status']=='reviewing' else END)
    graph.add_edge('correct','write')
    graph.add_edge('blocked',END)
    graph.add_edge('next_episode','prepare')
    graph.add_conditional_edges('assets',lambda s:'assemble_preview' if s['status']=='preparing_audio' else ('parent_approval' if s['status']=='awaiting_parent_approval' else END))
    graph.add_conditional_edges('assemble_preview',lambda s:'parent_approval' if s['status']=='awaiting_parent_approval' else END)
    graph.add_conditional_edges('parent_approval',lambda s:'rendering' if s['status']=='approved' else END)
    graph.add_conditional_edges('rendering',lambda s:'render' if s['status']=='rendering' else END)
    graph.add_edge('render',END)
    return graph.compile(checkpointer=checkpointer,name='storybook-workflow')
