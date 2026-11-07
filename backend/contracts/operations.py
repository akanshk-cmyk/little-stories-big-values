from typing import Literal
from .common import Contract, Identifier, NonNegative, Positive, Text
from .requests import ProviderMode


class ProviderAttempt(Contract):
    attempt_id: Identifier
    record_version: Positive
    operation_id: Identifier
    requested_mode: ProviderMode
    role: Literal['writer', 'reviewer', 'correction', 'source_reviewer']
    provider: Literal['groq', 'nebius']
    model_id: Text
    status: Literal['started', 'succeeded', 'failed', 'outcome_unknown']
    latency_ms: NonNegative | None = None
    provider_request_id: Text | None = None
    input_tokens: NonNegative | None = None
    output_tokens: NonNegative | None = None
    fallback_reason: Text | None = None
    error_code: Identifier | None = None


class WorkflowError(Contract):
    error_id: Identifier
    node: Identifier
    code: Identifier
    parent_message: Text
    retryable: bool
    trace_reference: Identifier | None = None


RunStatus = Literal['queued', 'validating', 'awaiting_clarification', 'retrieving', 'evidence_blocked',
                    'writing', 'reviewing', 'correcting', 'assets_pending', 'awaiting_script_approval',
                    'preparing_audio', 'awaiting_parent_approval', 'approved', 'rendering', 'complete',
                    'cancelled', 'failed', 'outcome_unknown']

# Pure validation table only: no graph, worker, checkpoint or routing execution.
TRANSITIONS = {
    'queued': {'validating'},
    'validating': {'awaiting_clarification', 'retrieving', 'writing', 'evidence_blocked'},
    'awaiting_clarification': {'validating'},
    'retrieving': {'writing', 'evidence_blocked'},
    'evidence_blocked': {'validating'},
    'writing': {'reviewing', 'evidence_blocked'},
    'reviewing': {'validating', 'correcting', 'assets_pending', 'awaiting_script_approval', 'preparing_audio', 'awaiting_parent_approval', 'evidence_blocked'},
    'correcting': {'reviewing', 'evidence_blocked'},
    'assets_pending': {'reviewing', 'awaiting_script_approval', 'awaiting_parent_approval'},
    'awaiting_script_approval': {'preparing_audio', 'writing'},
    'preparing_audio': {'assets_pending', 'awaiting_parent_approval'},
    'awaiting_parent_approval': {'writing', 'reviewing', 'approved'},
    'approved': {'rendering'},
    'rendering': {'complete'},
    'outcome_unknown': {'validating', 'writing', 'reviewing', 'preparing_audio', 'rendering'},
    'complete': set(), 'cancelled': set(), 'failed': set(),
}


def validate_transition(current: str, target: str, *, allow_output_revision=False):
    if current not in TRANSITIONS or target not in TRANSITIONS:
        raise ValueError('Unknown workflow status.')
    if current=='complete' and target=='reviewing' and allow_output_revision is True:
        # Only a trusted managed-service method supplies this after validating
        # exact text/review context. Default/legacy terminal states stay terminal.
        return target
    allowed = set(TRANSITIONS[current])
    if current not in ('complete', 'cancelled', 'failed'):
        allowed |= {'cancelled', 'failed', 'outcome_unknown'}
    if target == current or target not in allowed:
        raise ValueError('Invalid workflow transition.')
    return target
