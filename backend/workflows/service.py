"""Internal run API with async execution; live HTTP integration is deferred.

Replay remains the default. Trusted provider adapters opt into explicit provider
modes; the public replay HTTP route does not activate them. The caller supplies
an authenticated owner; this service is not an authentication API.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from pathlib import Path
from langgraph.types import Command
from langsmith import tracing_context
from backend.contracts import StoryRequest, content_hash, parse_payload
from backend.contracts.common import Identifier
from pydantic import TypeAdapter
from .checkpoints import checkpoint_store, run_lock
from .repository import RunRepository
from .story_graph import build_graph

STATE_VERSION = 'story-graph-state-v1'


class WorkflowService:
    def __init__(self, folder, adapters, *, enabled=False):
        self.folder = Path(folder)
        self.adapters = adapters
        self.enabled = enabled
        # No filesystem writes while the feature is disabled.
        self.repository = RunRepository(folder) if enabled else None
        self.pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='storybook-replay') if enabled else None
        self._submissions={};self._submission_epochs={};self._submission_lock=Lock()

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _enabled(self):
        if not self.enabled:
            raise PermissionError('The new story workflow is not enabled.')

    def start(self, owner, request_key, request, *, provider_mode='auto', generation_mode='replay',creation_options=None):
        self._enabled()
        for value in (owner, request_key, self.adapters.version):
            TypeAdapter(Identifier).validate_python(value,strict=True)
        if provider_mode not in ('auto','groq','nebius') or generation_mode not in getattr(self.adapters,'supported_modes',('replay',)):
            raise ValueError('Generation mode is not supported by these workflow adapters.')
        request = parse_payload(StoryRequest,request)
        payload = dict(request=request.model_dump(mode='json'),request_hash=content_hash(request),
                       provider_mode=provider_mode,generation_mode=generation_mode,
                       adapter_version=self.adapters.version,capabilities_hash=content_hash(self.adapters.capabilities),
                       playbook_id='storybook-guide',playbook_version='stage3-playbook-v1',
                       reviewer_version='stage3-reviewer-v1',state_version=STATE_VERSION)
        if hasattr(self.adapters,'runtime_identity'):
            payload.update(runtime_identity=self.adapters.runtime_identity(),
                           reviewer_version=self.adapters.reviewer_version,
                           playbook_version=self.adapters.playbook_version)
        if creation_options is not None:payload['creation_options']=creation_options
        return self.repository.create(owner,request_key,payload)

    def read_payload(self, run_id, owner):
        self._enabled()
        payload = self.repository.get(run_id,owner)
        if payload['state_version'] != STATE_VERSION:
            raise ValueError('Checkpoint migration is required.')
        return payload

    def _payload(self, run_id, owner):
        payload = self.read_payload(run_id,owner)
        if payload['adapter_version'] != self.adapters.version or payload['capabilities_hash'] != content_hash(self.adapters.capabilities):
            raise ValueError('Run recovery requires the original adapter and capability versions.')
        if payload['state_version'] != STATE_VERSION:
            raise ValueError('Checkpoint migration is required.')
        if hasattr(self.adapters,'runtime_identity') and payload.get('runtime_identity') != self.adapters.runtime_identity():
            raise ValueError('Recovery requires the original prompts, models and policy configuration.')
        return payload

    def _initial(self, run_id, payload):
        return dict(state_version=STATE_VERSION,run_id=run_id,thread_id=run_id,
            request=payload['request'],request_hash=payload['request_hash'],playbook_id=payload['playbook_id'],
            playbook_version=payload['playbook_version'],source_snapshot_ids=[],source_eligibility_status='pending',
            index_version=None,dense_hits=[],sparse_hits=[],fused_hits=[],ranked_hits=[],evidence_bundle_id=None,
            draft_id=None,draft_hash=None,character_manifest_id=None,asset_manifest_id=None,review=None,
            reviewer_version=payload['reviewer_version'],correction_count=0,provider_attempts=[],approval_id=None,
            book_version_hash=None,generation_mode=payload['generation_mode'],provider_mode=payload['provider_mode'],
            status='queued',errors=[],creation_options=payload.get('creation_options',{}),
            complete_preview_hash=None,incomplete_reasons=[],revision_epoch='',revision_notes='',parent_revision_count=0)

    def _graph(self, owner, saver):
        return build_graph(self.repository,self.adapters,owner=owner,checkpointer=saver)

    @staticmethod
    def _config(run_id):
        return {'configurable':{'thread_id':run_id},'recursion_limit':70,'callbacks':[]}

    def status(self, run_id, owner):
        self.read_payload(run_id,owner)
        # Polling is a snapshot read, not a competing writer. Holding run_lock
        # here can make a newly submitted nonblocking worker exit before it starts.
        with checkpoint_store(self.folder/'checkpoints.sqlite') as saver, tracing_context(enabled=False):
            snapshot = self._graph(owner,saver).get_state(self._config(run_id))
            return dict(values=dict(snapshot.values),next=list(snapshot.next))

    def execute(self, run_id, owner):
        payload = self._payload(run_id,owner)
        with run_lock(self.folder,run_id), checkpoint_store(self.folder/'checkpoints.sqlite') as saver, tracing_context(enabled=False):
            graph = self._graph(owner,saver)
            config = self._config(run_id)
            snapshot = graph.get_state(config)
            if snapshot.values:
                # Never resume a parent interrupt through ordinary worker execution.
                if snapshot.values['status'] == 'awaiting_parent_approval' or not snapshot.next:
                    return dict(snapshot.values)
                value = None
            else:
                value = self._initial(run_id,payload)
            graph.invoke(value,config,durability='sync')
            return dict(graph.get_state(config).values)

    def submit(self, run_id, owner):
        self._payload(run_id,owner)
        epoch=self.status(run_id,owner).get('values',{}).get('revision_epoch','')
        with self._submission_lock:
            key=(run_id,owner);old=self._submissions.get(key)
            if old and not old.done() and self._submission_epochs.get(key)==epoch:return old
            future=self.pool.submit(self.execute,run_id,owner)
            self._submissions[key]=future
            self._submission_epochs[key]=epoch
            # Completed entries are bounded to avoid retaining every result forever.
            for previous in list(self._submissions):
                if previous!=key and self._submissions[previous].done():
                    del self._submissions[previous];self._submission_epochs.pop(previous,None)
            return future

    def recovery_options(self,run_id,owner):
        self.read_payload(run_id,owner)
        with self._submission_lock:
            active=self._submissions.get((run_id,owner))
            if active and not active.done():return dict(can_resume=False,requires_attention=False,reason='This request is already running.',status='running')
        state=self.status(run_id,owner)['values'];status=state.get('status','queued')
        if status not in ('queued','validating','retrieving','writing','reviewing','correcting'):
            return dict(can_resume=False,requires_attention=status=='outcome_unknown',reason='An earlier provider result needs confirmation before any retry.' if status=='outcome_unknown' else 'This request does not need automatic continuation.',status=status)
        with self.repository.db() as db:
            unresolved=db.execute("SELECT count(*) FROM operations WHERE run_id=? AND status!='succeeded'",(run_id,)).fetchone()[0]
        if unresolved:return dict(can_resume=False,requires_attention=True,reason='An earlier operation has an unconfirmed result. It will not be repeated automatically.',status=status)
        try:self._payload(run_id,owner)
        except (ValueError,PermissionError):return dict(can_resume=False,requires_attention=True,reason='Creation settings changed. This request cannot continue under different settings.',status=status)
        return dict(can_resume=True,requires_attention=False,reason='Continue this saved request without creating a new request. Provider credits may be used for remaining work.',status=status)

    def resume_saved(self,run_id,owner):
        result=self.recovery_options(run_id,owner)
        if not result['can_resume']:raise ValueError(result['reason'])
        self.submit(run_id,owner)
        return dict(run_id=run_id,status='accepted')

    def approve(self, run_id, owner, expected_book_hash):
        self._payload(run_id,owner)
        with run_lock(self.folder,run_id), checkpoint_store(self.folder/'checkpoints.sqlite') as saver, tracing_context(enabled=False):
            graph = self._graph(owner,saver)
            config = self._config(run_id)
            snapshot = graph.get_state(config)
            state = snapshot.values
            if (snapshot.next != ('parent_approval',) or state.get('status') != 'awaiting_parent_approval'
                or expected_book_hash != state.get('book_version_hash')):
                raise ValueError('This book version is not awaiting approval.')
            # A review reconstructed from the exact saved artifact must still PASS.
            from backend.contracts import ReviewReport
            report = parse_payload(ReviewReport,self.repository.load(run_id,'review-'+state['draft_hash']))
            if report.overall_result != 'PASS' or report.draft_hash != state['draft_hash'] or report.model_dump(mode='json') != state['review']:
                raise ValueError('A current review PASS is required.')
            approval = self.repository.approve(run_id,owner,expected_book_hash,
                                               content_hash(state['review']),state['playbook_version'])
            graph.invoke(Command(resume=approval),config,durability='sync')
            return dict(graph.get_state(config).values)
