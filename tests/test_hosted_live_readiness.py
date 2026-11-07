import importlib.util
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from pydantic import SecretStr
from pypdf import PdfWriter
from backend.retrieval.hosted_config import HostedSettings

spec = importlib.util.spec_from_file_location("readiness", Path(__file__).resolve().parents[1] / "scripts/check_hosted_live_readiness.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LiveReadinessTests(unittest.TestCase):
    def configured(self, **changes):
        values = dict(enabled=True, api_key=SecretStr("never-export-this"), organization_id="org", project_id="project", index_id="index", api_generation="v2")
        return HostedSettings(**(values | changes))

    def test_missing_config_blocks_without_exposing_secret(self):
        result = module.inspect(HostedSettings())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("api_key", result["missing_configuration"])
        self.assertEqual(result["network_calls"], 0)

    def test_sample_is_not_live_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(sample)
            result = module.inspect(self.configured(), sample)
        self.assertEqual(result["status"], "LOCAL_PREFLIGHT_PASS")
        self.assertFalse(result["spending_authorized"])
        self.assertNotIn("never-export-this", str(result))
        self.assertEqual(result["authentication"], "NOT_CHECKED")

    def test_full_book_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.pdf"
            writer = PdfWriter()
            for _ in range(4): writer.add_blank_page(width=100, height=100)
            writer.write(sample)
            result = module.inspect(self.configured(), sample)
        self.assertEqual(result["status"], "BLOCKED")

    def test_wrong_api_generation_blocks(self):
        result = module.inspect(self.configured(api_generation="v1"))
        self.assertTrue(any("v2" in text for text in result["blockers"]))
