'use strict';
const $=id=>document.getElementById(id);
let csrf='',current=null,page=null,offset=0,timer=null;
const notify=text=>{$('notice').textContent=text;};
async function api(path,{method='GET',body,headers={}}={}){
  const response=await fetch(path,{method,headers:{...(method==='POST'?{'X-Bala-CSRF':csrf}:{}),...headers},body});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||'This source step could not be completed.');
  return data;
}
function post(path,data){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});}
function node(tag,text){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;return value;}
function button(text,action){const value=node('button',text);value.type='button';value.onclick=()=>safe(action);return value;}
async function safe(action){try{await action();}catch(error){notify(error.message);}}
async function library(){
  const sources=await api('/api/source-workspace');$('documents').replaceChildren();
  if(!sources.length)$('documents').textContent='Your uploaded PDFs will appear here.';
  for(const source of sources)$('documents').append(button(`${source.title} · ${source.status}`,()=>openSource(source.id)));
}
async function openSource(id){offset=0;await refresh(id);if(current.status==='ready'){await showPage(1);await passages();}}
async function refresh(id=current.id){
  clearTimeout(timer);current=await api('/api/source-workspace/'+id);$('workspace').hidden=false;
  $('document-title').textContent=current.title+' · '+current.edition;
  $('progress').textContent=`${current.status} · ${current.extracted_pages} of ${current.page_count??'unknown'} pages · ${current.chunk_count} passages · version ${current.generation}`;
  $('resume').hidden=!['failed','extracting','queued'].includes(current.status);
  $('quality').textContent=current.error|| (current.metadata?`Text coverage: ${current.metadata.audit.coverage_pass?'all extracted words accounted for':'needs attention'}. Empty PDF pages: ${current.metadata.audit.empty_pdf_pages.join(', ')||'none'}. Section boundaries: ${current.metadata.boundaries_confirmed?'confirmed by you':'need review'}. Embedding and reranker token checks: pending.`:'Preparing readable page records.');
  if(current.metadata){$('boundaries').replaceChildren();for(const boundary of current.metadata.boundaries)addBoundary(boundary);$('target').value=current.metadata.target_words;$('overlap').value=current.metadata.overlap_words;}
  $('page-number').max=current.page_count||1;
  if(['queued','extracting'].includes(current.status))timer=setTimeout(()=>safe(async()=>{await refresh(id);if(current.status==='ready'){await showPage(1);await passages();}}),1500);
}
async function showPage(number=Number($('page-number').value)){
  page=await api(`/api/source-workspace/${current.id}/pages?page=${number}`);$('page-number').value=number;
  $('page-meta').textContent=`PDF page ${page.pdf_page}; printed page label unknown. ${page.word_count} words. ${page.quality_flags.join('; ')}`;
  drawPageText();
  // A distinct URL reloads Chrome's PDF viewer; fragment-only changes can leave
  // its previous page selected even after the extracted-text page changes.
  const url=`/api/source-workspace/${current.id}/original?viewPage=${number}#page=${number}`;
  const viewer=$('original');viewer.dataset.loaded='false';viewer.onload=()=>{viewer.dataset.loaded='true';};
  viewer.src=url;$('original-link').href=url;
}
function checkbox(form,text){const label=node('label');const field=node('input');field.type='checkbox';label.append(field,document.createTextNode(text));form.append(label);return field;}
function drawPageText(){if(page)$('page-text').textContent=$('raw').checked?page.raw_text:page.clean_text.split('\n').map((line,index)=>`${index+1}  ${line}`).join('\n');}
function addBoundary(value={pdf_page:1,line:1,title:'',kind:'section',label:null}){
  const row=node('fieldset');row.className='boundary-row';row.append(node('legend','Section start'));
  for(const [name,label,type] of [['pdf_page','PDF page','number'],['line','Extracted line','number'],['title','Heading title','text'],['label','Book or chapter number (if known)','text']]){
    const wrap=node('label',label),input=node('input');input.type=type;input.dataset.field=name;input.value=value[name]??'';if(type==='number')input.min=1;wrap.append(input);row.append(wrap);
  }
  const label=node('label','Heading type'),select=node('select');select.dataset.field='kind';
  for(const kind of ['book','canto','chapter','section']){const option=node('option',kind);option.value=kind;select.append(option);}select.value=value.kind;label.append(select);row.append(label);
  row.append(button('Remove this boundary',()=>row.remove()));$('boundaries').append(row);
}
function boundaryValues(){return [...$('boundaries').children].map(row=>{
  const result={};for(const input of row.querySelectorAll('[data-field]'))result[input.dataset.field]=input.type==='number'?Number(input.value):(input.value||null);return result;
});}
async function passages(){
  const items=await api(`/api/source-workspace/${current.id}/chunks?offset=${offset}&limit=5`);$('passages').replaceChildren();
  $('range').textContent=items.length?`${offset+1}–${offset+items.length} of ${current.chunk_count}`:'No passages prepared yet.';
  $('previous').disabled=offset===0;$('next').disabled=offset+items.length>=current.chunk_count;
  for(const item of items){
    const card=node('article');card.className='passage';card.append(node('h3',item.chapter_or_section),node('span',item.review_status));
    card.append(node('p',`PDF pages ${item.pdf_pages.join(', ')} · ${item.word_count} words · ${item.chunk_id}`),node('pre',item.text));
    card.append(button('Check original page',()=>showPage(item.pdf_pages[0])));
    if(item.review?.notes)card.append(node('p','Previous review: '+item.review.notes));
    const form=node('div');
    const extraction=checkbox(form,' I checked the extracted words against the original.');
    const culture=checkbox(form,' I checked the cultural context and any regional or edition differences.');
    const child=checkbox(form,' I checked suitability for my child.');
    const rights=checkbox(form,' I have permission to use this passage for my intended purpose.');
    const external=checkbox(form,' I also permit sending this passage to the configured AI provider in a future connected writing step.');
    const pinecone=checkbox(form,' I separately permit storing numerical search vectors and passage references in Pinecone when cloud search is enabled.');
    const rightsLabel=node('label','Permission basis');const rightsText=node('input');rightsText.maxLength=1000;rightsLabel.append(rightsText);form.append(rightsLabel);
    const notesLabel=node('label','Review notes, limitations or regional differences');const notes=node('textarea');notes.maxLength=3000;notesLabel.append(notes);form.append(notesLabel);
    for(const [label,action] of [['Allow for my inspiration workspace','allow'],['Restrict','restrict'],['Reject','reject'],['Withdraw permission','withdraw']]){
      form.append(button(label,async()=>{
        await post(`/api/source-workspace/${current.id}/reviews`,{expected_generation:current.generation,chunk_id:item.chunk_id,content_hash:item.content_hash,action,
          extraction_checked:extraction.checked,cultural_context_checked:culture.checked,child_suitability_checked:child.checked,
          rights_permitted:rights.checked,rights_basis:rightsText.value||null,notes:notes.value,external_processing_permitted:external.checked,pinecone_storage_permitted:pinecone.checked});
        notify('Your decision was saved for this exact passage version. Traditional claims still require qualified review.');await passages();
      }));
    }
    card.append(form);$('passages').append(card);
  }
}
$('upload').onsubmit=event=>{event.preventDefault();safe(async()=>{
  const file=$('pdf').files[0];if(!file||file.size>50*1024*1024)throw new Error('Choose a PDF up to 50 MB.');
  notify('Uploading your PDF. Text preparation will continue on this computer.');
  const result=await api('/api/source-workspace/upload',{method:'POST',body:file,headers:{'Content-Type':'application/pdf','X-Document-Title':encodeURIComponent($('title').value),'X-Document-Edition':encodeURIComponent($('edition').value)}});
  await library();await openSource(result.id);
});};
$('show-page').onclick=()=>safe(()=>showPage());$('raw').onchange=drawPageText;$('add-boundary').onclick=()=>addBoundary();
$('confirm-boundaries').onclick=()=>safe(async()=>{
  await post(`/api/source-workspace/${current.id}/boundaries`,{expected_generation:current.generation,boundaries:boundaryValues(),target_words:Number($('target').value),overlap_words:Number($('overlap').value)});
  offset=0;await refresh();await passages();notify('New passage version prepared. Earlier reviews remain in history and do not approve the new version.');
});
$('resume').onclick=()=>safe(async()=>{await post(`/api/source-workspace/${current.id}/resume`,{});await refresh();});
$('previous').onclick=()=>safe(async()=>{offset=Math.max(0,offset-5);await passages();});$('next').onclick=()=>safe(async()=>{offset+=5;await passages();});
safe(async()=>{csrf=(await api('/api/health')).csrf;await library();notify('Import and review use no AI credits.');});
