'use strict';
let festivalSelection='diwali',festivalRecord=null,festivalDirty=false,festivalHealth=null,festivalPending=null,festivalTracks=[],festivalTrack=0,festivalPlayAll=false;
let festivalConnectionReady=Promise.resolve();
const festivalObjectives=['Gratitude','Kindness','Sharing','Respect','Patience','Helping Others','Care','Inclusion','Learning','Responsibility'];
function festivalById(id){return festivalCatalog.find(festival=>festival.id===id);}
function selectTheme(theme){
 document.querySelectorAll('[data-theme-choice]').forEach(button=>{const active=button.dataset.themeChoice===theme;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
 document.querySelectorAll('[data-theme-content]').forEach(section=>section.hidden=section.dataset.themeContent!==theme);
}
async function festivalApi(path,options={}){
 if(path!=='/health'){
  if(!festivalHealth)await festivalCheckConnection();
  if(festivalHealth?.singleCompleteReviewEnabled)throw Error('Open My Story Studio to create an original story or an available Ramayan adaptation. Festival creation is not available here.');
 }
 const response=await fetch('/api'+path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...(options.method?{'X-Bala-CSRF':festivalHealth?.csrf||''}:{}),...options.headers}});
 const type=response.headers.get('content-type')||'';
 if(!type.includes('application/json'))throw Error('Open the full local app at http://localhost:8001 to use approval, PDF and narration.');
 const result=await response.json();if(!response.ok)throw Error(result.error||'The operation could not finish.');return result;
}
async function festivalCheckConnection(){
 try{festivalHealth=await festivalApi('/health');$('festival-connection').textContent=festivalHealth.singleCompleteReviewEnabled?'Explore festival traditions here. Festival story creation is coming later; My Story Studio supports the available Ramayan adaptations.':festivalHealth.nebiusConfigured?'Nebius story generation is connected. Parent review is required before narration or download.':'AI generation is not connected yet. You can open an explicitly labeled prewritten example. Narration also needs its service connection.';
 if(festivalHealth.singleCompleteReviewEnabled)document.querySelectorAll('#festival-form [type="submit"]').forEach(button=>{button.disabled=true;button.title='Festival story creation is not available in this release.';});}
 catch{$('festival-connection').textContent='This preview shows festival choices. Open http://localhost:8001 for local story examples, approval and printable PDF.';}
}
function festivalPick(id){festivalSelection=id;const festival=festivalById(id);$('festival-select').value=id;$('festival-objective').value=festival.objective;chooseStoryCharacter(null);go('festivals');renderFestivalChoice();}
function renderFestivalChoice(){
 const festival=festivalById(festivalSelection);$('festival-name').textContent=festival.name;$('festival-intro').textContent=festival.fact;
 const art=$('selected-festival-art');art.style.background=`linear-gradient(135deg,${festival.colors[0]},${festival.colors[1]})`;art.querySelector('use').setAttribute('href',`assets/festivals.svg#${festival.id}`);
}
function festivalInvalidate(){
 $('run-independent-reviewer').disabled=true;$('independent-reviewer-output').replaceChildren(node('p','small','Text changed. Save the new version before running its independent review.'));
 festivalDirty=true;festivalTracks=[];$('festival-audio').pause();$('festival-audio').removeAttribute('src');$('festival-audio').load();$('festival-audio-area').hidden=true;
 $('festival-result-status').textContent='Unsaved text changes. Save a new version and review it again; downloads and narration are disabled.';
 ['festival-approve','festival-pdf','festival-narrate'].forEach(id=>$(id).disabled=true);
 ['festival-review-text','festival-review-culture','festival-review-safety'].forEach(id=>$(id).checked=false);
}
function festivalEdits(){return {title:$('festival-title-edit').value,pages:[...document.querySelectorAll('[data-festival-page]')].map(input=>input.value),moral:$('festival-moral-edit').value,discussionQuestion:$('festival-question-edit').value};}
function festivalAddContext(host,label,text){const section=node('section','festival-context-block');section.append(node('h3','',label),node('p','',text));host.append(section);}
function renderFestivalBook(){
 if(!festivalRecord)return;const {book,version,approved}=festivalRecord;festivalDirty=false;$('festival-result').hidden=false;
 $('festival-title-edit').value=book.title;$('festival-title-edit').oninput=festivalInvalidate;
 $('festival-classification').textContent=book.classification+' · '+(book.generation.provider==='prewritten_example'?'Prewritten example, not AI generated':'Generated with Nebius Qwen');
 $('festival-version').textContent=`Version ${version} · ${book.request.language} · Age ${book.request.age} · ${book.request.objective}`;
 $('festival-duration-note').textContent=`Requested: ${book.request.duration} minutes. Estimated text reading: ${book.estimatedMinutes} minutes. ${book.durationNote}`;
 $('festival-disclosure').textContent=book.fictionDisclosure;$('festival-cultural-ack-text').textContent=book.request.theme==='mythology'?'I reviewed respectful portrayal and the disclosure that these scenes and dialogue are imagined. No scriptural source or expert approval is claimed.':'I reviewed the source-linked fact and regional context. This is my parent review, not a claim of expert cultural approval.';
 $('festival-page-editor').replaceChildren();book.pages.forEach((text,index)=>{const article=node('article','edit-page'),label=node('label','',`PAGE ${index+1}`),input=node('textarea');input.id=`festival-page-${index}`;input.dataset.festivalPage=String(index);input.value=text;input.rows=5;input.maxLength=6000;input.lang=book.request.language==='Hindi'?'hi':'en';label.htmlFor=input.id;input.addEventListener('input',festivalInvalidate);article.append(label,input);$('festival-page-editor').append(article);});
 const context=$('festival-cultural-context');context.replaceChildren();
 if(book.didYouKnow)festivalAddContext(context,'Did You Know?',book.didYouKnow);festivalAddContext(context,book.request.theme==='mythology'?'Imagined story context':'Regional traditions',book.regionalNote);festivalAddContext(context,'Gentle and safe celebrations',book.safetyNote);
 if(book.familyActivity)festivalAddContext(context,'Optional family activity',book.familyActivity);
 (book.sourceEvidence||[]).filter(e=>e.text).forEach(e=>{const details=node('details','festival-context-block');details.append(node('summary','',`Source passage: ${e.title||e.id} - ${e.location||'Excerpt'} (parent reviewed)`),node('p','',e.text));(book.sourceCitations||[]).filter(c=>c.excerptId===e.id).forEach(c=>details.append(node('p','small',`${c.page?'Page '+c.page:'Cultural fact'}: ${c.claim} | Source quote: ${c.sourceQuote}`)));context.append(details);});
 book.sources.filter(source=>source.url).forEach(source=>{const link=node('a','festival-source',source.title+' ↗');link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';context.append(link);});
 context.append(node('p','small muted',book.sourceReview));
 $('festival-moral-edit').value=book.moral;$('festival-question-edit').value=book.discussionQuestion;
 $('festival-moral-edit').oninput=festivalInvalidate;$('festival-question-edit').oninput=festivalInvalidate;
 ['festival-review-text','festival-review-culture','festival-review-safety'].forEach(id=>$(id).checked=approved);
 $('festival-result-status').textContent=approved?`Version ${version} is approved for its exact text and voice settings. A text edit creates a new version that needs approval.`:'Draft saved. Read each page and its cultural context, then complete the three reviews to approve this version.';
 $('festival-approve').disabled=approved||!festivalRecord.qaReady;$('festival-pdf').disabled=!approved||!festivalRecord.qaReady;
 $('festival-narrate').disabled=!approved||!festivalRecord.qaReady||!festivalHealth?.narrationConfigured;
 $('festival-narration-status').textContent=festivalHealth?.narrationConfigured?(book.voice.id?'Narration uses the approved script and one saved voice. It is generated only when you request it.':'This version has no saved narrator voice; create a new version after connecting narration.'):'ElevenLabs is not connected on the server. No narration file has been generated.';
 try{localStorage.setItem('bala-festival-book',festivalRecord.id);}catch{}
 renderIndependentReviewer();
}
async function festivalSaveEdits(){
 if(!festivalRecord)throw Error('Open a festival story first.');if(!festivalDirty)return;
 festivalRecord=await festivalApi(`/books/${festivalRecord.id}/versions`,{method:'POST',body:JSON.stringify({version:festivalRecord.version,changes:festivalEdits()})});renderFestivalBook();
}
async function festivalAction(button,action){
 button.disabled=true;const status=$('festival-result-status');
 try{await action();}catch(error){status.textContent=error.message;}finally{button.disabled=button.id==='festival-approve'&&Boolean(festivalRecord?.approved);if(festivalDirty||!festivalRecord?.qaReady){$('festival-approve').disabled=true;$('festival-pdf').disabled=true;$('festival-narrate').disabled=true;}}
}
function setupFestivalFeatures(){
 $('festival-back').addEventListener('click',()=>{go('library');selectTheme('festivals');});
 document.querySelectorAll('[data-theme-choice]').forEach(button=>button.addEventListener('click',()=>selectTheme(button.dataset.themeChoice)));selectTheme('values');
 const host=$('festival-gallery');festivalCatalog.forEach(festival=>{
  const button=node('button','festival-card');button.type='button';button.dataset.festival=festival.id;
  const art=node('div','festival-card-art');art.style.background=`linear-gradient(135deg,${festival.colors[0]},${festival.colors[1]})`;
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 240 180');svg.setAttribute('aria-hidden','true');const use=document.createElementNS('http://www.w3.org/2000/svg','use');use.setAttribute('href',`assets/festivals.svg#${festival.id}`);svg.append(use);art.append(svg);
  button.append(art,node('strong','',festival.name),node('span','',festival.objective));button.addEventListener('click',()=>festivalPick(festival.id));host.append(button);
  const option=node('option','',festival.name);option.value=festival.id;$('festival-select').append(option);
 });
 festivalObjectives.forEach(value=>{const option=node('option','',value);option.value=value;$('festival-objective').append(option);});$('festival-objective').value='Gratitude';
 $('festival-select').addEventListener('change',()=>{festivalSelection=$('festival-select').value;renderFestivalChoice();});
 $('festival-form').addEventListener('submit',event=>{
  event.preventDefault();festivalPending={sourceIds:selectedSourceIds('festival-source-options'),mode:event.submitter?.value||'generate',request:{festival:$('festival-select').value,age:Number($('festival-age').value),language:$('festival-language').value,objective:$('festival-objective').value,duration:Number($('festival-duration').value),region:$('festival-region').value.trim()||'Family traditions vary',include_activity:$('festival-activity').checked}};
  const req=festivalPending.request;$('festival-confirm-summary').textContent=`Indian Festivals → ${festivalById(req.festival).name} → ${req.language} → ${req.objective}. Age ${req.age}; requested ${req.duration} minutes. ${festivalPending.mode==='example'?'Open a prewritten example; its actual reading estimate may differ from your requested duration.':'Generate a new original festival adventure with Nebius.'} Regional context: ${req.region}. Family activity: ${req.include_activity?'included':'not included'}.`;
  $('festival-confirm-error').textContent='';$('festival-confirm').showModal();
 });
 $('festival-cancel-confirm').addEventListener('click',()=>$('festival-confirm').close());
 $('festival-confirm-go').addEventListener('click',async()=>{
  const button=$('festival-confirm-go');button.disabled=true;$('festival-confirm-error').textContent=festivalPending.mode==='generate'?'Preparing your story. Please wait…':'Opening the labeled example…';
  try{if(!festivalHealth)await festivalCheckConnection();festivalRecord=await festivalApi('/books',{method:'POST',body:JSON.stringify(festivalPending)});festivalTracks=[];$('festival-audio-area').hidden=true;$('festivals').append($('festival-result'));renderFestivalBook();$('festival-confirm').close();$('festival-result').scrollIntoView({block:'start'});}
  catch(error){$('festival-confirm-error').textContent=error.message;}finally{button.disabled=false;}
 });
 $('festival-save').addEventListener('click',()=>festivalAction($('festival-save'),async()=>{await festivalSaveEdits();$('festival-result-status').textContent='Text version saved. Review again before approving any changed version.';}));
 $('festival-approve').addEventListener('click',()=>festivalAction($('festival-approve'),async()=>{
  if(festivalDirty)throw Error('Save your edits and review the new version first.');
  festivalRecord=await festivalApi(`/books/${festivalRecord.id}/approve`,{method:'POST',body:JSON.stringify({version:festivalRecord.version,digest:festivalRecord.digest,textReviewed:$('festival-review-text').checked,cultureReviewed:$('festival-review-culture').checked,safetyReviewed:$('festival-review-safety').checked})});renderFestivalBook();
 }));
 $('festival-pdf').addEventListener('click',()=>festivalAction($('festival-pdf'),async()=>{
  if(festivalDirty)throw Error('Save and approve the new text version first.');
  $('festival-result-status').textContent='Preparing the printable PDF from the approved version…';
  const response=await fetch(`/api/books/${festivalRecord.id}/pdf?version=${festivalRecord.version}`,{credentials:'same-origin'});
  if(!response.ok){const result=await response.json();throw Error(result.error);}
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=`festival-story-v${festivalRecord.version}.pdf`;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);$('festival-result-status').textContent=`Printable PDF downloaded from approved version ${festivalRecord.version}.`;
 }));
 $('festival-narrate').addEventListener('click',()=>festivalAction($('festival-narrate'),async()=>{
  if(festivalDirty)throw Error('Save and approve the new text version first.');$('festival-result-status').textContent='Generating the approved narration. Completed page clips are saved if a later clip fails.';
  const result=await festivalApi(`/books/${festivalRecord.id}/narrate`,{method:'POST',body:JSON.stringify({version:festivalRecord.version})});
  festivalTracks=result.tracks;festivalTrack=0;festivalPlayAll=false;$('festival-track-select').replaceChildren();festivalTracks.forEach((url,index)=>{const option=node('option','',index===0?'Title':index===festivalTracks.length-1?'Cultural notes and discussion':`Story page ${index}`);option.value=String(index);$('festival-track-select').append(option);});
  $('festival-audio').src=festivalTracks[0];$('festival-audio-area').hidden=false;$('festival-result-status').textContent=`Narration files are ready for approved version ${result.version}.`;
 }));
 $('festival-track-select').addEventListener('change',()=>{festivalTrack=Number($('festival-track-select').value);festivalPlayAll=false;$('festival-audio').src=festivalTracks[festivalTrack];});
 $('festival-play-all').addEventListener('click',()=>{festivalPlayAll=true;festivalTrack=0;$('festival-track-select').value='0';$('festival-audio').src=festivalTracks[0];$('festival-audio').play().catch(()=>{$('festival-result-status').textContent='Press Play in the audio controls to begin.';});});
 $('festival-audio').addEventListener('ended',()=>{if(festivalPlayAll&&festivalTrack<festivalTracks.length-1){festivalTrack++;$('festival-track-select').value=String(festivalTrack);$('festival-audio').src=festivalTracks[festivalTrack];$('festival-audio').play().catch(()=>{});}else festivalPlayAll=false;});
 renderFestivalChoice();festivalConnectionReady=festivalCheckConnection();
}
