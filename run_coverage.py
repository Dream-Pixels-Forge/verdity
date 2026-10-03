#!/usr/bin/env python
"""Run coverage for enforcement module."""
import subprocess
import sys

result = subprocess.run([
    sys.executable, "-m", "pytest", 
    "tests/test_enforcement.py",
    "--cov=src/verdity/enforcement",
    "--cov-report=term-missing",
    "-v"
], cwd="/home/dimona/Dream-Pixels-Forge/Dev/cli/verdity", capture_output=True, text=True)

print("STDOUT:")
print(result.stdout)
print("\nSTDERR:")
print(result.stderr)
print(f"\nReturn code: {result.returncode}")