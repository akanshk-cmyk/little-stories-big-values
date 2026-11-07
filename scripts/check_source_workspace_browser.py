"""Isolated Chrome UI check using existing dependencies and a synthetic PDF.

Starts its own loopback server and hidden headless browser, never the live app.
Real model keys are disabled. Browser page requests outside loopback are blocked.
No actual Ramayan content is approved. Outputs a report and two screenshots.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]


class Browser:
    def __init__(self, connection):
        self.connection=connection;self.sequence=0;self.exceptions=[];self.blocked=[]

    def send(self, method, params=None):
        self.sequence+=1
        self.connection.send(json.dumps(dict(id=self.sequence,method=method,params=params or {})))
        return self.sequence

    def call(self, method, params=None):
        expected=self.send(method,params)
        while True:
            result=json.loads(self.connection.recv())
            if result.get('method')=='Runtime.exceptionThrown':
                details=result['params']['exceptionDetails']
                self.exceptions.append(details.get('exception',{}).get('description',details.get('text','JavaScript exception')))
            if result.get('method')=='Fetch.requestPaused':
                event=result['params'];target=urlsplit(event['request']['url'])
                allowed=target.scheme in ('about','data','blob','chrome-extension') or target.hostname in ('127.0.0.1','localhost')
                if not allowed:self.blocked.append(target.hostname)
                self.send('Fetch.continueRequest' if allowed else 'Fetch.failRequest',
                          {'requestId':event['requestId']}|({} if allowed else {'errorReason':'BlockedByClient'}))
            if result.get('id')==expected:
                if result.get('error'):raise RuntimeError('Browser command failed: '+method)
                return result.get('result',{})

    def js(self, expression):
        result=self.call('Runtime.evaluate',dict(expression=expression,awaitPromise=True,returnByValue=True))
        if result.get('exceptionDetails'):raise RuntimeError('Browser script failed: '+result['exceptionDetails'].get('text',''))
        return result.get('result',{}).get('value')

    def wait(self, expression, timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.js(expression):return
            time.sleep(.1)
        raise RuntimeError('UI condition timed out: '+expression)


def main():
    work=ROOT/'.local'/('source-ui-'+uuid4().hex);work.mkdir(parents=True)
    for key in ('NEBIUS_API_KEY','GROQ_API_KEY','ELEVENLABS_API_KEY','LANGSMITH_API_KEY'):
        os.environ[key]=''
    os.environ.update(BALA_DATA_DIR=str(work/'data'),LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
    from backend.server import Handler,ThreadingHTTPServer
    from test_stage4_ingestion import pdf_bytes
    import websocket
    chrome=Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
    if not chrome.is_file():raise RuntimeError('Installed Chrome is unavailable.')
    source=work/'synthetic.pdf';source.write_bytes(pdf_bytes(['BOOK I','A gentle synthetic story about sharing.']))
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    profile=work/'browser-profile'
    process=subprocess.Popen([str(chrome),'--headless=new','--disable-gpu','--disable-background-networking',
        '--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check',
        '--remote-debugging-port=0',f'--user-data-dir={profile}','about:blank'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
    connection=None;checks=[];report={}
    output=ROOT/'docs'/'verification';output.mkdir(exist_ok=True)
    try:
        active=profile/'DevToolsActivePort';deadline=time.monotonic()+20
        while not active.exists() and time.monotonic()<deadline:time.sleep(.1)
        if not active.exists():raise RuntimeError('Headless browser did not start.')
        port=active.read_text().splitlines()[0]
        with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:tabs=json.load(response)
        connection=websocket.create_connection(next(t for t in tabs if t['type']=='page')['webSocketDebuggerUrl'],suppress_origin=True,timeout=30)
        browser=Browser(connection)
        browser.call('Runtime.enable');browser.call('Page.enable');browser.call('Fetch.enable',{'patterns':[{'urlPattern':'*'}]})
        browser.call('Emulation.setDeviceMetricsOverride',dict(width=1440,height=1050,deviceScaleFactor=1,mobile=False))
        browser.call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/source-workspace.html'})
        browser.wait("document.getElementById('notice')?.textContent.includes('no AI credits')")
        checks.append('Workspace JavaScript loads and health/library calls complete')
        browser.call('DOM.getDocument')
        root=browser.call('DOM.getDocument')['root']['nodeId']
        field=browser.call('DOM.querySelector',{'nodeId':root,'selector':'#pdf'})['nodeId']
        browser.call('DOM.setFileInputFiles',{'nodeId':field,'files':[str(source)]})
        browser.js("document.getElementById('title').value='Synthetic browser source';document.getElementById('edition').value='Synthetic fixture only';document.getElementById('upload').requestSubmit();true")
        browser.wait("typeof current!=='undefined' && current?.status==='ready' && document.querySelectorAll('.passage').length>0")
        checks.append('Synthetic PDF uploads, finishes processing and displays passages')
        browser.js("document.querySelector('details').open=true;document.getElementById('confirm-boundaries').click();true")
        browser.wait("current.generation===2 && document.getElementById('notice').textContent.includes('New passage version')")
        checks.append('Boundary confirmation creates a new generation in the UI')
        browser.js("{const card=document.querySelector('.passage');[...card.querySelectorAll('input[type=checkbox]')].slice(0,4).forEach(x=>x.checked=true);card.querySelector('input:not([type=checkbox])').value='Synthetic fixture owned for this UI check';card.querySelector('textarea').value='Synthetic content review only';[...card.querySelectorAll('button')].find(x=>x.textContent.startsWith('Allow')).click();}true")
        browser.wait("document.querySelector('.passage span')?.textContent==='allow'")
        checks.append('Explicit review checks save a parent-only passage decision')
        browser.js("{const card=document.querySelector('.passage');card.querySelector('textarea').value='Withdraw this synthetic fixture';[...card.querySelectorAll('button')].find(x=>x.textContent.startsWith('Withdraw')).click();}true")
        browser.wait("document.querySelector('.passage span')?.textContent==='withdraw'")
        checks.append('Withdrawal updates the displayed passage decision')
        browser.js("document.getElementById('page-number').value='2';document.getElementById('show-page').click();true")
        browser.wait("document.getElementById('page-text').textContent.includes('sharing')")
        browser.wait("document.getElementById('original').dataset.loaded==='true' && document.getElementById('original').src.includes('viewPage=2')")
        browser.js('new Promise(resolve=>setTimeout(resolve,700))')
        checks.append('Page navigation displays matching extracted text and original-PDF URL')
        browser.js("document.querySelector('details').open=false;document.getElementById('workspace').scrollIntoView();true")
        for name,width,height in [('desktop',1440,1050),('mobile',390,844)]:
            browser.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=name=='mobile'))
            browser.js("document.getElementById('workspace').scrollIntoView();true")
            if not browser.js('document.documentElement.scrollWidth<=innerWidth+1'):
                raise RuntimeError(name+' layout has horizontal overflow')
            image=browser.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
            (output/f'source-workspace-{name}.png').write_bytes(base64.b64decode(image['data']))
        checks.append('Desktop and 390px mobile layouts have no horizontal overflow')
        if browser.exceptions:raise RuntimeError('Browser reported JavaScript exceptions')
        report=dict(status='PASS',checks=checks,javascript_exceptions=browser.exceptions,
                    blocked_external_page_hosts=browser.blocked,scope='Synthetic UI interaction only; no real source approval or model calls')
    except Exception as error:
        report=dict(status='FAIL',checks=checks,error=str(error),scope='Synthetic UI check')
        if connection:
            report['javascript_exceptions']=browser.exceptions
            report['blocked_external_page_hosts']=browser.blocked
            report['visible_text']=browser.js('document.body?.innerText')
            image=browser.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
            (output/'source-workspace-failure.png').write_bytes(base64.b64decode(image['data']))
        raise
    finally:
        (output/'source-workspace-browser.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        if connection:
            try:connection.send(json.dumps({'id':999999,'method':'Browser.close'}))
            except Exception:pass
            connection.close()
        try:process.wait(timeout=6)
        except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=6)
        server.shutdown();server.server_close()
        workspace=getattr(server,'source_workspace',None)
        if workspace:workspace.close()
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
