/* Exact user-selected static card artwork. No AI generation or external fetching. */
(() => {
 'use strict';
 const key='little-stories-rama-selection-picture-v1';
 const gallery=document.getElementById('studio-character-gallery');if(!gallery)return;
 const tools=document.createElement('div');tools.className='rama-picture-tools';
 const choose=document.createElement('button');choose.type='button';choose.className='text-button';choose.textContent='Choose Rama picture';choose.id='choose-rama-picture';
 const paste=document.createElement('button');paste.type='button';paste.className='text-button';paste.textContent='Paste Rama picture';paste.id='paste-rama-picture';
 const picker=document.createElement('input');picker.type='file';picker.accept='image/png,image/jpeg,image/webp';picker.hidden=true;picker.id='rama-picture-file';picker.setAttribute('aria-label','Choose Rama illustration');
 const status=document.createElement('p');status.className='small muted';status.id='rama-picture-status';status.setAttribute('role','status');
 tools.append(choose,paste,picker,status);gallery.after(tools);
 function display(url){
  document.querySelectorAll('[data-story-character="rama"] .character-picture').forEach(frame=>{
   [...frame.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE||n.nodeName==='IMG').forEach(n=>n.remove());
   const image=document.createElement('img');image.src=url;image.alt='Rama with folded hands, a golden crown, yellow clothing and an orange shawl, in a flower-filled courtyard.';image.width=320;image.height=320;frame.prepend(image);
  });
 }
 async function install(file){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>8*1024*1024){status.textContent='Choose a PNG, JPG or WebP image up to 8 MB.';return;}
  choose.disabled=paste.disabled=true;
  try{
   const url=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('The picture could not be read.'));reader.readAsDataURL(file);});
   const check=new Image();check.src=url;await check.decode();
   if(check.width>8000||check.height>8000)throw Error('Choose a picture smaller than 8,000 pixels on each side.');
   display(url);
   try{localStorage.setItem(key,url);status.textContent='Rama’s picture is saved in this browser.';}
   catch{status.textContent='Rama’s picture is shown for this visit. Choose a smaller file to keep it after refreshing.';}
  }catch(error){status.textContent=error.message||'The picture could not be opened.';}
  finally{choose.disabled=paste.disabled=false;picker.value='';}
 }
 choose.onclick=()=>picker.click();picker.onchange=()=>{if(picker.files[0])install(picker.files[0]);};
 paste.onclick=async()=>{
  try{for(const item of await navigator.clipboard.read()){const type=item.types.find(t=>['image/png','image/jpeg','image/webp'].includes(t));if(type){await install(await item.getType(type));return;}}status.textContent='Copy the picture itself, then click Paste Rama picture.';}
  catch{status.textContent='Clipboard access is unavailable. Use Choose Rama picture to select the saved image.';}
 };
 try{const saved=localStorage.getItem(key);if(saved&&/^data:image\/(png|jpeg|webp);base64,/.test(saved)){const image=new Image();image.src=saved;image.decode().then(()=>display(saved)).catch(()=>{status.textContent='Please choose Rama’s picture again.';});}}catch{}
})();
