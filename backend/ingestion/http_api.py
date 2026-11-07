"""Owner-scoped source-review workspace routes; called after session/CSRF checks."""
from backend.contracts.common import parse_payload
from .workspace import PassageReview


def handle(workspace, method, parts, query, owner, data=None):
    if method=='GET' and len(parts)==2:return workspace.list(owner),200
    if len(parts)<3:raise LookupError('Source endpoint not found.')
    source_id=parts[2]
    if method=='GET' and len(parts)==3:return workspace.summary(source_id,owner),200
    if method=='GET' and len(parts)==4 and parts[3]=='pages':
        return workspace.page(source_id,owner,int(query.get('page',['1'])[0])),200
    if method=='GET' and len(parts)==4 and parts[3]=='chunks':
        return workspace.chunks(source_id,owner,offset=int(query.get('offset',['0'])[0]),
                                limit=int(query.get('limit',['20'])[0])),200
    if method=='POST' and len(parts)==4 and parts[3]=='resume':
        if data!={}:raise ValueError('Resume accepts an empty request only.')
        workspace.submit(source_id,owner)
        return {'id':source_id},202
    if method=='POST' and len(parts)==4 and parts[3]=='boundaries':
        if set(data)!={'expected_generation','boundaries','target_words','overlap_words'}:
            raise ValueError('Boundary confirmation needs generation, boundaries, target and overlap.')
        if type(data['expected_generation']) is not int or not isinstance(data['boundaries'],list):
            raise ValueError('Invalid boundary request.')
        return workspace.boundaries(source_id,owner,data['expected_generation'],data['boundaries'],
            target=data['target_words'],overlap=data['overlap_words']),200
    if method=='POST' and len(parts)==4 and parts[3]=='reviews':
        return workspace.review(source_id,owner,parse_payload(PassageReview,data)),201
    raise LookupError('Source endpoint not found.')
