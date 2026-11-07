"""Small request-aware local episode catalog; no cloud retrieval is claimed."""
import re,unicodedata
from backend.contracts.common import content_hash,text_hash
from backend.ingestion.hosted_review import PassageQualification,PageSpan
from backend.ingestion.source_qa import SourceQAAgent,RoutedSourceJudge,validate_source_report

def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text) if not unicodedata.combining(c)).lower()

def requested_characters(request):
    from backend.contracts.local_intent import resolve_local_intent
    result=resolve_local_intent(request)
    if result.status!='READY':raise ValueError(result.message)
    return result.request.required_character_ids

def hanuman_candidate(document):
    pages=document.pages
    spans=[]
    for number,start_marker,end_marker in (
        (1178,'I, Hanum',None),
        (1179,'For one whose words','547 The Vedas'),
        (1180,'Thus R', '[327]'),
        (1185,'Sugr',None),
    ):
        text=pages[number-1].text
        start=text.find(start_marker);end=text.find(end_marker,start) if end_marker else len(text)
        if start<0 or end<=start:raise ValueError('Rama–Hanuman passage boundaries need inspection.')
        spans.append(PageSpan(pdf_page=number,start=start,end=end))
    text='\n\n'.join(pages[s.pdf_page-1].text[s.start:s.end] for s in spans)
    command=PassageQualification(extraction_id=content_hash([document.original_file_hash,'rama-hanuman-first-meeting-v1']),
        spans=tuple(spans),expected_text_hash=text_hash(text),scope='traditional_claims',
        rights_basis='User authorized private local Ramayan PDF retrieval and Groq story testing. No public redistribution license asserted.',
        edition_reference='Uploaded Ramayan, Hanuman meeting Rama and Lakshmana, PDF pages 1178–1180 and 1185. Edition metadata unverified.',
        cultural_review_basis='Internal automated source QA, not human certification.',
        notes='Adapt only the courteous meeting, appreciation of clear speech, offer of help and journey to meet Sugriva. Preserve Rama and Hanuman participation. Selected spans omit weapons and technical footnotes; surrounding pages remain available for context. No invented dialogue, actions or scripture quotations. Do not add Guha or the horse-care episode.')
    return command,text

class LocalRamayanCatalog:
    version='local-ramayan-two-episode-v1'
    def __init__(self,document,guha_command,guha_text,guha_report,router,folder):
        self.document=document;self.router=router;self.folder=folder
        self.guha=(guha_command,guha_text,guha_report)
        self.records={'rama-guha-welcome-v1':self.guha}

    def select(self,request,top_k=5):
        if not 1<=top_k<=10:raise ValueError('Invalid shortlist size.')
        from backend.contracts.local_intent import resolve_local_intent
        intent=resolve_local_intent(request)
        if intent.status!='READY':raise ValueError(intent.message)
        shortlist=intent.candidate_ids[:top_k]
        # Identity eligibility is a hard filter, not a soft score the model may ignore.
        if shortlist[0]=='rama-guha-welcome-v1':return 'rama-guha-welcome-v1',*self.guha
        key='rama-hanuman-first-meeting-v1'
        if key not in self.records:
            command,text=hanuman_candidate(self.document)
            path=self.folder/('source-qa-'+command.expected_text_hash+'.json')
            if path.is_file():report=validate_source_report(__import__('json').loads(path.read_text(encoding='utf-8')),self.document,command,'source-qa-qwen-v5')
            else:
                judge=RoutedSourceJudge(self.router,owner='private-ramayan-source-test',authorize=lambda *args:True,mode='groq')
                report=SourceQAAgent(judge=judge,version='source-qa-qwen-v5',authorize=lambda owner,source,cmd:source==self.document.source_id).review('private-ramayan-source-test',self.document,command)
                path.write_text(report.model_dump_json(indent=2),encoding='utf-8')
            if report.overall_result!='PASS':raise ValueError('The matching Rama–Hanuman source did not pass internal checks. No different episode will be substituted.')
            self.records[key]=(command,text,report)
        return key,*self.records[key]

    def current(self,bundle):
        try:
            for passage in bundle.passages:
                key=passage.reference.chunk_id
                if key=='rama-guha-welcome-v1':command,text,report=self.guha
                elif key=='rama-hanuman-first-meeting-v1':
                    command,text=hanuman_candidate(self.document)
                    path=self.folder/('source-qa-'+command.expected_text_hash+'.json')
                    report=validate_source_report(__import__('json').loads(path.read_text(encoding='utf-8')),self.document,command,'source-qa-qwen-v5')
                else:return False
                if passage.reference.source_id!=self.document.source_id or passage.reference.snapshot_id!=self.document.snapshot_id:return False
                if passage.reference.pdf_pages!=tuple(sorted({span.pdf_page for span in command.spans})):return False
                if report.overall_result!='PASS' or passage.text!=text or passage.reference.content_hash!=text_hash(text):return False
                if passage.reference.review_record_ids!=('automated-qa-'+content_hash(report),):return False
            return bool(bundle.passages)
        except (ValueError,OSError,KeyError):return False
