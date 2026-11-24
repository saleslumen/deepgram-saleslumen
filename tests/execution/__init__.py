import os
import unittest


def load_tests(loader, tests, pattern):
    return loader.discover(os.path.dirname(__file__), pattern="test_*.py")
