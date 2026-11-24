"""The verification code accepts one, two, three and nothing else."""

import unittest

from agents.verification.passcode import accepts, attempt


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


if __name__ == "__main__":
    unittest.main()
