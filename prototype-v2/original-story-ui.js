'use strict';
function prepareOriginalConfirmation(){
 const active=Boolean(pending?.storyCharacter&&pending.mode==='original');
 $('original-example').hidden=!active;
 $('original-example').textContent=active?`Open prewritten ${pending.storyCharacter.name} example`:'Open prewritten original example';
 $('accept-request').textContent=active?'Generate original story':pending?.mode==='original'?'Open Veyd & Pip sample':'Confirm request';
 $('original-live-note').hidden=false;
 $('original-live-note').textContent=active
  ? `${pending.storyCharacter.name} will take part in a newly imagined story. ${festivalHealth?.nebiusConfigured?'Your Nebius key is loaded on the server.':'The server needs a Nebius key before it can generate.'} Generate original story uses Nebius credits for writing and automatic review. The example button opens a separate prewritten story.`
  : pending?.mode==='original'?'Veyd & Pip are prewritten samples in this release. To generate a new AI story, go back and choose Krishna, Ganesha or Hanuman.':'Traditional and adapted retellings require eligible reviewed source records.';
}
async function openOriginalStory(mode){
 if(!pending?.storyCharacter||pending.mode!=='original')return;
 if(mode==='example'&&pending.sourceIds?.length){$('confirm-error').textContent='Prewritten examples do not use sources. Choose Generate original story or remove the selected sources.';return;}
 const generate=$('accept-request'),example=$('original-example');generate.disabled=true;example.disabled=true;
 $('confirm-error').textContent=mode==='generate'?'Preparing your original story...':'Opening the prewritten original example...';
 try{
  if(typeof festivalConnectionReady!=='undefined')await festivalConnectionReady;
  if(!festivalHealth)await festivalCheckConnection();
  const request={mode:pending.mode,character:pending.storyCharacter.id,objective:pending.value,age:parseInt(pending.age,10),language:pending.language,duration:parseInt(pending.duration,10),idea:pending.idea,mood:pending.mood,exclusions:pending.exclusions};
  const record=await festivalApi('/original-books',{method:'POST',body:JSON.stringify({request,mode,sourceIds:pending.sourceIds||[]})});
  festivalRecord=record;festivalTracks=[];festivalDirty=false;
  $('festival-audio').pause();$('festival-audio').removeAttribute('src');$('festival-audio').load();$('festival-audio-area').hidden=true;
  $('original-story-result').append($('festival-result'));
  renderFestivalBook();$('confirm-dialog').close();go('original-story');
 }catch(error){$('confirm-error').textContent=error.message;}
 finally{generate.disabled=false;example.disabled=false;}
}
function setupOriginalStories(){
 $('original-example').addEventListener('click',()=>openOriginalStory('example'));
 $('original-story-back').addEventListener('click',()=>go('create'));
}
