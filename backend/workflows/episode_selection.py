"""Deterministic candidate ordering and bounded content recovery.

Eligibility callback must consult trusted current records, not model flags.
No source review, provider execution or persistence is performed here.
"""
from backend.contracts.episode_selection import SelectionPolicy, EpisodeCandidate


class EpisodeSelector:
    def __init__(self, *, eligible, policy=None):
        if not callable(eligible):raise TypeError('Trusted eligibility callback required.')
        self.eligible=eligible
        self.policy=policy or SelectionPolicy()

    def shortlist(self,owner,request,candidates):
        if len(candidates)>self.policy.retrieval_pool_size:raise ValueError('Candidate pool exceeds limit.')
        ids=[c.episode_id for c in candidates]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate episode IDs.')
        chosen=[]
        for c in candidates:
            if not isinstance(c,EpisodeCandidate):raise TypeError('Validated episode required.')
            if c.policy_version!=self.policy.version or c.assessment!='PASS' or not c.faithful_adaptation:continue
            if not c.minimum_age<=request.age<=c.maximum_age:continue
            if not set(request.required_character_ids)<=set(c.character_ids):continue
            if any(r.review_status!='approved' or r.eligibility_status!='eligible' or r.rights_status!='permitted' or not r.review_record_ids for r in c.references):continue
            if self.eligible(owner,request,c) is not True:continue
            score=sum((c.relevance*self.policy.relevance_weight,c.age_fit*self.policy.age_weight,c.reliability*self.policy.reliability_weight,c.adaptability*self.policy.adaptability_weight))
            chosen.append((score,c))
        return tuple(c for _,c in sorted(chosen,key=lambda pair:(-pair[0],pair[1].episode_id))[:self.policy.top_k])

    def run(self,owner,request,candidates,*,write,review):
        """Callbacks handle saved artifacts/usage. A review must return all six verdicts.

        Failed drafts are never returned; history stores no source or draft text.
        Exceptions propagate rather than being misclassified as content failure.
        """
        history=[]
        for c in self.shortlist(owner,request,candidates):
            feedback=None
            for attempt in range(self.policy.max_corrections+1):
                if self.eligible(owner,request,c) is not True:break
                draft=write(c,feedback)
                verdict=review(c,draft)
                dimensions=('factual_correctness','clarity','value_insight','engagement','tone_fit','critical_safety')
                passed=all(verdict.get(key)=='PASS' for key in dimensions)
                history.append(dict(episode_id=c.episode_id,attempt=attempt,passed=passed))
                if passed and self.eligible(owner,request,c) is True:
                    return dict(status='awaiting_parent_approval',candidate=c,draft=draft,history=history)
                feedback=verdict
                if any(verdict.get(key) not in ('PASS','FAIL') for key in dimensions):
                    return dict(status='blocked',reason='Review is incomplete.',history=history)
        return dict(status='blocked',reason='No suitable candidate passed all checks.',history=history)
