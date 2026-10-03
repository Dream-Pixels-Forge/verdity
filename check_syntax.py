#!/usr/bin/env python
"""Quick syntax check for the test file."""
import ast
import sys

with open("/home/dimona/Dream-Pixels-Forge/Dev/cli/verdity/tests/test_enforcement.py", "r") as f:
    source = f.read()

try:
    ast.parse(source)
    print("✅ Syntax check passed - no syntax errors")
except SyntaxError as e:
    print(f"❌ Syntax error: {e}")
    sys.exit(1)