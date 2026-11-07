"""Optional server dependency; the current app does not install this service.

Import this module only when a developer has injected an enabled replay service
on the HTTP server. It never constructs adapters or enables production runs.
"""
from typing import Literal
from backend.contracts.common import Contract, Digest, Identifier, parse_payload
from backend.contracts.requests import StoryRequest


class StartRun(Contract):
    request_key: Identifier
    request: StoryRequest
    workflow: Literal['stage3_replay']


class ApproveRun(Contract):
    book_version_hash: Digest


def handle(service, method, parts, owner, data=None):
    """Return (payload,status). Authentication and CSRF stay in the HTTP handler."""
    if getattr(service.adapters,'supported_modes',('replay',)) != ('replay',):
        raise PermissionError('This endpoint accepts replay workflows only.')
    if method == 'GET' and len(parts) == 3:
        result = service.status(parts[2],owner)
        return dict(result,provenance='offline_replay'),200
    if method == 'POST' and len(parts) == 2:
        command = parse_payload(StartRun,data)
        run_id = service.start(owner,command.request_key,command.request.model_dump(mode='json'))
        service.submit(run_id,owner)
        return {'run_id':run_id,'status_url':'/api/workflow-runs/'+run_id,'provenance':'offline_replay'},202
    if method == 'POST' and len(parts) == 4 and parts[3] == 'approve':
        command = parse_payload(ApproveRun,data)
        result = service.approve(parts[2],owner,command.book_version_hash)
        return {'values':result,'provenance':'offline_replay'},200
    if method == 'POST' and len(parts) == 4 and parts[3] == 'resume':
        if data != {}:
            raise ValueError('Resume accepts no approval or state overrides.')
        service.submit(parts[2],owner)
        return {'run_id':parts[2],'provenance':'offline_replay'},202
    raise LookupError('Workflow endpoint not found.')
