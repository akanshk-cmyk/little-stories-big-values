"""Explicit first real test: local uploaded PDF + Groq + text-only approval/PDF.
No hosted retrieval, image certification or human source review is claimed.
"""
import hashlib,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
from backend.contracts import (CharacterDesign,CharacterManifest,PlaybookCapabilities,SourceReference,RankedPassage,EvidenceBundle,content_hash)
from backend.contracts.common import text_hash
from backend.contracts.hosted_retrieval import ParsedDocument,ParsedPage
from backend.ingestion.hosted_review import PassageQualification,PageSpan
from backend.ingestion.source_qa import SourceQAAgent,RoutedSourceJudge,validate_source_report
from backend.contracts.source_qa import SourceQAReport
from backend.providers.config import from_environment
from backend.providers.router import ProviderRouter
from backend.providers.attempt_ledger import AttemptLedger
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
from backend.accounts.assets import AssetRegistry
from backend.accounts.assembly import assemble_complete_accounts

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.local/ramayan-live-test'


def source():
    workspace=ROOT/'.local/source-workspace-audit'
    with sqlite3.connect((workspace/'source-workspace.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('select id,original_hash,generation from documents where page_count=1960').fetchone()
        if not row:raise ValueError('The audited original Ramayan PDF is unavailable.')
        records=[json.loads(r[0]) for r in db.execute('select payload from pages where source_id=? order by page',(row[0],))]
    original=workspace/'originals'/(row[1]+'.pdf')
    if hashlib.sha256(original.read_bytes()).hexdigest()!=row[1]:raise ValueError('Ramayan original changed.')
    pages=tuple(ParsedPage(pdf_page=r['pdf_page'],text=r['clean_text'],content_hash=text_hash(r['clean_text'])) for r in records)
    doc=ParsedDocument(source_id=row[0],snapshot_id='local-audit-'+str(row[2]),original_file_hash=row[1],parse_version='local-extraction-v2',expected_page_count=1960,pages=pages)
    # First limited corpus: the actual welcome/horse-care passage in Canto L.
    # This is a source excerpt with page references, not an invented episode.
    start_page,end_page=549,551
    first=pages[start_page-1].text;last=pages[end_page-1].text
    marker=first.find('dear friend')
    ending=last.find('Give water to the steeds, and grain.')
    if marker<0 or ending<0:raise ValueError('Expected source passage boundaries changed.')
    begin=first.rfind('\n',0,marker)+1;end=ending+len('Give water to the steeds, and grain.')
    spans=(PageSpan(pdf_page=549,start=begin,end=len(first)),PageSpan(pdf_page=550,start=0,end=len(pages[549].text)),PageSpan(pdf_page=551,start=0,end=end))
    text='\n\n'.join(pages[s.pdf_page-1].text[s.start:s.end] for s in spans)
    command=PassageQualification(extraction_id=content_hash([row[1],row[2],549,551]),spans=spans,expected_text_hash=text_hash(text),scope='traditional_claims',rights_basis='User explicitly authorized private local PDF retrieval and Groq story testing; no public redistribution license asserted.',edition_reference='User-uploaded The Ramayana, Canto L, The Halt Under The Ingudi; PDF pages 549-551. Translator/edition metadata unverified; do not claim universal or verbatim scriptural authority.',cultural_review_basis='Internal automated source QA only; faithful adaptation of the uploaded version.',notes='Keep Rama welcoming Guha, declining rich gifts and asking that the horses receive food and water. Do not invent dialogue or events. Diacritics may be simplified in English; unclear names must be avoided or flagged.')
    return doc,command,text


def characters():
    return CharacterManifest(manifest_id='ramayan-text-cast-v2',characters=tuple(CharacterDesign(character_id=k,design_version='text-only-v1',appearance=v,clothing='Source-appropriate simple forest clothing; no image is supplied.',identifying_details=('Text-only adaptation; no invented visual scene is claimed.',)) for k,v in [('rama','Rama, the visiting prince.'),('guha','Guha, friend and host of Rama.'),('lakshmana','Lakshmana, brother of Rama.'),('sita','Sita, accompanying Rama.'),('sumantra','Sumantra, the charioteer.'),('hanuman','Hanuman, the courteous envoy and helper of Rama.'),('sugriva','Sugriva, the leader represented by Hanuman.')]))


def create_router(*,interactive=False):
    _,settings=from_environment()
    from dataclasses import replace
    settings['groq']=replace(settings['groq'],source_review_model=settings['groq'].writer_model,review_model=settings['groq'].writer_model)
    if not settings['groq'].configured('writer') or not settings['groq'].configured('reviewer'):raise ValueError('Configure Groq writer and reviewer models.')
    FOLDER.mkdir(parents=True,exist_ok=True)
    ledger=AttemptLedger(FOLDER/'provider-ledger')
    with ledger.db() as db:
        started=db.execute("SELECT count(*) FROM provider_attempt_events").fetchone()[0]
    if started>=24 and not interactive:raise ValueError('The bounded live verification attempt limit was reached. Review saved traces before further paid calls.')
    def diagnostics(request,output,error):
        folder=FOLDER/'validation-diagnostics';folder.mkdir(exist_ok=True)
        details=error.errors(include_input=False) if hasattr(error,'errors') else str(error)
        (folder/(request.operation_id+'.json')).write_text(json.dumps(dict(role=request.role,output=output,validation_error=details),indent=2,default=str),encoding='utf-8')
    return ProviderRouter(settings,ledger,enabled=True,default_mode='groq',validation_diagnostics=diagnostics,max_retry_delay=65)


def prepare_source(router,*,allow_paid_review=False):
    doc,command,text=source()
    cached=FOLDER/'source-qa-report.json'
    if cached.is_file():
        report=validate_source_report(json.loads(cached.read_text(encoding='utf-8')),doc,command,'source-qa-v1')
    else:
        if not allow_paid_review:raise ValueError('Saved source QA is missing. Startup will not run paid source review automatically.')
        judge=RoutedSourceJudge(router,owner='private-ramayan-source-test',authorize=lambda *args:True,mode='groq')
        report=SourceQAAgent(judge=judge,authorize=lambda owner,s,c:s==doc.source_id).review('private-ramayan-source-test',doc,command)
        cached.write_text(report.model_dump_json(indent=2),encoding='utf-8')
    (FOLDER/'source-scope.json').write_text(json.dumps(dict(review_origin='automated_qa',source_id=doc.source_id,original_file_hash=doc.original_file_hash,pdf_pages=[549,550,551],usage_scope=command.rights_basis,result=report.overall_result),indent=2),encoding='utf-8')
    if report.overall_result!='PASS':raise ValueError('Source QA did not PASS; see the saved actual report. No story was generated.')
    return doc,command,text,report


def build_app(*,accounts_folder=None,workflow_folder=None,interactive=False,daily_operation_limit=30):
    router=create_router(interactive=interactive)
    try:
        doc,command,text,report=prepare_source(router)
        from backend.ingestion.local_ramayan_catalog import LocalRamayanCatalog
        catalog=LocalRamayanCatalog(doc,command,text,report,router,FOLDER)
        def current(bundle):
            try:
                actual,cmd,_,saved=prepare_source(router)
                return saved==report and actual.original_file_hash==doc.original_file_hash and catalog.current(bundle)
            except (ValueError,OSError):return False
        def prepare(req):
            if req.story_type=='original_inspired':
                from backend.original_fiction import prepare_original
                return prepare_original(req)
            if not set(req.required_character_ids)<={'rama','hanuman','guha','lakshmana'} or not set(req.required_character_ids)&{'rama','hanuman'} or req.story_type!='adapted_retelling' or req.objective not in ('Kindness','Sharing') or req.target_minutes!=3:raise ValueError('Choose Rama or Hanuman, Adapted retelling, Kindness/Sharing and 3 minutes.')
            key,chosen,selected,source_report=catalog.select(req)
            page_numbers=tuple(sorted({s.pdf_page for s in chosen.spans}))
            ref=SourceReference(source_id=doc.source_id,snapshot_id=doc.snapshot_id,edition_id='uploaded-edition-unverified',chunk_id=key,parent_section_id=key,pdf_pages=page_numbers,printed_pages=tuple(str(n-20) for n in page_numbers),content_hash=text_hash(selected),review_record_ids=('automated-qa-'+content_hash(source_report),),eligibility_epoch=1,review_status='approved',eligibility_status='eligible',rights_status='permitted')
            passage=RankedPassage(reference=ref,text=selected,index_version=catalog.version,fused_rank=1,token_count=len(selected.split()))
            evidence=EvidenceBundle(bundle_id='ramayan-evidence-'+content_hash(req),request_hash=content_hash(req),passages=(passage,),index_version=catalog.version,retrieval_config_hash=content_hash(dict(method='request_character_matched_local_episode',episode_id=key,pdf_pages=page_numbers)),eligibility_epoch=1)
            return dict(characters=characters().model_dump(mode='json'),evidence=evidence.model_dump(mode='json'))
        from backend.original_fiction import FICTION_IDS,ORIGINAL_OBJECTIVES
        caps=PlaybookCapabilities(character_ids=('rama','hanuman','guha','lakshmana','krishna','ganesha',*FICTION_IDS),objectives=ORIGINAL_OBJECTIVES,story_types=('adapted_retelling','original_inspired'))
        base=SimpleNamespace(version='local-ramayan-request-matched-v2',capabilities=caps,prepare=prepare,eligibility=current,uses_prepared_evidence=True,dense=lambda *args:[],sparse=lambda *args:[],write=lambda *args:None,review=lambda *args:None,assets=lambda *args:None,render=lambda *args:None)
        accounts=Accounts(Store(accounts_folder or ROOT/'.local/accounts'))
        def resolve_evidence(owner,req,options):
            if req.story_type=='original_inspired' and options.selected_source_ids:raise ValueError('Original stories use your description; remove selected sources.')
            return prepare(req)
        api=assemble_complete_accounts(workflow_folder or FOLDER/'workflow',accounts=accounts,entitlements=Entitlements(accounts,max_generation_operations_per_day=daily_operation_limit),registry=AssetRegistry(accounts),base=base,router=router,authorize=lambda owner,req:accounts.require(owner) is True,evidence_sufficiency=lambda req,b:'PASS' if current(b) else 'PENDING',provider_consent=lambda owner,req,b,provider:provider=='groq',evidence_resolver=resolve_evidence,media_policy_version='explicit-text-only-test-v1',script_first=True,text_only=True,narration_enabled=False,allow_live_calls=True,allow_short_stories_for_testing=True)
        return api,router
    except Exception:
        router.close();raise
