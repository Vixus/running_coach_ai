"""Root conftest.py — ensures the project root is on sys.path for all tests."""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
