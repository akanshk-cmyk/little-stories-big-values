/* Product simulation only. All commerce/profile state stays in the browser. */
(() => {
 'use strict';
 let store=null,editingChild=null;
 const screens=['dashboard','family','child-profiles','pricing','checkout','payment-success','payment-failure','billing'];
 const el=(tag,classes,text)=>node(tag,classes,text);
 const action=(label,handler,classes='button subtle',id)=>{const b=el('button',classes,label);b.type='button';if(id)b.id=id;b.addEventListener('click',handler);return b;};
 const date=value=>new Date(value).toLocaleString('en-US',{dateStyle:'medium',timeStyle:'short',timeZone:'America/Los_Angeles'});
 function safe(fn){try{return fn();}catch(error){toast(error.message);return null;}}
 function passBalance(s){
  const p=s.weeklyPass,active=p&&Date.now()+(s.demoClockOffsetMs||0)<Date.parse(p.expiresAt);
  const legacy=s.legacyStoryCredits?` ${s.legacyStoryCredits} earlier story credits also available.`:'';
  return (active?`${s.paidStoryCredits} of 10 weekly stories remaining. Pass ends ${date(p.expiresAt)}.`:p?'Weekly pass expired. Start a story to renew for $5 / 7 days.':'$5 / week for up to 10 stories after your 3 free starters.')+legacy;
 }
 function balance(s){return `${s.freeStoriesRemaining} of 3 free stories remaining`;}
 function options(select,values,value){select.replaceChildren(...values.map(v=>{const o=el('option','',v);o.value=v;return o;}));if(value!==undefined)select.value=value;}
 function childOptions(select){const s=store.state;select.replaceChildren(...s.children.map(c=>{const o=el('option','',`${c.avatar} ${c.nickname} · age ${c.age}`);o.value=c.id;return o;}));select.value=s.activeChildId;}
 function updateChrome(){
  if(!store)return;const s=store.state,c=store.active();
  document.querySelectorAll('[data-demo-balance]').forEach(n=>n.textContent=balance(s));
  document.querySelectorAll('[data-demo-paid]').forEach(n=>n.textContent=passBalance(s));
  $('demo-family-pill').textContent=`${c.avatar} ${c.nickname} · Reader`;
  childOptions($('demo-studio-child'));childOptions($('demo-dashboard-child'));
  $('demo-dashboard-preferences').textContent=`${c.language} · ${c.category} · ${c.favorites.join(', ')||'No favorite chosen'}. Illustration preference: ${c.illustrationStyle} (saved preference only).`;
  $('demo-persistence-note').hidden=store.persistenceAvailable;
 }
 function applyChild(){
  const c=store.active();$('age').selectedIndex=c.age<5?0:c.age<7?1:2;$('language').value='English';
  const favorite=c.favorites.find(name=>['Krishna','Ganesha','Hanuman'].includes(name));
  if(typeof chooseStoryCharacter==='function')chooseStoryCharacter(favorite?favorite.toLowerCase():null);
 }
 function start(options={}){ store.track('story_creation_started',{flow_id:'flow-'+crypto.randomUUID()});$('demo-story-mode').checked=options.demo===true;$('demo-story-mode').dispatchEvent(new Event('change'));applyChild();go('create'); }
 function profileCard(c,manage=false){
  const card=el('article',`demo-profile-card ${store.state.activeChildId===c.id?'is-active':''}`);
  card.dataset.childId=c.id;
  card.append(el('span','demo-profile-avatar',c.avatar),el('h3','',c.nickname),el('p','small',`Age ${c.age} · ${c.language}`),el('p','small muted',`Favorite: ${c.favorites.join(', ') || c.category}`));
  if(store.state.activeChildId===c.id)card.append(el('p','account-selected','✓ Active reader'));
  const read=action('Read with this profile',()=>safe(()=>{store.selectChild(c.id);applyChild();go('dashboard');}),'button subtle');read.dataset.readChild=c.id;card.append(read);
  if(manage)card.append(action('Edit preferences',()=>editChild(c.id),'text-button'));
  return card;
 }
 function bookCard(story){
  const card=el('article','demo-book-card'),art=el('div',`demo-book-art demo-art-${story.config.sampleKey}`),name=story.config.character;
  if(name){const image=el('img');image.src=`assets/characters/${name.toLowerCase()}.jpg`;image.alt=`Static ${name} character artwork, not an illustration of this story's scene`;image.loading='lazy';art.append(image);}
  else art.append(el('span','demo-book-symbol',story.child.avatar));
  art.append(el('span','demo-art-label','PREWRITTEN STORY'));
  card.append(art,el('span','tag',story.creditType==='free'?'Complimentary prewritten story':story.pricingModel==='legacy_per_story'?'Earlier story credit':'Weekly pass story'),el('h3','',story.title),el('p','small muted',`${story.child.nickname} · Six text pages · v${story.version}`),action('Open Story Theater →',()=>openStory(story.id),'button subtle'));
  return card;
 }
 function renderBooks(host,stories){host.replaceChildren();if(!stories.length)host.append(el('p','demo-empty','Your story collection starts here. Explore a prewritten story or create a new one in Story Studio.'));stories.forEach(story=>host.append(bookCard(story)));}
 function renderDashboard(){
  const s=store.state;const dots=$('demo-credit-dots');dots.replaceChildren();for(let i=0;i<3;i++)dots.append(el('span',i<s.freeStoriesRemaining?'available':'used',i<s.freeStoriesRemaining?'✦':'✓'));
  renderBooks($('demo-dashboard-books'),s.stories.slice(-3).reverse());
  const pending=s.pending,active=pending&&!['completed','canceled','creation_canceled'].includes(pending.status);
  $('demo-resume-area').hidden=!active;
  if(active)$('demo-resume-message').textContent=`Continue ${pending.config.title}. Your completed-story balance has not been used for this request.`;
 }
 function renderFamily(){
  const s=store.state,summary=$('demo-parent-summary');summary.replaceChildren(el('span','demo-profile-avatar',s.avatar),el('span','overline','PARENT PROFILE'),el('h2','',s.parentName.replace(/\s*·\s*Demo Parent$/i,'')),el('p','',s.email));
  $('demo-family-children').replaceChildren(...s.children.map(c=>profileCard(c,true)));
  const groups=new Map();for(const child of s.children){const key=child.nickname.trim().toLocaleLowerCase();groups.set(key,[...(groups.get(key)||[]),child]);}
  const repeated=[...groups.values()].filter(group=>group.length>1);
  for(const group of repeated)group.forEach((child,index)=>{
   const card=[...$('demo-family-children').children].find(n=>n.dataset.childId===child.id);
   card.append(el('p','small muted',`Profile ${index+1} of ${group.length} named ${child.nickname}`));
  });
  const note=$('account-duplicate-note');note.replaceChildren();
  if(repeated.length){note.append(document.createTextNode('Some profiles share a nickname. Each is a separate saved profile; none has been removed. '));note.append(action('Review same-name profiles',()=>{const ids=new Set(repeated.flat().map(c=>c.id));document.querySelectorAll('#demo-family-children article').forEach(card=>card.classList.toggle('account-review-match',ids.has(card.dataset.childId)));focusSection('account-family');},'text-button'));}
  const p=s.weeklyPass,active=p&&Date.now()+(s.demoClockOffsetMs||0)<Date.parse(p.expiresAt),credit=$('account-credit-summary');
  credit.replaceChildren(el('p','account-credit-number',`${s.freeStoriesRemaining} free stories remaining`),el('p','small',`${3-s.freeStoriesRemaining} free stories completed`),el('p','small',active?`${p.used} of ${p.limit} weekly stories used · ${s.paidStoryCredits} remaining`:p?'Weekly pass expired':'No active weekly pass'));
  $('account-credits').querySelector('[data-demo-paid]').textContent=active?`Valid until ${date(p.expiresAt)}.`:'The weekly allowance is shared by all child profiles.';
  $('account-pass').disabled=Boolean(active);$('account-pass').textContent=active?'Weekly pass active':s.freeStoriesRemaining>0?'Use your starter stories':p?'Renew Story Pass — $5':'Explore Story Pass — $5';
  const host=$('account-saved-books');host.replaceChildren();
  for(const story of s.stories.slice(-3).reverse()){
   const card=bookCard(story);card.querySelector('.demo-art-label').textContent='SAVED TEXT STORY';card.querySelector('.tag').textContent=story.creditType==='free'?'Complimentary story':'Story Pass';card.append(el('p','small',`${story.config.character || story.config.sampleKey} · Saved · ${date(story.createdAt)}`));host.append(card);
  }
  if(!s.stories.length)host.append(el('p','demo-empty','Your storybook collection starts here. Create your first story to begin.'));
  const drafts=typeof savedBooks==='function'?savedBooks().length:0;$('account-draft-summary').textContent=drafts?`${drafts} other saved browser draft${drafts===1?'':'s'} in My Storybooks.`:'';
  const reader=store.active();$('account-reading-settings').replaceChildren(el('p','',`${reader.nickname} · ${reader.language} · ${reader.category}`),el('p','small muted',`Artwork preference: ${reader.illustrationStyle}. Preferences apply when this reader is selected.`));
  renderBilling();
 }
 function focusSection(id){const section=$(id);if(section){section.focus({preventScroll:true});section.scrollIntoView({block:'start',behavior:'instant'});}}
 function editChild(id=null){
  editingChild=id;const c=id?store.state.children.find(c=>c.id===id):{nickname:'',age:5,language:'English',avatar:'🌼',favorites:[],category:'Good Habits & Values',illustrationStyle:'Soft storybook'};
  $('demo-child-form').hidden=false;$('account-close-child').hidden=false;
  $('demo-child-id').value=id||'';$('demo-child-form-title').textContent=id?`Edit ${c.nickname}'s profile`:'Add child profile';
  $('demo-child-name').value=c.nickname;$('demo-child-age').value=String(c.age);$('demo-child-avatar').value=c.avatar;$('demo-child-language').value='English';$('demo-child-category').value=c.category;$('demo-child-style').value=c.illustrationStyle;
  document.querySelectorAll('[data-demo-favorite]').forEach(input=>input.checked=c.favorites.includes(input.value));
  $('demo-child-status').textContent='';
  $('demo-child-name').focus();
 }
 
 function summary(p){
  const box=el('div','demo-order-summary');box.append(el('span','overline','YOUR NEXT LITTLE ADVENTURE'),el('h2','',p.config.title));
  const list=el('dl','demo-summary-list');[['Reader',`${p.child.nickname} · age ${p.child.age}`],['Format',p.config.format],['Plan','Family weekly pass: up to 10 stories / 7 days'],['Renewal','Manual only; no rollover'],['Price','$5.00 / week'],['Subtotal','$5.00'],['Total','$5.00']].forEach(([key,value])=>{list.append(el('dt','',key),el('dd','',value));});
  box.append(list,el('p','demo-simulation-notice','Payments are simulated. No money will be charged.'),el('p','small muted','No taxes are calculated or collected. No real card details are needed.'));return box;
 }
 function emptyCheckout(host){host.replaceChildren(el('p','notice','There is no active checkout. Start a new story in Story Studio.'),action('My Story Studio',()=>go('dashboard'),'button primary'));}
 function cancelCheckout(){safe(()=>{const p=store.state.pending;store.payment('cancel',p.id);$('demo-story-mode').checked=true;$('story-mode').value='original';$('story-value').value=p.config.sampleKey.charAt(0).toUpperCase()+p.config.sampleKey.slice(1);if(store.state.children.some(c=>c.id===p.child.id)){store.selectChild(p.child.id);applyChild();}if(typeof chooseStoryCharacter==='function')chooseStoryCharacter(p.config.character?.toLowerCase()||null);go('create');toast('Checkout canceled. Story choices preserved; balances unchanged.');});}
 function renderCheckout(){
  const p=store.state.pending,host=$('demo-checkout-content');if(!p||p.status!=='pending'){emptyCheckout(host);return;}
  const layout=el('div','demo-checkout-layout'),methods=el('section','panel demo-methods');
  methods.append(el('span','overline','STORY PASS'),el('h2','','Choose a payment option'));
  ['Card payment ? simulated','Digital wallet ? simulated','Guest checkout ? simulated'].forEach((method,index)=>{const label=el('label','demo-method'),radio=el('input');radio.type='radio';radio.name='demo-payment-method';radio.value=String(index);radio.checked=index===0;label.append(radio,el('span','',method));methods.append(label);});
  methods.append(el('p','small muted','Payments are simulated. No money will be charged. No payment details are collected.'));
  const buttons=el('div','demo-checkout-actions');
  buttons.append(action('Confirm Story Pass',event=>safe(()=>{event.currentTarget.disabled=true;store.payment('success',p.id);go('payment-success');}),'button primary','demo-pay-success'),action('Show declined payment',()=>safe(()=>{store.payment('failure',p.id);go('payment-failure');}),'button subtle','demo-pay-failure'),action('Cancel Checkout',cancelCheckout,'text-button','demo-checkout-cancel'));
  methods.append(buttons);layout.append(summary(p),methods);host.replaceChildren(layout);
 }
 function renderSuccess(){
  const s=store.state,p=s.pending,t=p&&s.transactions.find(t=>t.id===p.receiptId),host=$('demo-success-content');
  if(!p||!t||p.paymentStatus!=='simulated_success'){emptyCheckout(host);return;}
  const card=el('section','panel demo-outcome demo-success');card.append(el('span','demo-outcome-icon','✓'),el('span','overline','STORY PASS CONFIRMED'),el('h1','','Your Weekly Story Pass Is Ready!'),el('p','',`${p.config.title} · ${p.child.nickname}`));
  const list=el('dl','demo-summary-list');[['Amount','$5.00 (simulated)'],['Receipt reference',t.id],['Purchased',date(t.createdAt)],['Allowance',t.pricingModel==='weekly_10'?'Up to 10 stories across all family profiles':'Earlier story credit'],['Pass ends',t.expiresAt?date(t.expiresAt):'Legacy credit has no weekly expiry']].forEach(([k,v])=>list.append(el('dt','',k),el('dd','',v)));card.append(list,el('p','demo-simulation-notice','Demo transaction — No real payment was processed or charged.'));
  const controls=el('div','demo-actions');controls.append(action(p.status==='completed'?'Open My Story':'Create My Story',()=>safe(()=>{if(p.status==='completed')openStory(p.storyId);else{store.prepare(p.id);creationDialog();}}),'button primary','demo-create-paid-story'),action('View My Storybooks',()=>go('shelf'),'button subtle'));
  card.append(controls);host.replaceChildren(card);
 }
 function renderFailure(){
  const p=store.state.pending,host=$('demo-failure-content');if(!p||p.status!=='payment_failed'){emptyCheckout(host);return;}
  const card=el('section','panel demo-outcome demo-failure');card.append(el('span','demo-outcome-icon','☁'),el('span','overline','SIMULATED OUTCOME'),el('h1','','Payment was declined'),el('p','','The Story Pass was not added. Your existing story balance is unchanged.'),el('p','demo-simulation-notice','Payments are simulated. No money will be charged.'));
  const buttons=el('div','demo-actions');buttons.append(action('Try Again',()=>safe(()=>{store.payment('retry',p.id);go('checkout');}),'button primary','demo-payment-retry'),action('Cancel',cancelCheckout,'button subtle','demo-failure-cancel'));card.append(buttons);host.replaceChildren(card);
 }
 function table(rows){
  const wrap=el('div','demo-table-wrap'),table=el('table','demo-history-table'),head=el('thead'),header=el('tr');
  ['Date','Item','Receipt reference','Amount','Status'].forEach(label=>{const th=el('th','',label);th.scope='col';header.append(th);});head.append(header);table.append(head);
  const body=el('tbody');rows.forEach(t=>{const tr=el('tr');[t.createdAt?date(t.createdAt):'Date unavailable',t.storyTitle,t.id,t.amount===0?'Free':`$${t.amount.toFixed(2)}`,t.status==='completed'?'Completed':'Confirmed (simulated)'].forEach(value=>tr.append(el('td','',value)));body.append(tr);});table.append(body);wrap.append(table);return wrap;
 }
 function renderBilling(){
  const rows=store.state.transactions.slice().reverse(),host=$('demo-billing-history');host.replaceChildren();
  if(rows.length)host.append(table(rows));else host.append(el('p','demo-empty','No billing activity yet. Your family receipts will appear here.'));
 }
 function makeSample(p){
  const n=p.child.nickname,companion=p.config.character||'Pip',key=p.config.sampleKey;
  const plots={
   kindness:[`${n} was making a paper garden. In this imagined story, ${companion} came to visit.`,`${n} noticed a friend looking for a place at the busy table.`,`${n} moved a few paper flowers aside. “There is room here,” ${n} said.`,`The friend sat down. ${n} offered a crayon and waited for the friend to choose a colour.`,`${companion} smiled as the friends added a sunny path to their picture.`,`${n} looked at the finished garden. A little space and a kind welcome had made the afternoon happier.`],
   sharing:[`${n} and ${companion} were making a pretend garden with wooden blocks. This is an imagined adventure.`,`${n} gathered the long blocks. A friend wanted to make a path, too.`,`“Which blocks would help?” ${n} asked. “Two long ones,” the friend said.`,`${n} passed over two long blocks and kept enough to finish a small path.`,`The friends joined their paths beside a pretend flower bed. ${companion} admired their idea.`,`${n} smiled. Sharing a few blocks had given everyone a new way to play together.`],
   patience:[`${n} and ${companion} spread paper on the garden table. In this imagined story, everyone wanted to paint.`,`A friend was using the only blue paint. ${n} wanted to paint a stream.`,`“May I have a turn when you finish?” ${n} asked. The friend nodded.`,`${n} chose yellow paint for a small sun while the friend finished a cloud.`,`“Your turn,” the friend said. ${n} added a winding blue stream.`,`${companion} admired the picture. Waiting had given ${n} time to add a lovely sun.`],
   honesty:[`${n} and ${companion} were decorating a paper basket. This is a newly imagined story.`,`${n} put a friend's blue ribbon beside the basket without asking. The friend began looking for it.`,`“I know where it is,” ${n} said. “I took it because I liked the colour.”`,`${n} gave the ribbon back. “Next time I will ask,” ${n} said.`,`The friends chose their ribbons together. ${companion} listened without blaming or shouting.`,`${n} added a yellow bow. Telling the truth had helped everyone understand and put things right.`]
  };
  return {pages:plots[key],moral:{kindness:'A small kind action can help someone feel welcome.',sharing:'Sharing gives everyone room to join in.',patience:'Waiting calmly can make room for a new idea.',honesty:'Tell the truth and help put things right.'}[key],discussionQuestion:'Which small choice helped the friends, and what could we try together?',classification:'Original teaching-inspired fiction',fictionDisclosure:'This is a prewritten demo story. Its characters, scenes and dialogue are imagined, not a traditional episode or scriptural quotation.'};
 }
 function requestStory(config){
  try{store.begin({sampleKey:config.value.toLowerCase(),character:config.storyCharacter?.name||null,mode:config.mode});continueRequest();}
  catch(error){toast(error.message);}
 }
 function creationDialog(){
  const p=store.state.pending;if(!p)return;
  $('demo-creation-title').textContent=p.config.title;
  $('demo-creation-summary').textContent=`For ${p.child.nickname}, age ${p.child.age}. ${p.config.format}. ${store.state.freeStoriesRemaining>0?'One complimentary credit':'One story from your existing allowance'} is used only after successful completion and saving. Your selected illustration style is a preference, not generated artwork.`;
  $('demo-creation-status').textContent=p.status==='creation_failed'?'This prewritten story could not open. No credit was used. Retry or cancel.':'';
  $('demo-complete-story').textContent=p.status==='creation_failed'?'Try opening the story again':'Open prewritten story';
  $('demo-complete-story').disabled=false;$('demo-fail-story').disabled=p.status==='creation_failed';
  if(!$('demo-creation-dialog').open)$('demo-creation-dialog').showModal();
 }
 function continueRequest(){
  const p=store.state.pending;if(!p){start();return;}
  if(p.status==='pending')go('checkout');else if(p.status==='payment_failed')go('payment-failure');else if(p.status==='weekly_limit'){go('pricing');toast('All 10 weekly stories are used. Wait until this pass ends to renew.');}else if(p.status==='paid_ready')go('payment-success');else if(['ready','creation_failed'].includes(p.status))creationDialog();else start();
 }
 function openStory(id){
  const story=store.state.stories.find(s=>s.id===id);if(!story){toast('This prewritten story could not be found.');return;}
  const book={id:story.id,key:story.config.sampleKey,pages:clonePages(story.pages),format:'text',version:story.version,ackVersion:null,request:{isDemo:true,childId:story.child.id},demoId:story.id,demoTitle:story.title,demoChild:story.child.nickname};
  store.track('storybook_opened',{story_id:story.id});store.track('story_page_viewed',{story_id:story.id,page_number:1});openBook(book.key,book);
 }
 const clonePages=pages=>pages.slice();
 function renderShelf(){
  renderBooks($('demo-shelf-books'),store.state.stories.slice().reverse());
  if(store.state.stories.length)$('shelf-empty').hidden=true;
 }
 function saveCurrent(book){
  try{const updated=store.edit(book.demoId,book.pages);current.version=updated.version;current.ackVersion=null;toast('Story edits saved in this browser. No additional credit used.');}catch(error){toast(error.message);}
 }
 function render(target){
  if(!store)return;updateChrome();
  if(target==='dashboard')renderDashboard();if(target==='family')renderFamily();if(target==='checkout')renderCheckout();if(target==='payment-success')renderSuccess();if(target==='payment-failure')renderFailure();if(target==='shelf')renderShelf();
 }
 function resetLocal(){if($('demo-creation-dialog').open)$('demo-creation-dialog').close();store.reset();if(current?.demoId)current=null;editChild();}
 function setup(){
  let storage=null;try{storage=localStorage;}catch{}
  store=new LittleStoriesDemo.DemoStore({storage});store.subscribe(()=>render(location.hash.slice(1)));
  const modeLabel=()=>{$('story-form').querySelector('[type=submit]').textContent=globalThis.ManagedStoryUI?.enabled?'Create Story':$('demo-story-mode').checked?'Open a prewritten story →':'Review my story request →';};
  $('demo-story-mode').addEventListener('change',modeLabel);modeLabel();
  options($('demo-parent-avatar'),LittleStoriesDemo.avatars);options($('demo-child-avatar'),LittleStoriesDemo.avatars);options($('demo-child-category'),LittleStoriesDemo.categories);
  const favorites=$('demo-child-favorites');LittleStoriesDemo.characters.forEach(name=>{const label=el('label','check-row'),input=el('input');input.type='checkbox';input.value=name;input.dataset.demoFavorite='';label.append(input,el('span','',name));favorites.append(label);});
  $('demo-start-story').addEventListener('click',()=>start());$('demo-pricing-start').addEventListener('click',()=>start({demo:true}));
  ['demo-studio-child','demo-dashboard-child'].forEach(id=>$(id).addEventListener('change',event=>safe(()=>{store.selectChild(event.target.value);applyChild();})));
  $('demo-edit-parent').addEventListener('click',()=>{const s=store.state;$('demo-parent-name').value=s.parentName.replace(/\s*?\s*Demo Parent$/i,'');$('demo-parent-email').value=s.email;$('demo-parent-avatar').value=s.avatar;$('demo-parent-form').hidden=false;$('demo-parent-name').focus();});
  $('demo-cancel-parent').addEventListener('click',()=>$('demo-parent-form').hidden=true);
  $('demo-parent-form').addEventListener('submit',event=>{event.preventDefault();try{store.parent({parentName:$('demo-parent-name').value,email:$('demo-parent-email').value,avatar:$('demo-parent-avatar').value});$('demo-parent-form').hidden=true;$('demo-parent-status').textContent='Profile saved in this browser.';}catch(error){$('demo-parent-status').textContent=error.message;}});
  $('demo-add-child').addEventListener('click',()=>{editChild();$('demo-child-name').focus();});
  $('demo-child-form').addEventListener('submit',event=>{event.preventDefault();try{const savedChild=store.child({nickname:$('demo-child-name').value,age:$('demo-child-age').value,language:$('demo-child-language').value,avatar:$('demo-child-avatar').value,category:$('demo-child-category').value,illustrationStyle:$('demo-child-style').value,favorites:[...document.querySelectorAll('[data-demo-favorite]:checked')].map(i=>i.value)},editingChild);editingChild=savedChild.id;$('demo-child-id').value=savedChild.id;applyChild();$('demo-child-status').textContent='Child profile saved and selected in this browser.';}catch(error){$('demo-child-status').textContent=error.message;}});
  document.querySelectorAll('[data-demo-advance-week]').forEach(button=>button.addEventListener('click',()=>safe(()=>{if($('demo-creation-dialog').open)$('demo-creation-dialog').close();store.advanceWeek();go('pricing');toast('Preview clock advanced by 7 days. No payment or renewal occurred.');})));
  $('demo-resume').addEventListener('click',continueRequest);
  $('demo-complete-story').addEventListener('click',()=>{
   const button=$('demo-complete-story');button.disabled=true;
   try{let p=store.state.pending;if(p.status==='creation_failed'){store.prepare(p.id);p=store.state.pending;}const story=store.complete(p.id,makeSample(p));$('demo-creation-dialog').close();openStory(story.id);toast('Prewritten story saved in this browser. No actual payment or AI call was made.');}
   catch(error){$('demo-creation-status').textContent=error.message;button.disabled=false;}
  });
  $('demo-fail-story').addEventListener('click',()=>safe(()=>{store.failCreation(store.state.pending.id);creationDialog();}));
  function cancelStory(){safe(()=>{store.cancelCreation(store.state.pending.id);$('demo-creation-dialog').close();toast('Story creation canceled. No story credit used.');});}
  $('demo-cancel-story').addEventListener('click',cancelStory);$('demo-creation-dialog').addEventListener('cancel',event=>{event.preventDefault();cancelStory();});
  document.querySelectorAll('[data-demo-reset]').forEach(button=>button.addEventListener('click',()=>{resetLocal();go('dashboard');toast('Browser data reset. Starting reader profiles and credits restored.');}));
  document.querySelectorAll('[data-account-section]').forEach(button=>button.addEventListener('click',()=>focusSection(button.dataset.accountSection)));
  $('account-close-child').addEventListener('click',()=>{$('demo-child-form').hidden=true;$('account-close-child').hidden=true;$('demo-add-child').focus();});
  $('account-edit-preferences').addEventListener('click',()=>editChild(store.state.activeChildId));
  $('account-pass').addEventListener('click',()=>safe(()=>{if(store.state.freeStoriesRemaining>0){start();return;}store.begin({sampleKey:'kindness',mode:'original'});continueRequest();}));
  $('account-reset').addEventListener('click',()=>{if(confirm('Reset this browser’s family profiles, stories, credits and billing history? This cannot be undone.')){resetLocal();$('demo-child-form').hidden=true;$('account-close-child').hidden=true;go('family');}});
  updateChrome();
 }
 const accountAliases={'child-profiles':'account-family',pricing:'account-credits',billing:'account-billing'};
 function resolveAccountRoute(){const section=accountAliases[location.hash.slice(1)];if(section){history.replaceState(null,'','#family');setTimeout(()=>focusSection(section),0);}}
 globalThis.DemoExperience={setup,render,start,requestStory,openStory,renderShelf,saveCurrent,screens,resetLocal,resolveAccountRoute,pageViewed(id,page){store.track('story_page_viewed',{story_id:id,page_number:page});},get store(){return store;}};
})();
