"""Explicitly fictional corpus and program-authored engineering labels.

These are NOT human-reviewed Ramayan facts or a cultural golden dataset.
"""
from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from backend.ingestion.workspace import PassageReview

OWNER='synthetic-retrieval-owner'
TEXTS=(
    'Krishna and Mira share a red basket of mangoes in an imaginary garden.',
    'Ganesha and Veyd wait beside a seed in this fictional patience episode.',
    'Asha doth bestow sustenance upon a hungry neighbour.',
    'Ramachandra rings a bronze bell after keeping a promise in this fictional fixture.',
    'Saraswati and Tara paint with turquoise crayons in an imagined classroom.',
    'The golden lantern passage has not been reviewed.',
    'The silver boat passage has had its permission withdrawn.',
    'The orange ribbon passage is restricted.')


def pdf(texts):
    writer=PdfWriter()
    for text in texts:
        page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 10 Tf 20 700 Td ('+text+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    target=BytesIO();writer.write(target);return target.getvalue()


def prepare(workspace):
    raw=pdf(TEXTS)
    source_id=workspace.create(BytesIO(raw),len(raw),owner=OWNER,title='Synthetic fictional retrieval corpus',edition='fixture-v1')
    summary=workspace.process(source_id,OWNER)
    if not summary['metadata']['boundaries_confirmed']:
        summary=workspace.boundaries(source_id,OWNER,summary['generation'],[
            dict(pdf_page=n,line=1,title='Fictional fixture '+str(n),kind='section') for n in range(1,len(TEXTS)+1)])
    chunks=workspace.chunks(source_id,OWNER,limit=100)
    by_page={chunk['pdf_pages'][0]:chunk for chunk in chunks}
    for page,chunk in by_page.items():
        if page==6:continue
        action='withdraw' if page==7 else ('restrict' if page==8 else 'allow')
        workspace.review(source_id,OWNER,PassageReview(expected_generation=summary['generation'],
            chunk_id=chunk['chunk_id'],content_hash=chunk['content_hash'],action=action,
            extraction_checked=True,cultural_context_checked=True,child_suitability_checked=True,
            rights_permitted=True,rights_basis='Program-authored synthetic text.',
            notes='Engineering fixture only; not a human cultural review.'))
    # A second edition/source has a deliberately tempting, out-of-scope passage.
    other=pdf(('Krishna and Mira mend a purple kite in a different fictional edition.',))
    other_id=workspace.create(BytesIO(other),len(other),owner=OWNER,title='Other fictional edition',edition='fixture-v2')
    workspace.process(other_id,OWNER)
    scope=dict(source_id=source_id,edition_id=summary['edition_id'],snapshot_id=summary['snapshot_id'],generation=summary['generation'])
    definitions=[
        ('RET-001','Krishna Mira mangoes','relevant',1),
        ('RET-002','Ganesha Veyd seed','relevant',2),
        ('RET-003','giving food','relevant',3),
        ('RET-004','Rama','relevant',4),
        ('RET-005','Tara turquoise crayons','relevant',5),
        ('RET-006','purple kite','no_match',None),
        ('RET-007','golden lantern','no_match',None),
        ('RET-008','silver boat','no_match',None),
        ('RET-009','Krishna mangoes','blocked',None),
        ('RET-010','Mira basket','relevant',1)]
    cases=[]
    for test_id,query,expectation,page in definitions:
        cases.append(dict(test_id=test_id,query=query,source=scope,
            purpose='traditional' if test_id=='RET-009' else 'parent_inspiration',
            expected_chunk_ids=[by_page[page]['chunk_id']] if page else [],expectation=expectation,
            label_status='pending' if test_id=='RET-010' else 'synthetic',
            label_reference='Program-authored fictional fixture. RET-003 tests older wording; RET-004 supplies a fictional name-variant target, not a verified religious identity claim.'))
    return cases
