"""Opt-in real OS OCR smoke test using a synthetic notice, never user data."""
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get('QQMANAGER_TEST_OCR') == '1', 'native OCR smoke is opt-in')
class NativeOCRTests(unittest.TestCase):
    def test_synthetic_notice_has_time_and_room(self):
        from qqmanager.ocr import recognize
        text = recognize(Path(__file__).parent / 'fixtures' / 'ocr-notice.png')
        self.assertIn('A101', text.replace(' ', ''))
        self.assertIn('09:00', text.replace(' ', ''))
