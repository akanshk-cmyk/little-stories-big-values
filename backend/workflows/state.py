"""JSON-only checkpoints; Pydantic contracts are validated at every boundary."""
from typing import Annotated, TypedDict
from backend.contracts import ProviderAttempt, WorkflowError
from backend.contracts.common import parse_payload


def merge_errors(left, right):
    values = {item['error_id']: item for item in left}
    for raw in right:
        item = parse_payload(WorkflowError, raw).model_dump(mode='json')
        key = item['error_id']
        if key in values and values[key] != item:
            raise ValueError('Conflicting error records.')
        values[key] = item
    return [values[key] for key in sorted(values)]


def merge_attempts(left, right):
    values = {item['attempt_id']: item for item in left}
    for raw in right:
        item = parse_payload(ProviderAttempt, raw).model_dump(mode='json')
        old = values.get(item['attempt_id'])
        if old:
            stable = ('operation_id', 'requested_mode', 'role', 'provider', 'model_id')
            if any(old[k] != item[k] for k in stable):
                raise ValueError('Attempt identity cannot change.')
            if old['record_version'] == item['record_version'] and old != item:
                raise ValueError('Conflicting attempt versions.')
            if item['record_version'] < old['record_version']:
                continue
            if old['status'] != 'started' and item != old:
                raise ValueError('Terminal attempt cannot change.')
        values[item['attempt_id']] = item
    return [values[key] for key in sorted(values)]


class StoryState(TypedDict):
    state_version: str
    run_id: str
    thread_id: str
    request: dict
    request_hash: str
    playbook_id: str
    playbook_version: str
    source_snapshot_ids: list[str]
    source_eligibility_status: str
    index_version: str | None
    dense_hits: list[dict]
    sparse_hits: list[dict]
    fused_hits: list[dict]
    ranked_hits: list[dict]
    evidence_bundle_id: str | None
    draft_id: str | None
    draft_hash: str | None
    character_manifest_id: str | None
    asset_manifest_id: str | None
    review: dict | None
    reviewer_version: str
    correction_count: int
    provider_attempts: Annotated[list[dict], merge_attempts]
    approval_id: str | None
    book_version_hash: str | None
    generation_mode: str
    provider_mode: str
    status: str
    errors: Annotated[list[dict], merge_errors]
    creation_options: dict
    complete_preview_hash: str | None
    incomplete_reasons: list[str]
    revision_epoch: str
    revision_notes: str
    parent_revision_count: int

    context_id: str
    episode_id: str
    episode_index: int
    episode_count: int
    selection_hash: str
