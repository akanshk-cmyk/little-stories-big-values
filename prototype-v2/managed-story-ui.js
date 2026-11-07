/* Complete-book mode uses existing Studio inputs and one final review screen.
 * It activates only when the authenticated server advertises the v4 workflow.
 */
(() => {
 'use strict';
 let originalModeHandler=null;
 let enabled=false,ready=false,children=[],activeChild=null,currentRun=null,currentReview=null,poll=null,creationKey=null,connecting=null,connectionError='',accountBalance=null;
 const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const csrf=()=>sessionStorage.getItem('bala-account-csrf')||'';
 const createStatus=el('p');createStatus.id='managed-create-status';createStatus.setAttribute('role','alert');$('story-form').querySelector('[type=submit]').before(createStatus);
 const signIn=el('a','Sign in to create your story');signIn.id='managed-sign-in';signIn.href='account.html?from=main';signIn.className='button subtle';signIn.hidden=true;createStatus.after(signIn);
 const createMessage=text=>{createStatus.textContent=text;};

 async function api(path,body){const r=await fetch('/api/accounts/'+path,{credentials:'same-origin',cache:'no-store',...(body===undefined?{signal:AbortSignal.timeout(15000)}:{}),method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Bala-CSRF':csrf()},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok){const error=Error(d.error||'This story step could not be completed.');error.status=r.status;throw error;}return d;}
 const section=el('section');section.id='managed-book';section.className='screen';section.hidden=true;
 section.innerHTML='<div class="page-heading"><span class="overline">YOUR STORYBOOK</span><h1 id="managed-title">Creating your storybook</h1><p id="managed-progress" role="status" aria-live="polite"></p></div><div id="managed-details" hidden><p id="managed-classification"></p><p id="managed-provenance" class="notice" hidden></p><div id="managed-pages" role="region" aria-label="Complete illustrated storybook and source notes"></div><div id="managed-audio-area" hidden><h2>Narration preview</h2><audio id="managed-audio" controls preload="none"></audio></div><details><summary>Quality checks and source notes</summary><div id="managed-review-notes"></div></details><div id="managed-actions"><label for="managed-revision">What would you like changed?</label><textarea id="managed-revision" maxlength="2000" rows="3"></textarea><div class="managed-actions"><button type="button" id="managed-approve" class="button primary">Approve Book</button><button type="button" id="managed-change" class="button subtle">Request Changes</button><button type="button" id="managed-reject" class="button subtle">Reject</button></div></div><div id="managed-delivery" hidden><a id="managed-pdf" class="button primary">Download PDF</a><a href="#shelf" class="button subtle">My Storybooks</a></div></div><button id="managed-refresh" type="button" class="button subtle">Check progress</button><a href="#create" class="text-button">Story choices</a>';
 document.querySelector('main').append(section);routes.push('managed-book');
 const cancel=el('button','Cancel story creation');cancel.type='button';cancel.className='button subtle';cancel.hidden=true;section.append(cancel);
 const working=new Set(['queued','validating','retrieving','writing','reviewing','correcting','preparing_audio']);
 cancel.onclick=async()=>{if(!currentRun||!confirm('Cancel this story request? An AI call already sent may still use provider credits.'))return;cancel.disabled=true;try{await api('runs/'+encodeURIComponent(currentRun)+'/cancel',{});clearTimeout(poll);await check();}catch(e){status(e.message);}finally{cancel.disabled=false;}};
 const resume=el('button','Continue saved request');resume.id='managed-resume';resume.type='button';resume.className='button subtle';resume.hidden=true;section.append(resume);
 resume.onclick=async()=>{if(!currentRun||!confirm('Continue the remaining work for this request? Provider credits may be used.'))return;resume.disabled=true;try{await api('runs/'+encodeURIComponent(currentRun)+'/resume',{});resume.hidden=true;await check();}catch(e){status(e.message);}finally{resume.disabled=false;}};
 const sharing=el('div');sharing.className='managed-sharing';sharing.setAttribute('role','group');sharing.setAttribute('aria-label','Future story sharing options');
 const sharingNote=el('p','Share your story · sharing options coming soon');sharingNote.id='managed-sharing-note';sharing.append(sharingNote);
 const shareIcons={
  Facebook:'<path fill="currentColor" d="M14 22v-9h3l.5-4H14V7c0-1.2.4-2 2-2h2V1.4A24 24 0 0 0 15 1c-3 0-5 1.8-5 5v3H7v4h3v9z"/>',
  Instagram:'<rect x="3" y="3" width="18" height="18" rx="5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="17.5" cy="6.5" r="1.2" fill="currentColor"/>',
  WhatsApp:'<path d="M20.5 11.5a8.5 8.5 0 0 1-12.7 7.4L3 20l1.2-4.6a8.5 8.5 0 1 1 16.3-3.9Z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m8 7 2 3-1 1c1 2 2 3 4 4l1-1 3 1c-.3 2-1.7 3-3.5 2-4-1.5-7-4.5-7-7C6.5 8.5 7 7.5 8 7Z" fill="currentColor"/>',
  YouTube:'<rect x="1" y="4" width="22" height="16" rx="5" fill="currentColor"/><path d="m10 8 7 4-7 4Z" fill="white"/>'
 };
 for(const platform of ['Facebook','Instagram','WhatsApp','YouTube']){
  const button=el('button');button.type='button';button.className='button subtle';
  const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');icon.setAttribute('focusable','false');icon.innerHTML=shareIcons[platform];button.append(icon,el('span',platform));
  button.dataset.platform=platform.toLowerCase();button.title=platform+' sharing is coming soon';button.setAttribute('aria-describedby','managed-sharing-note');button.onclick=()=>{sharingNote.textContent='Share your story · sharing options coming soon';};sharing.append(button);
 }
 $('managed-delivery').append(sharing);
 const status=text=>$('managed-progress').textContent=text;
 const labels={queued:'Your story request is saved.',validating:'Checking your story preferences.',retrieving:'Finding eligible source material.',writing:'Writing your story.',reviewing:'Checking story quality and safety.',correcting:'Making a story correction.',preparing_audio:'Assembling your book and preparing requested narration.',assets_pending:'The book is incomplete. Required pictures or narration are not ready yet.',awaiting_parent_approval:'Your complete storybook is ready for your review.',complete:'Your approved storybook is saved.',cancelled:'This story was rejected or cancelled. No final outputs were released.',evidence_blocked:'We could not confirm suitable evidence or pass the required checks. Your selected story type has not been changed.',failed:'This story could not be completed. Your saved request is retained.',outcome_unknown:'A service result needs confirmation before we can continue.',awaiting_clarification:'A story preference needs clarification.'};
 function sync(){if(!enabled)return;if(accountBalance){document.querySelectorAll('[data-demo-balance]').forEach(n=>n.textContent=accountBalance.starter_available+' free stories available');document.querySelectorAll('[data-demo-paid]').forEach(n=>n.textContent=accountBalance.weekly_available+' weekly stories available');}if(!children.length)return;const select=$('demo-studio-child');select.replaceChildren(...children.map(c=>{const n=el('option',c.name+' · age '+c.age);n.value=c.id;return n;}));select.value=activeChild;$('demo-family-pill').textContent=(children.find(c=>c.id===activeChild)?.name||'Reader')+' · Reader';}
 function applyChild(){const child=children.find(c=>c.id===activeChild);if(child)$('age').selectedIndex=child.age<5?0:child.age<7?1:2;sync();}
 $('demo-studio-child').addEventListener('change',event=>{if(!enabled)return;event.stopImmediatePropagation();activeChild=event.target.value;sessionStorage.setItem('bala-managed-child',activeChild);applyChild();},true);
 const oldRender=DemoExperience.render;DemoExperience.render=function(target){if(enabled&&target==='family'){location.assign('account.html?from=main');return;}if(enabled&&target==='dashboard'){go('create');return;}oldRender(target);sync();if(enabled&&target==='shelf')loadLibrary().catch(e=>status(e.message));};
 async function loadLibrary(){let host=$('managed-library');if(!host){host=el('section');host.id='managed-library';$('shelf').prepend(host);}host.replaceChildren(el('p','Loading your saved storybooks...'));try{const [data,me]=await Promise.all([api('library'),api('me')]);host.replaceChildren(el('h1','My Storybooks'));
  const profile=me.profile||{},details=el('details');details.id='managed-parent-details';details.className='panel';details.append(el('summary','Parent account · '+(profile.display_name||profile.login||'Your details')));
  for(const [label,value] of [['Name',profile.display_name||'Not provided'],['Email',profile.email||'Not provided'],['Sign-in login',profile.login||'Unavailable']])details.append(el('p',label+': '+value));
  const edit=el('a','Edit parent profile');edit.className='button subtle';edit.href='account.html#parent-profile';details.append(edit);host.append(details);
  if(!data.books.length){host.append(el('p','Your completed account storybooks will appear here.'));return;}$('shelf-empty').hidden=true;for(const book of data.books){const card=el('article');card.className='panel managed-library-card';card.append(el('h3',book.title||'Saved storybook'),el('p','Saved '+new Date(book.created_at*1000).toLocaleString()));const button=el('button','Open storybook');button.type='button';button.className='button primary';button.onclick=()=>open(book.run_id);card.append(button);host.append(card);}}catch(e){host.replaceChildren(el('p','Saved storybooks could not load. '+e.message));throw e;}}
 function showReview(r){currentReview=r;const complete=r.status==='complete';$('managed-refresh').hidden=complete;section.querySelector('a[href="#create"]').hidden=complete;status(labels[r.status]||'Preparing your storybook.');if(r.errors?.length){const code=r.errors[r.errors.length-1].code;status(code==='rate_limited'?'Groq is temporarily rate-limited. Your request is retained. No story has been saved; please try again later.':code==='output_validation'?'The AI response did not pass the required format or evidence checks. No failed draft is offered for saving.':(r.errors[r.errors.length-1].parent_message||labels[r.status]));}if(r.incomplete_reasons?.length)status($('managed-progress').textContent+' '+r.incomplete_reasons.join(' '));$('managed-details').hidden=!r.preview;$('managed-delivery').hidden=!complete;$('managed-actions').hidden=!r.approval_ready;
  if(r.draft_preview&&!r.preview){
   const draft=r.draft_preview;status(r.draft_warning);$('managed-title').textContent=draft.title+' — Unapproved draft';
   $('managed-details').hidden=false;$('managed-actions').hidden=true;$('managed-delivery').hidden=true;$('managed-audio-area').hidden=true;$('managed-provenance').hidden=true;
   $('managed-classification').textContent='Parent preview · QA incomplete · Not saved';
   const host=$('managed-pages');host.dataset.version='';host.dataset.loaded='false';host.replaceChildren();for(const page of draft.pages){const part=el('section');part.append(el('h2','Page '+page.page_number),el('p',page.text));host.append(part);}
   host.append(el('h2','Moral'),el('p',draft.moral),el('h2','Parent question'),el('p',draft.parent_question));
   const saveNote=el('aside');const unavailableSave=el('button','Save Book');unavailableSave.type='button';unavailableSave.className='button primary';unavailableSave.disabled=true;unavailableSave.title='Complete quality and safety checks before saving';saveNote.append(unavailableSave,el('p','Saving is unavailable because this draft has not passed its required checks. The findings are listed below.'));host.append(saveNote);
   const notes=$('managed-review-notes');notes.replaceChildren(...(r.draft_issues||[]).map(issue=>el('p',issue)));return;
  }
  cancel.hidden=!working.has(r.status);
  if(r.errors?.some(e=>e.code==='generation_limit_reached'))api('operation-usage').then(usage=>{if(currentReview===r)status('Your AI-use limit has been reached ('+usage.used+'/'+usage.limit+'). The daily allowance resets '+new Date(usage.resets_at*1000).toLocaleString()+'. Story credits are separate.');}).catch(()=>{});
  if(!r.preview){$('managed-title').textContent=({failed:'Your story needs attention',evidence_blocked:'A suitable source is not available',cancelled:'Story request closed',outcome_unknown:'Confirming the service result',awaiting_clarification:'Please clarify your story request',assets_pending:'Your book is not ready yet'})[r.status]||'Creating your storybook';return;}
  $('managed-title').textContent=r.draft.title;$('managed-classification').textContent=(r.draft.fiction_disclosure||r.draft.classification)+(r.duration_notice?' — '+r.duration_notice:'');
  if($('managed-pages').dataset.version!==r.book_hash){$('managed-pages').dataset.loaded='false';$('managed-pages').dataset.version=r.book_hash;renderPages(r.html,r.book_hash);}$('managed-provenance').hidden=r.provenance!=='provider_synthetic';$('managed-provenance').textContent='Offline integration fixture. This preview does not certify actual story, image or audio quality.';
  $('managed-audio-area').hidden=!r.audio_available;
  if(r.audio_available)$('managed-audio').src='/api/accounts/runs/'+encodeURIComponent(currentRun)+'/'+(r.status==='complete'?'audio':'audio-preview')+'?book_hash='+encodeURIComponent(r.book_hash);
  const notes=$('managed-review-notes');notes.replaceChildren();for(const name of ['factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety'])notes.append(el('p',name.replaceAll('_',' ')+': '+r.review[name].status+' — '+r.review[name].reason));
  if(r.status==='complete')$('managed-pdf').href='/api/accounts/runs/'+encodeURIComponent(currentRun)+'/pdf?book_hash='+encodeURIComponent(r.book_hash);
 }
 function renderPages(html,version){
  const host=$('managed-pages'),doc=new DOMParser().parseFromString(html,'text/html');
  const allowed=new Set(['H1','H2','P','SECTION','IMG','FOOTER','ASIDE','OL','LI']);
  function copy(node){
   if(node.nodeType===Node.TEXT_NODE)return document.createTextNode(node.textContent);
   if(node.nodeType!==Node.ELEMENT_NODE||!allowed.has(node.tagName))return document.createDocumentFragment();
   const result=document.createElement(node.tagName.toLowerCase());
   if(node.tagName==='IMG'){
    const source=node.getAttribute('src')||'';
    if(!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(source))throw Error('A page picture is not available.');
    result.src=source;result.alt=node.getAttribute('alt')||'Story scene';result.loading='eager';
   }else node.childNodes.forEach(child=>result.append(copy(child)));
   return result;
  }
  let firstPage=false;const content=[...doc.body.childNodes].filter(node=>{if(node.nodeType===Node.ELEMENT_NODE&&node.tagName==='SECTION')firstPage=true;return firstPage;});if(!firstPage)throw Error('The book pages are incomplete.');host.replaceChildren(...content.map(copy));
  const notes=[...host.querySelectorAll(':scope > aside')];
  if(notes.length){const disclosure=el('details'),summary=el('summary','Source and adaptation notes');disclosure.append(summary);notes[0].before(disclosure);notes.forEach(note=>disclosure.append(note));}
  Promise.all([...host.querySelectorAll('img')].map(image=>image.decode())).then(()=>{
   if(host.dataset.version===version)host.dataset.loaded='true';
  }).catch(()=>{if(host.dataset.version===version){$('managed-actions').hidden=true;status('A picture could not be displayed. Check progress before approving.');}});
 }
 async function check(){if(!currentRun)return;const requestedRun=currentRun;try{const r=await api('runs/'+encodeURIComponent(requestedRun)+'/complete-review');if(requestedRun!==currentRun)return;$('managed-refresh').textContent='Check progress';showReview(r);if(['queued','validating','retrieving','writing','reviewing','correcting','preparing_audio'].includes(r.status)){clearTimeout(poll);poll=setTimeout(check,1000);}}catch(e){if(requestedRun!==currentRun)return;$('managed-title').textContent='Your storybook could not be opened';status(e.message);$('managed-refresh').textContent='Try again';$('managed-refresh').hidden=false;}}
 async function open(run){clearTimeout(poll);currentRun=run;currentReview=null;sessionStorage.setItem('bala-complete-run',run);const url=new URL(location.href);url.searchParams.set('run',run);history.replaceState(null,'',url);$('managed-title').textContent='Opening your storybook';status('Loading your saved story and review status.');$('managed-details').hidden=true;$('managed-actions').hidden=true;$('managed-delivery').hidden=true;$('managed-refresh').hidden=true;go('managed-book');await check();}
 $('managed-refresh').onclick=check;
 const originalCheck=check;
 check=async function(){await originalCheck();if(!currentRun)return;const run=currentRun;try{const recovery=await api('runs/'+encodeURIComponent(run)+'/recovery');if(run!==currentRun)return;resume.hidden=!recovery.can_resume;resume.title=recovery.reason;if(recovery.can_resume||recovery.requires_attention){clearTimeout(poll);status(recovery.reason);}if(recovery.requires_attention&&!currentReview?.preview&&!currentReview?.draft_preview)$('managed-title').textContent='This request needs attention';}catch(e){resume.hidden=true;}};
 async function decision(action){if(!currentReview?.book_hash)return;const buttons=['managed-approve','managed-change','managed-reject'].map($);buttons.forEach(b=>b.disabled=true);try{await api('runs/'+encodeURIComponent(currentRun)+'/book-decision',{book_hash:currentReview.book_hash,action,request_key:crypto.randomUUID(),notes:$('managed-revision').value});$('managed-audio').pause();await check();api('balance').then(value=>{accountBalance=value;sync();}).catch(()=>{});}catch(e){status(e.message);}finally{buttons.forEach(b=>b.disabled=false);}}
 $('managed-approve').onclick=()=>decision('approve');$('managed-change').onclick=()=>decision('request_changes');$('managed-reject').onclick=()=>decision('reject');
 $('story-form').addEventListener('submit',async event=>{
  if(!enabled)return;event.preventDefault();event.stopImmediatePropagation();
  const submit=$('story-form').querySelector('[type=submit]');submit.disabled=true;createMessage('Checking your preferences...');
  try{
   if(!ready)await connect();
   if(!ready)throw Error(connectionError||'Story creation could not connect. Your preferences are retained.');
   if($('story-mode').value!=='original'&&globalThis.HostedSourceUI)await HostedSourceUI.beforeStory();
   if(!csrf())throw Error('Sign in to your account in this tab before starting an account story.');
   const child=children.find(c=>c.id===activeChild);if(!child)throw Error('Add a child profile to your account before creating a story.');
   if(!selectedStoryCharacter)throw Error('Choose the character you want in the story.');
   const selectedBand=$('age').selectedIndex,childBand=child.age<5?0:child.age<7?1:2;if(selectedBand!==childBand)throw Error('The selected age does not match this child profile. Confirm the reader or age.');
   const voice=$('voice').selectedOptions[0],audio=Boolean(voice?.value);
   const request={child_profile_id:child.id,age:child.age,required_character_ids:[selectedStoryCharacter.id],objective:$('story-value').value,language:$('language').value,target_minutes:parseInt($('duration').value,10),mood:$('mood').value,story_type:{original:'original_inspired',traditional:'traditional_retelling',adapted:'adapted_retelling'}[$('story-mode').value],idea:$('idea').value.trim(),exclusions:$('exclusions').value.trim()?[$('exclusions').value.trim()]:[],narration_requested:audio};
   const options={selected_source_ids:$('story-mode').value==='original'?[]:selectedSourceIds('story-source-options'),audio_processing_consent:audio&&$('managed-audio-consent').checked};
   if(audio){options.voice_id=voice.value;options.voice_model=voice.dataset.model;}
   if(document.body.dataset.localRamayan==='true'){const preflight=await api('preflight',request);if(preflight.status!=='READY')throw Error(preflight.message);}
   createMessage('Creating your story. Writing and QA may take a moment.');creationKey=creationKey||crypto.randomUUID();const result=await api('runs',{request_key:creationKey,request,options,provider_mode:document.body.dataset.localRamayan==='true'?'groq':'auto'});creationKey=null;await open(result.run_id);
  }catch(e){if(e.status===400||e.status===422)creationKey=null;if(e.status===401){ready=false;signIn.hidden=false;createMessage('Please sign in again to create your story. Your choices are retained.');}else createMessage(e.message);createStatus.scrollIntoView({block:'center'});toast(e.message);}finally{submit.disabled=false;}
 },true);
 async function initialize(){try{const h=await fetch('/api/health',{cache:'no-store'}).then(r=>r.json());if(!h.singleCompleteReviewEnabled)return;enabled=true;document.body.classList.add('managed-account-mode');$('story-form').querySelector('[type=submit]').textContent='Create Story';$('demo-story-mode').checked=false;$('demo-story-mode').closest('label').hidden=true;const session=await api('csrf');sessionStorage.setItem('bala-account-csrf',session.csrf);const [caps,profiles,balance]=await Promise.all([api('capabilities'),api('children'),api('balance')]);accountBalance=balance;children=profiles.children;const saved=sessionStorage.getItem('bala-managed-child');activeChild=children.some(c=>c.id===saved)?saved:children[0]?.id;
  $('demo-story-mode').checked=false;$('demo-story-mode').closest('label').hidden=true;const voice=$('voice');voice.replaceChildren();const none=el('option','Read without narration');none.value='';voice.append(none);for(const v of caps.voice_options){const n=el('option',v.label);n.value=v.voice_id;n.dataset.model=v.model_id;voice.append(n);}
  const studioLink=document.querySelector('[data-nav="dashboard"]');if(studioLink){studioLink.href='#create';studioLink.dataset.nav='create';}
  const consentLabel=el('label');consentLabel.className='check-row';const checkbox=el('input');checkbox.type='checkbox';checkbox.id='managed-audio-consent';consentLabel.append(checkbox,el('span',caps.narration_after_approval?'Prepare narration only after I approve the exact story script. Voice-service credits may then be used.':'Prepare narration with my story when selected. This may use voice-service credits before final approval.'));voice.parentNode.append(consentLabel);
  if(caps.narration_enabled===false||caps.voice_options.length===0){
   voice.value='';voice.hidden=true;consentLabel.hidden=true;
   const label=document.querySelector('label[for="voice"]');if(label){label.hidden=true;const heading=label.previousElementSibling;if(heading?.classList.contains('step-heading'))heading.hidden=true;}
  }
  if(caps.source_mode==='local_ramayan_text_only'){
   document.body.dataset.localRamayan='true';
   $('sample-cast').hidden=true;
   let limits=$('ramayan-available-choices');if(!limits){limits=el('p','Available now: Rama or Hanuman; Kindness or Sharing; a 3-minute adapted retelling in English. Other choices are unavailable in this release. Your idea can include Guha for the welcome episode or Lakshmana for the Hanuman meeting.');limits.id='ramayan-available-choices';limits.className='notice';$('story-mode').closest('.form-grid').before(limits);}
   document.querySelectorAll('#story-form .sample-cast-heading,#story-form .character-grid').forEach(n=>n.hidden=true);
   document.querySelectorAll('#story-form p').forEach(p=>{if(p.textContent.includes('Choose a character for an original imagined story'))p.textContent='Select Rama or Hanuman above. Only matching Ramayan passages are used; your finished story appears after writing and quality checks.';});
   if(caps.parent_save_flow){$('managed-approve').textContent='Save Book';$('managed-reject').textContent='Reject Draft';labels.awaiting_parent_approval='Your draft is ready. Read it, then save it or reject it.';labels.complete='Your story is saved. You can download the PDF.';}
   const note=el('p','Choose Rama or Hanuman and describe your story. Available episodes include Rama’s welcome with Guha and Rama meeting Hanuman. The app selects a matching passage and checks it before writing. This edition has text pages.');note.className='notice';$('story-form').prepend(note);
   $('story-mode').value='adapted';$('duration').selectedIndex=0;chooseStoryCharacter('rama');
   [...$('story-mode').options].forEach(o=>o.disabled=!(o.value==='adapted'||(caps.original_fiction_enabled&&o.value==='original')));[...$('duration').options].forEach((o,i)=>o.disabled=i!==0);[...$('story-value').options].forEach(o=>o.disabled=!['Kindness','Sharing'].includes(o.value));
   document.querySelectorAll('[data-story-character]').forEach(button=>{if(!['rama','hanuman'].includes(button.dataset.storyCharacter)){button.disabled=true;button.title='More characters are coming soon. Rama and Hanuman are available now.';}});
   if(caps.original_fiction_enabled){
    let descriptionChoice=$('original-description-choice');
    if(!descriptionChoice){descriptionChoice=el('button','Use characters from my description');descriptionChoice.id='original-description-choice';descriptionChoice.type='button';descriptionChoice.className='button subtle';$('studio-character-gallery').before(descriptionChoice);descriptionChoice.onclick=()=>{chooseStoryCharacter('original-main');descriptionChoice.setAttribute('aria-pressed','true');};}
    function applyStoryMode(event){
     const original=$('story-mode').value==='original';descriptionChoice.hidden=!original;
     if(event){if(original)chooseStoryCharacter('original-main');else if(!['rama','hanuman'].includes(selectedStoryCharacter?.id))chooseStoryCharacter('rama');}
     document.querySelectorAll('[data-story-character]').forEach(button=>{button.disabled=!original&&!['rama','hanuman'].includes(button.dataset.storyCharacter);button.title=button.disabled?'Available for original fiction; this Ramayan passage does not support this character.':'';});
     [...$('story-value').options].forEach(o=>o.disabled=!original&&!['Kindness','Sharing'].includes(o.value));
     if($('story-value').selectedOptions[0]?.disabled)$('story-value').value='Kindness';
     $('ramayan-available-choices').textContent=original?'Original fiction: describe your own adventure and characters above. No Ramayan passages are used. Choose Kindness, Sharing, Patience or Honesty; this text edition uses a 3-minute target in English.':'Ramayan adaptation: Rama or Hanuman; Kindness or Sharing; a 3-minute English story. Available episodes are the welcome with Guha and the meeting with Hanuman and Lakshmana.';
     note.textContent=original?'Your description guides a newly imagined story. It is clearly labeled Original teaching-inspired fiction. Writing and independent QA use Groq; review the story before saving and downloading its PDF.':'Choose Rama or Hanuman and describe a supported episode. The app selects matching Ramayan evidence and checks it before writing. This edition has text pages.';
     $('idea').placeholder=original?'For example: A little rabbit and a bird learn to share fruit in a sunny garden.':'For example: Rama and Hanuman meet and show kindness.';
     descriptionChoice.setAttribute('aria-pressed',String(selectedStoryCharacter?.id==='original-main'));
    }
    if(!originalModeHandler){originalModeHandler=applyStoryMode;$('story-mode').addEventListener('change',event=>originalModeHandler(event));}else originalModeHandler=applyStoryMode;
    applyStoryMode();
   }
   $('story-source-options').closest('fieldset')?.setAttribute('hidden','');
   $('source-review-form').hidden=true;$('source-upload-form').hidden=true;$('source-url-form').hidden=true;
   $('source-journey-title').textContent='Your Ramayan story source';
   $('sources').querySelector('.source-journey-steps').hidden=true;
   $('sources').querySelector('.page-heading p').textContent='Stories use matching passages from your uploaded Ramayan. The welcome and first-meeting episodes are available; source and safety checks happen before your final review.';
   document.querySelectorAll('#story-form p').forEach(p=>{if(p.textContent.includes('Nebius uses'))p.textContent='Choose your preferences to create a source-grounded Rama story. Read and approve the finished story before saving.';});

  }
  $('story-form').querySelector('[type=submit]').textContent='Create Story';ready=true;connectionError='';signIn.hidden=true;createMessage('Ready to create your story.');applyChild();if(location.hash==='#family'){location.assign('account.html?from=main');return;}if(location.hash==='#dashboard')go('create');if(location.hash==='#shelf')await loadLibrary();if(location.hash==='#managed-book'){const savedRun=new URLSearchParams(location.search).get('run')||sessionStorage.getItem('bala-complete-run');if(savedRun)open(savedRun);}
 }catch(e){connectionError='Story creation could not connect. '+e.message+' Use the sign-in button below, then return to Story Studio.';signIn.hidden=false;createMessage(connectionError);toast(connectionError);}}
 async function connect(){if(!connecting)connecting=initialize().finally(()=>{connecting=null;});return connecting;}
 connect();
 globalThis.ManagedStoryUI={get enabled(){return enabled;},get ready(){return ready;},open,check};
})();
