/* Static Discover artwork: local-server persistence, with browser-only fallback. */
(() => {
 'use strict';
 const picker=document.getElementById('main-picture-file');
 const choose=document.getElementById('choose-main-picture');
 const paste=document.getElementById('paste-main-picture');
 const reset=document.getElementById('reset-main-picture');
 const status=document.getElementById('main-picture-status');
 const hero=document.querySelector('.hero');
 let objectUrl=null,dbPromise=null,serverToken=null;
 function database(){
  if(!dbPromise)dbPromise=new Promise((resolve,reject)=>{
   const request=indexedDB.open('little-stories-appearance',1);
   request.onupgradeneeded=()=>request.result.createObjectStore('appearance');
   request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
  });
  return dbPromise;
 }
 async function storage(operation,value){
  const db=await database();return new Promise((resolve,reject)=>{
   const transaction=db.transaction('appearance',operation==='get'?'readonly':'readwrite');
   const store=transaction.objectStore('appearance');
   const request=operation==='get'?store.get('main-picture'):operation==='put'?store.put(value,'main-picture'):store.delete('main-picture');
   transaction.oncomplete=()=>resolve(request.result);transaction.onerror=()=>reject(transaction.error);transaction.onabort=()=>reject(transaction.error);
  });
 }
 async function request(path,method,body){
  const response=await fetch(path,{method:method||'GET',body,credentials:'same-origin',cache:'no-store',headers:method?{'X-Bala-CSRF':serverToken,'Content-Type':body instanceof Blob?body.type:'application/json'}:{}});
  if(!response.ok){let message='Could not save the Discover picture.';try{message=(await response.json()).error||message;}catch{}throw Error(message);}
  return response;
 }
 async function display(blob){
  const next=URL.createObjectURL(blob),image=new Image();image.src=next;
  try{await image.decode();if(image.naturalWidth*image.naturalHeight>25000000)throw Error('Choose a picture smaller than 25 megapixels.');}
  catch(error){URL.revokeObjectURL(next);throw error;}
  if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=next;
  hero.style.backgroundImage=`url("${objectUrl}")`;reset.hidden=false;
 }
 function busy(value){choose.disabled=value;paste.disabled=value;reset.disabled=value;}
 async function setPicture(file){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>15*1024*1024){status.textContent='Choose a PNG, JPG or WebP picture up to 15 MB.';return;}
  busy(true);status.textContent='Opening your Discover picture...';
  try{
   await display(file);
   if(serverToken){await request('/api/appearance/banner','POST',file);status.textContent='Your Discover picture is saved for this local app.';}
   else{try{await storage('put',file);status.textContent='Your main picture is saved in this browser. Open localhost:8001 to save it for the local app.';}catch{status.textContent='Picture shown for this visit; browser storage is unavailable.';}}
  }catch(error){status.textContent=error.message+' The app-wide banner was not changed.';}
  finally{busy(false);picker.value='';}
 }
 choose.addEventListener('click',()=>picker.click());
 picker.addEventListener('change',()=>{if(picker.files[0])setPicture(picker.files[0]);});
 paste.addEventListener('click',async()=>{
  try{
   if(!navigator.clipboard?.read)throw Error('Clipboard reading unavailable');
   const items=await navigator.clipboard.read();
   for(const item of items){const type=item.types.find(type=>['image/png','image/jpeg','image/webp'].includes(type));if(type){await setPicture(await item.getType(type));return;}}
   status.textContent='Copy the actual image (not its link), then click Paste picture.';
  }catch{status.textContent='Right-click your image and choose Copy image, then press Ctrl+V while Discover is open. Or use Choose main picture.';}
 });
 document.addEventListener('paste',event=>{
  if(document.getElementById('library').hidden||event.target.closest('input,textarea,[contenteditable="true"]'))return;
  const image=[...(event.clipboardData?.items||[])].find(item=>item.kind==='file'&&item.type.startsWith('image/'));
  if(image){event.preventDefault();const file=image.getAsFile();if(file)setPicture(file);}
 });
 reset.addEventListener('click',async()=>{
  busy(true);
  try{
   if(serverToken)await request('/api/appearance/reset','POST','{}');else await storage('delete');
   if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=null;}
   hero.style.removeProperty('background-image');reset.hidden=true;status.textContent='Original Discover picture restored.';
  }catch(error){status.textContent=error.message;}
  finally{busy(false);}
 });
 busy(true);
 (async()=>{
  if(location.protocol==='http:'||location.protocol==='https:'){
   try{
    if(typeof festivalConnectionReady!=='undefined')await festivalConnectionReady;
    if(typeof festivalHealth!=='undefined'&&festivalHealth?.csrf)serverToken=festivalHealth.csrf;
    else{
     const health=await fetch('/api/health',{credentials:'same-origin',cache:'no-store'});
     if(health.ok&&(health.headers.get('content-type')||'').includes('application/json'))serverToken=(await health.json()).csrf;
    }
   }catch{}
  }
  if(serverToken){
   try{
    const saved=await (await request('/api/appearance')).json();
    if(saved.bannerUrl){await display(await (await request(saved.bannerUrl)).blob());status.textContent='Your Discover picture is saved for this local app.';}
    else status.textContent='Choose your picture, or copy it from chat and use Paste picture. This sets the Discover banner for the local app.';
   }catch(error){status.textContent=error.message;}
  }else{
   try{const saved=await storage('get');if(saved instanceof Blob){await display(saved);status.textContent='Your main picture is saved in this browser.';}}catch{}
  }
 })().finally(()=>busy(false));
})();
