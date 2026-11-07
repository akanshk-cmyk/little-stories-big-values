"""Synthetic contract tests; never calls a paid model or scores real stories."""
import copy
import csv
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
from backend.env_config import load_local_env
from evaluation.storytelling_v1.cases import cases
from evaluation.storytelling_v1 import runner as r


class EvaluationContracts(unittest.TestCase):
    def test_ten_original_requests_preserve_required_scenarios(self):
        data = cases()
        self.assertEqual([c['Test_ID'] for c in data], [f'STORY-{i:03}' for i in range(1, 11)])
        for case in data:
            text = case['User_Input']
            self.assertIn('\nApproved facts:\n', text)
            for term in ['Child age:', 'Required characters:', 'Learning objective:', 'Preferred language:',
                         'Story length:', 'Mood:', 'Content restrictions:', 'Required output:']:
                self.assertIn(term, text)
            self.assertEqual(case['Model_Output'], '')
            self.assertLessEqual(len(text), 1200)
        self.assertIn('Mira', data[0]['User_Input'])
        self.assertEqual(data[3]['request']['character'], 'rama')
        self.assertEqual(data[4]['request']['character'], 'saraswati')
        self.assertEqual(data[6]['request']['objective'], 'Generosity')

    def test_overall_never_passes_blank_or_unresolved_or_safety_failure(self):
        for scores in itertools.product(['PASS', 'FAIL', 'PENDING', ''], repeat=6):
            actual = r.overall(scores)
            if 'FAIL' in scores:
                self.assertEqual(actual, 'FAIL')
            elif set(scores) == {'PASS'}:
                self.assertEqual(actual, 'PASS')
            else:
                self.assertEqual(actual, 'PENDING')
        self.assertEqual(r.overall(['PASS'] * 5), 'PENDING')
        self.assertIn('COUNTIF(F2:K2,"PASS")=6', r.overall_formula(2))

    def test_workbook_and_csv_are_pending_without_execution_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            r.prepare(folder)
            with (folder / 'story_generation_inputs.csv').open(encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                self.assertEqual(reader.fieldnames, ['Test_ID', 'User_Input', 'Model_Output'])
                self.assertEqual(len(list(reader)), 10)
            book = load_workbook(folder / 'Little_Stories_Evaluation.xlsx')
            self.assertEqual(book.sheetnames, ['Evaluation Log', 'Failure Analysis', 'Failure Traces'])
            sheet = book['Evaluation Log']
            self.assertEqual(sheet.max_row, 11)
            self.assertEqual([c.value for c in sheet[1]], r.COLUMNS)
            for row in range(2, 12):
                self.assertEqual([sheet.cell(row, c).value for c in range(6, 12)], ['PENDING'] * 6)
                self.assertEqual(sheet.cell(row, 12).value, r.overall_formula(row))
                self.assertIsNone(sheet.cell(row, 19).value)
            self.assertTrue(sheet.data_validations.count)
            self.assertTrue(sheet.conditional_formatting)
            self.assertEqual(sheet.auto_filter.ref, 'A1:T11')
            before = (folder / 'story_generation_inputs.csv').read_bytes()
            r.prepare(folder)
            self.assertEqual(before, (folder / 'story_generation_inputs.csv').read_bytes())
            self.assertFalse((folder / 'runs').exists())

    def test_original_inputs_and_rubric_cannot_be_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            r.prepare(folder)
            with patch.object(r, 'RUBRIC', r.RUBRIC + '\nchanged'):
                with self.assertRaisesRegex(ValueError, 'sealed'):
                    r.prepare(folder)
            path = folder / 'story_generation_inputs.csv'
            path.write_text('Test_ID,User_Input,Model_Output\nwrong,changed,\n')
            with self.assertRaisesRegex(ValueError, 'dataset changed'):
                r.prepare(folder)

    def test_missing_key_does_not_invent_executions(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch.object(r, 'load_local_env'):
            folder = Path(temp)
            with self.assertRaisesRegex(ValueError, 'NEBIUS_API_KEY'):
                r.run(folder)
            self.assertFalse((folder / 'runs').exists())
            self.assertEqual(json.loads((folder / 'preflight.json').read_text())['status'], 'BLOCKED')

    def test_runner_uses_app_generator_and_keeps_validation_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            case = cases()[3]
            with patch.object(r.original, 'make_original_book', side_effect=r.festival.UserError('Choose supported character.', 400)) as generate:
                row = r.execute(case, Path(temp))
            generate.assert_called_once_with(case['request'], 'generate')
            self.assertEqual(row['Overall_Result'], 'PENDING')
            self.assertEqual(row['Model_Output'], '')
            trace = json.loads((Path(temp) / 'runs' / row['Execution_ID'] / 'trace.json').read_text())
            self.assertEqual(trace['error'], 'Choose supported character.')
            self.assertIsNone(trace['langgraph_run_id'])
            self.assertEqual(trace['calls'], [])

    def test_redaction_excludes_headers_and_exact_configured_secrets(self):
        with patch.dict(os.environ, {'NEBIUS_API_KEY': 'SYNTHETIC-SECRET-ONLY'}):
            clean = r.redact({'headers': {'Authorization': 'SYNTHETIC-SECRET-ONLY'}, 'content': 'key SYNTHETIC-SECRET-ONLY'})
            self.assertNotIn('headers', clean)
            self.assertNotIn('SYNTHETIC-SECRET-ONLY', json.dumps(clean))

    def test_dimensional_reviews_and_separate_safety_need_evidence(self):
        artifact = {'book': {'title': 'Synthetic fixture'}}
        ref = [{'artifact_id': 'book', 'pointer': '/title', 'quote': 'Synthetic fixture'}]
        result = {'bundle': {'artifacts': artifact}, 'report': {'dimensions': [
            {'name': name, 'status': 'PASS', 'evidence': ref} for name in r.DIMENSIONS],
            'rules': [{'rule_id': 'critical_safety', 'status': 'FAIL', 'evidence': ref}]}}
        self.assertEqual(r.score_review(result), ['PASS'] * 5 + ['FAIL'])
        result['report']['dimensions'][0]['evidence'] = [{'artifact_id': 'book', 'pointer': '/missing', 'quote': 'invented'}]
        self.assertEqual(r.score_review(result)[0], 'PENDING')
        result['report']['rules'] = []
        self.assertEqual(r.score_review(result)[5], 'PENDING')

    def test_full_story_word_count_includes_extras(self):
        self.assertEqual(r.story_words({'title': 'Title words', 'pages': ['one'] * 6, 'moral': 'share kindly',
                                        'discussionQuestion': 'What helped?', 'didYouKnow': 'A fact'}), 14)

    def test_matched_comparison_excludes_unrerun_baseline(self):
        def row(id, kind, verdict, parent=''):
            return {'Test_ID': id, 'Run_Type': kind, 'Original_Test_ID': parent, 'Overall_Result': verdict,
                    **dict.fromkeys(r.SCORE_COLUMNS, verdict)}
        rows = [row('A', 'Original', 'FAIL'), row('B', 'Original', 'PASS'), row('C', 'Original', 'PASS'),
                row('A-R1', 'Revised', 'PASS', 'A'), row('B-R1', 'Revised', 'FAIL', 'B')]
        data = r.comparison(rows)
        self.assertEqual(data['matched_requests'], 2)
        self.assertEqual(data['transitions']['FAIL -> PASS'], 1)
        self.assertEqual(data['prompt_fix_rate'], 1)
        self.assertEqual(data['regression_rate'], 1)
        self.assertEqual(data['matched_original_pass'], 1)
        self.assertEqual(data['matched_revised_pass'], 1)
        self.assertIsNone(r.comparison([])['prompt_fix_rate'])

    def test_no_improvement_claim_without_observed_recurring_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            r.prepare(folder)
            with self.assertRaisesRegex(ValueError, 'two saved failed'):
                r.revise(folder, 'One instruction.', 'Low Informational Value', 'Expected action clarity.')
            self.assertFalse((folder / 'revision.json').exists())

    def test_env_file_is_data_and_process_environment_wins(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'NEBIUS_MODEL': 'existing'}, clear=True):
            path = Path(temp) / '.env'
            path.write_text('NEBIUS_API_KEY="SYNTHETIC-KEY"\nNEBIUS_MODEL=replacement\nUNSAFE=ignored\n')
            load_local_env(path)
            self.assertEqual(os.environ['NEBIUS_API_KEY'], 'SYNTHETIC-KEY')
            self.assertEqual(os.environ['NEBIUS_MODEL'], 'existing')
            self.assertNotIn('UNSAFE', os.environ)

    def test_real_generation_and_reviewer_paths_with_mocked_network(self):
        case = cases()[1]
        story = {'title': 'Synthetic test fixture', 'pages': ['Veyd and Ganesha shared paint. '*7]*6,
                 'moral': 'Take turns.', 'discussionQuestion': 'What helped?'}
        raw = json.dumps(story)
        calls = []
        def provider(url, payload, headers):
            calls.append(copy.deepcopy(payload))
            if len(calls) == 1:
                output = raw
            else:
                data = json.loads(payload['messages'][1]['content'])
                bundle, playbook = data['bundle'], data['playbook']
                refs = [{'artifact_id': 'book', 'pointer': '/pages/0', 'quote': story['pages'][0]}]
                output = json.dumps({'verdict': 'REVISION_REQUIRED', 'summary': 'Synthetic fixture only.',
                    'coverage': bundle['required_coverage'],
                    'dimensions': [{'name': name, 'status': 'FAIL' if name == 'Engagement' else 'PASS',
                                    'note': 'Synthetic assertion fixture.', 'evidence': refs} for name in r.DIMENSIONS],
                    'rules': [{'rule_id': rule['id'], 'status': 'PASS', 'note': 'Synthetic fixture.', 'evidence': refs} for rule in playbook['rules']],
                    'claims': [{'artifact_pointer': '/pages/0', 'claim': story['pages'][0], 'kind': 'FICTIONAL', 'decision': 'FICTIONAL', 'evidence': refs}],
                    'revisions': []})
            return json.dumps({'id': 'synthetic-provider-id', 'model': 'synthetic-model', 'choices': [{'message': {'content': output}}]}).encode(), 'application/json'
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'NEBIUS_API_KEY': 'SYNTHETIC-NOT-A-REAL-KEY'}), patch.object(r.festival, 'provider_json', side_effect=provider):
            row = r.execute(case, Path(temp))
            self.assertEqual(row['Model_Output'], raw)
            self.assertEqual(row['Engagement'], 'FAIL')
            self.assertEqual(row['Overall_Result'], 'FAIL')
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0]['messages'][0]['content'], calls[1]['messages'][0]['content'])
            trace = json.loads((Path(temp) / 'runs' / row['Execution_ID'] / 'trace.json').read_text())
            self.assertEqual([c['phase'] for c in trace['calls']], ['generation', 'review'])
            self.assertNotIn('SYNTHETIC-NOT-A-REAL-KEY', json.dumps(trace))

    def test_human_completion_requires_all_dimensions_and_failure_explanations(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            r.prepare(folder)
            path = folder / 'human_reviews.json'
            reviews = json.loads(path.read_text())
            reviews['STORY-001'].update(status='COMPLETED', reviewer='Synthetic reviewer', feedback='Synthetic review only.')
            path.write_text(json.dumps(reviews))
            with self.assertRaisesRegex(ValueError, 'all five dimensions'):
                r.report(folder)


if __name__ == '__main__':
    unittest.main()
