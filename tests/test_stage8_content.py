"""Synthetic provider responses through the actual graph; no quality claims."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from backend import contracts as c
from backend.providers.attempt_ledger import AttemptLedger
from backend.providers.router import ProviderRouter
from backend.workflows.service import WorkflowService
from backend.workflows.provider_content import ProviderContentAdapters
from backend.workflows import content_prompts as prompts
from test_contracts import request,draft
from test_stage3_workflows import adapters
from test_stage7_providers import SETTINGS


class ContentGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.calls=[];self.sufficiency='PASS';self.allowed=True;self.consent=True
        self.failures=0;self.review_status={};self.short=False;self.bad_label=False
        self.bad_citation=False;self.timeout=False;self.unavailable=0;self.revoke_on_review=False
        self.eligible=True;self.bundle=False
        self.mutate=lambda raw,payload:raw
        self.refusal=False

    def handler(self,provider):
        def handle(req):
            envelope=json.loads(req.content);payload=json.loads(envelope['messages'][1]['content'])
            self.calls.append((provider,envelope,payload))
            if self.refusal:return httpx.Response(200,json=dict(model=envelope['model'],choices=[dict(
                message=dict(refusal='Synthetic safety refusal'),finish_reason='stop')]))
            if self.timeout:raise httpx.ReadTimeout('Synthetic uncertain outcome')
            if provider=='groq' and self.unavailable:
                self.unavailable-=1
                return httpx.Response(503,json={'error':{'message':'Synthetic availability failure'}})
            story_request=c.parse_payload(c.StoryRequest,payload['request'])
            context=payload['context'];bundle=c.parse_payload(c.EvidenceBundle,context['evidence']) if context['evidence'] else None
            if 'draft' in payload:
                book=c.parse_payload(c.StoryDraft,payload['draft'])
                pointer=c.EvidencePointer(artifact_id=book.draft_id,pointer='/title',quote=book.title)
                def check(name):
                    status=self.review_status.get(name,'PASS')
                    if name=='value_insight' and len([x for x in self.calls if 'draft' in x[2]])<=self.failures:status='FAIL'
                    refs=(pointer,)
                    if name=='factual_correctness' and bundle and not self.bad_citation:
                        refs+=(c.EvidencePointer(artifact_id=bundle.bundle_id,pointer='/passages/0/text',quote=bundle.passages[0].text),)
                    return c.ReviewCheck(status=status,reason='Synthetic routing fixture only.',evidence=refs if status=='PASS' else (),
                        requested_correction='Correct this requirement.' if status=='FAIL' else None)
                result=c.ReviewReport(draft_hash=book.draft_hash,evidence_bundle_hash=bundle.bundle_hash if bundle else None,
                    reviewer_version=payload['reviewer_version'],**{name:check(name) for name in
                    ('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety')})
                if self.revoke_on_review:self.eligible=False
            else:
                fixture=draft(story_request,bundle)
                pages=tuple(p.model_copy(update={'text':p.text if self.short else p.text+' '+
                    'Mira folds a flower. Krishna gives a friend some paper. They smile and make room at the table. '
                    'Mira shares a blue sheet. Krishna waits for a turn. Their friend smiles and joins them.'}) for p in fixture.pages)
                result=draft(story_request,bundle,pages=pages,draft_id=payload['draft_id'],
                    classification=story_request.story_type,prompt_version=payload['prompt_version'],
                    fiction_disclosure='Imagined fiction' if self.bad_label else 'original teaching-inspired fiction')
            raw=self.mutate(result.model_dump(mode='json'),payload)
            return httpx.Response(200,json=dict(model=envelope['model'],choices=[dict(
                message=dict(content=json.dumps(raw)),finish_reason='stop')],
                usage=dict(prompt_tokens=31,completion_tokens=71)))
        return handle

    def service(self,**kwargs):
        clients={n:httpx.Client(transport=httpx.MockTransport(self.handler(n))) for n in SETTINGS}
        for client in clients.values():self.addCleanup(client.close)
        router=ProviderRouter(SETTINGS,AttemptLedger(Path(self.temp.name)/'ledger'),enabled=True,
            clients=clients,sleep=lambda _:None)
        self.addCleanup(router.close)
        base=replace(adapters(with_evidence=self.bundle),eligibility=lambda b:self.eligible)
        bridge=ProviderContentAdapters(base,router,authorize=lambda owner,r:self.allowed,
            evidence_sufficiency=lambda r,b:self.sufficiency,
            provider_consent=lambda owner,r,b,p:self.consent,synthetic=True,**kwargs)
        service=WorkflowService(Path(self.temp.name)/'runs',bridge,enabled=True)
        self.addCleanup(service.close)
        return service

    def run_story(self,service=None,req=None,mode='auto'):
        service=service or self.service()
        run=service.start('parent1','key1',(req or request()).model_dump(mode='json'),
            generation_mode='provider_synthetic',provider_mode=mode)
        return service,run,service.execute(run,'parent1')

    def test_qa_temperature_is_half_for_both_providers(self):
        service=self.service()
        for mode in ('groq','nebius'):
            before=len(self.calls)
            run=service.start('parent1','temperature-'+mode,request().model_dump(mode='json'),generation_mode='provider_synthetic',provider_mode=mode)
            state=service.execute(run,'parent1')
            self.assertEqual(state['status'],'assets_pending')
            calls=self.calls[before:]
            self.assertEqual(calls[0][1]['temperature'],0.2)
            self.assertEqual(calls[1][1]['temperature'],0.5)
            self.assertEqual([call[0] for call in calls],[mode,mode])

    def test_writer_and_independent_reviewer_use_actual_saved_artifacts(self):
        service,run,state=self.run_story()
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertEqual(len(self.calls),2)
        self.assertIn('Story Generation Agent',self.calls[0][1]['messages'][0]['content'])
        self.assertIn('independent Review',self.calls[1][1]['messages'][0]['content'])
        saved=service.repository.load(run,state['draft_id'])
        self.assertEqual(saved,self.calls[1][2]['draft'])
        self.assertEqual(state['review']['draft_hash'],saved['draft_hash'])
        self.assertEqual(len(state['provider_attempts']),2)
        self.assertTrue(all(x['status']=='succeeded' for x in state['provider_attempts']))
        self.assertIsNone(state['approval_id']);self.assertIsNone(state['book_version_hash'])

    def test_request_and_character_manifest_preserved_for_both_roles(self):
        _,_,state=self.run_story()
        for _,_,payload in self.calls:
            self.assertEqual(payload['request']['required_character_ids'],['krishna','mira'])
            self.assertEqual(payload['context']['characters']['manifest_id'],'cast1')
        self.assertEqual(state['review']['value_insight']['status'],'PASS')

    def test_existing_playbook_is_reused_with_explicit_scoped_overrides(self):
        from backend.workflows.provider_content import PLAYBOOK_PATH
        original=PLAYBOOK_PATH.read_bytes()
        self.run_story()
        baseline=json.loads(original)
        for _,_,payload in self.calls:
            policy=payload['application_playbook']
            self.assertEqual(policy['base_version'],baseline['version'])
            self.assertEqual(policy['base_hash'],c.content_hash(baseline))
            self.assertEqual(policy['scoped_overrides'],['duration_alignment','text_only_scope'])
            overrides=set(policy['scoped_overrides'])
            self.assertEqual([r for r in policy['rules'] if r['id'] not in overrides],
                [r for r in baseline['rules'] if r['id'] not in overrides])
        self.assertEqual(PLAYBOOK_PATH.read_bytes(),original)

    def test_changed_playbook_rejects_checkpoint_recovery(self):
        service,run,_=self.run_story()
        from backend.workflows import provider_content
        previous=provider_content.application_playbook
        with patch.object(provider_content,'application_playbook',lambda:dict(previous(),base_hash='b'*64)):
            with self.assertRaises(ValueError):service.execute(run,'parent1')

    def test_two_corrections_maximum_and_full_reviews_each_time(self):
        self.failures=99
        service,run,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked',state)
        self.assertEqual(state['correction_count'],2)
        self.assertEqual(len(self.calls),6)
        for number in range(1,3):
            payload=self.calls[2*number][2]
            self.assertEqual(payload['previous_draft']['draft_hash'],payload['review']['draft_hash'])
            self.assertEqual(len([k for k in payload['review'] if k in
                ('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety')]),6)
        note=service.repository.load(run,'routing-'+state['draft_hash'])
        self.assertEqual(note['responsible_component'],'Story Generation Agent')
        self.assertFalse(note['automatic_correction_allowed'])

    def test_successful_correction_gets_new_hash_and_reviewer(self):
        self.failures=1
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertEqual(state['correction_count'],1)
        self.assertNotEqual(self.calls[1][2]['draft_hash'],self.calls[3][2]['draft_hash'])

    def test_false_pass_for_short_story_is_overridden_without_rewriting_output(self):
        self.short=True
        service,run,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(state['review']['value_insight']['status'],'FAIL')
        key='review-'+state['draft_hash']
        raw=service.repository.load(run,'provider-'+service.adapters.operation_id(state,key))
        self.assertEqual(json.loads(raw['output_json'])['value_insight']['status'],'PASS')

    def test_explicit_short_story_allowance_keeps_other_qa_mandatory(self):
        self.short=True
        service=self.service();service.adapters.strict_duration=False
        _,_,state=self.run_story(service)
        self.assertEqual(state['status'],'assets_pending')
        self.assertEqual(state['review']['critical_safety']['status'],'PASS')

    def test_short_story_allowance_does_not_upgrade_safety_failure(self):
        self.short=True;self.review_status={'critical_safety':'FAIL'}
        service=self.service();service.adapters.strict_duration=False
        _,_,state=self.run_story(service)
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(state['review']['critical_safety']['status'],'FAIL')

    def test_mechanical_findings_preserve_existing_semantic_corrections(self):
        self.short=True;self.failures=99
        _,_,state=self.run_story()
        self.assertIn('Correct this requirement.',state['review']['value_insight']['requested_correction'])
        self.assertIn('duration',state['review']['value_insight']['requested_correction'])

    def test_exact_fiction_label_is_enforced(self):
        self.bad_label=True
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertIn('exact original',state['review']['value_insight']['reason'])

    def test_factual_failures_go_to_knowledge_not_writer(self):
        self.review_status['factual_correctness']='FAIL'
        service,run,state=self.run_story()
        self.assertEqual(state['correction_count'],0)
        self.assertEqual(state['status'],'evidence_blocked')
        self.assertEqual(service.repository.load(run,'routing-'+state['draft_hash'])['responsible_component'],'Cultural Knowledge Agent')

    def test_critical_safety_failure_blocks_even_if_all_dimensions_pass(self):
        self.review_status['critical_safety']='FAIL'
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(len(self.calls),2)

    def test_pending_review_does_not_trigger_blind_correction(self):
        self.review_status['engagement']='PENDING'
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(state['correction_count'],0)

    def test_missing_traditional_evidence_blocks_before_paid_call(self):
        _,_,state=self.run_story(req=request(story_type='traditional_retelling'))
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(self.calls,[])

    def test_retrieved_but_insufficient_evidence_cannot_authorize_writing(self):
        self.bundle=True;self.sufficiency='PENDING'
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(self.calls,[])

    def test_provider_specific_consent_is_required_before_calls(self):
        self.consent=False
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(self.calls,[])

    def test_unknown_submission_is_not_retried_or_fallen_back(self):
        self.timeout=True
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'outcome_unknown');self.assertEqual(len(self.calls),1)
        self.assertEqual(state['provider_attempts'][0]['status'],'outcome_unknown')

    def test_availability_fallback_is_logged_in_graph(self):
        self.unavailable=2
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertEqual([p for p,_,_ in self.calls],['groq','groq','nebius','groq'])
        self.assertEqual(len(state['provider_attempts']),4)

    def test_explicit_nebius_switch_for_both_roles(self):
        _,_,state=self.run_story(mode='nebius')
        self.assertEqual(state['status'],'assets_pending')
        self.assertEqual([p for p,_,_ in self.calls],['nebius','nebius'])

    def test_factual_pass_needs_source_references_not_only_draft(self):
        self.bundle=True;self.bad_citation=True
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(state['errors'][-1]['code'],'output_validation')
        self.assertEqual(len(self.calls),2)

    def test_revocation_after_review_response_is_checked_before_acceptance(self):
        self.bundle=True;self.revoke_on_review=True
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked');self.assertIsNone(state['review'])

    def test_reexecution_does_not_generate_duplicate_stories(self):
        service,run,state=self.run_story()
        self.assertEqual(service.execute(run,'parent1'),state);self.assertEqual(len(self.calls),2)

    def test_prompt_changes_reject_checkpoint_recovery(self):
        service,run,_=self.run_story()
        with patch.object(prompts,'WRITER',prompts.WRITER+' Changed.'):
            with self.assertRaises(ValueError):service.execute(run,'parent1')

    def test_live_not_enabled_by_default_and_synthetic_does_not_accept_real_clients(self):
        service=self.service()
        with self.assertRaises(ValueError):service.start('parent1','key',request().model_dump(mode='json'))
        for adapter in service.adapters.router.adapters.values():adapter.client=None
        with self.assertRaises(ValueError):ProviderContentAdapters(service.adapters.base,service.adapters.router,
            authorize=lambda *a:True,evidence_sufficiency=lambda *a:'PASS',provider_consent=lambda *a:True,synthetic=True)


    def test_live_flag_defaults_off_even_with_configured_router(self):
        service=self.service();bridge=service.adapters
        live=ProviderContentAdapters(bridge.base,bridge.router,authorize=lambda *a:True,
            evidence_sufficiency=lambda *a:'PASS',provider_consent=lambda *a:True)
        from backend.providers.retry_policy import ProviderFailure
        from backend.workflows.story_graph import PreparedContext
        context=c.parse_payload(PreparedContext,bridge.base.prepare(request()))
        with self.assertRaises(ProviderFailure) as caught:live.guard('parent1',request(),context,'auto')
        self.assertEqual(caught.exception.code,'provider_router_disabled');self.assertEqual(self.calls,[])

    def test_cached_graph_operation_rechecks_sufficiency(self):
        self.bundle=True
        service,run,state=self.run_story();bridge=service.adapters
        from backend.workflows.story_graph import PreparedContext
        context=c.parse_payload(PreparedContext,service.repository.load(run,'context'))
        self.sufficiency='PENDING'
        from backend.providers.retry_policy import ProviderFailure
        with self.assertRaises(ProviderFailure):bridge.check_operation('write-0',state,(request(),context,0,None),'parent1')
        self.assertEqual(len(self.calls),2)

    def test_cultural_fact_without_evidence_blocks_before_writing(self):
        _,_,state=self.run_story(req=request(required_elements=('moral','parent_question','cultural_fact')))
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(self.calls,[])

    def test_authorization_denied_does_not_attempt_provider_fallback(self):
        self.allowed=False
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(self.calls,[])

    def test_missing_character_is_rejected_without_fallback(self):
        def mutate(raw,payload):
            if 'pages' in raw:
                raw.pop('draft_hash')
                for page in raw['pages']:page['character_ids']=['mira']
            return raw
        self.mutate=mutate
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(len(self.calls),1)

    def test_fabricated_citation_is_rejected_without_review(self):
        self.bundle=True
        def mutate(raw,payload):
            if 'pages' in raw:
                raw.pop('draft_hash')
                raw['pages'][0]['citations'][0]['source_quote']='This quote is absent from the approved source.'
            return raw
        self.mutate=mutate
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(len(self.calls),1)

    def test_one_structural_repair_then_independent_review(self):
        def mutate(raw,payload):
            if 'pages' in raw and 'invalid_previous_response' not in payload:
                raw.pop('draft_hash')
                raw['pages'][0]['citations']=[dict(chunk_id='excerpt',claim_text='Absent claim',source_quote='quote',claim_kind='factual')]
            return raw
        self.mutate=mutate;service=self.service();service.adapters.format_repair=True
        _,_,state=self.run_story(service)
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertEqual(len(self.calls),3)
        self.assertEqual([p for p,_,_ in self.calls],['groq']*3)

    def test_structural_repair_is_bounded(self):
        def mutate(raw,payload):
            if 'pages' in raw:
                raw.pop('draft_hash')
                raw['pages'][0]['citations']=[dict(chunk_id='excerpt',claim_text='Absent claim',source_quote='quote',claim_kind='factual')]
            return raw
        self.mutate=mutate;service=self.service();service.adapters.format_repair=True
        _,_,state=self.run_story(service)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(len(self.calls),2)

    def test_reviewer_wrong_draft_hash_is_rejected(self):
        self.mutate=lambda raw,payload:dict(raw,draft_hash='b'*64) if 'draft' in payload else raw
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(len(self.calls),2)

    def test_safety_refusal_never_falls_back(self):
        self.refusal=True
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'evidence_blocked');self.assertEqual(len(self.calls),1)

    def test_traditional_request_keeps_classification_and_frozen_evidence(self):
        self.bundle=True
        _,_,state=self.run_story(req=request(story_type='traditional_retelling'))
        self.assertEqual(state['status'],'assets_pending',state)
        self.assertEqual(self.calls[1][2]['draft']['classification'],'traditional_retelling')
        self.assertEqual(self.calls[0][2]['context']['evidence'],self.calls[1][2]['context']['evidence'])

    def test_unrequested_cultural_fact_without_source_is_rejected(self):
        def mutate(raw,payload):
            if 'pages' in raw:
                raw.pop('draft_hash');raw['cultural_fact']='An ungrounded religious assertion.'
            return raw
        self.mutate=mutate
        _,_,state=self.run_story()
        self.assertEqual(state['status'],'failed');self.assertEqual(len(self.calls),1)

    def test_public_replay_endpoint_cannot_activate_provider_workflow(self):
        from backend.workflows.http_api import handle
        service=self.service()
        with self.assertRaises(PermissionError):handle(service,'POST',['api','workflow-runs'],'parent1',{})
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
