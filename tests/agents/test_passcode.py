"""The verification code accepts one, two, three and nothing else."""

import unittest

from agents.verification.passcode import accepts, attempt, contains_digits


class PasscodeTest(unittest.TestCase):
    def test_words_and_digits_match(self):
        self.assertTrue(accepts("one, two, three"))
        self.assertTrue(accepts("1 2 3"))
        self.assertTrue(accepts("123"))
        self.assertTrue(accepts("please say one two three"))

    def test_anything_else_does_not_match(self):
        self.assertFalse(accepts(""))
        self.assertFalse(accepts("one two"))
        self.assertFalse(accepts("one two three four"))
        self.assertFalse(accepts("one two four"))

    def test_the_caller_must_have_said_the_code(self):
        self.assertTrue(attempt("one two three", "1, 2, 3"))
        self.assertFalse(attempt("one two three", "hello"))

    def test_contains_digits_matches_contiguous_subsequence(self):
        self.assertTrue(contains_digits("my 1 account is 555 010 2000", "5550102000"))
        self.assertFalse(contains_digits("555 9 010 2000", "5550102000"))
        self.assertFalse(contains_digits("5550102000", ""))


if __name__ == "__main__":
    unittest.main()
