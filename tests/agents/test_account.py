"""The stand-in account. find is the only door a later lookup replaces."""

import unittest

from agents.verification.account import find


class AccountTest(unittest.TestCase):
    def test_a_known_number_has_a_name(self):
        self.assertEqual(find("5550102000"), "Jordan")
        self.assertEqual(find("five five five zero one zero two zero zero zero"), "Jordan")
        self.assertEqual(find("555-010-2000"), "Jordan")
        self.assertIsNone(find(""))
        self.assertIsNone(find("5550199999"))


if __name__ == "__main__":
    unittest.main()
