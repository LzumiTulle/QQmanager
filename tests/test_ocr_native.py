"""Opt-in real OS OCR smoke test using a synthetic notice, never user data."""
import os
import sys
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get('QQMANAGER_TEST_OCR') == '1', 'native OCR smoke is opt-in')
class NativeOCRTests(unittest.TestCase):
    def test_synthetic_notice_has_time_and_room(self):
        from qqmanager.ocr import recognize
        # Use an English fixture on Windows so English-only runners can also read it.
        fixture = 'ocr-notice-en.png' if sys.platform == 'win32' else 'ocr-notice.png'
        text = recognize(Path(__file__).parent / 'fixtures' / fixture)
        self.assertIn('A101', text.replace(' ', ''))
        # Windows Chinese OCR can render the time separator at full width.
        self.assertRegex(text.replace(' ', ''), r'09[:：]00')
