"""Opt-in local ONNX adapters. No implicit downloads or cloud inference."""
import hashlib
import json
from pathlib import Path
from backend.contracts.common import content_hash


def verified_manifest(folder):
    folder=Path(folder)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    required={'bge/onnx/model.onnx','bge/tokenizer.json','bge/config.json','bge/tokenizer_config.json'}
    required.update('flashrank/ms-marco-MiniLM-L-12-v2/'+name for name in (
        'flashrank-MiniLM-L-12-v2_Q.onnx','tokenizer.json','tokenizer_config.json','config.json','special_tokens_map.json'))
    if not required<=set(manifest['files']):raise ValueError('Required model hashes are missing.')
    for name,expected in manifest['files'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder.resolve()):raise ValueError('Invalid artifact path.')
        with path.open('rb') as file:
            if hashlib.file_digest(file,'sha256').hexdigest()!=expected:raise ValueError('Model artifact integrity failure.')
    return manifest


class BgeEncoder:
    dimension=384
    limit=512
    query_prefix='Represent this sentence for searching relevant passages: '

    def __init__(self, folder):
        self.manifest=verified_manifest(folder)
        self.signature='bge-small-'+content_hash({k:v for k,v in self.manifest['files'].items() if k.startswith('bge/')})
        self.inference_calls=0
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tokenizer=Tokenizer.from_file(str(Path(folder)/'bge'/'tokenizer.json'))
        self.tokenizer.no_truncation();self.tokenizer.no_padding()
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(Path(folder)/'bge'/'onnx'/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])

    def count(self,text,*,query=False):
        return len(self.tokenizer.encode((self.query_prefix if query else '')+text).ids)

    def encode(self,texts,*,query=False):
        import numpy as np
        vectors=[]
        for text in texts:
            encoded=self.tokenizer.encode((self.query_prefix if query else '')+text)
            if len(encoded.ids)>self.limit:raise ValueError('BGE token budget exceeded; subdivision is required.')
            values={'input_ids':np.array([encoded.ids],dtype=np.int64),
                    'attention_mask':np.array([encoded.attention_mask],dtype=np.int64),
                    'token_type_ids':np.array([encoded.type_ids],dtype=np.int64)}
            outputs=self.session.run(None,{item.name:values[item.name] for item in self.session.get_inputs()})
            self.inference_calls+=1
            # BGE uses normalized CLS pooling, not mean pooling.
            first=outputs[0]
            vector=first[0,0,:] if first.ndim==3 else first[0]
            norm=float(np.linalg.norm(vector))
            if norm==0 or not np.isfinite(norm):raise ValueError('Invalid embedding vector.')
            vector=vector/norm
            if vector.shape!=(self.dimension,) or not np.isfinite(vector).all():raise ValueError('Unexpected embedding shape or value.')
            vectors.append([float(v) for v in vector])
        return vectors


class FlashRanker:
    limit=512
    def __init__(self,folder):
        self.manifest=verified_manifest(folder)
        self.inference_calls=0
        model=Path(folder)/'flashrank'/'ms-marco-MiniLM-L-12-v2'
        required=('flashrank-MiniLM-L-12-v2_Q.onnx','config.json','tokenizer_config.json','special_tokens_map.json','tokenizer.json')
        if any(not (model/name).is_file() for name in required):raise ValueError('FlashRank must be explicitly downloaded first.')
        from flashrank import Ranker
        self.ranker=Ranker(model_name='ms-marco-MiniLM-L-12-v2',cache_dir=str(model.parent),max_length=self.limit,log_level='WARNING')
        self.ranker.tokenizer.no_truncation()
        self.counter=type(self.ranker.tokenizer).from_str(self.ranker.tokenizer.to_str())
        self.counter.no_padding();self.counter.no_truncation()

    def count(self,query,text):return len(self.counter.encode(query,text).ids)

    def score(self,query,passages):
        if not passages:return {}
        if any(self.count(query,p['text'])>self.limit for p in passages):
            raise ValueError('FlashRank query/passage token budget exceeded.')
        from flashrank import RerankRequest
        results=self.ranker.rerank(RerankRequest(query=query,passages=[dict(p) for p in passages]))
        self.inference_calls+=1
        return {p['id']:float(p['score']) for p in results}
