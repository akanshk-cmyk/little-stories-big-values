"""Explicit full backend composition. No default startup, grants or secret reads."""
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import Field, model_validator

from backend.contracts.common import Contract, Identifier, content_hash, parse_payload
from backend.contracts.requests import StoryRequest
from backend.contracts.complete_book import CreationOptions
from backend.ingestion.workspace import SourceWorkspace
from backend.ingestion.llamaparse import LlamaParseAdapter, ParseJobs
from backend.ingestion.hosted_review import HostedSourceReview
from backend.ingestion.hosted_service import HostedSourceService
from backend.retrieval.llamacloud import LlamaCloudPublisher, HostedPublications
from backend.retrieval.hosted_search import LlamaCloudSearch, HostedSearches
from backend.retrieval.evidence_resolver import HostedEvidenceResolver, EvidenceResolutions
from backend.retrieval.hosted_catalog import HostedSourceCatalog
from .assembly import assemble_hosted_complete_accounts


def deny(*args):
    return False


@dataclass(frozen=True)
class HostedPolicyPorts:
    automated_source_authorize: object = deny
    source_rights_authorize: object = deny
    source_qa_review: object = None
    qualified_review: object = deny
    parse_usage: object = deny
    publication_usage: object = deny
    index_usage_reference: object = deny
    query_usage_reference: object = deny
    query_authorize: object = deny
    story_authorize: object = deny
    provider_authorize: object = deny
    provider_consent: object = deny
    episode_planner: object = None
    characters: object = None
    token_counter: object = None
    assess: object = None


class HostedApplicationConfig(Contract):
    source_qa_version: Identifier = 'source-qa-v1'
    parser_version: Identifier
    parse_config_id: Identifier
    policy_version: Identifier
    sufficiency_version: Identifier
    character_version: Identifier
    token_counter_version: Identifier
    media_policy_version: Identifier
    generation_operation_limit: int = Field(default=6, ge=1, le=1000)
    pdf_only: bool = True
    script_first: bool = False  # Explicit v5 opt-in; old saved workflows remain v4.
    worker_steps: int = Field(default=5, ge=1, le=10)
    poll_interval: float = Field(default=2.0, ge=0, le=30)

    @model_validator(mode='after')
    def pinned_parser(self):
        if self.parser_version.lower() == 'latest':
            raise ValueError('Use a pinned parser version.')
        return self


class HostedApplication:
    """Owns created workspace/workflow workers; borrows accounts, router and registry."""
    def __init__(self):
        self.api = None
        self.workspace = None
        self.sources = None

    def close(self):
        if self.sources:
            self.sources.close()
        if self.api and self.api.workflow:
            self.api.workflow.close()
        if self.workspace:
            self.workspace.close()

    def attach(self, server):
        if server.server_address[0] not in ('127.0.0.1', '::1'):
            raise PermissionError('This account application is loopback-only.')
        old = getattr(server, 'account_api', None)
        if old and old is not self.api:
            if old.accounts is not self.api.accounts or old.workflow is not None:
                raise ValueError('Existing account/workflow authority cannot be silently replaced.')
        server.account_api = self.api


