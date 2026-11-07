"""Render an immutable text version to PDF using installed headless Chrome."""
import base64
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import urlopen
from uuid import uuid4

def find_pdf_browser(environment=None):
    environment=os.environ if environment is None else environment
    candidates=[environment.get('CHROME_PATH',''),r'C:\Program Files\Google\Chrome\Application\chrome.exe',r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',shutil.which('chromium') or '',shutil.which('google-chrome') or '']
    return next((path for path in candidates if path and Path(path).is_file()),None)


def wait_debug_port(active,process,*,timeout=15,clock=time.monotonic,sleep=time.sleep):
    """Chrome can create its port file before releasing the Windows write lock."""
    deadline=clock()+timeout
    while clock()<deadline:
        try:
            lines=active.read_text().splitlines()
            if len(lines)>=2 and lines[0].isdigit() and 0<int(lines[0])<65536:return lines[0]
        except (FileNotFoundError,PermissionError):pass
        if process.poll() is not None:break
        sleep(.1)
    raise RuntimeError('PDF browser could not start; no file was generated.')

def render_pdf(book,version,digest,output,work_dir):
    esc=lambda value:html.escape(str(value))
    pages=''.join(f'<section class="page"><h2>{esc(book["title"])}</h2><div class="passage">{esc(text)}</div><footer>{index+1} / {len(book["pages"])}</footer></section>' for index,text in enumerate(book['pages']))
    labels={'didYouKnow':'क्या आप जानते हैं?' if book['request']['language']=='Hindi' else 'Did You Know?','regionalNote':'Regional traditions','moral':'सीख' if book['request']['language']=='Hindi' else 'Moral lesson','discussionQuestion':'Parent discussion','familyActivity':'Optional family activity','safetyNote':'Gentle celebrations'}
    extras=''.join(f'<h3>{esc(label)}</h3><p>{esc(book[key])}</p>' for key,label in labels.items() if book.get(key))
    sources=''.join(f'<p>{esc(source["title"])}: {esc(source["url"])}</p>' for source in book['sources'])
    provenance='Fictional adventure; no traditional episode is claimed.' if book['request'].get('theme')=='mythology' else 'Source-based cultural notes; fictional adventure.'
    document=f'''<!doctype html><html lang="{'hi' if book['request']['language']=='Hindi' else 'en'}"><meta charset="utf-8"><style>@page{{size:A4;margin:18mm}}body{{font:15pt/1.8 "Nirmala UI","Noto Sans Devanagari",Arial,sans-serif;color:#32263d}}h1,h2,h3{{color:#65438c}}.page{{break-after:page;position:relative;min-height:225mm}}.passage{{font-size:20pt;line-height:1.9;white-space:pre-wrap}}footer{{margin-top:20mm;color:#76617e;font-size:10pt}}.notes{{font-size:12pt}}p{{overflow-wrap:anywhere}}.version{{font-size:9pt}}</style><section class="page"><h1>{esc(book['title'])}</h1><p>{esc(book['classification'])}</p><p>{esc(book['fictionDisclosure'])}</p><p>{esc(book['request']['language'])} · Age {book['request']['age']} · {esc(book['request']['objective'])}</p><p>{esc(book['durationNote'])}</p><p>{esc(provenance)} Parent-approved text version {version}.</p><p class="version">Version manifest: {esc(digest)}</p></section>{pages}<section class="notes">{extras}<h3>Classification and sources</h3><p>{esc(book['classification'])}. {esc(book['fictionDisclosure'])}</p>{sources}</section></html>'''
    return render_html_pdf(document,output,work_dir)


def render_html_pdf(document,output,work_dir):
    """Print already authorized self-contained HTML using the existing engine."""
    import websocket
    chrome=find_pdf_browser()
    if not chrome:raise RuntimeError('PDF requires Chrome/Chromium. Set CHROME_PATH on the server.')
    folder=Path(work_dir)/('print-'+uuid4().hex);folder.mkdir(parents=True)
    source=folder/'book.html';source.write_text(document,encoding='utf-8')
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    process=subprocess.Popen([chrome,'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',f'--user-data-dir={folder / "profile"}',source.as_uri()],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
    connection=None
    try:
        active=folder/'profile/DevToolsActivePort'
        port=wait_debug_port(active,process)
        with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:tabs=json.load(response)
        connection=websocket.create_connection(next(tab for tab in tabs if tab['type']=='page')['webSocketDebuggerUrl'],suppress_origin=True,timeout=25)
        sequence=0
        def call(method,params=None):
            nonlocal sequence
            sequence+=1;connection.send(json.dumps({'id':sequence,'method':method,'params':params or {}}))
            while True:
                result=json.loads(connection.recv())
                if result.get('id')==sequence:
                    if result.get('error'):raise RuntimeError('PDF browser rejected the print request.')
                    return result.get('result',{})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            state=call('Runtime.evaluate',{'expression':'document.readyState','returnByValue':True})
            if state.get('result',{}).get('value')=='complete':break
            time.sleep(.1)
        call('Runtime.evaluate',{'expression':'document.fonts.ready.then(()=>true)','awaitPromise':True,'returnByValue':True})
        result=call('Page.printToPDF',{'printBackground':True,'preferCSSPageSize':True})
        content=base64.b64decode(result['data'])
        if not content.startswith(b'%PDF-'):raise RuntimeError('PDF verification failed.')
        Path(output).write_bytes(content)
    finally:
        if connection:connection.close()
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill()
