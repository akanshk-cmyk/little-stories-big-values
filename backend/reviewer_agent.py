"""Independent, evidence-cited review invocation, separate from story writing."""
import hashlib
import json
import os
from pathlib import Path
from .festival_engine import UserError, provider_json

ROOT=Path(__file__).resolve().parent
PROMPT=ROOT/'prompts/reviewer_system.txt'
PLAYBOOK=ROOT/'review_playbook.json'

def configuration():
    prompt=PROMPT.read_text(encoding='utf-8');playbook=json.loads(PLAYBOOK.read_text(encoding='utf-8'))
    return prompt,playbook,hashlib.sha256(prompt.encode()).hexdigest(),hashlib.sha256(json.dumps(playbook,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def json_pointer(value,pointer):
    if pointer=='':return value
    if not isinstance(pointer,str) or not pointer.startswith('/'):raise ValueError('Invalid pointer')
    for token in pointer[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        if isinstance(value,list):
            if not token.isdigit():raise ValueError('Invalid index')
            value=value[int(token)]
        elif isinstance(value,dict):value=value[token]
        else:raise ValueError('Pointer does not resolve')
    return value

def text_value(value):return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,sort_keys=True)

def evidence_bundle(record,inventory):
    book=record['book'];required=['/title','/classification','/fictionDisclosure','/moral','/discussionQuestion','/regionalNote','/safetyNote','/sourceReview','/durationNote']+[f'/pages/{i}' for i in range(len(book['pages']))]
    if book.get('didYouKnow'):required.append('/didYouKnow')
    if book.get('familyActivity'):required.append('/familyActivity')
    if book.get('sourceRetrieval'):required.extend(['/sourceCitations','/sourceRetrieval/method'])
    return {'book_id':record['id'],'book_version':record['version'],'book_digest':record['digest'],'required_coverage':required,
      'artifacts':{'book':book,'request':book['request'],'source_evidence':book.get('sourceEvidence',[]),'application_evidence':inventory},
      'limitations':['Only supplied evidence is available; links are not fetched.','A separate model call is not proof of human expertise or statistically independent judgment.','Fiction is assessed as fiction; it does not need proof that imagined events happened.']}

def validate_report(report,bundle,playbook):
    """Fail closed on fabricated citations, incomplete checks, or optimistic verdicts."""
    if not isinstance(report,dict):raise UserError('Reviewer returned an invalid result. No PASS was recorded.',502)
    errors=[];artifacts=bundle['artifacts'];rules={rule['id'] for rule in playbook['rules']};required=set(bundle['required_coverage'])
    def evidence_valid(items):
        if not isinstance(items,list):return False
        for item in items:
            if not isinstance(item,dict) or item.get('artifact_id') not in artifacts:return False
            try:resolved=json_pointer(artifacts[item['artifact_id']],item.get('pointer'))
            except (KeyError,TypeError,ValueError,IndexError):return False
            quote=item.get('quote')
            if not isinstance(quote,str) or not quote or quote not in text_value(resolved):return False
        return True
    if report.get('verdict') not in ('PASS','REVISION_REQUIRED','BLOCKED'):errors.append('Invalid verdict.')
    if not isinstance(report.get('summary'),str) or not report['summary'].strip():errors.append('Missing summary.')
    coverage=report.get('coverage')
    if not isinstance(coverage,list) or any(not isinstance(path,str) for path in coverage) or not required.issubset(set(coverage)):errors.append('Incomplete artifact coverage.')
    for field,key,names in [('dimensions','name',set(playbook['dimensions'])),('rules','rule_id',rules)]:
        rows=report.get(field)
        if not isinstance(rows,list) or len(rows)!=len(names) or any(not isinstance(row,dict) or not isinstance(row.get(key),str) for row in rows) or {row[key] for row in rows}!=names:
            errors.append('Missing or duplicated '+field+'.');continue
        for row in rows:
            if row.get('status') not in ('PASS','FAIL','UNRESOLVED'):errors.append('Invalid check status.')
            if not isinstance(row.get('note'),str) or not row['note'].strip():errors.append('Missing check finding.')
            if not evidence_valid(row.get('evidence')) or (row.get('status')=='PASS' and not row.get('evidence')):errors.append('Invalid or missing check evidence.')
            if row.get('status')!='PASS':errors.append('Failed or unresolved review check.')
    claims=report.get('claims')
    if not isinstance(claims,list) or not claims:errors.append('Missing claim review.')
    else:
        for claim in claims:
            if not isinstance(claim,dict):errors.append('Invalid claim.');continue
            try:artifact=text_value(json_pointer(artifacts['book'],claim.get('artifact_pointer')))
            except (KeyError,TypeError,ValueError,IndexError):errors.append('Claim pointer does not resolve.');continue
            if not isinstance(claim.get('claim'),str) or not claim['claim'] or claim['claim'] not in artifact:errors.append('Claim excerpt is not in the artifact.')
            if claim.get('kind') not in ('FACTUAL','OPERATIONAL','FICTIONAL'):errors.append('Unknown claim kind.')
            if claim.get('decision') not in ('SUPPORTED','UNSUPPORTED','NOT_CHECKABLE','FICTIONAL'):errors.append('Unknown claim decision.')
            refs=claim.get('evidence')
            if not evidence_valid(refs):errors.append('Claim evidence is invalid.')
            if not isinstance(refs,list):refs=[]
            if claim.get('decision') in ('UNSUPPORTED','NOT_CHECKABLE'):errors.append('Unsupported or uncheckable claim.')
            if claim.get('decision') in ('SUPPORTED','FICTIONAL') and not refs:errors.append('Claim lacks supporting evidence.')
            if claim.get('kind')=='FACTUAL' and claim.get('decision')=='SUPPORTED' and (not refs or any(not isinstance(ref,dict) or ref.get('artifact_id')!='source_evidence' for ref in refs)):errors.append('Factual support must come from source evidence, not self-citation.')
            if claim.get('kind')=='FACTUAL' and claim.get('decision')=='SUPPORTED' and any(isinstance(ref,dict) and str(ref.get('quote','')).startswith(('http://','https://')) for ref in refs):errors.append('A source URL alone does not support a factual claim.')
            if claim.get('kind')=='OPERATIONAL' and claim.get('decision')=='SUPPORTED' and not any(isinstance(ref,dict) and ref.get('artifact_id')=='application_evidence' for ref in refs):errors.append('Operational claims need application evidence.')
            if claim.get('decision')=='FICTIONAL' and claim.get('kind')!='FICTIONAL':errors.append('A factual claim cannot be relabeled as fiction.')
    revisions=report.get('revisions')
    if not isinstance(revisions,list):errors.append('Invalid revision notes.');revisions=[]
    else:
        for revision in revisions:
            if not isinstance(revision,dict) or revision.get('rule_id') not in rules or not isinstance(revision.get('issue'),str) or not isinstance(revision.get('requested_change'),str):errors.append('Invalid revision note.')
    if revisions:errors.append('Open revision notes remain.')
    valid_pass=report.get('verdict')=='PASS' and not errors
    return {**report,'verdict':'PASS' if valid_pass else ('BLOCKED' if report.get('verdict')=='BLOCKED' else 'REVISION_REQUIRED'),'validationNotes':list(dict.fromkeys(errors)),'reviewScope':'Automated artifact review against the supplied evidence and Playbook; not parent approval or expert certification.'}

def run_review(record,inventory):
    if not os.environ.get('NEBIUS_API_KEY'):raise UserError('The independent Reviewer needs the server Nebius connection. No Reviewer PASS or revision result was generated.',503)
    prompt,playbook,prompt_hash,playbook_hash=configuration();bundle=evidence_bundle(record,inventory)
    model=os.environ.get('NEBIUS_REVIEW_MODEL') or os.environ.get('NEBIUS_MODEL','Qwen/Qwen3-30B-A3B-Instruct-2507')
    # Separate invocation: no writer conversation, chain-of-thought or self-score.
    raw,_=provider_json('https://api.tokenfactory.nebius.com/v1/chat/completions',{'model':model,'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps({'playbook':playbook,'bundle':bundle},ensure_ascii=False)}],'response_format':{'type':'json_object'},'temperature':0.1,'max_tokens':6500},{'Authorization':'Bearer '+os.environ['NEBIUS_API_KEY']})
    try:
        response=json.loads(raw);report=json.loads(response['choices'][0]['message']['content'])
    except (ValueError,KeyError,IndexError,TypeError):raise UserError('Reviewer response was invalid or incomplete. No PASS was recorded; revise or retry the review.',502) from None
    validated=validate_report(report,bundle,playbook)
    return {'report':validated,'bundle':bundle,'model':model,'promptHash':prompt_hash,'playbookHash':playbook_hash,'playbookVersion':playbook['version'],'usage':response.get('usage',{})}
