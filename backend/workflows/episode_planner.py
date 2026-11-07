"""Trusted episode retrieval bridge for the actual persistent story graph."""
from backend.contracts import parse_payload,StoryRequest,content_hash
from backend.contracts.episode_selection import EpisodeCandidate,SelectionPolicy
from backend.workflows.story_graph import PreparedContext
from backend.workflows.episode_selection import EpisodeSelector


class EpisodePlanner:
    def __init__(self,*,retrieve,eligible,policy=None,version='episode-planner-v1'):
        if not callable(retrieve) or not callable(eligible):raise TypeError('Trusted retrieval and eligibility required.')
        self.retrieve,self.eligible=retrieve,eligible
        self.policy=policy or SelectionPolicy();self.version=version

    def identity(self):return content_hash([self.version,self.policy.model_dump(mode='json')])

    def prepare(self,owner,state,repository):
        request=parse_payload(StoryRequest,state['request'])
        # One immutable shortlist per run; paid retrieval is never repeated on fallback.
        def retrieve():
            raw=self.retrieve(owner,request,state.get('creation_options',{}))
            if len(raw)>self.policy.retrieval_pool_size:raise ValueError('Episode pool exceeds bound.')
            candidates=[];contexts={}
            for item in raw:
                candidate=parse_payload(EpisodeCandidate,item['candidate'])
                context=parse_payload(PreparedContext,item['context'])
                if not context.evidence or context.evidence.request_hash!=state['request_hash']:
                    raise ValueError('Candidate requires exact-request evidence.')
                if tuple(p.reference for p in context.evidence.passages)!=candidate.references:
                    raise ValueError('Episode and evidence references differ.')
                candidates.append(candidate);contexts[candidate.episode_id]=context.model_dump(mode='json')
            ordered=EpisodeSelector(eligible=self.eligible,policy=self.policy).shortlist(owner,request,candidates)
            return [dict(candidate=c.model_dump(mode='json'),context=contexts[c.episode_id]) for c in ordered]
        # Check account/source authority again on each selected candidate below.
        frozen=repository.operation(state['run_id'],'episode-shortlist-'+self.identity(),retrieve)
        repository.save(state['run_id'],'episode-shortlist',frozen)
        index=state.get('episode_index',0)
        while index<len(frozen):
            item=frozen[index];candidate=parse_payload(EpisodeCandidate,item['candidate'])
            if self.eligible(owner,request,candidate) is True:
                return dict(context=item['context'],episode_index=index,episode_count=len(frozen),
                    episode_id=candidate.episode_id,context_id='episode-context-'+content_hash(item),
                    selection_hash=content_hash(frozen),revision_epoch='episode-'+str(index))
            index+=1
        from backend.providers.retry_policy import ProviderFailure
        raise ProviderFailure('evidence_missing')
