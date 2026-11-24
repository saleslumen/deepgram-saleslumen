"""Speech-to-text settings the call depends on."""

import unittest
from unittest.mock import MagicMock

from voice.providers import build_stt


class SpeechToTextTest(unittest.TestCase):
    def test_flux_writes_spoken_numbers_as_digits(self):
        stt = build_stt(MagicMock(stt_provider="deepgram-flux", deepgram_api_key="key"))
        self.assertIs(stt._settings.numerals, True)


if __name__ == "__main__":
    unittest.main()
