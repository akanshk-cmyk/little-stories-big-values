"""Build a source-only public snapshot and stop on potential confidential content.

Uses the standard library. Does not call providers, publish, or print matched values.
Private local configuration is read only to detect exact secret/tenant identifiers.
"""
import argparse,hashlib,json,os,re,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES=('README.md','SECURITY.md','SPECIFICATION.md','.gitignore','.env.example','.python-version','pyproject.toml','uv.lock')
DOCS=('PRD.md','design.md','PROJECT_STATUS.md','development-blueprint.md','current-release-status.md','SETUP.md','llamaindex-integration-plan.md','real-ramayan-local-test.md')
PUBLIC_SCRIPTS=('prepare_public_release.py','check_source_workspace_browser.py','check_complete_book_browser.py','check_original_fiction_browser.py')
PATTERNS={
 'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
 'groq_key':rb'gsk_[A-Za-z0-9]{20,}',
 'provider_key':rb'(?<![A-Za-z])sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}',
 'github_token':rb'(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,})',
 'aws_key':rb'(?:AKIA|ASIA)[A-Z0-9]{16}',
 'google_key':rb'AIza[A-Za-z0-9_-]{30,}',
 'credential_url':rb'https?://[^\s/:<>]{1,120}:[^\s/@<>]{3,160}@',
}
# Exact invalid URL fixtures used to assert that credential URLs are rejected.
INVALID_URL_FIXTURES={
 'tests/test_source_ingestion.py':{b'https://' + b'user:pass@'},
 'tests/test_stage6_hybrid.py':{b'https://' + b'user:secret@'},
}
PRIVATE_SUFFIXES={'.pdf','.docx','.xlsx','.sqlite','.db','.log','.har','.pem','.key','.p12','.pfx','.zip','.pyc'}


def private_values(root):
    values=[]
    for name in ('.local/.env','.env','.env.local'):
        path=root/name
        if not path.is_file():continue
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            match=re.match(r'\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$',line)
            if not match or not re.search(r'KEY|TOKEN|SECRET|PASSWORD|ORGANIZATION_ID|PROJECT_ID|INDEX_ID',match[1]):continue
            value=match[2].strip().strip('"').strip("'")
            if len(value)>=8:values.append((match[1],value.encode()))
    for name,value in os.environ.items():
        if re.search(r'^(?:GROQ|NEBIUS|LLAMA|LLAMACLOUD|ELEVENLABS|GH|GITHUB|OPENAI|PINECONE).*?(?:KEY|TOKEN|SECRET|PASSWORD|ORGANIZATION_ID|PROJECT_ID|INDEX_ID)$',name) and len(value)>=8:
            values.append((name,value.encode()))
    return list(dict.fromkeys(values))


def selected_files(root):
    files={root/name for name in ROOT_FILES if (root/name).is_file()}
    for folder in ('backend','tests'):
        for path in (root/folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.txt','.json'):files.add(path)
    for path in (root/'prototype-v2').iterdir():
        if path.is_file() and path.suffix in ('.html','.js','.css') and not path.name.startswith('qa-'):files.add(path)
    files.add(root/'prototype-v2/admin/index.html')
    for path in (root/'prototype-v2/assets').rglob('*'):
        if path.is_file() and path.suffix in ('.png','.jpg','.svg','.json','.md'):files.add(path)
    for folder in ('hhh_v1','storytelling_v1','retrieval_v1'):
        for path in (root/'evaluation'/folder).iterdir():
            if path.is_file() and path.suffix in ('.py','.md','.txt'):files.add(path)
    files.update(root/'docs'/name for name in DOCS)
    files.update(root/'scripts'/name for name in PUBLIC_SCRIPTS)
    return sorted(files)


def scan(folder,values):
    findings=[];fixture_count=0
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or '.git' in path.relative_to(folder).parts:continue
        rel=path.relative_to(folder).as_posix();data=path.read_bytes()
        if path.suffix.lower() in PRIVATE_SUFFIXES or path.name=='.env' or path.name.startswith('.env.') and path.name!='.env.example':
            findings.append(dict(file=rel,kind='prohibited_runtime_artifact'))
        for kind,pattern in PATTERNS.items():
            for match in re.finditer(pattern,data):
                if kind=='credential_url' and match[0] in INVALID_URL_FIXTURES.get(rel,set()):fixture_count+=1;continue
                findings.append(dict(file=rel,line=data[:match.start()].count(b'\n')+1,kind=kind))
        for name,value in values:
            if value in data:findings.append(dict(file=rel,kind='private_configuration_value',configuration_name=name))
    return findings,fixture_count


def prepare(output):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'.local/github-release'):raise ValueError('Release output must stay inside .local/github-release.')
    if output.exists():raise ValueError('Choose a new output folder; existing snapshots are not overwritten.')
    values=private_values(ROOT);files=selected_files(ROOT)
    # Scan before copying; runtime/private folders are not traversed by the allowlist.
    output.mkdir(parents=True)
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT):raise ValueError('Selected public file must be a regular file inside the project.')
        rel=path.relative_to(ROOT);destination=output/rel;destination.parent.mkdir(parents=True,exist_ok=True)
        if rel.as_posix()=='prototype-v2/assets/characters/README.md':
            text=path.read_text(encoding='utf-8')
            text=re.sub(r'C:/Users/[^/\s]+/Downloads/(veyd|rama)\.png',r'the user-supplied \1.png attachment',text)
            destination.write_text(text,encoding='utf-8')
        else:shutil.copy2(path,destination)
    (output/'docs/verification').mkdir(exist_ok=True)
    (output/'docs/verification/.gitkeep').write_text('')
    findings,fixtures=scan(output,values)
    report=dict(status='BLOCKED' if findings else 'PASS',scope='Source-only snapshot; private runtime records excluded.',
        files=len(files)+1,private_configuration_values_checked=len(values),unresolved_findings=findings,
        invalid_security_test_urls_reviewed=fixtures,excluded=['.local and populated env files','accounts and databases','private source PDFs','saved user books','provider logs and traces','browser profiles and screenshots','historical reports and evaluation artifacts','pitch/export packages'],
        limitations='Pattern/exact-value scanning and curated file selection reduce risk; they do not prove absence of every confidential detail. Synthetic test accounts are not working credentials.')
    (output/'PUBLICATION_AUDIT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    manifest=[dict(path=p.relative_to(output).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(output.rglob('*')) if p.is_file()]
    (ROOT/'.local'/('public-release-manifest-'+output.name+'.json')).write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if findings:raise SystemExit('Publishing blocked. Resolve findings and build a new snapshot.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    prepare(parser.parse_args().output)
