'use strict';
let sourceRecords=[];
let sourcePreviewRecord=null;
function sourceLabel(source){return source.reviewStatus==='approved'?'Parent reviewed - eligible for story selection':'Pending review - not eligible';}
function selectedSourceIds(host){
 if(globalThis.HostedSourceUI?.enabled)return HostedSourceUI.selected(host);
 const ids=[...$(host).querySelectorAll('input:checked')].map(i=>i.value);
 if(ids.length>3)throw Error('Select at most three reviewed sources.');
 return ids;
}
function renderSourceChoices(){
 if(globalThis.HostedSourceUI?.enabled)return HostedSourceUI.renderChoices();
 ['story-source-options','festival-source-options'].forEach(id=>{
  const host=$(id),chosen=new Set([...host.querySelectorAll('input:checked')].map(i=>i.value));host.replaceChildren();
  const available=sourceRecords.filter(s=>s.reviewStatus==='approved');
  if(!available.length)host.append(node('p','small muted','No parent-reviewed sources yet. Add a source and review its preview in Sources.'));
  available.forEach(s=>{const label=node('label','check-row'),input=node('input');input.type='checkbox';input.value=s.id;input.checked=chosen.has(s.id);input.addEventListener('change',()=>{if(host.querySelectorAll('input:checked').length>3){input.checked=false;toast('Select at most three sources.');}});label.append(input,node('span','',s.title));host.append(label);});
 });
}
function renderSourceGuide(){
 if(globalThis.HostedSourceUI?.enabled)return HostedSourceUI.renderGuide();
 const record=sourcePreviewRecord;
 $('source-journey-status').textContent=record
  ? `${record.title}: ${sourceLabel(record)}. Select it in Story Studio to use matching passages.`
  : sourceRecords.length?`${sourceRecords.length} saved sources. Preview and allow a source, then select it in Story Studio.`
  : 'Add a source or open a saved source to follow its progress.';
 const hasDraft=Boolean(festivalRecord);
 $('source-review-current').disabled=!hasDraft;
 $('source-review-current-note').textContent=hasDraft
  ? `Open the QA and parent review area for ${festivalRecord.book.title}. ${festivalRecord.book.sourceRetrieval?'Selected source passages are saved with this draft.':'No uploaded sources were attached to this draft.'}`
  : 'Generate an AI draft first to open its QA and parent review area.';
}
async function refreshSources(){
 try{
  if(globalThis.HostedSourceUI){await HostedSourceUI.ready;if(HostedSourceUI.enabled)return await HostedSourceUI.refresh();if(HostedSourceUI.unavailable)return;}
  await festivalConnectionReady;
  sourceRecords=await festivalApi('/sources');
  renderSourceChoices();
  renderSourceGuide();
  const host=$('source-library');host.replaceChildren();
  if(!sourceRecords.length)host.append(node('p','small muted','Your source shelf is empty. Add a document or a public web page to preview its text.'));
  sourceRecords.forEach(source=>{
   const card=node('article','source-card'),open=node('button','button subtle','Preview extracted text');
   card.append(node('span','source-kind',source.kind.toUpperCase()),node('h3','',source.title),node('p','small muted',`${source.wordCount.toLocaleString()} words - ${sourceLabel(source)}`));
   open.type='button';open.addEventListener('click',async()=>{
    open.disabled=true;
    try{showSource(await festivalApi(`/sources/${source.id}`));}catch(error){$('source-status').textContent=error.message;}
    finally{open.disabled=false;}
   });
   card.append(open);host.append(card);
  });
 }catch(error){$('source-status').textContent=error.message;}
}
function showSource(source){
 if(globalThis.HostedSourceUI?.enabled)return HostedSourceUI.show(source.id).catch(error=>{$('source-status').textContent=error.message;});
 sourcePreviewRecord=source;renderSourceGuide();
 $('source-preview').hidden=false;$('source-preview-title').textContent=source.title;
 $('source-preview-meta').textContent=`${source.wordCount.toLocaleString()} words - ${source.sections.length} text sections - Saved locally - ${sourceLabel(source)}`;
 $('source-text-reviewed').checked=false;$('source-culture-reviewed').checked=false;
 $('source-approve').hidden=source.reviewStatus==='approved';$('source-revoke').hidden=source.reviewStatus!=='approved';
 $('source-preview-text').textContent=source.sections.map(section=>`${section.location}\n${section.text||'[No extractable text]'}`).join('\n\n');
 $('source-preview-warnings').textContent=source.warnings.join(' ');
 const link=$('source-preview-link');link.hidden=!source.url;
 if(source.url){link.href=source.url;link.textContent='Open original web source';}
 $('source-status').textContent=source.reviewStatus==='approved'?'This exact text is allowed for story use. Select it in Story Studio; reading alone does not generate a story.':'Source saved and text extracted. Read the preview and explicitly allow this exact source before selecting it for story generation.';
 $('source-preview').scrollIntoView({block:'start',behavior:'smooth'});
}
function setupSourceIntake(){
 async function review(action){
  if(!sourcePreviewRecord)return;
  const id=action==='approve'?'source-approve':'source-revoke',button=$(id);button.disabled=true;
  try{const record=await festivalApi(`/sources/${sourcePreviewRecord.id}/review`,{method:'POST',body:JSON.stringify({action,textHash:sourcePreviewRecord.textHash,textReviewed:$('source-text-reviewed').checked,cultureReviewed:$('source-culture-reviewed').checked})});await refreshSources();showSource(record);}
  catch(error){$('source-status').textContent=error.message;}
  finally{button.disabled=false;}
 }
 $('source-review-form').addEventListener('submit',event=>{event.preventDefault();review('approve');});
 $('source-revoke').addEventListener('click',()=>review('revoke'));
 document.querySelectorAll('[data-source-jump]').forEach(button=>button.addEventListener('click',()=>{
  const target=$(button.dataset.sourceJump);
  if(target.id==='source-preview'&&target.hidden){
   $('source-status').textContent='Add a PDF or web link, or choose Preview extracted text on a saved source first.';
   $('source-status').scrollIntoView({block:'center',behavior:'smooth'});return;
  }
  target.scrollIntoView({block:'start',behavior:'smooth'});
  (target.querySelector('input')||target).focus({preventScroll:true});
 }));
 $('source-review-current').addEventListener('click',()=>{
  if(!festivalRecord)return;
  go(festivalRecord.book.request.theme==='mythology'?'original-story':'festivals');
  requestAnimationFrame(()=>$('independent-reviewer-heading').scrollIntoView({block:'start',behavior:'smooth'}));
 });
 $('source-upload-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('source-upload-button'),file=$('source-file').files[0];
  if(!file)return;
  if(file.size>10*1024*1024){$('source-status').textContent='Choose a document up to 10 MB.';return;}
  button.disabled=true;$('source-status').textContent='Uploading and extracting document text…';
  try{
   await festivalConnectionReady;
   const source=await festivalApi('/sources/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Source-Name':encodeURIComponent(file.name)},body:await file.arrayBuffer()});
   await refreshSources();showSource(source);$('source-file').value='';
  }catch(error){$('source-status').textContent=error.message;}
  finally{button.disabled=false;}
 });
 $('source-url-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('source-url-button');button.disabled=true;
  $('source-status').textContent='Fetching the public page and extracting text…';
  try{
   await festivalConnectionReady;
   const source=await festivalApi('/sources/url',{method:'POST',body:JSON.stringify({url:$('source-url').value.trim()})});
   await refreshSources();showSource(source);
  }catch(error){$('source-status').textContent=error.message;}
  finally{button.disabled=false;}
 });
 refreshSources();
}
