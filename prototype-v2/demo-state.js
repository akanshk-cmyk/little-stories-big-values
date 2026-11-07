/* Fictional, browser-only state. No network, payment provider or server entitlements. */
(() => {
 'use strict';
 const KEY='little-stories-family-demo-v1';
 const clone=value=>structuredClone(value);
 const uid=prefix=>`${prefix}-${crypto.randomUUID()}`;
 const text=(value,max=80)=>String(value||'').trim().slice(0,max);
 const WEEK=7*86400000;
 const clock=s=>Date.now()+(s.demoClockOffsetMs||0);
 function refresh(s){
  if(s.pricingVersion!==2){s.legacyStoryCredits=s.paidStoryCredits||0;s.weeklyPass=null;s.demoClockOffsetMs=0;s.pricingVersion=2;for(const t of s.transactions)if(t.status==='simulated_success'&&!t.pricingModel){t.pricingModel='legacy_per_story';t.type='Legacy demo story credit';}}
  const p=s.weeklyPass;
  s.paidStoryCredits=p&&clock(s)<Date.parse(p.expiresAt)?Math.max(0,p.limit-p.used):0;
  return s;
 }
 const available=s=>s.freeStoriesRemaining+s.paidStoryCredits+(s.legacyStoryCredits||0);
 const activePass=s=>s.weeklyPass&&clock(s)<Date.parse(s.weeklyPass.expiresAt);
 const categories=['Hindu Gods & Mythology','Indian Festivals','Good Habits & Values'];
 const characters=['Krishna','Ganesha','Hanuman','Rama','Sita','Saraswati','Durga','Lakshmi','Shiva','Parvati'];
 const avatars=['🌼','🌸','🌟','🦋','🌿','🦉'];
 function child(id,nickname,avatar){return {id,nickname,age:5,language:'English',avatar,favorites:['Krishna'],category:'Good Habits & Values',illustrationStyle:'Soft storybook'};}
 function seed(){return {schemaVersion:1,id:uid('demo-account'),parentName:'Asha · Demo Parent',email:'asha@example.test',avatar:'🌼',createdAt:'2026-01-01T12:00:00.000Z',isDemo:true,children:[child('child-mira','Mira','🌸'),child('child-veyd','Veyd','🌟')],activeChildId:'child-veyd',freeStoriesRemaining:3,paidStoryCredits:0,stories:[],transactions:[],pending:null};}
 function valid(s){return Boolean(s&&s.schemaVersion===1&&s.isDemo===true&&Number.isInteger(s.freeStoriesRemaining)&&s.freeStoriesRemaining>=0&&s.freeStoriesRemaining<=3&&Number.isInteger(s.paidStoryCredits)&&s.paidStoryCredits>=0&&Array.isArray(s.children)&&s.children.length&&s.children.some(c=>c.id===s.activeChildId)&&Array.isArray(s.stories)&&Array.isArray(s.transactions));}
 class DemoStore{
  constructor({storage=globalThis.localStorage,persist=true}={}){
   this.storage=storage;this.persist=persist;this.listeners=new Set();this.persistenceAvailable=true;
   try{const saved=persist?JSON.parse(storage.getItem(KEY)||'null'):null;this.value=valid(saved)?saved:seed();}
   catch{this.value=seed();this.persistenceAvailable=false;}
   refresh(this.value);
   if(globalThis.DemoAnalytics)DemoAnalytics.ensure(this.value);
   this.save();
  }
  get state(){return refresh(clone(this.value));}
  subscribe(listener){this.listeners.add(listener);return ()=>this.listeners.delete(listener);}
  save(){if(this.persist)try{this.storage.setItem(KEY,JSON.stringify(this.value));}catch{this.persistenceAvailable=false;}}
  change(action){const next=refresh(clone(this.value)),result=action(next);refresh(next);this.value=next;this.save();this.listeners.forEach(listener=>listener(this.state));return clone(result);}
  advanceWeek(){return this.change(s=>{if(s.pending&&['ready','creation_failed'].includes(s.pending.status)&&globalThis.DemoAnalytics)DemoAnalytics.endGeneration(s,s.pending,'canceled');s.demoClockOffsetMs+=WEEK;s.pending=null;return s;});}
  active(s=this.value){return s.children.find(c=>c.id===s.activeChildId);}
  parent(data){
   const name=text(data.parentName,60),email=text(data.email,100);
   if(!name||!/^[^\s@]+@example\.(test|com|org|net)$/i.test(email))throw Error('Use a fictional name and an example.test email, such as asha@example.test.');
   if(!avatars.includes(data.avatar))throw Error('Choose a demo avatar.');
   return this.change(s=>{s.parentName=name;s.email=email;s.avatar=data.avatar;if(globalThis.DemoAnalytics){const a=DemoAnalytics.ensure(s);if(!a.profileRegistered){a.profileRegistered=true;DemoAnalytics.emit(s,'profile_created',{}, {eventId:s.id+'-profile-created'});}}return s;});
  }
  selectChild(id){return this.change(s=>{if(!s.children.some(c=>c.id===id))throw Error('Choose an available child profile.');s.activeChildId=id;return s;});}
  child(data,id=null){
   const nickname=text(data.nickname,32),age=Number(data.age);
   if(!nickname||!Number.isInteger(age)||age<3||age>8)throw Error('Use a fictional nickname and an age from 3 to 8.');
   if(data.language!=='English'||!categories.includes(data.category)||!['Soft storybook','Watercolor','Colorful 3D','Pastel garden'].includes(data.illustrationStyle)||!avatars.includes(data.avatar))throw Error('Choose the available demo preferences.');
   const profile={id:id||uid('child'),nickname,age,language:'English',avatar:data.avatar,category:data.category,illustrationStyle:data.illustrationStyle,favorites:(data.favorites||[]).filter(v=>characters.includes(v))};
   return this.change(s=>{const index=s.children.findIndex(c=>c.id===id);if(id&&index<0)throw Error('This profile is no longer available.');if(index>=0)s.children[index]=profile;else{s.children.push(profile);if(globalThis.DemoAnalytics)DemoAnalytics.emit(s,'child_profile_added',{profile_id:DemoAnalytics.pseudo(s.id+':'+profile.id),age_band:DemoAnalytics.ageBand(profile.age)},{eventId:profile.id+'-added'});}s.activeChildId=profile.id;return profile;});
  }
  begin(config){
   const key=text(config.sampleKey,16);
   if(!['kindness','sharing','patience','honesty'].includes(key))throw Error('Choose kindness, sharing, patience or honesty.');
   if(config.mode&&config.mode!=='original')throw Error('The static demo offers original fiction. Traditional retellings still need reviewed sources. No credit was used.');
   return this.change(s=>{
    const child=clone(this.active(s)),cast=['Krishna','Ganesha','Hanuman'].includes(config.character)?config.character:null;
    const value=key.charAt(0).toUpperCase()+key.slice(1);
    const title=text(config.title,120)||`${cast?cast+' and ':''}${child.nickname}'s ${value} Adventure`;
    s.pending={id:uid('request'),storyId:uid('demo-story'),config:{sampleKey:key,character:cast,category:child.category,title,format:'Six-page text story (demo)'},child,status:available(s)>0?'ready':activePass(s)?'weekly_limit':'pending',paymentStatus:'not_started',receiptId:null};
    if(s.pending.status==='pending')s.pending.paymentStatus='pending';
    if(globalThis.DemoAnalytics){if(s.pending.status==='ready')DemoAnalytics.startGeneration(s,s.pending);else if(s.pending.status==='pending')DemoAnalytics.emit(s,'checkout_started',{checkout_id:s.pending.id,demo_price:5},{eventId:s.pending.id+'-checkout'});}
    return s.pending;
   });
  }
  payment(outcome,id){return this.change(s=>{
   const p=s.pending;if(!p||p.id!==id)throw Error('This checkout is no longer active. Your balances are unchanged.');
   if(outcome==='success'){
    if(p.paymentStatus==='simulated_success')return p;
    if(p.status!=='pending'||p.paymentStatus!=='pending')throw Error('Open the checkout before simulating a payment.');
    if(activePass(s)||available(s)>0)throw Error('Use your existing story allowance. Another weekly pass cannot be stacked.');
    const startsAt=new Date(clock(s)).toISOString(),expiresAt=new Date(clock(s)+WEEK).toISOString();
    const receipt={id:uid('DEMO'),requestId:p.id,storyTitle:'Weekly family pass (up to 10 stories)',childName:'All family profiles',amount:5,currency:'USD',status:'simulated_success',createdAt:startsAt,isDemo:true,type:'Weekly family pass - up to 10 stories / 7 days',pricingModel:'weekly_10',includedStories:10,startsAt,expiresAt,storyIds:[]};
    s.transactions.push(receipt);s.weeklyPass={id:uid('weekly-pass'),transactionId:receipt.id,startsAt,expiresAt,used:0,limit:10};s.paidStoryCredits=10;p.status='paid_ready';p.paymentStatus='simulated_success';p.receiptId=receipt.id;
    if(globalThis.DemoAnalytics)DemoAnalytics.emit(s,'mock_payment_succeeded',{checkout_id:p.id,mock_transaction_id:receipt.id,amount:5},{eventId:receipt.id+'-success'});
   }else if(outcome==='failure'){
    if(p.status!=='pending')throw Error('This checkout is not awaiting payment.');
    p.status='payment_failed';p.paymentStatus='simulated_failure';
    p.paymentAttempt=(p.paymentAttempt||0)+1;if(globalThis.DemoAnalytics)DemoAnalytics.emit(s,'mock_payment_failed',{checkout_id:p.id,mock_transaction_id:p.id+'-attempt-'+p.paymentAttempt,error_category:'simulated_decline'},{eventId:p.id+'-payment-failed-'+p.paymentAttempt});
   }else if(outcome==='retry'){
    if(p.status!=='payment_failed')throw Error('There is no failed checkout to retry.');p.status='pending';p.paymentStatus='pending';
   }else if(outcome==='cancel'){
    if(!['pending','payment_failed'].includes(p.status))throw Error('This checkout can no longer be canceled.');p.status='canceled';p.paymentStatus='canceled';
    if(globalThis.DemoAnalytics)DemoAnalytics.emit(s,'checkout_abandoned',{checkout_id:p.id,reason:'canceled'},{eventId:p.id+'-abandoned-canceled'});
   }else throw Error('Unknown simulation action.');
   return p;
  });}
  prepare(id){return this.change(s=>{const p=s.pending;if(!p||p.id!==id||!['paid_ready','creation_failed'].includes(p.status))throw Error('Start or retry a demo story first.');if(available(s)<1)throw Error('Your allowance has expired or is used up. Start a new story to check weekly availability.');p.status='ready';if(globalThis.DemoAnalytics)DemoAnalytics.startGeneration(s,p);return p;});}
  failCreation(id){return this.change(s=>{if(!s.pending||s.pending.id!==id||s.pending.status!=='ready')throw Error('No demo story is being created.');if(globalThis.DemoAnalytics)DemoAnalytics.endGeneration(s,s.pending,'failed');s.pending.status='creation_failed';return s.pending;});}
  cancelCreation(id){return this.change(s=>{if(!s.pending||s.pending.id!==id||!['ready','creation_failed'].includes(s.pending.status))throw Error('No demo story is being created.');if(globalThis.DemoAnalytics)DemoAnalytics.endGeneration(s,s.pending,'canceled');s.pending.status='creation_canceled';return s.pending;});}
  complete(id,book){return this.change(s=>{
   const existing=s.stories.find(story=>story.requestId===id);if(existing)return existing;
   const p=s.pending;if(!p||p.id!==id||p.status!=='ready')throw Error('This story request is not ready. No credit was used.');
   if(!book||!Array.isArray(book.pages)||book.pages.length!==6||book.pages.some(page=>typeof page!=='string'||!page.trim()||page.length>4000))throw Error('The sample story could not be completed. No credit was used.');
   const free=s.freeStoriesRemaining>0;
   if(available(s)<1)throw Error('Your weekly allowance is used up or expired. Start a new story to check availability.');
   const story={...clone(book),id:p.storyId||uid('demo-story'),requestId:id,title:p.config.title,child:clone(p.child),config:clone(p.config),createdAt:new Date(clock(s)).toISOString(),modifiedAt:new Date(clock(s)).toISOString(),completed:true,isDemo:true,qaStatus:'not_run',parentApproved:false,creditType:free?'free':'demo_paid',version:1};
   if(free){s.freeStoriesRemaining--;s.transactions.push({id:uid('DEMO-FREE'),storyId:story.id,storyTitle:story.title,childName:p.child.nickname,amount:0,currency:'USD',status:'completed',createdAt:story.createdAt,isDemo:true,type:'Complimentary demo story'});}
   else if(s.legacyStoryCredits>0){s.legacyStoryCredits--;const transaction=s.transactions.find(t=>t.status==='simulated_success'&&t.pricingModel==='legacy_per_story'&&!t.storyId);if(transaction){transaction.storyId=story.id;story.transactionId=transaction.id;}story.pricingModel='legacy_per_story';}
   else{const pass=s.weeklyPass;pass.used++;story.weeklyPassId=pass.id;story.transactionId=pass.transactionId;story.pricingModel='weekly_10';const transaction=s.transactions.find(t=>t.id===pass.transactionId);transaction.storyIds.push(story.id);transaction.storyId=transaction.storyIds[0];}
   s.stories.push(story);if(globalThis.DemoAnalytics)DemoAnalytics.endGeneration(s,p,'completed',story);p.status='completed';p.storyId=story.id;return story;
  });}
  edit(id,pages){return this.change(s=>{const story=s.stories.find(item=>item.id===id);if(!story)throw Error('Demo story not found.');if(!Array.isArray(pages)||pages.length!==6||pages.some(p=>typeof p!=='string'||!p.trim()||p.length>4000))throw Error('Keep six nonempty pages.');story.pages=clone(pages);story.version++;story.modifiedAt=new Date(clock(s)).toISOString();return story;});}
  track(name,props={},options={}){if(!globalThis.DemoAnalytics)return null;return this.change(s=>DemoAnalytics.emit(s,name,props,options));}
  analyticsSettings(changes){return this.change(s=>{const settings=DemoAnalytics.ensure(s).settings;for(const key of ['enabled','includeHistory','showOps','showRetention'])if(typeof changes[key]==='boolean')settings[key]=changes[key];return settings;});}
  simulateAgentTrace(){return this.change(s=>{const a=DemoAnalytics.ensure(s),stamp=new Date(clock(s)).toISOString(),storyId=uid('simulated-trace');for(const [index,[agentId]] of DemoAnalytics.AGENTS.entries()){const deferred=['illustration','music'].includes(agentId);a.agentRuns.push({id:uid('demo-run'),accountId:s.id,storyId,agentId,status:deferred?'deferred':index===5?'failed':'completed',occurredAt:stamp,durationMs:deferred?null:700+index*140,retryCount:index===2?1:0,reviewVerdict:agentId==='reviewer'?'REVISION_REQUIRED':null,missingAssets:index===5?1:0,costUsd:deferred?0:.001+index*.0005,isDemo:true,isSynthetic:true,data_source:'current_demo'});}return storyId;});}
  reset(){const anchor=this.value.analytics?.history?.anchor;this.value=refresh(seed());if(globalThis.DemoAnalytics)this.value.analytics=DemoAnalytics.initial(anchor);this.save();this.listeners.forEach(listener=>listener(this.state));return this.state;}
 }
 globalThis.LittleStoriesDemo={DemoStore,seed,KEY,categories,characters,avatars};
})();
