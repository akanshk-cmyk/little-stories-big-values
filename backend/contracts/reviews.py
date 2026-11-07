from typing import Literal
from pydantic import Field, model_validator, ConfigDict
from .common import Contract, Digest, Identifier, Text

Verdict = Literal['PASS', 'FAIL', 'PENDING']
DIMENSIONS = ('Factual Correctness', 'Clarity', 'Value / Insight', 'Engagement', 'Tone Fit')


def overall_verdict(statuses):
    statuses = tuple(statuses)
    if 'FAIL' in statuses:
        return 'FAIL'
    return 'PASS' if len(statuses) == 6 and all(s == 'PASS' for s in statuses) else 'PENDING'


class EvidencePointer(Contract):
    artifact_id: Identifier
    pointer: str = Field(pattern=r'^(?:/.*)?$')
    quote: Text


class ReviewCheck(Contract):
    model_config=ConfigDict(json_schema_extra={'allOf':[{'if':{'properties':{'status':{'const':'PASS'}},'required':['status']},'then':{'properties':{'evidence':{'minItems':1}},'required':['evidence']}}]})
    status: Verdict
    reason: Text
    evidence: tuple[EvidencePointer, ...] = ()
    requested_correction: Text | None = None

    @model_validator(mode='after')
    def pass_needs_references(self):
        if self.status == 'PASS' and not self.evidence:
            raise ValueError('PASS requires evidence references; references must also be resolved by the caller.')
        return self


class ReviewReport(Contract):
    draft_hash: Digest
    evidence_bundle_hash: Digest | None = None
    reviewer_version: Identifier
    factual_correctness: ReviewCheck
    clarity: ReviewCheck
    value_insight: ReviewCheck
    engagement: ReviewCheck
    tone_fit: ReviewCheck
    critical_safety: ReviewCheck
    validation_issues: tuple[Text, ...] = ()

    @property
    def checks(self):
        return (self.factual_correctness, self.clarity, self.value_insight,
                self.engagement, self.tone_fit, self.critical_safety)

    @property
    def overall_result(self):
        result = overall_verdict(c.status for c in self.checks)
        return 'PENDING' if result == 'PASS' and self.validation_issues else result


def resolve_pointer(artifact, pointer):
    import re
    if pointer == '':
        return artifact
    if not pointer.startswith('/') or re.search(r'~(?![01])',pointer):
        raise ValueError('Invalid JSON pointer.')
    for token in pointer[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(artifact, list):
            if not token.isdigit():
                raise ValueError('Invalid array pointer.')
            artifact = artifact[int(token)]
        elif isinstance(artifact, dict):
            artifact = artifact[token]
        else:
            raise ValueError('Pointer does not resolve.')
    return artifact


def validate_review_evidence(report: ReviewReport, artifacts):
    import json
    for check in report.checks:
        for ref in check.evidence:
            try:
                value = resolve_pointer(artifacts[ref.artifact_id], ref.pointer)
            except (KeyError, IndexError, ValueError, TypeError) as error:
                raise ValueError('Review evidence pointer does not resolve.') from error
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            if ref.quote not in text:
                raise ValueError('Review quote is not present in the referenced artifact.')
    return report  # Structural authenticity only; no semantic truth/authorization implied.
