"""Provider routing tests using synthetic content and HTTP transports only."""
import json
from pathlib import Path
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from backend import contracts as c
from backend.contracts.provider import ModelRequest,json_object
from backend.providers.config import ProviderSettings,from_environment,readiness
from backend.providers.attempt_ledger import AttemptLedger
from backend.providers.retry_policy import ProviderFailure,retry_after
from backend.providers.router import ProviderRouter
from test_contracts import request,draft,characters


SETTINGS={name:ProviderSettings(name,'SYNTHETIC-'+name.upper()+'-KEY-123456',name+'-writer-fixture',name+'-review-fixture')
          for name in ('groq','nebius')}


def model_request(**updates):
    values=dict(run_id='run1',thread_id='thread1',operation_id='operation1',request_hash=c.content_hash(request()),
        role='writer',prompt_version='writer-v1',reviewer_version='reviewer-v1',output_schema='story_draft',
        system_prompt='Write JSON for this synthetic contract check.',input_json=json.dumps({'fixture':'PRIVATE-CHILD-DETAIL'}))
    return ModelRequest(**(values|updates))


def reviewer_report():
    d=draft();ref=c.EvidencePointer(artifact_id=d.draft_id,pointer='/title',quote=d.title)
    check=c.ReviewCheck(status='PASS',reason='Synthetic fixture; not a content-quality measurement.',evidence=(ref,))
    return c.ReviewReport(draft_hash=d.draft_hash,reviewer_version='reviewer-v1',factual_correctness=check,clarity=check,
                         value_insight=check,engagement=check,tone_fit=check,critical_safety=check)


def context_validator(req,output):
    if req.role=='reviewer':c.validate_review_evidence(output,{draft().draft_id:draft().model_dump(mode='json')})
    else:c.validate_story_context(output,request(),characters())
    return True


