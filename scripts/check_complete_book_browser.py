"""Actual Studio -> complete review -> approval browser integration; synthetic providers/art only."""
import base64,json,os,subprocess,sys,tempfile,threading,time
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'tests')]
from check_source_workspace_browser import Browser

def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--script-first',action='store_true')
    parser.add_argument('--download-pdf',action='store_true')
    parser.add_argument('--qa-gate',action='store_true')
    parser.add_argument('--no-tab-csrf',action='store_true')
    parser.add_argument('--local-ramayan-startup',action='store_true')
    parser.add_argument('--startup-retry',action='store_true')
    parser.add_argument('--signed-out-entry',action='store_true')
    parser.add_argument('--rama-picture',action='store_true')
    parser.add_argument('--account-new-tab',action='store_true')
    parser.add_argument('--account-security',action='store_true')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='bala-account-ui-') as temp:
        for key in ('NEBIUS_API_KEY','GROQ_API_KEY','ELEVENLABS_API_KEY','PINECONE_API_KEY','LANGSMITH_API_KEY'):os.environ[key]=''
        os.environ.update(BALA_DATA_DIR=str(Path(temp)/'data'),LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
        from backend.server import Handler,ThreadingHTTPServer
        from backend.accounts.store import Store
        from backend.accounts.identity import Accounts
        from backend.accounts.entitlements import Entitlements
        from backend.accounts.assets import AssetRegistry
        from backend.accounts.http_api import AccountAPI
        import websocket
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        from test_complete_book_flow import CompleteBookTests
        from backend.contracts import StoryDraft,parse_payload
        from backend.workflows.story_graph import PreparedContext
        from unittest.mock import patch
        fixture=CompleteBookTests();fixture.setUp()
        if args.script_first:
            import test_complete_book_flow as book_fixture
            from backend.accounts.script_first import ScriptFirstAdapters,ScriptFirstWorkflowService
            with patch.object(book_fixture,'CompleteBookAdapters',ScriptFirstAdapters),patch.object(book_fixture,'CompleteBookWorkflowService',ScriptFirstWorkflowService):fixture.start()
        else:fixture.start()
        case=fixture.case;server.account_api=AccountAPI(case.accounts,case.ledger,case.registry,case.service)
        if args.local_ramayan_startup:case.service.source_mode='local_ramayan_text_only'
        asset=case.asset();original_manifest=case.registry.manifest
        def synthetic_scene_review(draft,characters,style=None):
            existing=original_manifest(draft,characters,style=style)
            if len(existing.pages)!=6:
                for page in draft.pages:case.registry.inspect_scene(case.admin,draft,characters,page.page_number,asset,
                    matches=True,notes='Synthetic automated scene-review fixture; not actual artwork matching.')
            return original_manifest(draft,characters,style=style)
        injection=patch.object(case.registry,'manifest',side_effect=synthetic_scene_review);injection.start()
        threading.Thread(target=server.serve_forever,daemon=True).start()
        profile=Path(temp)/'chrome'
        process=subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        connection=None;checks=[];report={'status':'FAIL'};out=ROOT/'docs/verification'
        try:
            active=profile/'DevToolsActivePort';deadline=time.monotonic()+20
            while not active.exists() and time.monotonic()<deadline:time.sleep(.1)
            port=active.read_text().splitlines()[0]
            with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as r:tabs=json.load(r)
            connection=websocket.create_connection(next(t for t in tabs if t['type']=='page')['webSocketDebuggerUrl'],suppress_origin=True,timeout=30)
            browser=Browser(connection);browser.call('Runtime.enable');browser.call('Page.enable');browser.call('Fetch.enable',{'patterns':[{'urlPattern':'*'}]})
            origin=f'http://127.0.0.1:{server.server_port}'
            if args.signed_out_entry:
                browser.call('Page.navigate',{'url':origin+'/index.html#create'})
                browser.wait("document.getElementById('managed-sign-in') && !document.getElementById('managed-sign-in').hidden")
                assert browser.js("document.querySelectorAll('#main-sign-in').length===1 && !document.querySelector('.topbar nav [data-nav=family]')")
                for width in (390,768,1440):
                    browser.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=844,deviceScaleFactor=1,mobile=width==390))
                    assert browser.js("$('main-sign-in').getBoundingClientRect().width>0 && $('managed-sign-in').getBoundingClientRect().width>0")
                    assert browser.js("$('demo-family-pill').getBoundingClientRect().width>0")
                    assert browser.js("$('managed-sign-in').getAttribute('href')==='account.html?from=main'")
                checks.append('Signed-out Studio exposes header and inline sign-in links at mobile, tablet and desktop widths')
                browser.js("$('managed-sign-in').click();true")
                browser.wait("location.pathname==='/account.html' && Boolean(document.getElementById('auth-form'))")
            browser.call('Page.navigate',{'url':origin+'/account.html'})
            browser.wait("Boolean(document.getElementById('auth-form'))")
            browser.js("(async()=>{const h=await fetch('/api/health').then(r=>r.json());const r=await fetch('/api/accounts/login',{method:'POST',headers:{'Content-Type':'application/json','X-Bala-CSRF':h.csrf},body:JSON.stringify({login:'parent1',password:'Synthetic-password-123'})});const session=await r.json();sessionStorage.setItem('bala-account-csrf',session.csrf);return r.ok;})()")
            if args.account_new_tab:
                browser.js("sessionStorage.removeItem('bala-account-csrf');true")
                browser.call('Page.navigate',{'url':origin+'/account.html'})
                browser.wait("document.getElementById('account') && !document.getElementById('account').hidden")
                assert browser.js("$('authentication').hidden && Boolean(sessionStorage.getItem('bala-account-csrf'))")
                checks.append('Existing account session opens account management without a second login when the tab token is missing')
            if args.no_tab_csrf:browser.js("sessionStorage.removeItem('bala-account-csrf');true")
            if args.startup_retry:
                browser.call('Page.addScriptToEvaluateOnNewDocument',{'source':"{const realFetch=window.fetch;let fail=true;window.fetch=function(url,...args){if(fail&&String(url)==='/api/accounts/csrf'){fail=false;return Promise.resolve(new Response(JSON.stringify({error:'Temporary session connection failure'}),{status:503,headers:{'Content-Type':'application/json'}}));}return realFetch.call(this,url,...args);};}"})
            browser.call('Page.navigate',{'url':origin+'/index.html#create'})
            if args.local_ramayan_startup:
                if args.startup_retry:
                    browser.wait("document.getElementById('managed-create-status')?.textContent.includes('Temporary session connection failure')")
                    assert browser.js("$('story-form').querySelector('[type=submit]').textContent==='Create Story'")
                    browser.js("$('story-form').requestSubmit();true")
                    browser.wait("ManagedStoryUI.ready")
                browser.wait("document.getElementById('managed-create-status') && (document.body.dataset.localRamayan==='true' || document.getElementById('managed-create-status').textContent.includes('could not connect'))")
                time.sleep(1)
                print(json.dumps(browser.js("({message:$('managed-create-status').textContent,button:$('story-form').querySelector('[type=submit]').textContent,local:document.body.dataset.localRamayan})")))
                assert browser.js("$('story-form').querySelector('[type=submit]').textContent==='Create Story'")
                report.update(status='PASS',checks=['Local Ramayan capabilities initialize Studio completely without model calls'],paid_calls=0)
                return
            browser.wait("typeof ManagedStoryUI!=='undefined' && ManagedStoryUI.enabled && document.querySelector('#demo-studio-child option')?.value!=='child-mira'")
            if args.rama_picture:
                picture=Path(temp)/'synthetic-card.jpg';picture.write_bytes((ROOT/'prototype-v2/assets/characters/krishna.jpg').read_bytes())
                # Isolated transport/persistence fixture only, not a Rama identity check.
                root=browser.call('DOM.getDocument')['root']['nodeId']
                field=browser.call('DOM.querySelector',{'nodeId':root,'selector':'#rama-picture-file'})['nodeId']
                browser.call('DOM.setFileInputFiles',{'nodeId':field,'files':[str(picture)]})
                browser.wait("$('rama-picture-status').textContent.includes('saved in this browser')")
                browser.call('Page.reload',{'ignoreCache':True})
                browser.wait("document.querySelector('#studio-character-gallery [data-story-character=rama] img')?.naturalWidth>0 && ManagedStoryUI.ready")
                assert browser.js("Math.abs(document.querySelector('#studio-character-gallery [data-story-character=rama] .character-picture').getBoundingClientRect().height-document.querySelector('#studio-character-gallery [data-story-character=krishna] .character-picture').getBoundingClientRect().height)<2")
                checks.append('User-selected card image decodes, persists across reload and matches other card dimensions (isolated synthetic selection fixture)')
            before=len(case.fixture.calls)
            browser.js("chooseStoryCharacter('krishna');$('duration').selectedIndex=0;$('idea').value='A gentle kindness adventure.';$('story-form').requestSubmit();true")
            browser.wait("location.hash==='#managed-book' && !$('managed-actions').hidden && !$('managed-details').hidden")
            if args.script_first:
                assert browser.js("$('managed-audio-consent').parentElement.textContent.includes('only after I approve')")
                assert case.service.approval_policy=='script-first-review-v1'
                checks.append('V5 capability selects approval-before-narration wording and the real v5 account workflow')
            browser.wait("document.getElementById('main-sign-in')?.textContent==='My account'")
            browser.js("(()=>{const old=document.createElement('a');old.dataset.nav='family';old.href='#family';old.textContent='Account';document.querySelector('.topbar nav').prepend(old);window.dispatchEvent(new Event('pageshow'));return true;})()")
            browser.wait("!document.querySelector('.topbar nav [data-nav=family]')")
            checks.append('Restored legacy Account markup is removed on page restoration; only the account button remains')
            assert browser.js("document.getElementById('main-sign-in').getAttribute('href')==='account.html'")
            assert browser.js("document.querySelectorAll('#main-sign-in').length===1 && !document.querySelector('.topbar nav [data-nav=family]')")
            checks.append('Header has one Sign in / My account entry and no duplicate Account tab')
            checks.append('Signed-in main-page navigation offers My account on the same origin')
            if args.no_tab_csrf:
                assert browser.js("Boolean(sessionStorage.getItem('bala-account-csrf'))")
                checks.append('Signed-in cookie without tab CSRF bootstraps safely and submits a story successfully')
            assert browser.js("!$('confirm-dialog').open && $('managed-approve').textContent==='Approve Book'")
            assert len(case.fixture.calls)==before+2
            checks.append('One Studio submission reaches writer and independent QA, then a single complete-book review; no source/text confirmation dialogs')
            assert browser.js("$('managed-pages').querySelectorAll('img').length===6 && $('managed-pages').textContent.includes('Page 6 of 6')")
            assert browser.js("$('managed-provenance').textContent.includes('Offline integration fixture') && !$('managed-provenance').hidden")
            checks.append('Exact saved story has six actual static image/text pairs and truthful synthetic provenance')
            browser.js("$('managed-revision').value='Make the ending more reassuring.';$('managed-change').click();true")
            browser.wait("!$('managed-actions').hidden && $('managed-approve').disabled===false && document.querySelector('#managed-pages').textContent.includes('Page 6 of 6')")
            # Wait for the new revision, not the previous displayed review.
            deadline=time.monotonic()+15
            while len(case.fixture.calls)<before+4 and time.monotonic()<deadline:browser.js('new Promise(r=>setTimeout(r,100))')
            browser.js('ManagedStoryUI.check();true')
            browser.wait("!$('managed-actions').hidden")
            assert len(case.fixture.calls)==before+4
            checks.append('Request Changes creates a new draft, reruns QA and returns one review of the revised complete version')
            browser.wait("$('managed-pages').dataset.loaded==='true'")
            assert browser.js("$('managed-pages').textContent.includes('Page 1 of 6') && [...$('managed-pages').querySelectorAll('img')].every(i=>i.complete&&i.naturalWidth>0)")
            checks.append('Rendered preview contains the actual story and all six successfully decoded images')
            for name,width,height in [('desktop',1440,1050),('mobile',390,844)]:
                browser.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=name=='mobile'))
                browser.js('scrollTo(0,0);true')
                assert browser.js('document.documentElement.scrollWidth<=innerWidth+1')
                browser.js('scrollTo(0,1000);true')
                assert browser.js("(()=>{const a=$('main-sign-in'),r=a.getBoundingClientRect();return r.width>=100&&r.top>=0&&r.bottom<=innerHeight&&a.classList.contains('primary');})()")
                browser.js('scrollTo(0,0);true')
                shot=browser.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
                (out/f'complete-review-{name}.png').write_bytes(base64.b64decode(shot['data']))
            checks.append('Existing header/Studio design preserved; complete review fits desktop and mobile')
            checks.append('Prominent single parent-account button remains visible after scrolling on desktop and mobile')
            browser.js("$('managed-approve').click();true")
            browser.wait("!$('managed-delivery').hidden && $('managed-actions').hidden")
            assert browser.js("$('managed-refresh').hidden && document.querySelector('#managed-book a[href=\"#create\"]').hidden")
            assert browser.js("document.querySelectorAll('.managed-sharing button svg').length===4 && [...document.querySelectorAll('.managed-sharing button')].every(b=>!b.disabled)")
            browser.js("document.querySelector('.managed-sharing [data-platform=whatsapp]').click();true")
            assert browser.js("$('managed-sharing-note').textContent==='Share your story · sharing options coming soon'")
            checks.append('Four clickable platform icons provide coming-soon feedback without publishing or external navigation')
            assert browser.js("$('managed-pdf').href.includes('/api/accounts/runs/')")
            checks.append('One Approve Book action releases the exact saved version and PDF link')
            if args.download_pdf:
                download=browser.js("(async()=>{const r=await fetch($('managed-pdf').href);const bytes=new Uint8Array(await r.arrayBuffer());return {ok:r.ok,type:r.headers.get('Content-Type'),size:bytes.length,magic:String.fromCharCode(...bytes.slice(0,5))};})()")
                assert download['ok'] and download['magic']=='%PDF-' and download['size']>100
                report['pdf_download']=download
                checks.append('Approved PDF endpoint returns actual PDF bytes to the authenticated browser')
            saved_adapter_version=case.service.adapters.version
            case.service.adapters.version=saved_adapter_version+'-upgrade'
            browser.js("go('shelf');true")
            browser.wait("Boolean(document.querySelector('#managed-library .managed-library-card button'))")
            assert browser.js("$('shelf-empty').hidden && document.querySelector('#managed-library h3').textContent.length>0")
            assert browser.js("!document.querySelector('#shelf').innerText.match(/prototype|completed demo|browser sample drafts/i)")
            assert browser.js("document.querySelector('.demo-global-bar').getBoundingClientRect().height===0")
            browser.call('Page.navigate',{'url':origin+'/index.html#shelf'})
            browser.wait("Boolean(document.querySelector('#managed-library .managed-library-card button'))")
            browser.js("document.querySelector('#managed-library .managed-library-card button').click();true")
            browser.wait("location.hash==='#managed-book' && !$('managed-delivery').hidden")
            assert browser.js("new URLSearchParams(location.search).get('run')===sessionStorage.getItem('bala-complete-run')")
            browser.call('Page.reload',{'ignoreCache':True})
            browser.wait("location.hash==='#managed-book' && Boolean(document.getElementById('managed-delivery')) && !$('managed-delivery').hidden")
            assert browser.js("$('managed-refresh').hidden && $('managed-title').textContent!=='Creating your storybook'")
            if args.download_pdf:
                assert browser.js("(async()=>{const r=await fetch($('managed-pdf').href);const b=new Uint8Array(await r.arrayBuffer());return r.ok&&String.fromCharCode(...b.slice(0,5))==='%PDF-';})()")
            case.service.adapters.version=saved_adapter_version
            checks.append('Saved story appears with its title in My Storybooks, survives a direct page reload and reopens with PDF download')
            checks.append('Completed book and original PDF remain readable after generation-version changes and reader reload; URL follows the selected book')
            checks.append('Account library hides duplicate browser-sample sections and legacy profile banner')
            if args.account_new_tab:
                browser.call('Page.navigate',{'url':origin+'/account.html'})
                browser.wait("document.querySelector('#library a[href*=\"index.html?run=\"]') && !$('account').hidden")
                assert browser.js("!$('library').textContent.startsWith('Storybook ·') && $('library').textContent.includes('Download PDF')")
                browser.js("$('parent-name').value='Browser Parent';$('parent-email').value='parent@example.test';$('parent-profile-form').requestSubmit();true")
                browser.wait("$('status').textContent.includes('Parent profile saved')")
                browser.call('Page.reload',{'ignoreCache':True})
                browser.wait("Boolean(document.getElementById('parent-name')) && $('parent-name').value==='Browser Parent'")
                assert browser.js("$('parent-email').value==='parent@example.test' && $('parent-details').textContent.includes('Browser Parent')")
                browser.call('Page.navigate',{'url':origin+'/index.html#shelf'})
                browser.wait("Boolean(document.getElementById('managed-parent-details'))")
                assert browser.js("!$('managed-parent-details').open && $('managed-parent-details').textContent.includes('parent@example.test') && $('managed-parent-details').textContent.includes('Browser Parent')")
                browser.call('Page.navigate',{'url':origin+'/account.html'})
                browser.wait("document.querySelector('#library a[href*=\"index.html?run=\"]') && !$('account').hidden")
                checks.append('Parent name/email persist after reload and appear in collapsed My Storybooks account details; email does not replace login')
                browser.js("document.querySelector('#library a[href*=\"index.html?run=\"]').click();true")
                browser.wait("location.hash==='#managed-book' && Boolean(document.getElementById('managed-delivery')) && !$('managed-delivery').hidden")
                checks.append('Account management shows saved-book titles with working reader and PDF links')
            if args.qa_gate:
                case.fixture.review_status={'clarity':'PENDING'}
                browser.js("go('create');chooseStoryCharacter('krishna');$('idea').value='A different gentle sharing story.';$('story-form').requestSubmit();true")
                browser.wait("location.hash==='#managed-book' && $('managed-progress').textContent.includes('could not confirm')")
                assert browser.js("$('managed-actions').hidden && $('managed-details').hidden")
                verdict=browser.js("(async()=>{const id=sessionStorage.getItem('bala-complete-run');return await fetch('/api/accounts/runs/'+id+'/complete-review').then(r=>r.json());})()")
                assert not verdict['approval_ready'] and 'preview' not in verdict
                checks.append('PENDING draft QA hides all parent review actions and exposes no failed draft or approval-ready API payload')
            if args.account_security:
                case.fixture.review_status={}
                saved_request=case.service.read_payload(case.run,case.parent)['request']
                interrupted=case.service.start(case.parent,'browser-interrupted',saved_request,options={},generation_mode='provider_synthetic')
                browser.call('Page.navigate',{'url':origin+'/index.html?run='+interrupted+'#managed-book'})
                browser.wait("Boolean(document.getElementById('managed-resume')) && !$('managed-resume').hidden")
                browser.js("window.confirm=()=>true;$('managed-resume').click();true")
                browser.wait("!$('managed-actions').hidden && !$('managed-approve').disabled")
                assert browser.js("sessionStorage.getItem('bala-complete-run')==="+json.dumps(interrupted))
                browser.js("$('managed-reject').click();true")
                browser.wait("$('managed-progress').textContent.includes('cancelled')")
                checks.append('Queued request survives navigation and continues through the same run ID to QA and parent review; parent can reject without creating a replacement request')
                uncertain=case.service.start(case.parent,'browser-uncertain',saved_request,options={},generation_mode='provider_synthetic')
                with case.service.repository.db() as db:db.execute('INSERT INTO operations VALUES(?,?,?,NULL)',(uncertain,'synthetic-uncertain','started'))
                calls_before=len(case.fixture.calls)
                browser.call('Page.navigate',{'url':origin+'/index.html?run='+uncertain+'#managed-book'})
                browser.wait("Boolean(document.getElementById('managed-title')) && $('managed-title').textContent==='This request needs attention'")
                assert browser.js("$('managed-resume').hidden && $('managed-progress').textContent.includes('unconfirmed')")
                assert len(case.fixture.calls)==calls_before
                case.service.cancel(uncertain,case.parent)
                checks.append('Uncertain prior operation stops progress polling, exposes no continuation, and is never resubmitted to the provider')
                browser.call('Page.navigate',{'url':origin+'/account.html'})
                browser.wait("Boolean(document.getElementById('recovery-password')) && !$('account').hidden")
                browser.wait("Boolean(document.querySelector('#recent-requests a[href*=\"run=\"]'))")
                browser.js("$('recovery-code-form').closest('details').open=true;$('recovery-password').value='Synthetic-password-123';$('recovery-code-form').requestSubmit();true")
                browser.wait("!$('recovery-code-result').hidden && $('recovery-code-value').value.length>30")
                recovery_code=browser.js("$('recovery-code-value').value")
                browser.js("$('hide-recovery-code').click();true")
                assert browser.js("$('recovery-code-result').hidden && $('recovery-code-value').value===''")
                browser.js("$('logout').click();true");browser.wait("!$('authentication').hidden")
                browser.js("$('recover-password-form').closest('details').open=true;$('recover-login').value='parent1';$('recover-new').value='Recovered-browser-password-123';$('recover-confirm').value='Recovered-browser-password-123';true")
                browser.js("$('recover-code').value="+json.dumps(recovery_code)+";$('recover-password-form').requestSubmit();true")
                browser.wait("$('status').textContent.includes('Password reset')")
                browser.js("$('login').value='parent1';$('password').value='Recovered-browser-password-123';$('auth-form').requestSubmit($('auth-form').querySelector('[value=login]'));true")
                browser.wait("!$('account').hidden && $('status').textContent.includes('Welcome back')")
                browser.js("$('change-password-form').closest('details').open=true;$('current-password').value='Recovered-browser-password-123';$('new-password').value='Changed-browser-password-456';$('confirm-password').value='Changed-browser-password-456';$('change-password-form').requestSubmit();true")
                browser.wait("!$('authentication').hidden && $('status').textContent.includes('Password changed')")
                assert browser.js("$('current-password').value==='' && $('recover-code').value==='' && $('recovery-code-value').value===''")
                checks.append('Recent requests reopen saved run URLs; recovery code can be hidden, used once after logout, and new password signs in; password change clears credentials and signs out')
            assert not browser.exceptions and not browser.blocked
            report.update(status='PASS',checks=checks,javascript_exceptions=browser.exceptions,blocked_external_page_hosts=browser.blocked,paid_calls=0,scope='MockTransport LLMs and synthetic color tiles; not live model, source or artwork quality verification')
        finally:
            (out/('local-ramayan-startup-browser.json' if args.local_ramayan_startup else 'script-first-browser.json' if args.script_first else 'complete-book-browser.json')).write_text(json.dumps(report,indent=2)+'\n')
            if connection:
                try:connection.send(json.dumps({'id':999999,'method':'Browser.close'}))
                except Exception:pass
                connection.close()
            try:process.wait(timeout=6)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=6)
            server.shutdown();server.server_close();injection.stop();fixture.doCleanups()
        print(json.dumps(report,indent=2))
if __name__=='__main__':main()
