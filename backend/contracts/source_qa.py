"""Automated source QA. Never represents qualified human certification or rights."""
from typing import Literal
from pydantic import Field
from .common import Contract,Digest,Identifier,Positive,Text


class SourceQuote(Contract):
    pdf_page:Positive
    quote:Text


class SourceQACheck(Contract):
    status:Literal['PASS','FAIL','PENDING']='PENDING'
    reason:Text
    quotes:tuple[SourceQuote,...]=()


class SourceQAReport(Contract):
    subject_hash:Digest
    reviewer_version:Identifier
    extraction_accuracy:SourceQACheck
    cultural_context:SourceQACheck
    child_suitability:SourceQACheck
    faithful_adaptation:SourceQACheck
    critical_safety:SourceQACheck
    review_origin:Literal['automated_qa']='automated_qa'

    @property
    def overall_result(self):
        statuses=[getattr(self,key).status for key in SOURCE_CHECKS]
        return 'FAIL' if 'FAIL' in statuses else ('PASS' if all(v=='PASS' for v in statuses) else 'PENDING')


SOURCE_CHECKS=('extraction_accuracy','cultural_context','child_suitability','faithful_adaptation','critical_safety')
