"""Controlled HTTP responses through the current assembly; no live network tools."""
import json
from pathlib import Path
from types import SimpleNamespace
import httpx
from backend import contracts as c
from backend.contracts.common import text_hash
from backend.contracts.local_intent import resolve_local_intent
from backend.providers.config import ProviderSettings
from backend.providers.attempt_ledger import AttemptLedger
from backend.providers.router import ProviderRouter
from backend.providers.retry_policy import ProviderFailure
from backend.accounts.store import Store
from backend.accounts.identity import Accounts
from backend.accounts.entitlements import Entitlements
from backend.accounts.assets import AssetRegistry
from backend.accounts.assembly import assemble_complete_accounts

class OfflineApp:
    def __init__(self,folder,fault='none'):
        self.folder=Path(folder);self.fault=fault;self.fixture_exchanges=0
        self.accounts=Accounts(Store(self.folder/'accounts'))
        self.owner=self.accounts.register('evaluation-parent','Synthetic-evaluation-password-123')['account_id']
        self.child=self.accounts.add_child(self.owner,'Evaluation reader',5)['child_id']
        self.ledger=Entitlements(self.accounts)
        settings={name:ProviderSettings(name,'synthetic-transport-key','synthetic-writer','synthetic-reviewer') for name in ('groq','nebius')}
        self.router=ProviderRouter(settings,AttemptLedger(self.folder/'provider-records'),enabled=True,default_mode='groq',clients={name:httpx.Client(transport=httpx.MockTransport(self.respond)) for name in settings},sleep=lambda _:None)
        self.cast=c.CharacterManifest(manifest_id='synthetic-cast',characters=tuple(c.CharacterDesign(character_id=name,design_version='fixture-v1',appearance='Synthetic text fixture, not a sacred-figure artwork.',clothing='No visual assets.',identifying_details=('Engineering fixture only.',)) for name in ('rama','hanuman','guha','lakshmana')))
        base=SimpleNamespace(version='hhh-offline-current-flow-v1',capabilities=c.PlaybookCapabilities(character_ids=('rama','hanuman'),objectives=('Kindness','Sharing'),story_types=('adapted_retelling',)),prepare=self.prepare,eligibility=lambda b:True,uses_prepared_evidence=True,dense=lambda *a:[],sparse=lambda *a:[],write=lambda *a:None,review=lambda *a:None,assets=lambda *a:None,render=lambda *a:None)
        self.api=assemble_complete_accounts(self.folder/'workflow',accounts=self.accounts,entitlements=self.ledger,registry=AssetRegistry(self.accounts),base=base,router=self.router,authorize=lambda owner,req:self.accounts.require(owner) is True,evidence_sufficiency=lambda req,b:'PASS',provider_consent=lambda *a:True,evidence_resolver=lambda owner,req,options:self.prepare(req),media_policy_version='hhh-synthetic-text-only',script_first=True,text_only=True,narration_enabled=False,synthetic=True,allow_short_stories_for_testing=True)
        self.service=self.api.workflow

    def prepare(self,request):
        if self.fault=='missing_evidence':raise ProviderFailure('evidence_missing')
        intent=resolve_local_intent(request)
        buddy='Hanuman' if 'hanuman' in intent.candidate_ids[0] else 'Guha'
        text=f'Rama listened with care.\n{buddy} spoke kindly and offered help.\nThey shared a calm greeting.'
        ref=c.SourceReference(source_id='synthetic-source',snapshot_id='synthetic-snapshot',edition_id='synthetic-edition',chunk_id=intent.candidate_ids[0],parent_section_id='synthetic-section',pdf_pages=(1,),content_hash=text_hash(text),review_record_ids=('synthetic-review',),eligibility_epoch=1,review_status='approved',eligibility_status='eligible',rights_status='permitted')
        passage=c.RankedPassage(reference=ref,text=text,index_version='synthetic-index',fused_rank=1,token_count=len(text.split()))
        evidence=c.EvidenceBundle(bundle_id='synthetic-evidence-'+c.content_hash(request),request_hash=c.content_hash(request),passages=(passage,),index_version='synthetic-index',retrieval_config_hash=c.content_hash('synthetic fixture'),eligibility_epoch=1)
        return dict(characters=self.cast.model_dump(mode='json'),evidence=evidence.model_dump(mode='json'))

    def respond(self,http_request):
        self.fixture_exchanges+=1;envelope=json.loads(http_request.content);payload=json.loads(envelope['messages'][1]['content'])
        if 'draft' in payload:
            draft=payload['draft'];quote=draft['pages'][0]['text'].split('.')[0]+'.';source=payload['context']['evidence']['passages'][0]['text']
            checks={name:dict(status='FAIL' if name=='critical_safety' and self.fault=='reviewer_safety_failure' else 'PASS',reason='Controlled reviewer fixture; not a content-quality judgment.',page=1,quote=quote,source_quote=source if name=='factual_correctness' else None) for name in ('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety')}
            result=dict(draft_hash=draft['draft_hash'],evidence_bundle_hash=payload['context']['evidence']['bundle_hash'],reviewer_version=payload['reviewer_version'],**checks)
        else:
            buddy='Hanuman' if 'hanuman' in payload['context']['evidence']['passages'][0]['reference']['chunk_id'] else 'Guha'
            endings=['They began their quiet meeting.','They gave each other time to speak.','They asked a gentle question together.','They made space for a careful answer.','They agreed to offer a little help.','They ended their meeting with warm thanks.']
            pages=[dict(text=f'Rama listened with care. {buddy} spoke kindly and offered help. Rama waited for the words to finish and answered with a gentle smile. '+ending,character_ids=['rama',buddy.lower()]) for ending in endings]
            result=dict(title='Synthetic workflow story',pages=pages,citations=[dict(page_number=1,claim_text='Rama listened with care.',source_quote_id=payload['source_quote_bank'][0]['source_quote_id'])],moral='Listen kindly and give others time.',parent_question='How can we listen with care?')
        return httpx.Response(200,json=dict(model=envelope['model'],choices=[dict(message=dict(content=json.dumps(result)),finish_reason='stop')]))

    def close(self):self.service.close();self.router.close()
