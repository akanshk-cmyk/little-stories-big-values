/* Local, fictional analytics. Runtime schemas whitelist every event property. No network. */
(() => {
 'use strict';
 const DAY=86400000;
 const CATEGORIES=['Hindu Gods & Mythology','Indian Festivals','Good Habits & Values'];
 const CHARACTERS=['Krishna','Ganesha','Hanuman','Rama','Sita','Saraswati','Durga','Lakshmi','Shiva','Parvati','Other'];
 const AGENTS=[['supervisor','Sutradhar','Supervisor'],['knowledge','Saraswati','Cultural Scout'],['writer','Vyasa','Story Writer'],['archive','Smriti','Archivist'],['illustration','Vishwakarma','Illustration'],['narrator','Vaani','Narration'],['music','Raga','Music'],['reviewer','Dharma','Reviewer']];
 const id=v=>typeof v==='string'&&/^[a-zA-Z0-9:_-]{1,140}$/.test(v)?v:undefined;
 const number=(v,max)=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=max?v:undefined;
 const enums=values=>v=>values.includes(v)?v:undefined;
 const schema={
  profile_created:{},child_profile_added:{profile_id:id,age_band:enums(['3-4','5-6','7-8'])},
  story_category_selected:{category:enums(CATEGORIES),character:enums(CHARACTERS)},
  story_creation_started:{flow_id:id},story_generation_started:{story_id:id,attempt_id:id,credit_type:enums(['free','demo_paid']),category:enums(CATEGORIES),character:enums(CHARACTERS),language:enums(['English'])},
  story_generation_completed:{story_id:id,attempt_id:id,credit_type:enums(['free','demo_paid']),page_count:v=>number(v,100),word_count:v=>number(v,100000),category:enums(CATEGORIES),character:enums(CHARACTERS),language:enums(['English']),mock_transaction_id:id},
  story_generation_failed:{story_id:id,attempt_id:id,error_category:enums(['simulated_story_failure','invalid_sample','provider_unavailable'])},
  storybook_opened:{story_id:id},story_page_viewed:{story_id:id,page_number:v=>number(v,100)},narration_played:{story_id:id,language:enums(['English']),mode:enums(['static_demo','connected'])},
  checkout_started:{checkout_id:id,demo_price:v=>v===5?5:undefined},mock_payment_succeeded:{checkout_id:id,mock_transaction_id:id,amount:v=>v===5?5:undefined},mock_payment_failed:{checkout_id:id,mock_transaction_id:id,error_category:enums(['simulated_decline'])},
  checkout_abandoned:{checkout_id:id,reason:enums(['canceled','navigation'])},pdf_download_clicked:{story_id:id,mode:enums(['static_demo','connected'])},
  navigation_clicked:{route:enums(['library','dashboard','create','family','child-profiles','pricing','shelf','billing','sources','festivals','reader','editor','review'])}
 };
 /** @typedef {'profile_created'|'child_profile_added'|'story_category_selected'|'story_creation_started'|'story_generation_started'|'story_generation_completed'|'story_generation_failed'|'storybook_opened'|'story_page_viewed'|'narration_played'|'checkout_started'|'mock_payment_succeeded'|'mock_payment_failed'|'checkout_abandoned'|'pdf_download_clicked'|'navigation_clicked'} DemoEventName */
 /** @typedef {{event_id:string,event_name:DemoEventName,pseudonymous_account_id:string,session_id:string,occurred_at:string,sanitized_properties:Object,is_demo:true,data_source:'historical_seed'|'current_demo'}} DemoEvent */
 const pseudo=value=>{let h=2166136261;for(const c of String(value))h=Math.imul(h^c.charCodeAt(0),16777619);return 'profile-'+(h>>>0).toString(16);};
 const ageBand=age=>age<5?'3-4':age<7?'5-6':'7-8';
 const SESSION='session-'+crypto.randomUUID();
 /** @param {DemoEventName} name @returns {DemoEvent} */
 function event(name,props={},options={}){
  if(!schema[name])throw Error('Unknown demo event.');
  const clean={};for(const [key,validate] of Object.entries(schema[name])){const value=validate(props[key]);if(value!==undefined)clean[key]=value;}
  const account=id(options.accountId),session=id(options.sessionId||SESSION);
  if(!account||!session)throw Error('Analytics needs pseudonymous identifiers.');
  const at=new Date(options.at||Date.now());if(!Number.isFinite(at.getTime()))throw Error('Invalid analytics timestamp.');
  return {event_id:id(options.eventId)||'event-'+crypto.randomUUID(),event_name:name,pseudonymous_account_id:account,session_id:session,occurred_at:at.toISOString(),sanitized_properties:clean,is_demo:true,data_source:options.source==='historical_seed'?'historical_seed':'current_demo'};
 }
 function historical(anchor=new Date().toISOString()){
  const now=Date.parse(anchor),data={fixtureVersion:3,anchor,accounts:[],childProfiles:[],stories:[],storyGenerations:[],mockTransactions:[],storyEntitlements:[],analyticsEvents:[],agentRuns:[]};
  let serial=0;const emit=(account,name,props,at)=>data.analyticsEvents.push(event(name,props,{accountId:account.id,sessionId:'seed-session-'+account.id,eventId:'seed-event-'+(++serial),at,source:'historical_seed'}));
  for(let i=0;i<48;i++){
   const account={id:'hist-account-'+String(i+1).padStart(3,'0'),displayName:'Demo family '+String(i+1).padStart(3,'0'),signupAt:new Date(now-(2+(i*7)%176)*DAY).toISOString(),isDemo:true,data_source:'historical_seed',profileRegistered:true};
   const signup=Date.parse(account.signupAt);emit(account,'profile_created',{},signup);data.accounts.push(account);
   const childCount=1+i%3;
   for(let c=0;c<childCount;c++){data.childProfiles.push({id:`hist-child-${i}-${c}`,accountId:account.id,ageBand:c%2?'7-8':'5-6',createdAt:account.signupAt,isDemo:true,data_source:'historical_seed'});emit(account,'child_profile_added',{profile_id:`hist-child-${i}-${c}`,age_band:c%2?'7-8':'5-6'},signup+60000+c*1000);}
   const count=i%6+1;let free=3,paid=0,weekly=null;
   for(let j=0;j<count;j++){
    const at=signup+3600000+j*2*DAY,storyId=`hist-story-${i}-${j}`,attemptId=storyId+'-attempt',credit=j<3?'free':'demo_paid';
    const category=CATEGORIES[(i+j)%3],character=CHARACTERS[(i+j)%5],duration=1800+(i*317+j*503)%9000;
    let transaction=null;
    if(j>=3&&(!weekly||at>=Date.parse(weekly.expiresAt)||weekly.storyIds.length>=10)){
     const checkout=storyId+'-checkout';emit(account,'checkout_started',{checkout_id:checkout,demo_price:5},at-60000);
     if((i+j)%3===0)emit(account,'mock_payment_failed',{checkout_id:checkout,mock_transaction_id:checkout+'-failed',error_category:'simulated_decline'},at-40000);
     transaction={id:storyId+'-purchase',accountId:account.id,storyId,storyIds:[],pricingModel:'weekly_10',includedStories:10,startsAt:new Date(at-20000).toISOString(),expiresAt:new Date(at-20000+7*DAY).toISOString(),amount:5,status:'simulated_success',createdAt:new Date(at-20000).toISOString(),isDemo:true,data_source:'historical_seed'};
     data.mockTransactions.push(transaction);emit(account,'mock_payment_succeeded',{checkout_id:checkout,mock_transaction_id:transaction.id,amount:5},at-20000);paid=10;weekly=transaction;
    }
    if(j>=3){transaction=weekly;weekly.storyIds.push(storyId);}
    emit(account,'story_creation_started',{flow_id:storyId+'-flow'},at-100000);
    const meta={story_id:storyId,attempt_id:attemptId,credit_type:credit,category,character,language:'English'};
    const hadFailure=(i+j)%7===0;
    if((i+j)%7===0){
     const failed={id:attemptId+'-failed',accountId:account.id,storyId,status:'failed',startedAt:new Date(at).toISOString(),endedAt:new Date(at+800).toISOString(),durationMs:800,category,character,language:'English',creditType:credit,isDemo:true,data_source:'historical_seed'};
     data.storyGenerations.push(failed);emit(account,'story_generation_started',{...meta,attempt_id:failed.id},at);emit(account,'story_generation_failed',{story_id:storyId,attempt_id:failed.id,error_category:'simulated_story_failure'},at+800);
    }
    const completedAt=new Date(at+duration).toISOString();
    emit(account,'story_generation_started',meta,at+(hadFailure?900:0));
    data.storyGenerations.push({id:attemptId,accountId:account.id,storyId,status:'completed',startedAt:new Date(at+(hadFailure?900:0)).toISOString(),endedAt:completedAt,durationMs:duration-(hadFailure?900:0),category,character,language:'English',creditType:credit,isDemo:true,data_source:'historical_seed'});
    data.stories.push({id:storyId,accountId:account.id,createdAt:completedAt,creditType:credit,transactionId:transaction?.id,pricingModel:j>=3?'weekly_10':null,category,character,language:'English',pageCount:6,wordCount:160+(i*13+j*23)%420,isDemo:true,data_source:'historical_seed'});
    emit(account,'story_generation_completed',{...meta,page_count:6,word_count:160+(i*13+j*23)%420,mock_transaction_id:transaction?.id},at+duration);
    emit(account,'storybook_opened',{story_id:storyId},at+duration+1000);emit(account,'story_page_viewed',{story_id:storyId,page_number:1},at+duration+2000);
    if(j%2===0&&(i+j)%9!==0)emit(account,'narration_played',{story_id:storyId,language:'English',mode:'static_demo'},at+duration+3000);
    if(i%4===0)emit(account,'pdf_download_clicked',{story_id:storyId,mode:'static_demo'},at+duration+4000);
    if(free>0)free--;else paid--;
    for(const [index,[agentId]] of AGENTS.entries()){
     const deferred=['illustration','music'].includes(agentId),failed=agentId==='narrator'&&(i+j)%9===0;
     data.agentRuns.push({id:`hist-run-${i}-${j}-${agentId}`,accountId:account.id,storyId,agentId,status:deferred?'deferred':failed?'failed':'completed',occurredAt:completedAt,durationMs:deferred?null:400+(index*71+i*117+j*31)%2800,retryCount:(i+j+index)%11===0?1:0,reviewVerdict:agentId==='reviewer'?((i+j)%5===0?'REVISION_REQUIRED':'PASS'):null,missingAssets:failed?1:0,costUsd:deferred?0:Number((.001+index*.0007).toFixed(4)),isDemo:true,isSynthetic:true,data_source:'historical_seed'});
    }
   }
   if(i%4===0&&count>=3){const checkout=account.id+'-abandoned';emit(account,'checkout_started',{checkout_id:checkout,demo_price:5},signup+5*DAY);emit(account,'checkout_abandoned',{checkout_id:checkout,reason:'canceled'},signup+5*DAY+60000);}
   data.storyEntitlements.push({accountId:account.id,freeStoriesRemaining:free,paidStoryCredits:paid,isDemo:true,data_source:'historical_seed'});
  }
  // No fixture lies in the future, including accounts whose last story exceeds the anchor.
  data.stories=data.stories.filter(r=>Date.parse(r.createdAt)<=now);data.storyGenerations=data.storyGenerations.filter(r=>Date.parse(r.startedAt)<=now);data.mockTransactions=data.mockTransactions.filter(r=>Date.parse(r.createdAt)<=now);data.analyticsEvents=data.analyticsEvents.filter(r=>Date.parse(r.occurred_at)<=now);data.agentRuns=data.agentRuns.filter(r=>Date.parse(r.occurredAt)<=now);
  for(const t of data.mockTransactions)t.storyIds=t.storyIds.filter(id=>data.stories.some(r=>r.id===id));
  for(const a of data.accounts){const books=data.stories.filter(r=>r.accountId===a.id),pass=data.mockTransactions.filter(r=>r.accountId===a.id&&Date.parse(r.expiresAt)>now).at(-1);Object.assign(data.storyEntitlements.find(r=>r.accountId===a.id),{freeStoriesRemaining:Math.max(0,3-books.filter(r=>r.creditType==='free').length),paidStoryCredits:pass?Math.max(0,10-pass.storyIds.length):0,expiresAt:pass?.expiresAt});}
  return data;
 }
 function initial(anchor){return {version:1,currentAccountCreatedAt:new Date().toISOString(),profileRegistered:false,events:[],generations:[],agentRuns:[],settings:{enabled:true,includeHistory:true,showOps:true,showRetention:true},history:historical(anchor)};}
 function ensure(s){if(!s.analytics||s.analytics.version!==1)s.analytics=initial();else if(s.analytics.history?.fixtureVersion!==3)s.analytics.history=historical(s.analytics.history?.anchor);return s.analytics;}
 function emit(s,name,props={},options={}){
  const a=ensure(s);if(!a.settings.enabled)return null;
  const e=event(name,props,{at:Date.now()+(s.demoClockOffsetMs||0),...options,accountId:s.id});if(a.events.some(row=>row.event_id===e.event_id))return null;
  a.events.push(e);if(a.events.length>5000)a.events.shift();return e;
 }
 function startGeneration(s,p){
  const a=ensure(s),prior=a.generations.filter(r=>r.requestId===p.id).length;
  const row={id:p.id+'-attempt-'+(prior+1),requestId:p.id,storyId:p.storyId,accountId:s.id,status:'started',startedAt:new Date(Date.now()+(s.demoClockOffsetMs||0)).toISOString(),endedAt:null,durationMs:null,category:p.config.category,character:p.config.character||'Other',language:'English',creditType:s.freeStoriesRemaining>0?'free':'demo_paid',isDemo:true,data_source:'current_demo'};
  a.generations.push(row);p.generationAttemptId=row.id;
  emit(s,'story_generation_started',{story_id:p.storyId,attempt_id:row.id,category:row.category,character:row.character,language:'English',credit_type:row.creditType},{eventId:row.id+'-started'});
 }
 function endGeneration(s,p,status,book){
  const row=ensure(s).generations.find(r=>r.id===p.generationAttemptId);if(!row||row.status!=='started')return;
  row.status=status;row.endedAt=new Date(Date.now()+(s.demoClockOffsetMs||0)).toISOString();row.durationMs=Math.max(0,Date.parse(row.endedAt)-Date.parse(row.startedAt));
  if(status==='completed')emit(s,'story_generation_completed',{story_id:book.id,attempt_id:row.id,credit_type:book.creditType,page_count:book.pages.length,word_count:book.pages.join(' ').split(/\s+/).length,category:row.category,character:row.character,language:'English',mock_transaction_id:book.transactionId||s.transactions.find(t=>t.storyId===book.id&&t.status==='simulated_success')?.id},{eventId:book.id+'-completed'});
  if(status==='failed')emit(s,'story_generation_failed',{story_id:p.storyId,attempt_id:row.id,error_category:'simulated_story_failure'},{eventId:row.id+'-failed'});
 }
 function normalized(s){
  const a=ensure(s),h=a.history,source='current_demo',created=a.currentAccountCreatedAt;
  const current={accounts:[{id:s.id,displayName:'Current demo family',signupAt:created,profileRegistered:a.profileRegistered,isDemo:true,data_source:source}],childProfiles:s.children.map(c=>({id:pseudo(s.id+':'+c.id),accountId:s.id,ageBand:ageBand(c.age),createdAt:created,isDemo:true,data_source:source})),stories:s.stories.map(r=>({id:r.id,accountId:s.id,createdAt:r.createdAt,creditType:r.creditType,category:CATEGORIES.includes(r.config.category)?r.config.category:CATEGORIES[2],character:r.config.character||'Other',language:'English',pageCount:r.pages.length,wordCount:r.pages.join(' ').split(/\s+/).length,isDemo:true,data_source:source})),storyGenerations:a.generations,mockTransactions:s.transactions.filter(t=>t.status==='simulated_success').map(t=>({id:t.id,accountId:s.id,storyId:t.storyId,storyIds:t.storyIds||[t.storyId].filter(Boolean),pricingModel:t.pricingModel,includedStories:t.includedStories,expiresAt:t.expiresAt,amount:5,status:'simulated_success',createdAt:t.createdAt,isDemo:true,data_source:source})),storyEntitlements:[{accountId:s.id,freeStoriesRemaining:s.freeStoriesRemaining,paidStoryCredits:s.paidStoryCredits,expiresAt:s.weeklyPass?.expiresAt,isDemo:true,data_source:source}],analyticsEvents:a.events,agentRuns:a.agentRuns};
  return Object.fromEntries(Object.keys(current).map(key=>[key,[...(h[key]||[]),...current[key]]]));
 }
 function orderedFunnel(events){
  const stages=['Signup','First story completed','Third free story completed','Mock checkout started','Mock payment succeeded','Pass or legacy-credit story completed'],sets=stages.map(()=>new Set()),byAccount=new Map();
  events.forEach(e=>{const rows=byAccount.get(e.pseudonymous_account_id)||[];rows.push(e);byAccount.set(e.pseudonymous_account_id,rows);});
  for(const [account,rows] of byAccount){
   rows.sort((a,b)=>Date.parse(a.occurred_at)-Date.parse(b.occurred_at));
   const signup=rows.findIndex(e=>e.event_name==='profile_created');if(signup<0)continue;sets[0].add(account);
   const seen=new Set(),free=new Set();let first=false,third=false,checkout=false,paid=false;const checkouts=new Set(),purchases=new Set();
   for(const e of rows.slice(signup+1)){
    const p=e.sanitized_properties;
    if(e.event_name==='story_generation_completed'&&!seen.has(p.story_id)){
     seen.add(p.story_id);if(!first){first=true;sets[1].add(account);}if(p.credit_type==='free')free.add(p.story_id);
     if(free.size>=3){third=true;sets[2].add(account);}
     if(paid&&p.credit_type==='demo_paid'&&purchases.has(p.mock_transaction_id))sets[5].add(account);
    }
    if(third&&e.event_name==='checkout_started'){checkouts.add(p.checkout_id);checkout=true;sets[3].add(account);}
    if(checkout&&e.event_name==='mock_payment_succeeded'&&checkouts.has(p.checkout_id)){paid=true;purchases.add(p.mock_transaction_id);sets[4].add(account);}
   }
  }
  return stages.map((label,i)=>({label,count:sets[i].size,dropoff:i&&sets[i-1].size?1-sets[i].size/sets[i-1].size:null,signupRate:sets[0].size?sets[i].size/sets[0].size:null}));
 }
 function select(s,{days='30',source='combined',now=Date.now()+(s.demoClockOffsetMs||0)}={}){
  const a=ensure(s),all=normalized(s),start=days==='all'?-Infinity:now-Number(days)*DAY;
  const inRange=value=>{const t=Date.parse(value);return t>=start&&t<=now;};
  const keep=row=>(a.settings.includeHistory||row.data_source!=='historical_seed')&&(source==='combined'||row.data_source===source);
  const base=Object.fromEntries(Object.entries(all).map(([key,rows])=>[key,rows.filter(keep)]));
  const events=base.analyticsEvents.filter(e=>inRange(e.occurred_at)),active=new Set(events.map(e=>e.pseudonymous_account_id));
  const accounts=base.accounts.filter(r=>inRange(r.signupAt)||active.has(r.id)),ids=new Set(accounts.map(r=>r.id));
  const data={accounts,children:base.childProfiles.filter(r=>ids.has(r.accountId)),stories:base.stories.filter(r=>inRange(r.createdAt)),generations:base.storyGenerations.filter(r=>inRange(r.startedAt)),transactions:base.mockTransactions.filter(r=>inRange(r.createdAt)),events,runs:base.agentRuns.filter(r=>inRange(r.occurredAt)),entitlements:base.storyEntitlements.filter(r=>ids.has(r.accountId)).map(r=>({...r,paidStoryCredits:r.expiresAt&&Date.parse(r.expiresAt)<=now?0:r.paidStoryCredits})),days,source,now,start};
  const success=data.transactions.filter(r=>r.status==='simulated_success'),payers=new Set(success.map(r=>r.accountId)),terminal=data.generations.filter(r=>['completed','failed'].includes(r.status)),completed=terminal.filter(r=>r.status==='completed'),funnel=orderedFunnel(events),revenue=success.reduce((n,t)=>n+t.amount,0);
  data.funnel=funnel;data.kpis={parents:accounts.length,children:data.children.length,active:active.size,stories:data.stories.length,free:data.stories.filter(r=>r.creditType==='free').length,paid:data.stories.filter(r=>r.creditType==='demo_paid').length,revenue,payingAccounts:payers.size,arppa:payers.size?revenue/payers.size:null,conversion:funnel[2].count?funnel[4].count/funnel[2].count:null,signupConversion:funnel[0].count?funnel[4].count/funnel[0].count:null,checkoutCompletion:new Set(events.filter(e=>e.event_name==='checkout_started').map(e=>e.sanitized_properties.checkout_id)).size?new Set(events.filter(e=>e.event_name==='mock_payment_succeeded').map(e=>e.sanitized_properties.checkout_id)).size/new Set(events.filter(e=>e.event_name==='checkout_started').map(e=>e.sanitized_properties.checkout_id)).size:null,averageStories:active.size?data.stories.length/active.size:null,completion:terminal.length?completed.length/terminal.length:null,averageDuration:completed.length?completed.reduce((n,r)=>n+r.durationMs,0)/completed.length:null,failedPayments:events.filter(e=>e.event_name==='mock_payment_failed').length,canceledCheckouts:new Set(events.filter(e=>e.event_name==='checkout_abandoned').map(e=>e.sanitized_properties.checkout_id)).size,paidCredits:data.entitlements.reduce((n,r)=>n+r.paidStoryCredits,0)};
  // Retention uses a signup cohort with a full seven-day observation window, not clicks/users.
  const cohort=base.accounts.filter(r=>r.profileRegistered&&inRange(r.signupAt)&&Date.parse(r.signupAt)<=now-7*DAY);
  const returning=cohort.filter(r=>base.analyticsEvents.some(e=>e.pseudonymous_account_id===r.id&&Date.parse(e.occurred_at)>Date.parse(r.signupAt)+DAY&&Date.parse(e.occurred_at)<=Date.parse(r.signupAt)+7*DAY));
  const checkoutIds=new Set(events.filter(e=>e.event_name==='checkout_started').map(e=>e.sanitized_properties.checkout_id));
  const succeededIds=new Set(events.filter(e=>e.event_name==='mock_payment_succeeded'&&checkoutIds.has(e.sanitized_properties.checkout_id)).map(e=>e.sanitized_properties.checkout_id));
  data.kpis.checkoutCompletion=checkoutIds.size?succeededIds.size/checkoutIds.size:null;
  data.retention={eligible:cohort.length,returned:returning.length,returnedIds:returning.map(r=>r.id),rate:cohort.length?returning.length/cohort.length:null,cohort};return data;
 }
 globalThis.DemoAnalytics={schema,AGENTS,CATEGORIES,CHARACTERS,SESSION,pseudo,ageBand,event,historical,initial,ensure,emit,startGeneration,endGeneration,normalized,orderedFunnel,select,DAY};
})();
