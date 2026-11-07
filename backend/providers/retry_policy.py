"""Trusted error categories. Model text never chooses retries or fallback."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import math

AVAILABILITY_CODES=frozenset(('rate_limited','provider_unavailable','connect_timeout','connection_unavailable'))
ERROR_CODES=AVAILABILITY_CODES|frozenset((
    'provider_router_disabled','credential_in_request','credential_in_response','operation_conflict',
    'cached_result_integrity','outcome_unknown','operation_stopped','provider_configuration',
    'output_validation','preconditions_failed','deadline_exceeded','retry_budget_exhausted',
    'response_too_large','safety_refusal','provider_request_rejected','model_identity_mismatch',
    'incomplete_response','invalid_response','tls_configuration','transport_configuration',
    'evidence_missing','evidence_withdrawn','evidence_sufficiency_pending','unsupported_request',
    'unauthorized_request','safety_blocked','provider_operation_failed',
    'generation_hold_expired','generation_limit_reached'))


class ProviderFailure(RuntimeError):
    def __init__(self,code,*,retryable=False,unknown=False,refusal=False,retry_after=None,
                 provider_request_id=None,input_tokens=None,output_tokens=None):
        code=code if isinstance(code,str) and code in ERROR_CODES else 'provider_operation_failed'
        super().__init__(code)
        self.code=code;self.unknown=unknown or code=='outcome_unknown';self.refusal=refusal or code=='safety_refusal'
        self.retryable=bool(retryable and code in AVAILABILITY_CODES and not self.unknown and not self.refusal)
        self.retry_after=retry_after;self.provider_request_id=provider_request_id
        self.input_tokens=input_tokens;self.output_tokens=output_tokens


def retry_after(value,*,now=None):
    if not isinstance(value,str) or len(value)>128:return None
    try:
        seconds=float(value)
    except ValueError:
        try:
            date=parsedate_to_datetime(value)
            if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
            seconds=(date-(now or datetime.now(timezone.utc))).total_seconds()
        except (ValueError,TypeError,OverflowError):return None
    return max(0.,seconds) if math.isfinite(seconds) else None


def route_plan(mode):
    return ('groq','groq','nebius') if mode=='auto' else (mode,mode)