class Clock:
    def __init__(self):self.now=0.;self.sleeps=[]
    def __call__(self):return self.now
    def sleep(self,seconds):self.sleeps.append(seconds);self.now+=seconds


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ledger=AttemptLedger(self.temp.name);self.clock=Clock();self.calls=[];self.actions={'groq':[],'nebius':[]}
        clients={name:httpx.Client(transport=httpx.MockTransport(self.handler(name)),follow_redirects=False) for name in SETTINGS}
        for client in clients.values():self.addCleanup(client.close)
        self.router=ProviderRouter(SETTINGS,self.ledger,enabled=True,clients=clients,clock=self.clock,sleep=self.clock.sleep)
        self.addCleanup(self.router.close)

    def handler(self,provider):
        def handle(http_request):
            payload=json.loads(http_request.content);self.calls.append((provider,payload))
            if self.actions[provider]:
                action=self.actions[provider].pop(0)
                if isinstance(action,Exception):raise action
                if callable(action):return action(http_request)
                return action
            result=reviewer_report() if payload['model'].endswith('review-fixture') else draft()
            return httpx.Response(200,json=dict(id='fixture-request-id',model=payload['model'],
                choices=[dict(message=dict(content=result.model_dump_json()),finish_reason='stop')],
                usage=dict(prompt_tokens=21,completion_tokens=44)))
        return handle

    def generate(self,req=None,guard=lambda request:True,validator=context_validator):
        return self.router.generate('parent1',req or model_request(),guard=guard,validate_output=validator)

    def stops(self,code,req=None,**kwargs):
        with self.assertRaises(ProviderFailure) as caught:self.generate(req,**kwargs)
        self.assertEqual(caught.exception.code,code)
        return caught.exception

    def latest(self):
        events=self.ledger.events('parent1','operation1')
        return [event for event in events if event['attempt']['record_version']==2]

    def test_default_disabled(self):
        self.router.enabled=False;self.stops('provider_router_disabled');self.assertEqual(self.calls,[])

    def test_groq_primary_success_and_actual_usage(self):
        result=self.generate()
        self.assertEqual(result.provider,'groq');self.assertEqual(result.input_tokens,21)
        self.assertEqual(result.reported_model_id,'groq-writer-fixture')
        self.assertEqual(self.latest()[0]['validation_status'],'passed')
        self.assertIn('max_completion_tokens',self.calls[0][1]);self.assertNotIn('max_tokens',self.calls[0][1])

    def test_fixed_modes_never_switch(self):
        for provider in ('groq','nebius'):
            self.calls.clear();self.actions[provider]=[httpx.Response(503,json={}),httpx.Response(503,json={})]
            self.stops('provider_unavailable',model_request(mode=provider,operation_id='fixed-'+provider))
            self.assertEqual([p for p,_ in self.calls],[provider,provider])

    def test_auto_retry_then_nebius_fallback(self):
        self.actions['groq']=[httpx.Response(503,json={}),httpx.Response(503,json={})]
        result=self.generate()
        self.assertEqual(result.provider,'nebius');self.assertEqual([p for p,_ in self.calls],['groq','groq','nebius'])
        self.assertIn('max_tokens',self.calls[-1][1]);self.assertNotIn('max_completion_tokens',self.calls[-1][1])
        self.assertEqual(self.latest()[-1]['attempt']['fallback_reason'],'provider_unavailable')
        self.assertEqual(self.calls[0][1]['messages'],self.calls[-1][1]['messages'])

    def test_auto_budget_never_exceeds_three_attempts(self):
        self.actions['groq']=[httpx.Response(503,json={})]*4
        self.actions['nebius']=[httpx.Response(503,json={})]*4
        self.stops('provider_unavailable');self.assertEqual(len(self.calls),3)

    def test_short_retry_after_is_honored(self):
        self.actions['groq']=[httpx.Response(429,json={},headers={'Retry-After':'2'})]
        self.generate();self.assertEqual(self.clock.sleeps,[2.])

    def test_long_retry_after_skips_primary_retry_not_wait_rule(self):
        self.actions['groq']=[httpx.Response(429,json={},headers={'Retry-After':'600'})]
        result=self.generate()
        self.assertEqual([p for p,_ in self.calls],['groq','nebius']);self.assertEqual(self.clock.sleeps,[])
        self.assertEqual(result.provider,'nebius')

    def test_safe_connection_timeout_can_fallback(self):
        self.actions['groq']=[httpx.ConnectTimeout('fixture'),httpx.ConnectTimeout('fixture')]
        self.assertEqual(self.generate().provider,'nebius')

    def test_read_and_write_timeouts_are_unknown_without_retry(self):
        for index,error in enumerate((httpx.ReadTimeout('fixture'),httpx.WriteTimeout('fixture'))):
            self.calls.clear();self.actions['groq']=[error]
            req=model_request(operation_id='unknown-'+str(index),request_hash=str(index+1)*64)
            self.stops('outcome_unknown',req)
            self.assertEqual(len(self.calls),1)

    def test_unknown_scope_blocks_manual_provider_switch(self):
        self.actions['groq']=[httpx.ReadTimeout('fixture')]
        self.stops('outcome_unknown')
        self.stops('outcome_unknown',model_request(mode='nebius',operation_id='operation2'))
        self.assertEqual(len(self.calls),1)

    def test_auth_and_model_errors_never_fallback(self):
        for status in (401,403,404):
            self.calls.clear();self.actions['groq']=[httpx.Response(status,json={})]
            self.stops('provider_configuration',model_request(operation_id='auth-'+str(status)))
            self.assertEqual([p for p,_ in self.calls],['groq'])

    def test_client_request_error_is_not_retried(self):
        self.actions['groq']=[httpx.Response(400,json={'error':{'message':'SYNTHETIC-PRIVATE-ERROR'}})]
        self.stops('provider_request_rejected');self.assertEqual(len(self.calls),1)
        self.assertNotIn('SYNTHETIC-PRIVATE-ERROR',json.dumps(self.latest()))

    def test_gateway_timeout_is_uncertain(self):
        self.actions['groq']=[httpx.Response(504,json={})]
        self.stops('outcome_unknown');self.assertEqual(len(self.calls),1)

    def test_safety_refusal_blocks_cross_mode_and_new_operation(self):
        self.actions['groq']=[httpx.Response(200,json={'choices':[{'message':{'refusal':'Synthetic refusal'},'finish_reason':'stop'}]})]
        self.stops('safety_refusal')
        self.stops('safety_refusal',model_request(mode='nebius',operation_id='new-op'))
        self.assertEqual(len(self.calls),1)

    def test_http_safety_code_is_not_availability_failure(self):
        self.actions['groq']=[httpx.Response(400,json={'error':{'code':'content_policy_violation'}})]
        self.stops('safety_refusal');self.assertEqual(len(self.calls),1)

    def test_failed_preconditions_call_neither_provider(self):
        self.stops('preconditions_failed',guard=lambda req:False);self.assertEqual(self.calls,[])

    def test_preconditions_are_rechecked_before_fallback(self):
        self.actions['groq']=[httpx.Response(503,json={})]*2
        def guard(req):return len(self.calls)<2
        self.stops('preconditions_failed',guard=guard)
        self.assertEqual([p for p,_ in self.calls],['groq','groq'])

    def test_invalid_json_schema_and_context_do_not_fallback(self):
        for index,content in enumerate(('not JSON','{}')):
            self.calls.clear()
            self.actions['groq']=[httpx.Response(200,json={'choices':[{'message':{'content':content},'finish_reason':'stop'}]})]
            self.stops('output_validation',model_request(operation_id='invalid-'+str(index)))
            self.assertEqual(len(self.calls),1)
        self.stops('output_validation',model_request(operation_id='context'),validator=lambda *args:False)

    def test_shared_validation_also_applies_to_nebius(self):
        self.stops('output_validation',model_request(mode='nebius'),validator=lambda *args:False)
        self.assertEqual([p for p,_ in self.calls],['nebius'])

    def test_completion_length_cannot_pass_partial_output(self):
        self.actions['groq']=[httpx.Response(200,json={'choices':[{'message':{'content':draft().model_dump_json()},'finish_reason':'length'}]})]
        self.stops('incomplete_response');self.assertEqual(len(self.calls),1)

    def test_model_mismatch_does_not_switch_models(self):
        self.actions['groq']=[httpx.Response(200,json={'model':'unexpected-model','choices':[]})]
        self.stops('model_identity_mismatch');self.assertEqual(len(self.calls),1)

    def test_missing_usage_remains_unknown(self):
        self.actions['groq']=[httpx.Response(200,json={'choices':[{'message':{'content':draft().model_dump_json()},'finish_reason':'stop'}]})]
        result=self.generate();self.assertIsNone(result.input_tokens);self.assertIsNone(result.output_tokens)
        self.assertIsNone(result.reported_model_id)

    def test_logs_exclude_content_credentials_and_headers(self):
        self.generate();text=json.dumps(self.ledger.events('parent1','operation1'))
        for value in ('PRIVATE-CHILD-DETAIL',SETTINGS['groq'].api_key,'Authorization',draft().title):self.assertNotIn(value,text)

    def test_credentials_cannot_enter_input_or_saved_output(self):
        self.stops('credential_in_request',model_request(input_json=json.dumps({'secret':SETTINGS['groq'].api_key})))
        self.assertEqual(self.calls,[])
        raw=draft(title=SETTINGS['groq'].api_key).model_dump_json()
        self.actions['groq']=[httpx.Response(200,json={'choices':[{'message':{'content':raw},'finish_reason':'stop'}]})]
        self.stops('credential_in_response')
        with self.ledger.db() as db:
            stored=' '.join(str(tuple(r)) for r in db.execute('SELECT * FROM provider_operations'))
        self.assertNotIn(SETTINGS['groq'].api_key,stored)

    def test_duplicate_click_and_fresh_router_reuse_saved_response(self):
        first=self.generate();second=self.generate()
        self.assertEqual(first.output_hash,second.output_hash);self.assertTrue(second.cached);self.assertEqual(len(self.calls),1)
        with patch.object(self.router.adapters['groq'],'generate',side_effect=AssertionError('Must not call')):
            self.assertTrue(self.generate().cached)
        fresh=ProviderRouter(SETTINGS,AttemptLedger(self.temp.name),enabled=True,clock=self.clock,sleep=self.clock.sleep,
            clients={name:adapter.client for name,adapter in self.router.adapters.items()})
        self.addCleanup(fresh.close)
        self.assertTrue(fresh.generate('parent1',model_request(),guard=lambda req:True,validate_output=context_validator).cached)
        self.assertEqual(len(self.calls),1)

    def test_cached_result_still_requires_current_preconditions(self):
        self.generate();self.stops('preconditions_failed',guard=lambda req:False)
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.generate().cached)

    def test_operation_id_cannot_change_mode_or_prompt(self):
        self.generate()
        for update in (dict(mode='nebius'),dict(system_prompt='A changed prompt.')):
            self.stops('operation_conflict',model_request(**update))
        self.assertEqual(len(self.calls),1)

    def test_interrupted_intent_is_not_resubmitted(self):
        with patch.object(self.router.adapters['groq'],'generate',side_effect=SystemExit('Synthetic process death')):
            with self.assertRaises(SystemExit):self.generate()
        self.stops('outcome_unknown');self.assertEqual(self.calls,[])
        self.assertEqual(self.latest()[0]['attempt']['status'],'outcome_unknown')

    def test_reviewer_uses_its_own_model_and_prompt_for_each_provider(self):
        for provider in ('groq','nebius'):
            req=model_request(mode=provider,operation_id='review-'+provider,role='reviewer',output_schema='review_report',
                draft_hash=draft().draft_hash,system_prompt='Independent JSON reviewer fixture.')
            result=self.generate(req)
            self.assertEqual(result.model_id,provider+'-review-fixture')
            self.assertIn('Independent JSON reviewer fixture',self.calls[-1][1]['messages'][0]['content'])

    def test_reviewer_cannot_use_another_draft(self):
        req=model_request(role='reviewer',output_schema='review_report',draft_hash='f'*64)
        self.stops('output_validation',req)

    def test_missing_groq_configuration_never_turns_auto_into_nebius_only(self):
        self.router.settings=dict(SETTINGS,groq=ProviderSettings('groq','',writer_model='model'))
        self.stops('provider_configuration');self.assertEqual(self.calls,[])

    def test_global_deadline_stops_after_known_failure(self):
        def late(req):self.clock.now+=2;return httpx.Response(503,json={})
        self.actions['groq']=[late]
        self.stops('deadline_exceeded',model_request(deadline_seconds=1.));self.assertEqual(len(self.calls),1)

    def test_concurrent_operations_cannot_race_the_same_request(self):
        entered=threading.Event();release=threading.Event()
        def waiting(req):
            entered.set()
            if not release.wait(timeout=5):raise RuntimeError('Synthetic worker timeout')
            return httpx.Response(200,json={'model':json.loads(req.content)['model'],
                'choices':[{'message':{'content':draft().model_dump_json()},'finish_reason':'stop'}]})
        self.actions['groq']=[waiting]
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(self.generate)
            try:
                self.assertTrue(entered.wait(timeout=3))
                with self.assertRaises(RuntimeError):self.generate(model_request(mode='nebius',operation_id='parallel'))
            finally:release.set()
            self.assertEqual(first.result(timeout=5).provider,'groq')
        self.assertEqual(len(self.calls),1)

    def test_negative_or_boolean_usage_is_not_fabricated(self):
        self.actions['groq']=[httpx.Response(200,json={'choices':[{'message':{'content':draft().model_dump_json()},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':-1,'completion_tokens':True}})]
        response=self.generate();self.assertIsNone(response.input_tokens);self.assertIsNone(response.output_tokens)

    def test_cached_response_hash_is_verified(self):
        self.generate()
        with self.ledger.db() as db:db.execute("UPDATE provider_operations SET response='{}'")
        self.stops('cached_result_integrity');self.assertEqual(len(self.calls),1)

    def test_context_can_stop_a_known_output_for_missing_evidence(self):
        def blocked(*args):raise ProviderFailure('evidence_missing')
        self.stops('evidence_missing',validator=blocked);self.assertEqual(len(self.calls),1)

    def test_context_failure_cannot_be_mislabeled_retryable(self):
        def blocked(*args):raise ProviderFailure('evidence_missing',retryable=True)
        self.stops('evidence_missing',validator=blocked);self.assertEqual(len(self.calls),1)

    def test_custom_error_text_is_not_persisted_as_a_log_code(self):
        def blocked(*args):raise ProviderFailure('PRIVATE-CHILD-NAME-MUST-NOT-BE-LOGGED')
        self.stops('provider_operation_failed',validator=blocked)
        self.assertNotIn('PRIVATE-CHILD-NAME',json.dumps(self.latest()))


class ProviderContractTests(unittest.TestCase):
    def test_role_and_required_evidence_contracts(self):
        for change in (dict(role='reviewer'),dict(evidence_required=True),dict(evidence_bundle_id='bundle-only')):
            with self.assertRaises(ValueError):model_request(**change)
        for text in ('{"duplicate":1,"duplicate":2}','{"number":NaN}','[]'):
            with self.assertRaises(ValueError):json_object(text)

    def test_readiness_reports_configuration_only_and_no_secrets(self):
        env={'GROQ_API_KEY':'SYNTHETIC-SECRET','GROQ_MODEL':'writer-model','AI_PROVIDER_MODE':'groq'}
        mode,settings=from_environment(env);report=readiness(env)
        self.assertEqual(mode,'groq');self.assertTrue(report['providers']['groq']['reviewer_configured'])
        self.assertEqual(report['providers']['groq']['authentication'],'not_checked')
        self.assertNotIn('SYNTHETIC-SECRET',json.dumps(report));self.assertNotIn('SYNTHETIC-SECRET',repr(settings))
        with self.assertRaises(ValueError):from_environment({'AI_PROVIDER_MODE':'unsupported'})

    def test_retry_after_parsing(self):
        self.assertEqual(retry_after('3'),3.)
        self.assertIsNone(retry_after('nan'));self.assertIsNone(retry_after('bad header'))
