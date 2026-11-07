"""Source-free fiction configuration. No retrieval, network or invented citations."""
from backend.contracts import CharacterDesign, CharacterManifest
from backend.contracts.local_intent import IntentResult, resolve_local_intent

DESCRIPTION_CHARACTER='original-main'
FICTION_IDS=(DESCRIPTION_CHARACTER,*(f'original-friend-{i}' for i in range(1,8)))
SACRED_IDS=('rama','krishna','ganesha','hanuman')
ORIGINAL_OBJECTIVES=('Kindness','Sharing','Patience','Honesty')


def resolve_story_intent(request):
    if request.story_type!='original_inspired':return resolve_local_intent(request)
    message='Your description is ready for an original fictional story. No Ramayan passage will be used.'
    if not request.idea.strip():
        message='Describe the original story you would like before creating it.'
    elif request.target_minutes!=3 or request.objective not in ORIGINAL_OBJECTIVES:
        message='Choose a 3-minute original story about Kindness, Sharing, Patience or Honesty.'
    elif not set(request.required_character_ids)<=set((*FICTION_IDS,*SACRED_IDS)):
        message='Choose a displayed character or use characters from your description.'
    elif 'cultural_fact' in request.required_elements:
        message='An original story without reviewed sources cannot promise a verified cultural fact.'
    else:
        return IntentResult(status='READY',message=message,request=request)
    return IntentResult(status='UNSUPPORTED',message=message,request=request)


def prepare_original(request):
    intent=resolve_story_intent(request)
    if request.story_type!='original_inspired' or intent.status!='READY':raise ValueError(intent.message)
    characters=[]
    for name in list(dict.fromkeys((*request.required_character_ids,*FICTION_IDS)))[:8]:
        generic=name in FICTION_IDS
        characters.append(CharacterDesign(character_id=name,design_version='original-description-v1',
            appearance=('A fictional character whose name, nature and appearance follow the parent description. '
                        'Keep the same ID and identity on every page; use additional friend IDs only when needed.'
                        if generic else f'{name.title()}, respectfully portrayed in an explicitly imagined story.'),
            clothing='Follow the parent description and preserve continuity; this text edition supplies no illustration.',
            identifying_details=('Fictional events, not a traditional episode or scriptural quotation.',)))
    return dict(characters=CharacterManifest(manifest_id='original-description-cast-v1',characters=tuple(characters)).model_dump(mode='json'),evidence=None)
