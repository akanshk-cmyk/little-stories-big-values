"""Engineering baseline tests. No religious source is approved or evaluated."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.contracts.common import parse_payload
from backend.ingestion.workspace import SourceWorkspace, PassageReview
from backend.retrieval.local_baseline import RetrievalCase, ScopeChanged, search, evaluate_case
from backend.source_grounding import keyword_score
from evaluation.retrieval_v1.synthetic import prepare,OWNER
from evaluation.retrieval_v1.runner import run


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.workspace=SourceWorkspace(Path(self.temp.name)/'workspace');self.addCleanup(self.workspace.close)
        self.cases=[parse_payload(RetrievalCase,c) for c in prepare(self.workspace)]

    def test_existing_keyword_formula_is_preserved(self):
        self.assertEqual(keyword_score('mango mango seed',{'mango','seed'}),2+math.log1p(2)+math.log1p(1))

    def test_expected_names_and_provenance_are_returned(self):
        case=self.cases[0];result=search(self.workspace,OWNER,case)
        self.assertEqual(result['hits'][0]['chunk_id'],case.expected_chunk_ids[0])
        self.assertEqual(result['hits'][0]['edition_id'],case.source.edition_id)
        self.assertEqual(result['hits'][0]['pdf_pages'],[1])
        self.assertEqual(evaluate_case(case,result)['status'],'PASS')

    def test_pending_withdrawn_and_other_edition_never_reach_scoring(self):
        from backend.retrieval import local_baseline
        calls=[];original=local_baseline.keyword_score
        def record(text,terms):calls.append(text);return original(text,terms)
        for index in (5,6,7):
            with patch.object(local_baseline,'keyword_score',side_effect=record):
                result=search(self.workspace,OWNER,self.cases[index])
            self.assertEqual(result['status'],'no_match')
        self.assertFalse(any('lantern' in text or 'silver boat' in text or 'purple kite' in text or 'ribbon' in text for text in calls))

    def test_parent_review_cannot_satisfy_traditional_scope(self):
        case=self.cases[8]
        result=search(self.workspace,OWNER,case)
        self.assertEqual(result['status'],'blocked');self.assertEqual(result['hits'],[])
        self.assertEqual(evaluate_case(case,result)['status'],'PASS')

    def test_wrong_owner_edition_and_generation_rejected(self):
        case=self.cases[0]
        with self.assertRaises(PermissionError):search(self.workspace,'another-parent',case)
        for change in [dict(edition_id='another-edition'),dict(generation=999)]:
            altered=case.model_dump(mode='json');altered['source'].update(change)
            with self.assertRaises(ScopeChanged):search(self.workspace,OWNER,parse_payload(RetrievalCase,altered))

    def test_name_variant_and_older_wording_fail_honestly(self):
        for index in (2,3):
            case=self.cases[index]
            self.assertEqual(evaluate_case(case,search(self.workspace,OWNER,case))['status'],'FAIL')

    def test_pending_labels_do_not_pass(self):
        case=self.cases[9]
        self.assertEqual(evaluate_case(case,search(self.workspace,OWNER,case))['status'],'PENDING')

    def test_unavailable_expected_passage_is_pending_not_search_failure(self):
        case=self.cases[0]
        chunk=next(c for c in self.workspace.chunks(case.source.source_id,OWNER,limit=100) if c['chunk_id']==case.expected_chunk_ids[0])
        self.workspace.review(case.source.source_id,OWNER,PassageReview(expected_generation=case.source.generation,
            chunk_id=chunk['chunk_id'],content_hash=chunk['content_hash'],action='withdraw',notes='Synthetic withdrawal.'))
        self.assertEqual(evaluate_case(case,search(self.workspace,OWNER,case))['status'],'PENDING')

    def test_revision_during_search_invalidates_result(self):
        original=self.workspace.summary;calls=[]
        def changed(*args,**kwargs):
            result=original(*args,**kwargs);calls.append(True)
            if len(calls)>1:result['epoch']+=1
            return result
        with patch.object(self.workspace,'summary',side_effect=changed):
            with self.assertRaises(ScopeChanged):search(self.workspace,OWNER,self.cases[0])

    def test_synthetic_experiment_preserves_failures_and_denominators(self):
        folder=Path(self.temp.name)/'experiment'
        summary=run(self.workspace,OWNER,[c.model_dump(mode='json') for c in self.cases],folder,scope='synthetic')
        self.assertEqual((summary['pass_count'],summary['fail_count'],summary['pending_count']),(7,2,1))
        self.assertEqual(summary['relevance_cases_evaluated'],5)
        self.assertEqual(summary['negative_cases_evaluated'],4)
        self.assertEqual(summary['mean_recall_at_k'],.6)
        self.assertTrue((folder/'inputs.json').exists());self.assertTrue((folder/'traces.json').exists())
        with self.assertRaises(FileExistsError):run(self.workspace,OWNER,[c.model_dump(mode='json') for c in self.cases],folder,scope='synthetic')

    def test_synthetic_labels_cannot_be_reported_as_human_reviewed(self):
        with self.assertRaises(ValueError):run(self.workspace,OWNER,[c.model_dump(mode='json') for c in self.cases],Path(self.temp.name)/'bad',scope='human_reviewed')

    def test_permission_change_between_queries_invalidates_the_experiment_scope(self):
        from evaluation.retrieval_v1 import runner
        original=runner.search;calls=[]
        case=self.cases[0]
        chunk=next(c for c in self.workspace.chunks(case.source.source_id,OWNER,limit=100) if c['chunk_id']==case.expected_chunk_ids[0])
        def withdraw_after_search(*args,**kwargs):
            result=original(*args,**kwargs)
            if not calls:
                self.workspace.review(case.source.source_id,OWNER,PassageReview(expected_generation=case.source.generation,
                    chunk_id=chunk['chunk_id'],content_hash=chunk['content_hash'],action='withdraw',notes='Synthetic concurrent withdrawal.'))
            calls.append(True)
            return result
        with patch.object(runner,'search',side_effect=withdraw_after_search):
            result=run(self.workspace,OWNER,[c.model_dump(mode='json') for c in self.cases[:2]],Path(self.temp.name)/'changed',scope='synthetic')
        self.assertEqual(result['pending_count'],2)
        self.assertEqual(result['relevance_cases_evaluated'],0)
        self.assertIsNone(result['mean_recall_at_k'])
