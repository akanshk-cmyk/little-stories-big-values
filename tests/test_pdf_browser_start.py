import unittest
from unittest.mock import Mock
from backend.pdf_export_service import wait_debug_port


class BrowserPortTests(unittest.TestCase):
    def test_locked_or_partial_file_is_retried_without_relaunching_browser(self):
        active=Mock();active.read_text.side_effect=[PermissionError(),'', '1234\n', '1234\n/devtools/browser/fixture\n']
        process=Mock();process.poll.return_value=None
        self.assertEqual(wait_debug_port(active,process,clock=lambda:0,sleep=lambda _:None),'1234')
        self.assertEqual(active.read_text.call_count,4)

    def test_exited_browser_stops_waiting(self):
        active=Mock();active.read_text.side_effect=FileNotFoundError()
        process=Mock();process.poll.return_value=1
        with self.assertRaises(RuntimeError):wait_debug_port(active,process,clock=lambda:0,sleep=lambda _:None)

    def test_timeout_does_not_wait_forever(self):
        active=Mock();active.read_text.return_value='invalid'
        process=Mock();process.poll.return_value=None
        with self.assertRaises(RuntimeError):wait_debug_port(active,process,timeout=1,clock=Mock(side_effect=[0,0,2]),sleep=lambda _:None)
