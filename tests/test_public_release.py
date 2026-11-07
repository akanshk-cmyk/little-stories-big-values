import tempfile,unittest
from pathlib import Path
from scripts.prepare_public_release import scan,private_values


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_credential_match_never_appears_in_report_values(self):
        token='gsk_'+'A1b2C3d4'*6
        (self.root/'unsafe.py').write_text('key='+token)
        findings,_=scan(self.root,[])
        self.assertEqual(findings[0]['kind'],'groq_key')
        self.assertNotIn(token,str(findings))

    def test_private_configuration_match_is_blocked(self):
        value=b'private-tenant-fixture'
        (self.root/'unsafe.txt').write_bytes(value)
        findings,_=scan(self.root,[('LLAMACLOUD_PROJECT_ID',value)])
        self.assertEqual(findings[0]['kind'],'private_configuration_value')
        self.assertNotIn(value.decode(),str(findings))

    def test_invalid_security_url_only_allowed_in_its_exact_test_file(self):
        tests=self.root/'tests';tests.mkdir()
        value=b'https://' + b'user:pass@example.org/'
        (tests/'test_source_ingestion.py').write_bytes(value)
        self.assertEqual(scan(self.root,[]),([],1))
        (self.root/'application.py').write_bytes(value)
        findings,allowed=scan(self.root,[])
        self.assertEqual(allowed,1);self.assertEqual(findings[0]['kind'],'credential_url')

    def test_database_is_prohibited_even_without_detected_key(self):
        (self.root/'accounts.sqlite').write_bytes(b'fixture database')
        self.assertEqual(scan(self.root,[])[0][0]['kind'],'prohibited_runtime_artifact')

    def test_example_env_is_allowed_but_populated_env_is_not(self):
        (self.root/'.env.example').write_text('GROQ_API_KEY=\n')
        self.assertFalse(scan(self.root,[])[0])
        (self.root/'.env').write_text('GROQ_API_KEY=private-fixture-credential\n')
        self.assertTrue(scan(self.root,[])[0])
        values=private_values(self.root)
        self.assertIn(('GROQ_API_KEY',b'private-fixture-credential'),values)