def build_hosted_application(folder, *, accounts, entitlements, registry, base, router,
        settings, config, policies, enabled=False, synthetic=False, allow_live_calls=False,
        transport=None, catalog_defaults=None, narrator=None, audio_checker=None,
        audio_authorize=None, voice_options=()):
    if enabled is not True:
        raise PermissionError('Hosted application composition is disabled.')
    if type(synthetic) is not bool or type(allow_live_calls) is not bool or synthetic and allow_live_calls:
        raise ValueError('Choose an explicit offline or authorized live mode.')
    if not isinstance(config, HostedApplicationConfig) or not isinstance(policies, HostedPolicyPorts):
        raise TypeError('Validated configuration and trusted policy ports are required.')
    if not callable(policies.characters) or not callable(policies.token_counter):
        raise ValueError('Reviewed characters and an actual versioned token counter are required.')
    from backend.workflows.episode_planner import EpisodePlanner
    for name in HostedPolicyPorts.__dataclass_fields__:
        value = getattr(policies, name)
        if name == 'episode_planner':
            if value is not None and not isinstance(value, EpisodePlanner):
                raise ValueError('Episode planning requires a trusted EpisodePlanner instance.')
            if value is not None and not config.script_first:
                raise ValueError('Episode planning requires the v5 script-first workflow.')
        elif value is not None and not callable(value):
            raise ValueError('Policy ports must be trusted callables.')
    if not synthetic and not allow_live_calls:
        raise PermissionError('Live composition needs separate explicit authorization.')
    if synthetic and (not isinstance(transport, httpx.MockTransport) or any(
            adapter.client is None or not isinstance(adapter.client._transport, httpx.MockTransport)
            for adapter in router.adapters.values())):
        raise ValueError('Offline composition requires mock transports for all external services.')
    if (not settings.enabled or settings.api_generation != 'v2' or
            not settings.api_key.get_secret_value().strip() or not settings.organization_id or not settings.project_id):
        raise ValueError('Explicit hosted v2 credentials and destination are required.')
    if not router.enabled or any(not item.configured(role) for item in router.settings.values()
                                 for role in ('writer', 'reviewer')):
        raise ValueError('Both story providers must have configured writer/reviewer settings.')
    if entitlements.accounts is not accounts or registry.accounts is not accounts:
        raise ValueError('All account services must share the same authority.')
    runtime = HostedApplication()
    folder = Path(folder)
    try:
        runtime.workspace = SourceWorkspace(folder / 'sources')
        def reviewer(actor, action, owner, source):
            try:
                accounts.require(actor, 'admin')
                accounts.require(owner, role=None)
                return policies.qualified_review(actor, action, owner, source) is True
            except PermissionError:
                return False
        def automated_authority(owner,source,version):
            try:
                accounts.require(owner,role=None)
                return policies.automated_source_authorize(owner,source,version) is True
            except PermissionError:return False
        def source_rights(owner,source,purpose,destination):
            try:
                accounts.require(owner,role=None)
                return policies.source_rights_authorize(owner,source,purpose,destination) is True
            except PermissionError:return False
        runtime.reviews = HostedSourceReview(folder / 'reviews', runtime.workspace, settings,
            can_manage=reviewer, usage_allowed=policies.parse_usage,
            automated_authorize=automated_authority,rights_authorize=source_rights,automated_version=config.source_qa_version)
        runtime.parser = LlamaParseAdapter(settings, ParseJobs(folder / 'parse'),
            authorize=runtime.reviews.authorize_parse, transport=transport, allow_live_calls=allow_live_calls)
        runtime.publisher = LlamaCloudPublisher(settings, HostedPublications(folder / 'publications'),
            runtime.reviews, authorize=policies.publication_usage, transport=transport, allow_live_calls=allow_live_calls)
        runtime.catalog = HostedSourceCatalog(accounts, runtime.workspace, runtime.publisher,
            usage_reference=policies.query_usage_reference, defaults=catalog_defaults)

        def owned_child(owner, request):
            accounts.require(owner)
            return any(c['id'] == request.child_profile_id and c['age'] == request.age for c in accounts.children(owner))

        def story_guard(owner, request, options, operation):
            return owned_child(owner, request) and policies.story_authorize(owner, request, options, operation) is True

        def query_guard(owner, query, manifest, operation):
            if runtime.api is None:
                return False
            # Resolve the hash to an actual saved owned request; never trust a hash alone.
            with runtime.api.workflow.repository.db() as db:
                rows = db.execute('SELECT payload,fingerprint FROM runs WHERE owner=?', (owner,)).fetchall()
            for row in rows:
                import json
                payload = json.loads(row['payload'])
                if content_hash(payload) != row['fingerprint'] or payload.get('request_hash') != query.story_request_hash:
                    continue
                request = parse_payload(StoryRequest, payload['request'])
                options = parse_payload(CreationOptions, payload.get('creation_options', {}))
                purpose = HostedEvidenceResolver._purpose(request)
                if (not owned_child(owner, request) or manifest.owner_id != owner or manifest.purpose != purpose or
                        query.query != runtime.catalog.query_for(request) or query.options != runtime.catalog.options or
                        (options.selected_source_ids and options.selected_source_ids != (manifest.source_id,))):
                    continue
                if policies.query_authorize(owner, request, options, query, manifest, operation) is True:
                    return True
            return False

        runtime.search = LlamaCloudSearch(runtime.publisher, HostedSearches(folder / 'search'),
            authorize=query_guard, token_counter=policies.token_counter, token_counter_version=config.token_counter_version)
        runtime.resolver = HostedEvidenceResolver(runtime.search, EvidenceResolutions(folder / 'evidence'),
            characters=policies.characters, source_selector=runtime.catalog.select, authorize=story_guard,
            assess=policies.assess, policy_version=config.sufficiency_version,
            selector_version='composed-selector-' + content_hash([runtime.catalog.version, content_hash(config)]),
            character_version=config.character_version)
        runtime.sources = HostedSourceService(folder / 'jobs', accounts, runtime.workspace,
            runtime.parser, runtime.reviews, runtime.publisher, enabled=True,
            parser_version=config.parser_version, index_parse_config_id=config.parse_config_id,
            index_usage=policies.index_usage_reference, max_steps=config.worker_steps, poll_interval=config.poll_interval,source_qa=policies.source_qa_review)
        runtime.sources.catalog = runtime.catalog
        runtime.sources.readiness = lambda: dict(composition='hosted-account-v1',
            mode='offline_synthetic' if synthetic else 'live_opt_in', config_hash=content_hash(config),
            live_authentication='not_verified', actual_book_quality='not_verified',
            sufficiency_policy_configured=policies.assess is not None)
        runtime.api = assemble_hosted_complete_accounts(folder / ('workflow-v5' if config.script_first else 'workflow'), resolver=runtime.resolver,
            script_first=config.script_first,episode_planner=policies.episode_planner,
            generation_operation_limit=config.generation_operation_limit,narration_enabled=not config.pdf_only,
            base=base, accounts=accounts, entitlements=entitlements, registry=registry, router=router,
            authorize=lambda owner, request: owned_child(owner, request) and policies.provider_authorize(owner, request) is True,
            provider_consent=policies.provider_consent, media_policy_version=config.media_policy_version,
            hosted_sources=runtime.sources, narrator=narrator, audio_checker=audio_checker,
            audio_authorize=audio_authorize, voice_options=voice_options,
            synthetic=synthetic, allow_live_calls=allow_live_calls)
        return runtime
    except Exception:
        runtime.close()
        raise
