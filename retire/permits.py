from dataclasses import dataclass
import hmac
import json

from archive_store import store_identity
from custody.catalogue import catalogue_write
from custody.eligibility import eligible_domains, has_dual_domain_custody
from custody.registry import (
    list_retire_exclusions, list_verified_copies, record_retire_exclusion, registered_key,
)
from media.withdrawal import ExitResult
from retire.requests import RetireRequest, load_request, record_permit, record_rejection, request_fields


@dataclass(frozen=True)
class RetirePermit:
    catalogue_id: str
    request: RetireRequest
    signature: str


@dataclass(frozen=True)
class RetirementDecision:
    request: RetireRequest
    status: str
    permit: RetirePermit | None = None
    terminal: ExitResult | None = None
    reason: str | None = None


def _permit_signature(key, catalogue_id, request):
    payload = ['archive-retire-v1', catalogue_id, *request_fields(request)]
    return hmac.digest(key, json.dumps(payload, separators=(',', ':')).encode('ascii'), 'sha256').hex()


def validate_permit(permit, settings):
    if not isinstance(permit, RetirePermit):
        raise ValueError('a signed retirement permit is required')
    request_fields(permit.request)
    if permit.catalogue_id != settings.catalogue_id or permit.request.copy.volume != settings.identity:
        raise ValueError('permit is for a different catalogue or volume')
    original = _permit_signature(settings.permit_key, permit.catalogue_id, permit.request)
    if not isinstance(permit.signature, str) or not permit.signature.isascii():
        raise ValueError('invalid retirement permit signature')
    if not hmac.compare_digest(original, permit.signature):
        raise ValueError('retirement permit authentication failed')


def preview_retirement(catalogue, request):
    request_fields(request)
    return eligible_domains(catalogue, request.batch_id, without=request.copy)


def _decision(catalogue_id, record):
    request = record.request
    if record.status == 'rejected':
        return RetirementDecision(request, record.status, reason=record.reason)
    permit = RetirePermit(catalogue_id, request, record.permit_signature)
    if record.status == 'settled':
        terminal = ExitResult(catalogue_id, request.request_id, request.copy, record.permit_signature,
                              record.terminal_signature)
        return RetirementDecision(request, record.status, permit=permit, terminal=terminal)
    return RetirementDecision(request, record.status, permit=permit)


def permit(catalogue, request, *, after_precheck=None):
    request_fields(request)
    if after_precheck is not None:
        after_precheck(preview_retirement(catalogue, request))
    with catalogue_write(catalogue):
        catalogue_id = store_identity(catalogue, 'catalogue')
        old = load_request(catalogue, request.request_id)
        if old is not None:
            if old.request != request:
                raise ValueError('retirement request identity already binds a different target')
            return _decision(catalogue_id, old)
        copy = request.copy
        if copy not in list_verified_copies(catalogue, request.batch_id):
            raise ValueError('retirement target must be a verified copy')
        if copy in list_retire_exclusions(catalogue, request.batch_id):
            raise ValueError('copy already has a retirement permit')
        if not has_dual_domain_custody(catalogue, request.batch_id, without=copy):
            record_rejection(catalogue, request, 'insufficient_domains')
        else:
            key = registered_key(catalogue, copy.volume)
            signature = _permit_signature(key, catalogue_id, request)
            record_permit(catalogue, request, signature)
            record_retire_exclusion(catalogue, copy, request.request_id)
        return _decision(catalogue_id, load_request(catalogue, request.request_id))
