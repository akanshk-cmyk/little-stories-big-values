"""Authenticated account subroutes. Identity is supplied by server sessions only."""
from typing import Literal
from backend.contracts.common import Contract, Identifier, Digest, parse_payload
from .hosted_review import PassageQualification, ProcessingPermission


class QueueRequest(Contract):
    request_key: Identifier


class OwnerSource(Contract):
    owner_id: Identifier
    source_id: Identifier


class GrantCommand(Contract):
    permission: ProcessingPermission


class QualificationCommand(Contract):
    owner_id: Identifier
    qualification: PassageQualification


class PublicationCommand(Contract):
    owner_id: Identifier
    extraction_id: Digest
    purpose: Literal['traditional_claims', 'parent_inspiration']
    request_key: Identifier


class OwnedRecord(Contract):
    owner_id: Identifier
    record_id: Digest


def handle(service, method, tail, actor, data=None):
    owner = actor['account_id']
    service._require(owner)
    if method == 'GET':
        if tail == ['catalog']:
            catalog = getattr(service, 'catalog', None)
            if catalog is None:
                raise LookupError('The reviewed-source catalog is not connected.')
            return dict(sources=catalog.list(owner)), 200
        if tail == ['readiness']:
            readiness = getattr(service, 'readiness', None)
            if readiness is None:
                raise LookupError('Application composition is not connected.')
            return readiness(), 200
        if tail == []:
            return dict(sources=service.workspace.list(owner)), 200
        if len(tail) == 1:
            return service.source(owner, tail[0]), 200
        if len(tail) == 2 and tail[0] == 'jobs':
            return service.status(owner, tail[1]), 200
    if method == 'POST':
        if len(tail) == 2 and tail[1] == 'parse':
            command = parse_payload(QueueRequest, data)
            return service.start_parse(owner, tail[0], command.request_key), 202
        if len(tail) == 3 and tail[0] == 'jobs' and tail[2] == 'resume':
            if data != {}:
                raise ValueError('Job resume accepts no overrides.')
            return service.resume(owner, tail[1]), 202
        if len(tail) == 2 and tail[0] == 'admin':
            service.accounts.require(owner, 'admin')
            action = tail[1]
            if action == 'source':
                command = parse_payload(OwnerSource, data)
                return dict(source=service.source(command.owner_id, command.source_id),
                            parse_request=service.parse_request(command.owner_id, command.source_id).model_dump(mode='json')), 200
            if action == 'grant-processing':
                command = parse_payload(GrantCommand, data)
                key = service.reviews.grant_processing(owner, command.permission)
                return dict(permission_id=key), 201
            if action == 'qualify':
                command = parse_payload(QualificationCommand, data)
                key = service.reviews.qualify(owner, command.owner_id, command.qualification)
                return dict(qualification_id=key), 201
            if action == 'extraction':
                command = parse_payload(OwnedRecord, data)
                _, _, document = service.reviews._extraction(command.owner_id, command.record_id)
                service.reviews._manager(owner, 'inspect_extraction', command.owner_id, document.source_id)
                return document.model_dump(mode='json'), 200
            if action == 'publish':
                command = parse_payload(PublicationCommand, data)
                return service.start_publication(command.owner_id, command.extraction_id,
                                                  command.purpose, command.request_key), 202
            if action == 'activate':
                command = parse_payload(OwnedRecord, data)
                return service.activate(owner, command.owner_id, command.record_id), 200
            if action == 'job-status':
                command = parse_payload(OwnedRecord, data)
                return service.status(command.owner_id, command.record_id), 200
            if action == 'withdraw-processing':
                command = parse_payload(OwnedRecord, data)
                service.reviews.withdraw_processing(owner, command.owner_id, command.record_id)
                return dict(withdrawn=True), 200
            if action == 'withdraw-passage':
                command = parse_payload(OwnedRecord, data)
                service.reviews.withdraw_passage(owner, command.owner_id, command.record_id)
                return dict(withdrawn=True), 200
    raise LookupError('Hosted source endpoint not found.')
