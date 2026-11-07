"""Bounded HTTP transport and shared envelope normalization for both services."""
from dataclasses import dataclass
import json
import ssl
import re
import time
import httpx
from backend.contracts.common import Identifier
from backend.contracts.provider import json_object
from pydantic import TypeAdapter
from .config import ENDPOINTS
from .retry_policy import ProviderFailure,retry_after

MAX_RESPONSE_BYTES=1024*1024


def request_id(value):
    try:return TypeAdapter(Identifier).validate_python(value,strict=True)
    except ValueError:return None


def token_count(value):return value if type(value) is int and value>=0 else None


@dataclass(frozen=True)
class CandidateResponse:
    content: str
    reported_model_id: str | None
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None


class ChatAdapter:
    def __init__(self,settings,*,client=None,clock=time.monotonic):
        self.settings=settings;self.client=client;self.owns_client=False;self.clock=clock

    def close(self):
        if self.owns_client:self.client.close()

    def generate(self,request,schema,deadline):
        model=self.settings.model(request.role)
        payload=dict(model=model,messages=[{'role':'system','content':request.system_prompt+
            '\nReturn one JSON object only, matching this response contract: '+json.dumps(schema,separators=(',',':'))},
            {'role':'user','content':request.input_json}],response_format={'type':'json_object'},
            temperature=request.temperature,stream=False)
        if model.startswith('openai/gpt-oss-'):payload['reasoning_effort']='low'
        payload['max_completion_tokens' if self.settings.provider=='groq' else 'max_tokens']=request.max_tokens
        remaining=deadline-self.clock()
        if remaining<=0:raise ProviderFailure('deadline_exceeded')
        timeout=httpx.Timeout(min(self.settings.read_seconds,remaining),connect=min(self.settings.connect_seconds,remaining))
        if self.client is None:
            self.client=httpx.Client(follow_redirects=False,trust_env=False);self.owns_client=True
        try:
            with self.client.stream('POST',ENDPOINTS[self.settings.provider],json=payload,
                headers={'Authorization':'Bearer '+self.settings.api_key},timeout=timeout) as response:
                raw=bytearray()
                for block in response.iter_bytes(chunk_size=65536):
                    raw.extend(block)
                    if len(raw)>MAX_RESPONSE_BYTES:raise ProviderFailure('response_too_large')
                    if self.clock()>deadline and response.status_code==200:raise ProviderFailure('outcome_unknown',unknown=True)
                try:body=json_object(bytes(raw).decode('utf-8'))
                except (UnicodeError,ValueError):body=None
                provider_id=request_id(body.get('id')) if body else None
                provider_id=provider_id or request_id(response.headers.get('x-request-id'))
                usage=body.get('usage') if body else None
                usage=usage if isinstance(usage,dict) else {}
                details=dict(provider_request_id=provider_id,input_tokens=token_count(usage.get('prompt_tokens')),
                             output_tokens=token_count(usage.get('completion_tokens')))
                error=body.get('error') if body else None
                error=error if isinstance(error,dict) else {}
                if error.get('code') in ('content_policy_violation','content_filter','safety_refusal','responsible_ai_policy_violation'):
                    raise ProviderFailure('safety_refusal',refusal=True,**details)
                status=response.status_code
                if status in (401,403,404):raise ProviderFailure('provider_configuration',**details)
                if status in (408,504):raise ProviderFailure('outcome_unknown',unknown=True,**details)
                if status==429:
                    wait=retry_after(response.headers.get('Retry-After'))
                    match=re.search(r'Please try again in ([0-9]+(?:\.[0-9]+)?)s',str(error.get('message','')))
                    if match:wait=max(wait or 0,float(match.group(1))+.25)
                    failure=ProviderFailure('rate_limited',retryable=True,retry_after=wait,**details)
                    failure.provider_message=str(error.get('message','Rate limit reached'))[:1600]
                    raise failure
                if status in (500,502,503) or (status==498 and self.settings.provider=='groq'):
                    raise ProviderFailure('provider_unavailable',retryable=True,**details)
                if status!=200:
                    failure=ProviderFailure('provider_request_rejected',**details)
                    failure.provider_message=str(error.get('message','Request rejected'))[:1600]
                    raise failure
                if body is None:raise ProviderFailure('invalid_response',**details)
                reported=body.get('model')
                if reported is not None and (not isinstance(reported,str) or reported!=model):
                    raise ProviderFailure('model_identity_mismatch',**details)
                try:
                    choice=body['choices'][0];message=choice['message'];finish=choice['finish_reason']
                    if message.get('refusal') or finish=='content_filter':raise ProviderFailure('safety_refusal',refusal=True,**details)
                    if finish!='stop':raise ProviderFailure('incomplete_response',**details)
                    content=message['content']
                    if not isinstance(content,str):raise ValueError()
                    try:inner=json_object(content)
                    except ValueError:inner={}
                    if inner.get('refusal'):raise ProviderFailure('safety_refusal',refusal=True,**details)
                except (KeyError,IndexError,TypeError,ValueError):raise ProviderFailure('invalid_response',**details) from None
                return CandidateResponse(content,reported,provider_id,details['input_tokens'],details['output_tokens'])
        except (httpx.ConnectTimeout,httpx.PoolTimeout):raise ProviderFailure('connect_timeout',retryable=True) from None
        except httpx.ConnectError as error:
            cause=error
            while cause is not None:
                if isinstance(cause,ssl.SSLCertVerificationError):raise ProviderFailure('tls_configuration') from None
                cause=cause.__cause__
            raise ProviderFailure('connection_unavailable',retryable=True) from None
        except (httpx.ReadTimeout,httpx.WriteTimeout,httpx.ReadError,httpx.WriteError,httpx.RemoteProtocolError):
            raise ProviderFailure('outcome_unknown',unknown=True) from None
        except httpx.HTTPError:raise ProviderFailure('transport_configuration') from None
