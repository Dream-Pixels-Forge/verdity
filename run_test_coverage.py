#!/usr/bin/env python
"""Run enforcement tests and check coverage."""
import subprocess
import sys

print("Running enforcement tests...")
result = subprocess.run([
    sys.executable, "-m", "pytest", 
    "tests/test_enforcement.py",
    "-v",
    "--tb=short"
], cwd="/home/dimona/Dream-Pixels-Forge/Dev/cli/verdity", capture_output=True, text=True)

print("STDOUT:")
print(result.stdout)
print("\nSTDERR:")
print(result.stderr)
print(f"\nReturn code: {result.returncode}")

if result.returncode == 0:
    print("\n✅ Tests passed! Now checking coverage...")
    # Run coverage
    cov_result = subprocess.run([
        sys.executable, "-m", "pytest", 
        "tests/test_enforcement.py",
        "--cov=src/verdity/enforcement",
        "--cov-report=term-missing",
        "-v"
    ], cwd="/home/dimona/Dream-Pixels-Forge/Dev/cli/verdity", capture_output=True, text=True)
    
    print("\nCOVERAGE STDOUT:")
    print(cov_result.stdout)
    print("\nCOVERAGE STDERR:")
    print(cov_result.stderr)
    print(f"\nCoverage Return code: {cov_result.returncode}")
else:
    print("\n❌ Tests failed!")