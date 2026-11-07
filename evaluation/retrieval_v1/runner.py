"""Fixed-corpus keyword baseline and honest PENDING reporting."""
from pathlib import Path
import json
from statistics import mean
from backend.contracts.common import canonical_json, content_hash, parse_payload
from backend.retrieval.local_baseline import RetrievalCase, ScopeChanged, assert_scope, search, evaluate_case


def run(workspace,owner,cases,output,*,scope,limit=5,search_function=None,method_details=None):
    cases=[parse_payload(RetrievalCase,c) for c in cases]
    if scope not in ('synthetic','human_reviewed'):raise ValueError('Explicit experiment scope is required.')
    if not cases or len({c.test_id for c in cases})!=len(cases):raise ValueError('Use unique nonempty test IDs.')
    if any(c.label_status not in (scope,'pending') for c in cases):
        raise ValueError('Synthetic and human-reviewed labels must not be mixed.')
    destination=Path(output)
    destination.mkdir(parents=True,exist_ok=False)  # Existing experiments are never overwritten.
    inputs=[c.model_dump(mode='json') for c in cases]
    (destination/'inputs.json').write_text(canonical_json(inputs)+'\n',encoding='utf-8')
    rows=[];traces=[];corpora={}
    for case in cases:
        try:
            result=(search_function or search)(workspace,owner,case,limit=limit)
            key=content_hash([case.source.model_dump(mode='json'),case.purpose])
            corpora.setdefault(key,dict(source=case.source.model_dump(mode='json'),purpose=case.purpose,
                corpus_hash=result['corpus_hash'],eligibility_epoch=result['eligibility_epoch']))
            review=evaluate_case(case,result)
            traces.append(dict(test_id=case.test_id,**result))
        except (ScopeChanged,PermissionError) as error:
            review=dict(test_id=case.test_id,status='PENDING',reason=str(error))
            traces.append(dict(test_id=case.test_id,status='blocked',error=type(error).__name__))
        rows.append(review)
    # A review/edition change between queries invalidates every result for that
    # scope. Do not mix old and new permission snapshots into one headline score.
    invalid=set()
    for key,corpus in corpora.items():
        from backend.retrieval.local_baseline import SourceScope
        source=parse_payload(SourceScope,corpus['source'])
        try:
            current=workspace.summary(source.source_id,owner)
            assert_scope(current,source)
            if current['epoch']!=corpus['eligibility_epoch']:invalid.add(key)
        except (ScopeChanged,PermissionError):invalid.add(key)
    for index,case in enumerate(cases):
        if content_hash([case.source.model_dump(mode='json'),case.purpose]) in invalid:
            rows[index]=dict(test_id=case.test_id,status='PENDING',reason='Corpus or permissions changed during this experiment.')
    ranked=[r for r in rows if 'recall_at_k' in r]
    negatives=[r for r in rows if r.get('negative_case')]
    summary=dict(scope=scope,input_hash=content_hash(inputs),case_count=len(cases),limit=limit,
        corpus_manifests=list(corpora.values()),
        pass_count=sum(r['status']=='PASS' for r in rows),fail_count=sum(r['status']=='FAIL' for r in rows),
        pending_count=sum(r['status']=='PENDING' for r in rows),relevance_cases_evaluated=len(ranked),
        mean_recall_at_k=mean(r['recall_at_k'] for r in ranked) if ranked else None,
        mean_reciprocal_rank=mean(r['reciprocal_rank'] for r in ranked) if ranked else None,
        hit_rate_at_k=mean(int(r['hit_at_k']) for r in ranked) if ranked else None,
        negative_cases_evaluated=len(negatives),negative_cases_passed=sum(r['status']=='PASS' for r in negatives),
        measured_claim='Synthetic engineering fixture results only' if scope=='synthetic' else
            'Local fixed-corpus results; human label provenance is supplied by the experiment author',
        cloud_calls=0,model_calls=0)
    if method_details:
        details=method_details()
        summary['retrieval_configuration']=details
        summary['model_calls']=details.get('local_model_inference_calls',0)
        summary['remote_model_api_calls']=0
    for filename,payload in [('results.json',rows),('traces.json',traces),('summary.json',summary)]:
        (destination/filename).write_text(json.dumps(payload,indent=2)+'\n',encoding='utf-8')
    return summary
