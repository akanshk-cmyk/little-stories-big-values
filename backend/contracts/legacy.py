"""Version-1 compatibility boundaries. Do not migrate hashes or saved JSON in place.

Legacy requests retain English/Hindi and current field names. New StoryRequest is
English-only. A persisted full book is projected to its four story-content fields
only for the existing content validator; its metadata stays untouched by callers.
"""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from .common import Digest, Identifier, Text
from .reviews import DIMENSIONS, EvidencePointer, ReviewCheck, ReviewReport


class LegacyContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class LegacyOriginalRequest(LegacyContract):
    mode: Literal['original']
    character: Literal['krishna', 'ganesha', 'hanuman']
    objective: Literal['Kindness', 'Sharing', 'Patience', 'Honesty']
    age: int = Field(ge=3, le=8)
    language: Literal['English', 'Hindi']
    duration: int
    idea: str = Field(default='', max_length=1200)
    mood: str = Field(default='', max_length=1200)
    exclusions: str = Field(default='', max_length=1200)
    theme: Literal['mythology'] = 'mythology'
    include_activity: bool = False

    @model_validator(mode='after')
    def legacy_rules(self):
        if self.duration not in (3, 5, 7, 10) or self.include_activity:
            raise ValueError('Unsupported original-story duration or activity option.')
        return self


class LegacyFestivalRequest(LegacyContract):
    festival: str  # Existing engine checks membership in its actual catalog.
    age: int = Field(ge=5, le=8)
    language: Literal['English', 'Hindi']
    objective: str  # Existing engine checks membership in its actual values.
    duration: int
    region: str = Field(default='Family traditions vary', max_length=120)
    include_activity: bool = False

    @model_validator(mode='after')
    def legacy_duration(self):
        if self.duration not in (3, 5, 7, 10):
            raise ValueError('Unsupported duration.')
        return self


class LegacyStoryContent(LegacyContract):
    title: Text = Field(max_length=6000)
    pages: list[Text] = Field(min_length=6, max_length=6)
    moral: Text = Field(max_length=6000)
    discussionQuestion: Text = Field(max_length=6000)

    @model_validator(mode='after')
    def page_limits(self):
        if any(len(page) > 6000 for page in self.pages):
            raise ValueError('Story page exceeds its limit.')
        return self


class LegacySourceCitation(LegacyContract):
    page: int = Field(ge=0, le=6)
    excerptId: Identifier
    claim: Text
    sourceQuote: Text


class LegacySourceReviewRequest(LegacyContract):
    action: Literal['approve', 'revoke']
    textHash: Digest
    textReviewed: bool = False
    cultureReviewed: bool = False


class LegacyGenerationEnvelope(LegacyContract):
    mode: Literal['generate', 'example']
    request: dict[str, Any]  # Validated by the matching legacy request contract.
    sourceIds: list[Identifier] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def distinct_sources(self):
        if len(self.sourceIds) != len(set(self.sourceIds)):
            raise ValueError('Source IDs must be distinct.')
        return self


def project_story_content(book):
    # Deliberate v1 projection: full books include metadata not part of the model's
    # four-field response. This does not strip or rewrite the caller's saved book.
    return LegacyStoryContent.model_validate({k: book.get(k) for k in ('title', 'pages', 'moral', 'discussionQuestion')}).model_dump()


def legacy_review_to_contract(report, *, draft_hash, reviewer_version, evidence_bundle_hash=None):
    """Read-only conversion. Never infer a missing critical safety result as PASS."""
    if not isinstance(report, dict):
        raise ValueError('Expected a legacy review object.')
    def convert(rows, key, expected):
        matching = [r for r in rows if isinstance(r, dict) and r.get(key) == expected] if isinstance(rows, list) else []
        if len(matching) != 1:
            return ReviewCheck(status='PENDING', reason='Missing or duplicated legacy check: '+expected)
        row = matching[0]
        status = 'PENDING' if row.get('status') == 'UNRESOLVED' else row.get('status')
        if status not in ('PASS', 'FAIL', 'PENDING'):
            status = 'PENDING'
        refs = []
        try:
            for ref in row.get('evidence', []):
                refs.append(EvidencePointer.model_validate(ref))
        except (ValidationError, TypeError):
            return ReviewCheck(status='PENDING', reason='Legacy evidence references are invalid: '+expected)
        if status == 'PASS' and not refs:
            status = 'PENDING'
        return ReviewCheck(status=status, reason=row.get('note') or 'Legacy check requires review.', evidence=tuple(refs))
    names = ('factual_correctness', 'clarity', 'value_insight', 'engagement', 'tone_fit')
    checks = {field: convert(report.get('dimensions'), 'name', name) for field, name in zip(names, DIMENSIONS)}
    checks['critical_safety'] = convert(report.get('rules'), 'rule_id', 'critical_safety')
    notes = report.get('validationNotes', [])
    issues = (tuple(str(n) for n in notes if str(n).strip()) if isinstance(notes,(tuple,list))
              else ('Legacy validation notes are malformed.',))
    if report.get('verdict') != 'PASS':
        issues += ('Legacy report did not establish a release PASS.',)
    return ReviewReport(draft_hash=draft_hash, reviewer_version=reviewer_version,
                        evidence_bundle_hash=evidence_bundle_hash, validation_issues=issues, **checks)
