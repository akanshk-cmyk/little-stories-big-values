import json,tempfile,unittest
from pathlib import Path
from pydantic import ValidationError
from backend.contracts import content_hash
from evaluation.hhh_v1.contracts import HHHReview,HHHCheck,validate_review
from evaluation.hhh_v1.runner import offline
from evaluation.hhh_v1.runner import execute_case
from evaluation.hhh_v1.cases import cases

class HHHFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.output=Path(cls.temp.name)/'run'
        cls.summary=offline(cls.output)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def record(self,number):return json.loads((self.output/f'HHH-{number:03d}.json').read_text(encoding='utf-8'))

    def report(self,record,**updates):
        check=HHHCheck(status='PASS',reason='Synthetic schema test only.',evidence_quotes=(record['status'],))
        return HHHReview(case_id=record['case_id'],execution_id=record['execution_id'],subject_hash=content_hash(record),reviewer_id='schema-test',review_kind='synthetic_fixture',helpful=check,honest=check,harmless=check,**updates)

    def test_new_cases_are_unique_and_leave_original_dataset_separate(self):
        self.assertEqual(len(cases()),12);self.assertEqual(len({c.case_id for c in cases()}),12)
        self.assertTrue(all(c.case_id.startswith('HHH-') for c in cases()))

    def test_technical_success_never_becomes_measured_hhh_quality(self):
        self.assertEqual(self.summary['workflow_pass'],12)
        self.assertEqual(self.summary['remote_model_calls'],0)
        self.assertEqual(self.summary['hhh_content_pass'],0)
        self.assertEqual(self.summary['hhh_content_pending'],12)
        self.assertEqual(self.summary['human_reviews'],0)
        self.assertTrue(all(d['status']=='PENDING' for d in self.record(1)['hhh'].values()))

    def test_live_execution_is_not_implicitly_authorised(self):
        from unittest.mock import Mock
        service=Mock(public_generation_mode='provider_live')
        with self.assertRaises(PermissionError):execute_case(service,'owner','child',cases()[0])
        service.start.assert_not_called()

    def test_supported_story_uses_actual_service_and_is_not_parent_approved(self):
        record=self.record(2)
        self.assertEqual(record['execution_id_type'],'actual_workflow_run')
        self.assertIsNotNone(record['draft']);self.assertIsNotNone(record['evidence'])
        self.assertIsNone(record['trace']['values']['approval_id'])
        self.assertTrue(record['parent_approval_ready'])

    def test_missing_evidence_blocks_before_provider(self):
        record=self.record(11)
        self.assertEqual(record['outcome'],'blocked');self.assertEqual(record['fixture_exchanges'],0)
        self.assertFalse(record['parent_approval_ready'])

    def test_critical_safety_failure_is_not_exposed_for_parent_approval(self):
        record=self.record(12)
        self.assertEqual(record['outcome'],'blocked')
        self.assertFalse(record['parent_approval_ready']);self.assertFalse(record['parent_preview_exposed'])
        self.assertLessEqual(record['trace']['values']['correction_count'],2)

    def test_preflight_records_are_not_claimed_as_graph_executions(self):
        record=self.record(7)
        self.assertEqual(record['execution_id_type'],'deterministic_local_preflight_record')
        self.assertIsNone(record['trace']);self.assertEqual(record['fixture_exchanges'],0)

    def test_no_saved_outputs_are_overwritten(self):
        with self.assertRaises(ValueError):offline(self.output)

    def test_pass_requires_evidence(self):
        with self.assertRaises(ValidationError):HHHCheck(status='PASS',reason='No evidence')

    def test_review_is_bound_to_exact_record(self):
        record=self.record(1);report=self.report(record)
        self.assertEqual(validate_review(report,record).overall,'PASS')
        changed=dict(record,status='different')
        with self.assertRaises(ValueError):validate_review(report,changed)

    def test_missing_evidence_and_false_live_quality_labels_are_rejected(self):
        record=self.record(1);report=self.report(record)
        bad=report.model_copy(update={'helpful':HHHCheck(status='FAIL',reason='Fabricated finding',evidence_quotes=('This is not in the saved record.',))})
        with self.assertRaises(ValueError):validate_review(bad,record)
        with self.assertRaises(ValueError):validate_review(report.model_copy(update={'review_kind':'human'}),record)

    def test_pending_is_not_pass_and_failure_is_not_averaged_away(self):
        record=self.record(1);report=self.report(record)
        pending=HHHCheck(status='PENDING',reason='Review not performed.')
        self.assertEqual(report.model_copy(update={'honest':pending}).overall,'PENDING')
        failed=HHHCheck(status='FAIL',reason='Synthetic failure.',evidence_quotes=(record['status'],))
        self.assertEqual(report.model_copy(update={'harmless':failed,'honest':pending}).overall,'FAIL')
