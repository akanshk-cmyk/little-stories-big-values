"""Stage 2 contracts; importing this package performs no I/O or provider calls."""
from .common import Contract, canonical_json, content_hash, parse_payload
from .requests import StoryRequest, PlaybookCapabilities, validate_capabilities
from .evidence import SourceReference, SearchHit, RankedPassage, EvidenceBundle
from .assets import CharacterDesign, CharacterManifest, StaticAssetReference, PageAsset, AssetManifest
from .stories import ClaimCitation, StoryPage, StoryDraft, ParentApproval, BookVersion, validate_story_context, validate_approval_binding
from .reviews import ReviewCheck, ReviewReport, EvidencePointer, overall_verdict, validate_review_evidence
from .operations import ProviderAttempt, WorkflowError, RunStatus, validate_transition
from .billing import SimulatedWeeklyPass

CONTRACT_VERSION = 'storybook-contracts-v2-stage2'
PUBLIC_MODELS = (StoryRequest, PlaybookCapabilities, SourceReference, SearchHit, RankedPassage,
                 EvidenceBundle, CharacterDesign, CharacterManifest, StaticAssetReference, PageAsset,
                 AssetManifest, ClaimCitation, StoryPage, StoryDraft, ReviewCheck, ReviewReport,
                 ProviderAttempt, WorkflowError, ParentApproval, BookVersion, SimulatedWeeklyPass)
