"""Offline current-workflow exercise and evidence-bound HHH review import.

This CLI deliberately has no live-model execution command.
"""
import argparse,json,tempfile
from datetime import datetime,timezone
from pathlib import Path
from backend.contracts import content_hash,parse_payload
from backend.contracts.local_intent import resolve_local_intent
from .contracts import HHHReview,validate_review,RUBRIC_VERSION
from .cases import cases
from .offline import OfflineApp

def execute_case(service,owner,child,case):
    """Invoke the same service start/execute/review path used by AccountAPI.

    This foundation accepts provider_synthetic services only. Live execution needs
    a separately authorised, budgeted runner. CLI supplies MockTransport only.
    No parent approval, saving or PDF release occurs in this evaluation method.
    """
    if service.public_generation_mode!='provider_synthetic':raise PermissionError('This harness does not authorise paid model execution. Configure an explicitly budgeted live evaluation separately.')
    request=case.request.model_copy(update={'child_profile_id':child})
    intent=resolve_local_intent(request);record=dict(case_id=case.case_id,expected_outcome=case.expected_outcome,request=request.model_dump(mode='json'),purpose=case.purpose,fixture_fault=case.fixture_fault)
    if intent.status!='READY':
        outcome='clarification' if intent.status=='CLARIFICATION_REQUIRED' else 'blocked'
        record.update(execution_id='preflight-'+content_hash([case.case_id,request.model_dump(mode='json')]),execution_id_type='deterministic_local_preflight_record',outcome=outcome,status=intent.status,message=intent.message,draft=None,evidence=None,trace=None)
    else:
        run=service.start(owner,case.case_id,request.model_dump(mode='json'),provider_mode='groq',generation_mode=service.public_generation_mode,options={})
        state=service.execute(run,owner);review=service.review_book(run,owner)
        outcome='story_ready' if review.get('approval_ready') else 'blocked'
        record.update(execution_id=run,execution_id_type='actual_workflow_run',outcome=outcome,status=state['status'],message=review.get('incomplete_reasons',[]),draft=service.repository.load(run,state['draft_id']) if state.get('draft_id') else None,evidence=service.repository.load(run,state.get('context_id','context')) if state.get('evidence_bundle_id') else None,trace=service.status(run,owner),parent_approval_ready=review.get('approval_ready',False),parent_preview_exposed=bool(review.get('preview') or review.get('draft_preview')))
    record['workflow_check']='PASS' if record['outcome']==case.expected_outcome else 'FAIL'
    return record

def offline(output):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new empty output folder; saved runs are never overwritten.')
    output.mkdir(parents=True,exist_ok=True);records=[]
    manifest=[case.model_dump(mode='json') for case in cases()]
    (output/'cases.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    for case in cases():
        with tempfile.TemporaryDirectory(prefix='hhh-isolated-') as temp:
            app=OfflineApp(temp,case.fixture_fault)
            try:
                record=execute_case(app.service,app.owner,app.child,case)
                record.update(scope='synthetic_workflow',rubric_version=RUBRIC_VERSION,executed_at=datetime.now(timezone.utc).isoformat(),remote_model_calls=0,fixture_exchanges=app.fixture_exchanges,hhh={dimension:dict(status='PENDING',reason='Real content judgment and human calibration not executed. Synthetic routing success is not content quality.') for dimension in ('helpful','honest','harmless')})
            finally:app.close()
        (output/(case.case_id+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');records.append(record)
    summary=dict(scope='synthetic_workflow',dataset_hash=content_hash(manifest),cases=len(records),workflow_pass=sum(r['workflow_check']=='PASS' for r in records),workflow_fail=sum(r['workflow_check']=='FAIL' for r in records),hhh_content_pass=0,hhh_content_fail=0,hhh_content_pending=len(records),human_reviews=0,remote_model_calls=0,claims='Technical routing/validation checks only. Fixture reviewer PASS is not measured HHH content quality. Source QA, real retrieval, model robustness and RAGAS are not evaluated.')
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

def attach_review(record_path,review_path,output):
    record=json.loads(Path(record_path).read_text(encoding='utf-8'));raw=json.loads(Path(review_path).read_text(encoding='utf-8'))
    report=validate_review(parse_payload(HHHReview,raw),record)
    output=Path(output)
    if output.exists():raise ValueError('Review output already exists.')
    output.write_text(json.dumps(dict(review=report.model_dump(mode='json'),overall=report.overall,scope=record['scope']),indent=2),encoding='utf-8')
    return dict(overall=report.overall,scope=record['scope'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('offline');run.add_argument('--output',required=True)
    review=sub.add_parser('review');review.add_argument('--record',required=True);review.add_argument('--review',required=True);review.add_argument('--output',required=True)
    args=parser.parse_args()
    result=offline(args.output) if args.command=='offline' else attach_review(args.record,args.review,args.output)
    print(json.dumps(result,indent=2))
    if args.command=='offline' and result['workflow_fail']:raise SystemExit(1)

if __name__=='__main__':main()
