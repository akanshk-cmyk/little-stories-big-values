/* Display the parent's exact static Veyd illustration; no image-generation calls. */
(() => {
 'use strict';
 const portrait=document.getElementById('veyd-portrait'),placeholder=document.getElementById('veyd-placeholder');
 const picker=document.getElementById('veyd-picture-file'),choose=document.getElementById('choose-veyd-picture');
 const paste=document.getElementById('paste-veyd-picture'),status=document.getElementById('veyd-picture-status');
 const defaultPortrait='assets/characters/veyd.png';
 const ready=()=>typeof festivalConnectionReady==='undefined'?Promise.resolve():festivalConnectionReady;
 function busy(value){choose.disabled=value;paste.disabled=value;}
 function display(url){portrait.src=url;portrait.hidden=false;placeholder.hidden=true;}
 async function setPicture(file){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>15*1024*1024){status.textContent='Choose a PNG, JPG or WebP illustration up to 15 MB.';return;}
  busy(true);status.textContent='Saving Veyd’s illustration…';
  try{
   await ready();
   if(!festivalHealth?.csrf)throw Error('Open http://localhost:8001 to save Veyd’s picture.');
   const result=await festivalApi('/appearance/veyd',{method:'POST',headers:{'Content-Type':file.type},body:file});
   display(result.veydPortraitUrl);status.textContent='Veyd’s exact illustration is saved for this app.';
  }catch(error){status.textContent=error.message;}
  finally{busy(false);picker.value='';}
 }
 choose.addEventListener('click',()=>picker.click());
 picker.addEventListener('change',()=>{if(picker.files[0])setPicture(picker.files[0]);});
 paste.addEventListener('click',async()=>{
  try{
   const items=await navigator.clipboard.read();
   for(const item of items){const type=item.types.find(type=>['image/png','image/jpeg','image/webp'].includes(type));if(type){await setPicture(await item.getType(type));return;}}
   status.textContent='Copy the illustration itself, then use Paste picture or Ctrl+V in Story Studio.';
  }catch{status.textContent='Copy your Veyd illustration, then press Ctrl+V in Story Studio, or choose the saved picture file.';}
 });
 document.addEventListener('paste',event=>{
  if(document.getElementById('create').hidden||event.target.closest('input,textarea,[contenteditable="true"]'))return;
  const item=[...(event.clipboardData?.items||[])].find(item=>item.kind==='file'&&item.type.startsWith('image/'));
  if(item){event.preventDefault();const file=item.getAsFile();if(file)setPicture(file);}
 });
 portrait.addEventListener('error',()=>{
  if(portrait.getAttribute('src')!==defaultPortrait){display(defaultPortrait);return;}
  portrait.hidden=true;placeholder.hidden=false;status.textContent='Veyd’s picture could not load. Please refresh the page.';
 });
 display(defaultPortrait);
 status.textContent='Veyd’s illustration is included with this app.';
 busy(true);
 ready().then(async()=>{
  if(!festivalHealth?.csrf)return;
  const appearance=await festivalApi('/appearance');
  if(appearance.veydPortraitUrl){display(appearance.veydPortraitUrl);status.textContent='Veyd’s illustration is saved for this app.';}
 }).catch(error=>{status.textContent=error.message;}).finally(()=>busy(false));
})();
