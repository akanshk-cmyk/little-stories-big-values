"""Audit unit coverage and token budgeting; no model inference or network."""
import unittest
from scripts.audit_saved_source_tokens import checked_units
class AuditBudgetTests(unittest.TestCase):
    def test_subdivision_preserves_exact_source_and_both_budgets(self):
        text=' '.join('word'+str(i) for i in range(900))
        count=lambda value:len(value.split())+2
        units=checked_units(text,count,count)
        self.assertGreater(len(units),1)
        for unit in units:
            self.assertEqual(unit['text'],text[unit['start']:unit['end']])
            self.assertLessEqual(unit['bge_tokens'],512)
            self.assertLessEqual(unit['reranker_pair_tokens']+64,512)
        words=set().union(*(set(unit['text'].split()) for unit in units))
        self.assertEqual(words,set(text.split()))
    def test_small_passage_is_not_split(self):
        count=lambda value:len(value.split())+2
        units=checked_units('A gentle short passage.',count,count)
        self.assertEqual(len(units),1)
        self.assertEqual(units[0]['text'],'A gentle short passage.')
    def test_unsplittable_over_budget_word_is_rejected(self):
        with self.assertRaises(ValueError):checked_units('x'*600,len,len)
if __name__=='__main__':unittest.main()
