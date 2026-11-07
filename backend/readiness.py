"""Read-only startup checks. Never starts a provider request or approves evidence."""
from pathlib import Path
import os

ROOT=Path(__file__).resolve().parents[1]

def check_local_readiness(root=ROOT,environment=None,*,inspect_source=True):
    root=Path(root);env=os.environ if environment is None else environment;checks=[]
    def add(name,ready,message):checks.append(dict(check=name,status='PASS' if ready else 'BLOCKED',message=message))
    add('provider_key',bool(env.get('GROQ_API_KEY','').strip()),'Groq key is configured.' if env.get('GROQ_API_KEY','').strip() else 'Set GROQ_API_KEY in .local/.env. Do not paste the key into the app.')
    add('provider_model',bool(env.get('GROQ_MODEL','').strip()),'Groq model is configured.' if env.get('GROQ_MODEL','').strip() else 'Set GROQ_MODEL to an available model in your Groq account.')
    source_db=root/'.local/source-workspace-audit/source-workspace.sqlite'
    add('source_workspace',source_db.is_file(),'Audited source workspace found.' if source_db.is_file() else 'Prepare the authorised Ramayan PDF extraction before starting this story flow.')
    review=root/'.local/ramayan-live-test/source-qa-report.json'
    add('source_review',review.is_file(),'Saved source review found.' if review.is_file() else 'A saved source-QA review is required. Startup will not run paid source review automatically.')
    if inspect_source and source_db.is_file() and review.is_file():
        if root.resolve()!=ROOT.resolve():add('source_integrity',False,'Inspect this source using the project that owns it.')
        else:
            try:
                import json
                from backend.local_ramayan_test import source
                from backend.ingestion.source_qa import validate_source_report
                document,command,_=source()
                report=validate_source_report(json.loads(review.read_text(encoding='utf-8')),document,command,'source-qa-v1')
                add('source_integrity',report.overall_result=='PASS','Original PDF, extraction and saved review match.' if report.overall_result=='PASS' else 'Source review is incomplete; story creation remains unavailable.')
            except Exception:add('source_integrity',False,'Source files or saved review no longer match. Inspect the original PDF and review record. No paid review was started.')
    from backend.pdf_export_service import find_pdf_browser
    browser=bool(find_pdf_browser(env))
    add('pdf_browser',browser,'PDF browser found.' if browser else 'Install Chrome or set CHROME_PATH to enable PDF export.')
    return dict(status='READY' if all(c['status']=='PASS' for c in checks) else 'BLOCKED',checks=checks,paid_calls=0,scope='Local configuration and saved-source checks; does not verify live provider access or story quality.')
