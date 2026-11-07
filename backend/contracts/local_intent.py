"""Validated preflight for the explicitly bounded local Ramayan catalog."""
import re,unicodedata
from typing import Literal
from .common import Contract,Text
from .requests import StoryRequest

class IntentResult(Contract):
    status:Literal['READY','CLARIFICATION_REQUIRED','UNSUPPORTED']
    message:Text
    request:StoryRequest
    excluded_characters:tuple[str,...]=()
    candidate_ids:tuple[str,...]=()
    unmatched_terms:tuple[str,...]=()

ALIASES={
 'rama':r'ram(?:a|ji)?', 'hanuman':r'hanuman(?:\s*ji)?|hanumaan|anjaneya|bajrangbali',
 'guha':r'guha', 'lakshmana':r'lakshman(?:a)?', 'sita':r'sita',
 'sugriva':r'sugriv(?:a)?', 'ravana':r'ravan(?:a)?', 'krishna':r'krishna', 'ganesha':r'ganesh(?:a)?',
}
NEGATION=re.compile(r"\b(?:without|excluding|exclude|avoid|omit|no|not including|do not include|don't include)\b([^.;!?]*?)(?=[.;!?]|\bbut\b|$)",re.I)
COMMON=set('a an the i we my our your child children kid kids for of to from in on with and or but please story stories tell write create make about ramayan ramayana adapted retelling original inspired teaching english simple words language short long little warm gentle calm bedtime daytime age year years old five three minute minutes 3 5 can could would want wants like us them their he his they this that these those how what who when where why is are was were be being become becoming becomes have has had learn learns learning teach teaches lesson value values kind kindness sharing share shares shared friend friends friendship love loves loving caring care happy safe nice good help helps helped helping helpful helper together two brother brothers shows show showing show me more again new very also peaceful politely polite into s'.split())
WELCOME=set('welcome welcoming host hospitality generosity gifts gift giving give gives gave horse horses water grain food guha'.split())
MEETING=set('meet meeting introduction introductions message messenger envoy listening listen listens speech speaking spoke speaks courteous respect respectful carry carries carrying ride hanuman lakshmana'.split())

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c)).lower().replace('’',"'")

def names(text):
    return {key for key,pattern in ALIASES.items() if re.search(r'\b(?:'+pattern+r')\b',text)}

def resolve_local_intent(request):
    idea=normalized(request.idea)
    excluded=set();positive=idea
    for match in NEGATION.finditer(idea):excluded.update(names(match.group(1)))
    positive=NEGATION.sub(' ',positive)
    for item in request.exclusions:excluded.update(names(normalized(item)))
    mentioned=names(positive);selected=set(request.required_character_ids)
    conflicts=(selected|mentioned)&excluded
    if conflicts:
        return IntentResult(status='CLARIFICATION_REQUIRED',message='Should '+', '.join(sorted(conflicts))+' be included or left out? Your selected characters and exclusions conflict.',request=request,excluded_characters=tuple(sorted(excluded)))
    required=selected|mentioned
    if 'hanuman' in required:required.add('rama')
    # Never change the original request or silently erase an explicit exclusion.
    ordered=tuple(dict.fromkeys([*request.required_character_ids,*sorted(required-selected)]))
    updated=request.model_copy(update={'required_character_ids':ordered,
        'exclusions':tuple(dict.fromkeys([*request.exclusions,*sorted(excluded)]))})
    stripped=positive
    for pattern in ALIASES.values():stripped=re.sub(r'\b(?:'+pattern+r')\b',' ',stripped)
    terms=set(re.findall(r'[a-z]+',stripped))
    unknown=tuple(sorted(terms-COMMON-WELCOME-MEETING))
    if unknown:
        return IntentResult(status='UNSUPPORTED',message='The available Ramayan passages do not cover this topic ('+', '.join(unknown[:4])+'). Available themes are a kind welcome with Guha and Rama meeting Hanuman. Your request has not been replaced.',request=updated,excluded_characters=tuple(sorted(excluded)),unmatched_terms=unknown)
    candidates=[]
    if required<={'rama','guha'} and not excluded&{'rama','guha'} and not terms&MEETING:
        candidates.append('rama-guha-welcome-v1')
    if required<={'rama','hanuman','lakshmana'} and not excluded&{'rama','hanuman','lakshmana'} and not terms&WELCOME and 'guha' not in mentioned:
        candidates.append('rama-hanuman-first-meeting-v1')
    if not candidates:
        return IntentResult(status='UNSUPPORTED',message='No available episode supports all your characters, topic and exclusions. Your request has not been changed. Please describe a kind welcome with Guha or a friendly meeting with Hanuman.',request=updated,excluded_characters=tuple(sorted(excluded)))
    if 'hanuman' in required or 'lakshmana' in required or terms&MEETING:candidates.sort(key=lambda key:key!='rama-hanuman-first-meeting-v1')
    return IntentResult(status='READY',message='Your choices match an available Ramayan episode.',request=updated,excluded_characters=tuple(sorted(excluded)),candidate_ids=tuple(candidates))
