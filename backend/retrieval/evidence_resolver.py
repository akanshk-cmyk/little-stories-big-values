"""Request-bound bridge from hosted selections to existing graph evidence.

No default semantic PASS, character substitution, model call or live assembly.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Literal

from pydantic import Field, model_validator

from backend.contracts.common import Contract, Digest, Identifier, Text, content_hash, parse_payload
from backend.contracts.complete_book import CreationOptions
from backend.contracts.evidence import EvidenceBundle
from backend.contracts.requests import StoryRequest
from backend.contracts.assets import CharacterManifest
from backend.providers.retry_policy import ProviderFailure
from backend.workflows.story_graph import PreparedContext
from .hosted_search import HostedQuery, HostedSearchSelection, HybridSearchOptions
from .hosted_search import SearchBlocked
from .llamacloud import PublicationBlocked


class EvidenceSourcePlan(Contract):
    publication_id: Digest
    query: Text = Field(max_length=2000)
    usage_authorization_id: Identifier
    options: HybridSearchOptions = Field(default_factory=HybridSearchOptions)


class SupportQuote(Contract):
    passage_id: Identifier
    quote: Text


class SufficiencyAssessment(Contract):
    request_hash: Digest
    selection_hash: Digest
    policy_version: Identifier
    verdict: Literal['PASS', 'FAIL', 'PENDING'] = 'PENDING'
    reason: Text
    references: tuple[SupportQuote, ...] = Field(default=(), max_length=50)

    @model_validator(mode='after')
    def pass_requires_support(self):
        if self.verdict == 'PASS' and not self.references:
            raise ValueError('Evidence sufficiency PASS requires supporting quotes.')
        return self


class EvidenceResolution(Contract):
    seal_field = 'record_hash'
    resolution_id: Digest
    owner_id: Identifier
    request: StoryRequest
    options: CreationOptions
    query: HostedQuery
    selection: HostedSearchSelection
    assessment: SufficiencyAssessment
    bundle: EvidenceBundle | None = None
    record_hash: Digest | None = None


class EvidenceResolutions:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'evidence-resolutions.sqlite'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS resolutions '
                       '(id TEXT PRIMARY KEY,owner TEXT NOT NULL,bundle_id TEXT UNIQUE,payload TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, record):
        payload = record.model_dump_json()
        with self.db() as db:
            old = db.execute('SELECT payload FROM resolutions WHERE id=?', (record.resolution_id,)).fetchone()
            if old and old[0] != payload:
                raise ValueError('A frozen evidence resolution cannot be overwritten.')
            db.execute('INSERT OR IGNORE INTO resolutions VALUES(?,?,?,?)',
                (record.resolution_id, record.owner_id, record.bundle.bundle_id if record.bundle else None, payload))

    def for_bundle(self, bundle_id):
        with self.db() as db:
            row = db.execute('SELECT payload FROM resolutions WHERE bundle_id=?', (bundle_id,)).fetchone()
        if not row:
            raise PermissionError('Evidence bundle has no saved hosted resolution.')
        return EvidenceResolution.model_validate_json(row[0])


class HostedEvidenceResolver:
    version = 'hosted-evidence-resolver-v1'

    def __init__(self, search, store, *, characters, source_selector=None, authorize=None,
                 assess=None, policy_version='unconfigured-sufficiency-v1',
                 selector_version='unconfigured-selector-v1', character_version='characters-v1'):
        self.search, self.store, self.characters = search, store, characters
        self.source_selector = source_selector or (lambda owner, request, options: None)
        self.authorize = authorize or (lambda owner, request, options, operation: False)
        self.assess = assess
        self.policy_version, self.selector_version, self.character_version = policy_version, selector_version, character_version
        # Validate identities up front without reading secrets or installing tokenizers.
        class Versions(Contract):
            policy: Identifier
            selector: Identifier
            characters: Identifier
        Versions(policy=policy_version, selector=selector_version, characters=character_version)

    def runtime_identity(self):
        return content_hash([self.version, self.policy_version, self.selector_version,
                             self.character_version, self.search.token_counter_version])

    @staticmethod
    def _purpose(request):
        return ('traditional_claims' if request.story_type != 'original_inspired' or
                'cultural_fact' in request.required_elements else 'parent_inspiration')

    def _authorize(self, owner, request, options, operation):
        if self.authorize(owner, request, options, operation) is not True:
            raise ProviderFailure('unauthorized_request')

    def _validate_assessment(self, request, selection, assessment):
        if (assessment.request_hash != content_hash(request) or
                assessment.selection_hash != selection.selection_hash or
                assessment.policy_version != self.policy_version):
            raise ProviderFailure('evidence_sufficiency_pending')
        passages = {p.reference.chunk_id: p.text for p in selection.passages}
        for reference in assessment.references:
            if reference.passage_id not in passages or reference.quote not in passages[reference.passage_id]:
                raise ProviderFailure('evidence_sufficiency_pending')
        if assessment.verdict == 'PASS' and not selection.passages:
            raise ProviderFailure('evidence_missing')

    def __call__(self, owner, request, options):
        if not isinstance(request, StoryRequest) or not isinstance(options, CreationOptions):
            raise TypeError('Validated request and creation options are required.')
        self._authorize(owner, request, options, 'prepare')
        options.validate_request(request)
        characters = parse_payload(CharacterManifest, self.characters(owner, request))
        if not set(request.required_character_ids) <= {c.character_id for c in characters.characters}:
            raise ProviderFailure('preconditions_failed')
        if len(set(options.selected_source_ids)) != len(options.selected_source_ids):
            raise ProviderFailure('evidence_missing')
        # Pure original fiction can use the existing no-evidence path. Never downgrade a retelling.
        if (request.story_type == 'original_inspired' and 'cultural_fact' not in request.required_elements
                and not options.selected_source_ids):
            return PreparedContext(characters=characters).model_dump(mode='json')
        if len(options.selected_source_ids) > 1:
            raise ProviderFailure('evidence_missing')  # Initial single-source publication boundary.
        source = self.source_selector(owner, request, options)
        if not isinstance(source, EvidenceSourcePlan):
            raise ProviderFailure('evidence_missing')
        try:
            _, publication = self.search.publisher._load(owner, source.publication_id)
        except (PermissionError, ValueError):
            raise ProviderFailure('evidence_missing') from None
        manifest = publication.manifest
        if (manifest.purpose != self._purpose(request) or (options.selected_source_ids and
                options.selected_source_ids != (manifest.source_id,))):
            raise ProviderFailure('evidence_missing')
        operation = content_hash([owner, request.model_dump(mode='json'), options.model_dump(mode='json'),
                                  source.model_dump(mode='json'), self.runtime_identity()])
        query = HostedQuery(operation_id='resolve-' + operation, publication_id=source.publication_id,
            story_request_hash=content_hash(request), query=source.query,
            usage_authorization_id=source.usage_authorization_id, options=source.options)
        try:
            selection = self.search.search(owner, query)
        except (SearchBlocked, PublicationBlocked, PermissionError):
            raise ProviderFailure('evidence_missing') from None
        self._authorize(owner, request, options, 'assess')
        if not selection.passages:
            assessment = SufficiencyAssessment(request_hash=content_hash(request),
                selection_hash=selection.selection_hash, policy_version=self.policy_version,
                verdict='FAIL', reason='No validated passages were retrieved; source evidence is missing.')
        elif self.assess is None:
            assessment = SufficiencyAssessment(request_hash=content_hash(request),
                selection_hash=selection.selection_hash, policy_version=self.policy_version,
                reason='An independent evidence-sufficiency decision is not configured.')
        else:
            assessment = parse_payload(SufficiencyAssessment, self.assess(owner, request, selection))
        self._validate_assessment(request, selection, assessment)
        key = content_hash([owner, request.model_dump(mode='json'), options.model_dump(mode='json'),
                            query.model_dump(mode='json'), selection.selection_hash, assessment.model_dump(mode='json')])
        bundle = None
        if assessment.verdict == 'PASS':
            self.search.assert_current(owner, query, selection)
            self._authorize(owner, request, options, 'freeze')
            bundle = EvidenceBundle(bundle_id='bundle-' + key, request_hash=content_hash(request),
                passages=selection.passages, index_version=selection.index_version,
                retrieval_config_hash=selection.retrieval_config_hash,
                eligibility_epoch=manifest.eligibility_epoch)
        self.store.save(EvidenceResolution(resolution_id=key, owner_id=owner, request=request,
            options=options, query=query, selection=selection, assessment=assessment, bundle=bundle))
        if assessment.verdict != 'PASS':
            raise ProviderFailure('evidence_missing' if assessment.verdict == 'FAIL' else 'evidence_sufficiency_pending')
        return PreparedContext(characters=characters, evidence=bundle).model_dump(mode='json')

    def eligibility(self, bundle):
        """Recheck saved identity, request permissions, source rights and remote state."""
        try:
            if not isinstance(bundle, EvidenceBundle):
                return False
            record = self.store.for_bundle(bundle.bundle_id)
            if record.bundle != bundle or record.assessment.verdict != 'PASS':
                return False
            self._authorize(record.owner_id, record.request, record.options, 'use_evidence')
            self._validate_assessment(record.request, record.selection, record.assessment)
            _, publication = self.search.publisher._load(record.owner_id, record.query.publication_id)
            if (publication.manifest.purpose != self._purpose(record.request) or
                    (record.options.selected_source_ids and record.options.selected_source_ids != (publication.manifest.source_id,))):
                return False
            self.search.assert_current(record.owner_id, record.query, record.selection)
            return (bundle.request_hash == content_hash(record.request) and
                    bundle.passages == record.selection.passages and
                    bundle.index_version == record.selection.index_version and
                    bundle.retrieval_config_hash == record.selection.retrieval_config_hash)
        except Exception:
            return False

    def sufficiency(self, request, bundle):
        return 'PASS' if (isinstance(request, StoryRequest) and isinstance(bundle, EvidenceBundle) and bundle.request_hash == content_hash(request)
                          and self.eligibility(bundle)) else 'PENDING'

    def bind_adapters(self, base):
        return EvidenceBoundAdapters(base, self)


class EvidenceBoundAdapters:
    """Opt-in wrapper: reuse writer/assets; never fabricate local search hits."""
    uses_prepared_evidence = True
    version = 'hosted-evidence-bound-adapters-v1'

    def __init__(self, base, resolver):
        self.base, self.resolver = base, resolver
        self.playbook_version = getattr(base, 'playbook_version', 'stage3-playbook-v1')
        self.reviewer_version = getattr(base, 'reviewer_version', 'stage3-reviewer-v1')

    def __getattr__(self, name):
        return getattr(self.base, name)

    def eligibility(self, bundle):
        return self.resolver.eligibility(bundle)

    def review(self, draft, context, number):
        if context.evidence and not self.resolver.eligibility(context.evidence):
            raise ProviderFailure('evidence_withdrawn')
        return self.base.review(draft, context, number)

    def runtime_identity(self):
        return content_hash([self.version, getattr(self.base, 'runtime_identity', lambda: self.base.version)(),
                             self.resolver.runtime_identity()])
