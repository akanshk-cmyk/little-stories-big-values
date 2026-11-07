"""Deterministic BM25, rank fusion and lossless word-boundary subdivision."""
from collections import Counter
import math
import re
from backend.contracts.common import content_hash
from backend.source_grounding import tokens


def split_text(text, fits, *, overlap=20):
    words=list(re.finditer(r'\S+',text))
    start=0;result=[]
    while start<len(words):
        low=start+1;high=len(words);best=None
        while low<=high:
            stop=(low+high)//2
            part=text[words[start].start():words[stop-1].end()]
            if fits(part):best=stop;low=stop+1
            else:high=stop-1
        if best is None:raise ValueError('A single word exceeds the available token budget.')
        left,right=words[start].start(),words[best-1].end()
        if not fits(text[left:right]):raise ValueError('Token budget verification failed.')
        result.append(dict(text=text[left:right],start=left,end=right))
        if best==len(words):break
        start=max(start+1,best-min(overlap,(best-start)//4))
    return result


class BM25:
    """Corpus-frozen sparse dot-product representation; no vocabulary hashing."""
    def __init__(self,documents,*,k1=1.2,b=.75):
        if not math.isfinite(k1) or not math.isfinite(b) or k1<=0 or not 0<=b<=1:
            raise ValueError('Invalid BM25 parameters.')
        if len({d['id'] for d in documents})!=len(documents):raise ValueError('Duplicate document IDs.')
        counts={d['id']:Counter(tokens(d['text'])) for d in documents}
        frequency=Counter(t for row in counts.values() for t in row)
        self.vocabulary={term:i for i,term in enumerate(sorted(frequency))}
        self.idf={t:math.log1p((len(documents)-df+.5)/(df+.5)) for t,df in frequency.items()}
        average=sum(sum(row.values()) for row in counts.values())/len(documents) if documents else 1
        self.documents={}
        for key,row in counts.items():
            length=sum(row.values());weights={}
            for term,tf in row.items():
                weights[self.vocabulary[term]]=tf*(k1+1)/(tf+k1*(1-b+b*length/(average or 1)))
            self.documents[key]=weights
        self.model_hash=content_hash(dict(documents=documents,k1=k1,b=b,tokenizer='legacy-english-v1',vocabulary=self.vocabulary))

    @staticmethod
    def vector(values):return dict(indices=sorted(values),values=[values[i] for i in sorted(values)])

    def query_weights(self,query):return {self.vocabulary[t]:self.idf[t] for t in set(tokens(query)) if t in self.vocabulary}

    def search(self,query,limit=20):
        weights=self.query_weights(query)
        hits=[dict(id=key,score=sum(value*document.get(index,0) for index,value in weights.items()))
              for key,document in self.documents.items()]
        return sorted((h for h in hits if h['score']>0),key=lambda h:(-h['score'],h['id']))[:limit]


def reciprocal_rank_fusion(*lists,k=60,limit=20):
    if type(k) is not int or k<=0 or type(limit) is not int or limit<1:raise ValueError('Invalid fusion settings.')
    scores={}
    for hits in lists:
        seen=set();rank=0
        for hit in hits:
            key=hit['id']
            if key in seen:continue
            seen.add(key);rank+=1
            scores[key]=scores.get(key,0)+1/(k+rank)
    return sorted((dict(id=key,score=value) for key,value in scores.items()),key=lambda h:(-h['score'],h['id']))[:limit]


def cosine(left,right):
    if len(left)!=len(right) or not left:raise ValueError('Embedding dimension mismatch.')
    if any(not math.isfinite(v) for v in (*left,*right)):raise ValueError('Non-finite embedding.')
    denominator=math.sqrt(sum(v*v for v in left)*sum(v*v for v in right))
    if denominator==0:raise ValueError('Zero embedding.')
    return sum(a*b for a,b in zip(left,right))/denominator
