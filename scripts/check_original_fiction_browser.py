"""Real UI/HTTP/graph/PDF check with isolated accounts and MockTransport only."""
import json,os,sys,tempfile,threading,subprocess,time
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'scripts')]
from check_source_workspace_browser import Browser

def main():
    import websocket
    from test_original_fiction_flow import OriginalApp
    from backend.pdf_export_service import find_pdf_browser
    report=dict(status='FAIL',scope='Controlled HTTP model responses; actual UI, account workflow and PDF. Not a live quality evaluation.',paid_calls=0)
    with tempfile.TemporaryDirectory(prefix='bala-original-ui-') as temp:
        for key in ('GROQ_API_KEY','NEBIUS_API_KEY','ELEVENLABS_API_KEY','LANGSMITH_API_KEY'):os.environ[key]=''
        os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false',BALA_DATA_DIR=str(Path(temp)/'data'))
        from backend.server import Handler,ThreadingHTTPServer
        app=OriginalApp(Path(temp)/'fixture');server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.account_api=app.api
        threading.Thread(target=server.serve_forever,daemon=True).start()
        profile=Path(temp)/'chrome';profile.mkdir()
        chrome=subprocess.Popen([find_pdf_browser(),'--headless=new','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        connection=None
        try:
            active=profile/'DevToolsActivePort';deadline=time.monotonic()+20
            while not active.exists() and time.monotonic()<deadline:time.sleep(.1)
            port=active.read_text().splitlines()[0]
            with urlopen(f'http://127.0.0.1:{port}/json/list') as r:tabs=json.load(r)
            connection=websocket.create_connection(next(t for t in tabs if t['type']=='page')['webSocketDebuggerUrl'],suppress_origin=True,timeout=30)
            browser=Browser(connection);browser.call('Runtime.enable');browser.call('Page.enable');browser.call('Fetch.enable',{'patterns':[{'urlPattern':'*'}]})
            origin=f'http://127.0.0.1:{server.server_port}'
            browser.call('Page.navigate',{'url':origin+'/account.html'})
            browser.wait("document.getElementById('auth-form')")
            result=browser.js("(async()=>{const h=await fetch('/api/health').then(r=>r.json());const r=await fetch('/api/accounts/login',{method:'POST',headers:{'Content-Type':'application/json','X-Bala-CSRF':h.csrf},body:JSON.stringify({login:'evaluation-parent',password:'Synthetic-evaluation-password-123'})});return r.status;})()")
            assert result==200,result
            browser.call('Page.navigate',{'url':origin+'/index.html#create'})
            browser.wait("globalThis.ManagedStoryUI?.ready")
            assert browser.js("!document.querySelector('#story-mode option[value=original]').disabled && !document.querySelector('#story-mode option[value=traditional]')")
            browser.js("$('story-mode').value='original';$('story-mode').dispatchEvent(new Event('change',{bubbles:true}));$('idea').value='Bun the rabbit and Pip the bird share apples in a garden.';$('story-value').value='Sharing';true")
            assert browser.js("selectedStoryCharacter.id==='original-main' && !$('original-description-choice').hidden && !$('story-value').querySelector('option:nth-child(3)').disabled")
            assert app.fixture_exchanges==0
            browser.js("$('story-form').requestSubmit();true")
            browser.wait("!$('managed-book').hidden && !$('managed-actions').hidden && $('managed-title').textContent==='Bun and Pip Share a Picnic'",timeout=45)
            assert browser.js("$('managed-title').textContent==='Bun and Pip Share a Picnic' && $('managed-classification').textContent.includes('original teaching-inspired fiction')")
            assert browser.js("$('managed-pages').textContent.includes('No Ramayan passages were used.') && $('managed-pages').querySelectorAll('section').length===6 && $('managed-delivery').hidden")
            run=browser.js("new URLSearchParams(location.search).get('run')")
            assert app.service.repository.load(run,'context')['evidence'] is None
            browser.js("$('managed-approve').click();true")
            browser.wait("!$('managed-delivery').hidden",timeout=30)
            pdf=browser.js("(async()=>{const r=await fetch($('managed-pdf').href);const b=new Uint8Array(await r.arrayBuffer());return {status:r.status,type:r.headers.get('Content-Type'),size:b.length,magic:new TextDecoder().decode(b.slice(0,5))};})()")
            assert pdf['status']==200 and pdf['magic']=='%PDF-',pdf
            browser.js("go('shelf');true")
            browser.wait("document.querySelector('#managed-library h3')?.textContent==='Bun and Pip Share a Picnic'")
            browser.call('Page.reload',{'ignoreCache':True})
            browser.wait("document.querySelector('#managed-library h3')?.textContent==='Bun and Pip Share a Picnic'")
            browser.js("document.querySelector('#managed-library .managed-library-card button').click();true")
            browser.wait("!$('managed-delivery').hidden && $('managed-title').textContent==='Bun and Pip Share a Picnic'")
            assert browser.js("new URLSearchParams(location.search).get('run')")==run
            assert not browser.exceptions,browser.exceptions
            assert len(app.requests)==2
            report.update(status='PASS',pdf=pdf,model_fixture_exchanges=len(app.requests),javascript_exceptions=browser.exceptions,checks=['Original option enabled; unavailable traditional option absent','Description characters and original values selectable','No source retrieval/evidence or citations invented','Six-page draft passes controlled independent QA before Save Book','Parent approval saves exact version and exposes valid PDF','Saved story survives reload and reopens from My Storybooks'])
        finally:
            if connection:connection.close()
            chrome.terminate();chrome.wait(timeout=10);server.shutdown();server.server_close();app.close()
            destination=ROOT/'docs/verification/original-fiction-browser-2026-11-06.json';destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
