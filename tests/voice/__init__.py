import unittest


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(__path__[0], pattern="test_*.py"))
    return suite
