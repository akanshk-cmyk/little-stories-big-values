'use strict';
// User-supplied static selection art; no image-generation service is used.
const storyCharacters = [
 {id:'original-main',name:'Characters from your description',values:'Your own imagined adventure',descriptionOnly:true},
 {id:'rama',name:'Rama',values:'Kindness, responsibility, keeping promises',alt:'Rama with folded hands, a golden crown, yellow clothing and an orange shawl in a flower-filled courtyard.',image:'assets/characters/rama.png'},
 {id:'krishna',name:'Krishna',values:'Friendship, sharing, kindness',alt:'Child-friendly picture of Krishna playing a flute, wearing yellow clothing and a peacock feather.'},
 {id:'ganesha',name:'Ganesha',values:'Wisdom, patience, family',alt:'Child-friendly picture of Ganesha seated beside a small mouse.'},
 {id:'hanuman',name:'Hanuman',values:'Courage, helping others, devotion',alt:'Child-friendly picture of Hanuman smiling in a colorful flower garden.'}
];
let selectedStoryCharacter = null;
function chooseStoryCharacter(id){
 selectedStoryCharacter=storyCharacters.find(character=>character.id===id)||null;
 syncCharacterSelection();
}
function syncCharacterSelection(){
 document.querySelectorAll('[data-story-character]').forEach(button=>{
  const selected=button.dataset.storyCharacter===selectedStoryCharacter?.id;
  button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));
 });
 const status=document.getElementById('character-selection-status');
 const descriptionChoice=document.getElementById('original-description-choice');if(descriptionChoice)descriptionChoice.setAttribute('aria-pressed',String(selectedStoryCharacter?.id==='original-main'));
 if(status&&document.body.dataset.localRamayan==='true'&&document.getElementById('story-mode').value==='original'){status.textContent=selectedStoryCharacter?.id==='original-main'?'The characters and events come from your description. This is original fiction, not a Ramayan retelling.':selectedStoryCharacter?`${selectedStoryCharacter.name} selected for an explicitly imagined story. Include your story idea above.`:'Describe your characters or select a character above.';return;}
 if(status&&document.body.dataset.localRamayan==='true'){status.textContent=selectedStoryCharacter?`${selectedStoryCharacter.name} selected. Name any additional characters in your idea; the available Ramayan episode must support them.`:'Choose Rama or Hanuman for a source-grounded story.';return;}
 if(status)status.textContent=selectedStoryCharacter?`${selectedStoryCharacter.name} selected. Choose Original teaching-inspired fiction for an imagined adventure, or a retelling when eligible reviewed sources are available.`:'No cultural character selected. The available original samples feature Veyd and Pip.';
}
function renderCharacterSelections(){
 ['library-character-gallery','studio-character-gallery'].forEach(id=>{
  const host=document.getElementById(id);if(!host)return;
  storyCharacters.forEach(character=>{
   if(character.descriptionOnly)return;
   const button=document.createElement('button');button.type='button';button.className='story-character-card';button.dataset.storyCharacter=character.id;button.setAttribute('aria-pressed','false');
   const frame=document.createElement('div');frame.className='character-picture';
   const image=document.createElement('img');if(!character.textOnlyCard)image.src=character.image||`assets/characters/${character.id}.jpg`;image.alt=character.alt;image.width=320;image.height=320;image.loading='lazy';
   const check=document.createElement('span');check.className='character-check';check.setAttribute('aria-hidden','true');check.textContent='✓';
   if(character.textOnlyCard){frame.append(document.createTextNode('Rama'),check);}else frame.append(image,check);
   const name=document.createElement('strong');name.textContent=character.name;
   const values=document.createElement('span');values.className='character-values';values.textContent=character.values;
   button.append(frame,name,values);
   button.addEventListener('click',()=>{chooseStoryCharacter(character.id);if(id==='library-character-gallery')go('create');});
   host.append(button);
  });
 });
 document.getElementById('sample-cast').addEventListener('click',()=>{chooseStoryCharacter(null);document.getElementById('story-mode').value='original';});
 syncCharacterSelection();
}
