from pydantic import AwareDatetime, Field, model_validator
from typing import Literal
from .common import Contract, Digest, Identifier, Text, content_hash, unique
from .requests import StoryType, StoryRequest
from .assets import AssetManifest, CharacterManifest
from .evidence import EvidenceBundle


class ClaimCitation(Contract):
    chunk_id: Identifier
    claim_text: Text = Field(description='Copy the exact sentence or phrase from this page.text that needs source support. This is child-friendly STORY text, not text from the source document.')
    source_quote: Text = Field(description='Copy an exact contiguous supporting quote from the evidence passage identified by chunk_id. Preserve punctuation and newline characters. This is SOURCE text, not page.text.')
    claim_kind: Literal['factual', 'fictional_premise']


class StoryPage(Contract):
    page_number: int = Field(ge=1, le=6)
    text: Text = Field(max_length=6000)
    character_ids: tuple[Identifier, ...]
    scene_description: Text
    accessible_description: Text
    citations: tuple[ClaimCitation, ...] = ()
    selected_asset_id: Identifier | None = None

    @model_validator(mode='after')
    def valid_page(self):
        unique(self.character_ids, 'page characters')
        for citation in self.citations:
            if citation.claim_text not in self.text:
                raise ValueError('A cited claim does not appear on its story page.')
        return self


class StoryDraft(Contract):
    seal_field = 'draft_hash'
    draft_id: Identifier
    request_hash: Digest
    classification: StoryType
    fiction_disclosure: Text | None = None
    title: Text = Field(max_length=6000)
    pages: tuple[StoryPage, ...] = Field(min_length=6, max_length=6)
    moral: Text
    parent_question: Text | None = None
    cultural_fact: Text | None = None
    family_activity: Text | None = None
    evidence_bundle_id: Identifier | None = None
    character_manifest_id: Identifier
    prompt_version: Identifier
    draft_hash: Digest | None = None

    @model_validator(mode='after')
    def valid_draft(self):
        if tuple(p.page_number for p in self.pages) != tuple(range(1, 7)):
            raise ValueError('Story pages must be ordered exactly 1 through 6.')
        if self.classification == 'original_inspired' and (not self.fiction_disclosure or not any(t in self.fiction_disclosure.lower() for t in ('fiction', 'imagined'))):
            raise ValueError('Original sacred-figure scenes require explicit fiction disclosure.')
        if self.classification != 'original_inspired' and (not self.evidence_bundle_id or not any(p.citations for p in self.pages)):
            raise ValueError('Retellings require evidence references; a label alone is insufficient.')
        return self


class ParentApproval(Contract):
    approval_id: Identifier
    authenticated_parent_id: Identifier
    approval_kind: Literal['narration_script', 'final_book']
    book_version_hash: Digest
    review_report_id: Identifier
    policy_version: Identifier
    approved_at: AwareDatetime


class BookVersion(Contract):
    seal_field = 'book_version_hash'
    book_id: Identifier
    version: int = Field(ge=1)
    draft_id: Identifier
    draft_hash: Digest
    evidence_bundle_id: Identifier | None = None
    evidence_bundle_hash: Digest | None = None
    character_manifest_id: Identifier
    character_manifest_hash: Digest
    asset_manifest_id: Identifier
    asset_manifest_hash: Digest
    voice_settings_hash: Digest | None = None
    audio_manifest_id: Identifier | None = None
    playbook_version: Identifier
    book_version_hash: Digest | None = None

    @model_validator(mode='after')
    def coherent_references(self):
        if bool(self.evidence_bundle_id) != bool(self.evidence_bundle_hash):
            raise ValueError('Evidence ID and hash must be supplied together.')
        if self.audio_manifest_id and not self.voice_settings_hash:
            raise ValueError('Audio requires frozen voice settings.')
        return self


def validate_story_context(draft: StoryDraft, request: StoryRequest, manifest: CharacterManifest,
                           evidence: EvidenceBundle | None = None, assets: AssetManifest | None = None):
    """Structural checks only. Identity/action meaning and cultural support need review."""
    if draft.request_hash != content_hash(request) or draft.classification != request.story_type:
        raise ValueError('Draft does not match the confirmed request.')
    if draft.character_manifest_id != manifest.manifest_id:
        raise ValueError('Wrong character manifest.')
    allowed = {c.character_id for c in manifest.characters}
    declared = {c for page in draft.pages for c in page.character_ids}
    if not declared <= allowed or not set(request.required_character_ids) <= declared:
        raise ValueError('Missing required or unknown declared character.')
    if 'parent_question' in request.required_elements and not draft.parent_question:
        raise ValueError('Requested parent discussion question is missing.')
    if 'cultural_fact' in request.required_elements and not draft.cultural_fact:
        raise ValueError('Requested cultural fact is missing.')
    if 'family_activity' in request.required_elements and not draft.family_activity:
        raise ValueError('Requested family activity is missing.')
    passages = {}
    if evidence is not None:
        if draft.evidence_bundle_id != evidence.bundle_id or evidence.request_hash != draft.request_hash:
            raise ValueError('Wrong evidence bundle or request binding.')
        passages = {p.reference.chunk_id: p.text for p in evidence.passages}
    elif draft.evidence_bundle_id:
        raise ValueError('Referenced evidence was not supplied for verification.')
    for page in draft.pages:
        for citation in page.citations:
            if citation.chunk_id not in passages or citation.source_quote not in passages[citation.chunk_id]:
                raise ValueError('Source quote/reference does not resolve against frozen evidence.')
    selected = {page.page_number: page for page in assets.pages} if assets is not None else {}
    for page in draft.pages:
        if page.selected_asset_id:
            match = selected.get(page.page_number)
            if not match or match.asset.asset_id != page.selected_asset_id or not set(page.character_ids) <= set(match.asset.character_ids):
                raise ValueError('Page asset does not match declared characters or asset reference.')
    return draft


def validate_approval_binding(approval: ParentApproval, book: BookVersion, *, parent_id: str,
                              policy_version: str, approval_kind='final_book'):
    if (approval.authenticated_parent_id != parent_id or approval.policy_version != policy_version
            or approval.approval_kind != approval_kind or approval.book_version_hash != book.book_version_hash):
        raise ValueError('Approval belongs to a different owner, policy, type or book version.')
    # Does not grant authorization or QA: caller still loads trusted records and checks them.
    return approval
