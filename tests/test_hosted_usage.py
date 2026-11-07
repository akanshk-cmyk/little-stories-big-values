import unittest
from backend.ingestion.hosted_usage import parse_credits


class ParseUsageTests(unittest.TestCase):
    def test_documented_nested_credits(self):
        self.assertEqual(parse_credits({"job":{"usage":{"credits":3.0}}}),3.0)

    def test_top_level_extract_usage_is_not_parse_usage(self):
        self.assertIsNone(parse_credits({"usage":{"credits":7}}))

    def test_missing_or_unbilled_remains_unknown(self):
        for value in (None, {}, {"job":None}, {"job":{"usage":None}}, {"job":{"usage":{"credits":None}}}):
            with self.subTest(value=value):self.assertIsNone(parse_credits(value))

    def test_invalid_credits_remain_unknown(self):
        for value in (True,-1,"3",float("inf"),float("nan")):
            with self.subTest(value=value):self.assertIsNone(parse_credits({"job":{"usage":{"credits":value}}}))

    def test_zero_is_valid_measured_usage(self):
        self.assertEqual(parse_credits({"job":{"usage":{"credits":0}}}),0)
