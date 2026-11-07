/* Capability-gated source integration. No credentials, approvals or provider calls in the browser. */
(() => {
 'use strict';
 const byId=id=>document.getElementById(id), make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 let mode='checking',catalog=[],sources=[],chosen=new Set(),current=null,timer=null,refreshing=null,confirmed=false;
 const message=text=>{byId('source-status').textContent=text;};
 const csrf=()=>sessionStorage.getItem('bala-account-csrf')||'';
 async function api(path,body){const r=await fetch('/api/accounts/hosted-sources'+path,{credentials:'same-origin',cache:'no-store',method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Bala-CSRF':csrf()},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'Source preparation could not continue.');return d;}
 const purpose=()=>byId('story-mode').value==='original'?'parent_inspiration':'traditional_claims';
 const available=()=>catalog.filter(s=>s.purpose===purpose());
 const phaseText=job=>({queued:'Your source request is saved.',running:'Preparing your source.',waiting_provider:'Document processing is still pending. Preparation will need to continue before story use.',paused:'Preparation is paused.',needs_attention:'Source preparation needs attention before it can continue.'}[job?.state]||({awaiting_internal_source_qa:'The extracted document is awaiting internal automated source QA.',source_qa_passed:'Internal source QA passed. Index preparation is still required.',source_qa_blocked:'Source QA could not approve a suitable passage. No source is ready for story use.',awaiting_source_review:'The extracted document is awaiting internal source QA configuration.',awaiting_activation:'The reviewed source is awaiting final preparation.',active:'This publication was activated; availability is checked again when you create a story.'}[job?.details?.phase]||'Saved. Processing permission and internal review are required before story use.'));
 function renderChoices(){
  const host=byId('story-source-options');host.replaceChildren();const list=available();
  if(!list.length)host.append(make('p','No eligible sources are available for this story type yet. Uploading alone does not approve a source.'));
  for(const source of list){const label=make('label');label.className='check-row';const input=make('input');input.type='checkbox';input.value=source.source_id;input.checked=chosen.has(source.source_id);input.onchange=()=>{chosen=input.checked?new Set([source.source_id]):new Set();renderChoices();};label.append(input,make('span',source.title));host.append(label);}
  for(const id of chosen)if(!list.some(s=>s.source_id===id)){const label=make('label');label.className='check-row';const input=make('input');input.type='checkbox';input.checked=true;input.value=id;input.onchange=()=>{chosen.delete(id);renderChoices();};label.append(input,make('span','The previously selected source is unavailable for these choices. Uncheck it or choose another source.'));host.append(label);}
  const festival=byId('festival-source-options');festival.replaceChildren(make('p','Use Story Studio for account stories with reviewed sources.'));
 }
 function selected(host){if(host!=='story-source-options')return [];const ids=[...chosen];if(ids.some(id=>!available().some(s=>s.source_id===id)))throw Error('Your selected source is no longer available for these choices. Choose an eligible source before continuing.');return ids;}
 function renderGuide(){byId('source-journey-status').textContent='Upload a PDF, follow its preparation, then select an eligible source in Story Studio. Cultural review happens internally.';}
 async function refreshCatalog(){const data=await api('/catalog');catalog=data.sources;renderChoices();}
 async function refresh(){
  if(refreshing)return refreshing;
  refreshing=(async()=>{try{const data=await api('');sources=data.sources;await refreshCatalog();if(confirmed)mode='hosted';const host=byId('source-library');host.replaceChildren();if(!sources.length)host.append(make('p','Your account has no uploaded sources yet.'));for(const source of sources){const card=make('article');card.className='source-card';const button=make('button','Check preparation');button.type='button';button.className='button subtle';button.onclick=()=>show(source.id).catch(e=>message(e.message));card.append(make('h3',source.title),make('p',source.edition),button);host.append(card);}renderGuide();}catch(e){catalog=[];sources=[];renderChoices();byId('source-library').replaceChildren(make('p','Sources could not be loaded. Check your account connection.'));message(e.message);throw e;}})();
  try{return await refreshing;}finally{refreshing=null;}
 }
 async function show(id){
  const source=await api('/'+encodeURIComponent(id));current=id;sessionStorage.setItem('bala-hosted-source',id);
  byId('source-preview').hidden=false;byId('source-preview-title').textContent=source.title;byId('source-preview-meta').textContent='Account source · '+source.edition;byId('source-preview-link').hidden=true;byId('source-review-form').hidden=true;
  const job=source.jobs[0];byId('source-preview-text').textContent=phaseText(job);byId('source-preview-warnings').textContent='Reading, uploading or requesting processing does not grant cultural approval.';
  byId('hosted-parse').hidden=Boolean(job);message(phaseText(job));
  clearTimeout(timer);if(job&&['queued','running'].includes(job.state)&&!document.hidden)timer=setTimeout(()=>show(id).then(refresh).catch(e=>message(e.message)),1500);
 }
 async function upload(){const file=byId('source-file').files[0];if(!file)return;if(!/\.pdf$/i.test(file.name)||file.size>50*1024*1024){message('Choose a PDF up to 50 MB.');return;}const button=byId('source-upload-button');button.disabled=true;message('Saving your PDF to your account. This does not start AI processing.');try{const r=await fetch('/api/accounts/hosted-sources/upload',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/pdf','X-Bala-CSRF':csrf(),'X-Document-Title':encodeURIComponent(byId('hosted-title').value.trim()||file.name),'X-Document-Edition':encodeURIComponent(byId('hosted-edition').value.trim()||'Unknown; requires review')},body:file});const d=await r.json();if(!r.ok)throw Error(d.error||'The PDF could not be saved.');byId('source-file').value='';if(refreshing)await refreshing;await refresh();await show(d.source_id);}catch(e){message(e.message);}finally{button.disabled=false;}}
 async function intercept(event,action){if(mode==='legacy')return;event.preventDefault();event.stopImmediatePropagation();await ready;if(mode==='legacy'){event.target.requestSubmit();return;}if(mode!=='hosted'){message('Sign in and check your account connection before adding sources.');return;}await action();}
 byId('source-upload-form').addEventListener('submit',e=>intercept(e,upload),true);
 byId('source-url-form').addEventListener('submit',e=>intercept(e,async()=>message('Web links are not available for account stories yet. Upload a PDF instead.')),true);
 byId('source-review-form').addEventListener('submit',e=>intercept(e,async()=>message('Source review is handled internally. Review the finished book when it is ready.')),true);
 byId('source-revoke').addEventListener('click',e=>{if(mode!=='legacy'){e.preventDefault();e.stopImmediatePropagation();message('Source permission changes require the protected source-review workflow.');}},true);
 byId('story-mode').addEventListener('change',()=>{if(mode==='hosted')renderChoices();});
 function configure(){
  byId('sources').querySelector('a[href="source-workspace.html"]').closest('p').hidden=true;
  byId('sources').querySelector('.page-heading p').textContent='Save a PDF to your account and follow its preparation for story use.';
  byId('source-journey-title').textContent='What happens after I add a PDF?';
  byId('sources').querySelector('[data-source-jump="source-url-form"]').hidden=true;
  byId('sources').querySelector('[data-source-jump="source-preview"]').textContent='2. Check preparation';
  byId('source-preview').querySelector('.overline').textContent='SOURCE PREPARATION';
  byId('source-file').accept='.pdf';byId('source-upload-button').textContent='Save PDF';
  byId('source-upload-form').querySelector('p').textContent='Upload a PDF up to 50 MB. Processing permission and internal review are required before it can inform a story.';
  for(const [id,label]of[['hosted-title','Document title (optional)'],['hosted-edition','Edition (if known)']]){const l=make('label',label);l.htmlFor=id;const input=make('input');input.id=id;input.type='text';input.maxLength=240;byId('source-upload-form').insertBefore(l,byId('source-upload-button'));byId('source-upload-form').insertBefore(input,byId('source-upload-button'));}
  byId('source-url-button').disabled=true;byId('source-url-form').querySelector('p').textContent='Account sources currently accept PDFs. Web links are not available here yet.';
  byId('source-review-form').hidden=true;byId('source-preview').hidden=true;
  const parse=make('button','Request document processing');parse.id='hosted-parse';parse.type='button';parse.className='button subtle';parse.hidden=true;parse.onclick=async()=>{if(!current)return;parse.disabled=true;try{await api('/'+encodeURIComponent(current)+'/parse',{request_key:crypto.randomUUID()});await show(current);}catch(e){message('Processing cannot start yet. '+e.message);}finally{parse.disabled=false;}};byId('source-preview').append(parse);
  const refreshButton=make('button','Refresh source progress');refreshButton.type='button';refreshButton.className='button subtle';refreshButton.id='hosted-source-refresh';refreshButton.onclick=()=>refresh().then(()=>current?show(current):null).catch(e=>message(e.message));byId('source-library').before(refreshButton);
  document.querySelectorAll('#sources .notice').forEach(n=>n.textContent='Uploaded text needs processing permission and internal cultural review. Only eligible sources appear in Story Studio. You approve the finished book once.');
  const steps=document.querySelectorAll('.source-journey-steps li');const copy=[['Save your PDF','The PDF is saved to your account. No story is generated by uploading.'],['Prepare the document','With processing permission, the backend reads the PDF and checks its page references.'],['Internal source QA','Internal source QA checks page evidence, cultural context and suitability. Usage rights are verified separately.'],['Select an eligible source','Choose one available source for the selected story type. Pending sources cannot be selected.'],['Create your story','The backend finds reviewed evidence, writes the story and runs independent quality and safety checks.'],['Review the finished book','Review the complete text and pictures, and requested narration, then approve the exact book version.']];steps.forEach((li,i)=>{li.querySelector('h3').textContent=(i+1)+'. '+copy[i][0];li.querySelector('p').textContent=copy[i][1];li.querySelector('.tag').textContent='Source journey';});
  byId('source-next-steps').querySelector('p').textContent='Pending sources need internal preparation. When an eligible source is available, choose it in Story Studio. Uploading and reading are separate from final book approval.';
  byId('source-review-current').hidden=true;byId('source-review-current-note').textContent='Your account story appears in the complete-book review after creation.';
  const tail=byId('sources').lastElementChild;if(tail.tagName==='P')tail.textContent='Documents are stored in the app. Authorized processing and story creation may transmit permitted text to configured services.';
  renderGuide();renderChoices();
 }
 const ready=(async()=>{try{const r=await fetch('/api/accounts/capabilities',{credentials:'same-origin',cache:'no-store'});if(r.status===404){mode='legacy';return;}if(!r.ok){mode='unavailable';return;}const c=await r.json();mode=c.hosted_sources_enabled?(c.hosted_catalog_enabled?'hosted':'unavailable'):'legacy';if(mode==='hosted'){configure();confirmed=true;await refresh();const saved=sessionStorage.getItem('bala-hosted-source');if(saved&&sources.some(s=>s.id===saved))await show(saved);else sessionStorage.removeItem('bala-hosted-source');}}catch(e){mode='unavailable';globalThis.hostedSourceInitializationError=String(e);message('Source services could not connect. Please try again.');}})();
 globalThis.HostedSourceUI={ready,get enabled(){return mode==='hosted';},get unavailable(){return mode==='unavailable';},refresh,renderChoices,renderGuide,show,selected,async beforeStory(){await ready;if(mode==='unavailable')throw Error('The source connection is unavailable. Your choices are retained.');if(mode==='hosted'){await refreshCatalog();selected('story-source-options');}}};
 window.addEventListener('hashchange',()=>{if(mode==='hosted'&&location.hash==='#sources')refresh().catch(e=>message(e.message));});
 document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);});
})();
