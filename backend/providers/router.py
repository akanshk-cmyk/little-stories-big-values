"""Opt-in bounded routing. Trusted guards are mandatory, including on replay."""
from dataclasses import replace
from types import MappingProxyType
import time
from pydantic import TypeAdapter, ValidationError
from backend.contracts import StoryDraft,ReviewReport,parse_payload
from backend.contracts.common import Identifier,canonical_json,content_hash
from backend.contracts.provider import ModelRequest,ProviderResponse,json_object
from backend.workflows.checkpoints import run_lock
from .groq import GroqAdapter
from .nebius import NebiusAdapter
from .retry_policy import ProviderFailure,route_plan

from backend.contracts.source_qa import SourceQAReport
from backend.contracts.compact_review import CompactReview
from backend.contracts.compact_story import CompactStory
SCHEMAS=MappingProxyType(dict(story_draft=StoryDraft,review_report=ReviewReport,source_qa_report=SourceQAReport,compact_review_report=CompactReview,compact_story_draft=CompactStory))


class ProviderRouter:
    def __init__(self,settings,ledger,*,enabled=False,clients=None,clock=time.monotonic,sleep=time.sleep,default_mode='auto',validation_diagnostics=None,max_retry_delay=5):
        if set(settings)!={'groq','nebius'}:raise ValueError('Supply explicit settings for both provider names.')
        if default_mode not in ('auto','groq','nebius'):raise ValueError('Invalid default mode.')
        if type(max_retry_delay) not in (int,float) or not 0<=max_retry_delay<=65:raise ValueError('Invalid retry wait bound.')
        self.max_retry_delay=max_retry_delay
        self.validation_diagnostics=validation_diagnostics
        self.settings=MappingProxyType(dict(settings));self.ledger=ledger;self.enabled=enabled
        self.clock=clock;self.sleep=sleep;self.default_mode=default_mode
        clients=clients or {}
        self.adapters={name:kind(settings[name],client=clients.get(name),clock=clock)
            for name,kind in (('groq',GroqAdapter),('nebius',NebiusAdapter))}

    def close(self):
        for adapter in self.adapters.values():adapter.close()

    def new_request(self,**fields):return ModelRequest(**({'mode':self.default_mode}|fields))

    def _contains_secret(self,text):
        return any(len(s.api_key)>=8 and s.api_key in text for s in self.settings.values())

    def _validate(self,request,candidate,validator):
        if self._contains_secret(candidate.content):raise ProviderFailure('credential_in_response')
        try:
            output=parse_payload(SCHEMAS[request.output_schema],json_object(candidate.content))
            if request.role=='source_reviewer':
                if output.subject_hash!=request.source_subject_hash or output.reviewer_version!=request.reviewer_version:raise ValueError('Source reviewer artifact mismatch.')
            elif request.role=='reviewer':
                if output.draft_hash!=request.draft_hash or output.evidence_bundle_hash!=request.evidence_bundle_hash or output.reviewer_version!=request.reviewer_version:
                    raise ValueError('Reviewer artifact/version mismatch.')
            elif request.output_schema!='compact_story_draft' and (output.request_hash!=request.request_hash or output.evidence_bundle_id!=request.evidence_bundle_id):
                raise ValueError('Writer evidence/request mismatch.')
            if validator(request,output) is not True:raise ValueError('Context validation did not pass.')
        except ProviderFailure:raise
        except Exception as error:
            if self.validation_diagnostics:
                try:self.validation_diagnostics(request,candidate.content,error)
                except Exception:pass  # Logging failure must not turn validation rejection into an unknown paid outcome.
            failure=ProviderFailure('output_validation')
            if request.output_schema in ('story_draft','compact_story_draft') and isinstance(error,ValidationError):
                failure.invalid_output=candidate.content
                failure.validation_notes=str(error)
            raise failure from None
        return output

    def generate(self,owner,request,*,guard,validate_output):
        if not self.enabled:raise ProviderFailure('provider_router_disabled')
        TypeAdapter(Identifier).validate_python(owner,strict=True)
        if not isinstance(request,ModelRequest):raise TypeError('A validated ModelRequest is required.')
        # No request identifiers, prompt text or response content containing a
        # configured credential can enter operation storage or network payloads.
        if self._contains_secret(request.model_dump_json()) or any(
            self._contains_secret(s.model(request.role)) for s in self.settings.values()):
            raise ProviderFailure('credential_in_request')
        plan=route_plan(request.mode)
        model=SCHEMAS[request.output_schema]
        fingerprint=content_hash(dict(request=request.model_dump(mode='json'),schema=model.model_json_schema(),
            models={name:self.settings[name].model(request.role) for name in set(plan)}))
        lock_key=content_hash([owner,request.operation_id])
        # Serialize one confirmed parent request across roles/modes too. A
        # concurrent manual switch cannot race ahead of a saved safety block.
        with run_lock(self.ledger.folder,'scope-'+content_hash([owner,request.request_hash])), run_lock(self.ledger.folder,lock_key):
            state,cached=self.ledger.begin(owner,request.operation_id,fingerprint)
            try:
                self.ledger.check_scope(owner,request.request_hash)
                self._guard(request,guard)
                if state=='interrupted':
                    self.ledger.interrupted(owner,request)
                    raise ProviderFailure('outcome_unknown',unknown=True)
                if state=='cached':
                    response=parse_payload(ProviderResponse,cached)
                    from .base import CandidateResponse
                    self._validate(request,CandidateResponse(response.output_json,response.reported_model_id,response.provider_request_id,
                        response.input_tokens,response.output_tokens),validate_output)
                    return response.model_copy(update={'cached':True})
                for name in set(plan):
                    if not self.settings[name].configured(request.role):raise ProviderFailure('provider_configuration')
                return self._execute(owner,request,guard,validate_output,plan)
            except ProviderFailure as error:
                if error.refusal or error.unknown:self.ledger.block_scope(owner,request.request_hash,'safety_refusal' if error.refusal else 'outcome_unknown')
                self.ledger.terminal(owner,request.operation_id,'outcome_unknown' if error.unknown else 'failed',error=error.code)
                raise

    @staticmethod
    def _guard(request,guard):
        try:
            if guard(request) is not True:raise ValueError()
        except ProviderFailure:raise
        except Exception:raise ProviderFailure('preconditions_failed') from None

    def _execute(self,owner,request,guard,validator,plan):
        deadline=self.clock()+request.deadline_seconds;position=0;number=0;previous=None
        while position<len(plan):
            provider=plan[position]
            if self.clock()>=deadline:raise ProviderFailure('deadline_exceeded')
            self._guard(request,guard)
            number+=1
            started=self.ledger.started(owner,request,provider,self.settings[provider].model(request.role),number,
                fallback_reason=previous.code if previous and provider=='nebius' and request.mode=='auto' else None)
            began=self.clock();candidate=None
            try:
                candidate=self.adapters[provider].generate(request,SCHEMAS[request.output_schema].model_json_schema(),deadline)
                if candidate.provider_request_id and self._contains_secret(candidate.provider_request_id):candidate=replace(candidate,provider_request_id=None)
                output=self._validate(request,candidate,validator)
                self._guard(request,guard)
            except ProviderFailure as error:
                if hasattr(error,'invalid_output'):error.invalid_provider=provider
                if self.validation_diagnostics and getattr(error,'provider_message',None) and not self._contains_secret(error.provider_message):
                    try:self.validation_diagnostics(request,canonical_json({'provider_message':error.provider_message}),ValueError('Provider rejected request before response validation.'))
                    except Exception:pass
                if error.provider_request_id and self._contains_secret(error.provider_request_id):error.provider_request_id=None
                self.ledger.finished(owner,request,started,status='outcome_unknown' if error.unknown else 'failed',
                    latency_ms=max(0,int((self.clock()-began)*1000)),error=error,candidate=candidate)
                if not error.retryable:raise
                previous=error;position+=1
                if position>=len(plan):raise
                delay=error.retry_after if error.retry_after is not None else .25
                same_provider=plan[position]==provider
                if same_provider:
                    if delay>self.max_retry_delay or self.clock()+delay>=deadline:
                        # Never retry Groq earlier than its Retry-After. A
                        # different provider has a separate availability budget.
                        if request.mode=='auto':position+=1
                        else:raise
                    else:
                        remaining=delay
                        while remaining>0:
                            step=min(remaining,30);self.sleep(step);remaining-=step
                continue
            except Exception:
                error=ProviderFailure('outcome_unknown',unknown=True)
                self.ledger.finished(owner,request,started,status='outcome_unknown',
                    latency_ms=max(0,int((self.clock()-began)*1000)),error=error,candidate=candidate)
                raise error from None
            latency=max(0,int((self.clock()-began)*1000))
            result=ProviderResponse(provider=provider,model_id=self.settings[provider].model(request.role),
                reported_model_id=candidate.reported_model_id,operation_id=request.operation_id,attempt_id=started['attempt']['attempt_id'],
                output_schema=request.output_schema,output_json=canonical_json(output),output_hash=content_hash(output),
                provider_request_id=candidate.provider_request_id,input_tokens=candidate.input_tokens,output_tokens=candidate.output_tokens,latency_ms=latency)
            self.ledger.finished(owner,request,started,status='succeeded',latency_ms=latency,candidate=candidate,
                                 response=result.model_dump(mode='json'))
            return result
        raise ProviderFailure('retry_budget_exhausted')
