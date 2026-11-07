"""Explicit dependency assembly; no default secrets, cloud setup or live activation."""
from .complete_book import CompleteBookAdapters,CompleteBookWorkflowService
from .http_api import AccountAPI
from backend.workflows.provider_content import ProviderContentAdapters
from pathlib import Path


def assemble_complete_accounts(folder,*,accounts,entitlements,registry,base,router,
        authorize,evidence_sufficiency,provider_consent,evidence_resolver,
        media_policy_version,narrator=None,audio_checker=None,audio_authorize=None,
        voice_options=(),allow_live_calls=False,synthetic=False,hosted_sources=None,script_first=False,episode_planner=None,generation_operation_limit=6,narration_enabled=True,text_only=False,allow_short_stories_for_testing=False):
    """Caller supplies trusted eligibility/retrieval and current consent policies.

    Source curation is upstream of evidence_resolver. Never substitute a browser
    checkbox or a nearest-neighbor result for qualified traditional eligibility.
    The caller owns shutdown of the returned workflow and supplied router.
    """
    folder=Path(folder)
    bridge=ProviderContentAdapters(base,router,authorize=authorize,
        evidence_sufficiency=evidence_sufficiency,provider_consent=provider_consent,
        allow_live_calls=allow_live_calls,synthetic=synthetic)
    if script_first:
        from .script_first import ScriptFirstAdapters,ScriptFirstWorkflowService
    if text_only:
        if not script_first:raise ValueError('Text-only release requires the v5 approval policy.')
        from .text_only import TextOnlyAdapters,TextOnlyWorkflowService
    adapter_type=TextOnlyAdapters if text_only else (ScriptFirstAdapters if script_first else CompleteBookAdapters)
    service_type=TextOnlyWorkflowService if text_only else (ScriptFirstWorkflowService if script_first else CompleteBookWorkflowService)
    if text_only:
        bridge.compact_review=True
        bridge.format_repair=True
        bridge.playbook=dict(bridge.playbook)
        bridge.playbook['rules']=[dict(rule,instruction='This explicitly authorized release is text-only. Evaluate story text and scene descriptions; no images are required or claimed. Do not fail for absent illustrations.' if rule['id']=='text_only_scope' else rule['instruction']) for rule in bridge.playbook['rules']]
    if allow_short_stories_for_testing:
        if not text_only:raise ValueError('Short-story testing allowance requires explicit text-only mode.')
        bridge.strict_duration=False
        bridge.writer_max_tokens=980
        bridge.review_max_tokens=980
        bridge.compact_writer=True
        bridge.playbook_version='storybook-short-duration-test-v1'
        bridge.playbook=dict(bridge.playbook,version=bridge.playbook_version)
        bridge.playbook['rules']=[dict(rule,instruction='Parent-authorized testing allowance: shorter stories are acceptable. Requested duration is a target, not a release gate. Keep every other parent requirement, factual and safety check mandatory; disclose the shorter length.' if rule['id']=='duration_alignment' else rule['instruction']) for rule in bridge.playbook['rules']]
    adapters=adapter_type(bridge,accounts,registry,media_folder=folder/'media',
        narrator=narrator,audio_checker=audio_checker,audio_authorize=audio_authorize,
        evidence_resolver=evidence_resolver,media_policy_version=media_policy_version)
    if episode_planner is not None:
        if not script_first:raise ValueError('Episode planning requires the v5 workflow.')
        adapters.episode_planner=episode_planner
    from .entitlements import Entitlements
    workflow_ledger=Entitlements(accounts,clock=entitlements.clock,
        max_generation_operations_per_day=entitlements.max_generation_operations_per_day,
        max_generation_operations_per_run=generation_operation_limit)
    service_options={'narration_enabled':narration_enabled} if script_first else {}
    workflow=service_type(folder,adapters,accounts,workflow_ledger,enabled=True,**service_options,
        public_generation_mode='provider_synthetic' if synthetic else 'provider_live',voice_options=voice_options)
    return AccountAPI(accounts,entitlements,registry,workflow,hosted_sources=hosted_sources)


def assemble_hosted_complete_accounts(folder, *, resolver, base, **kwargs):
    """Explicit opt-in hosted-evidence assembly; never changes default startup."""
    from backend.retrieval.evidence_resolver import HostedEvidenceResolver
    if not isinstance(resolver, HostedEvidenceResolver):
        raise TypeError('A configured hosted evidence resolver is required.')
    if 'evidence_resolver' in kwargs or 'evidence_sufficiency' in kwargs:
        raise ValueError('Hosted assembly uses the resolver-bound evidence checks.')
    return assemble_complete_accounts(folder, base=resolver.bind_adapters(base),
        evidence_resolver=resolver, evidence_sufficiency=resolver.sufficiency, **kwargs)
