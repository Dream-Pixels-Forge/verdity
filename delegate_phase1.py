#!/usr/bin/env python
"""
Delegation script for Phase 1: Enforcement Engine coverage.
This script invokes the implement-coder agent to add missing tests.
"""
import subprocess
import sys
import os

# Read the context file
with open("/home/dimona/Dream-Pixels-Forge/Dev/cli/verdity/.prides/context/I/phase1-enforcement-engine.md", "r") as f:
    context = f.read()

# Create a prompt for the implement-coder
prompt = f"""
You are the implement-coder agent. Your task is to add missing test coverage for the Enforcement Engine (Phase 1).

CONTEXT:
{context}

Your task:
1. Read the source file: src/verdity/enforcement.py
2. Read the existing tests: tests/test_enforcement.py
3. Add the 6 new test cases specified in the context to tests/test_enforcement.py
4. Run the coverage check to verify 100% coverage for enforcement module
5. Ensure all existing tests still pass

Start by reading the files and then implement the tests.
"""

# Write prompt to a file for reference
with open("/home/dimona/Dream-Pixels-Forge/Dev/cli/verdity/phase1_prompt.txt", "w") as f:
    f.write(prompt)

print("Phase 1 delegation context prepared.")
print("Context file: .prides/context/I/phase1-enforcement-engine.md")
print("Prompt file: phase1_prompt.txt")
print()
print("To run the implement-coder agent, use the opencode CLI or manually implement the tests.")
print()
print("Key files to modify:")
print("  - tests/test_enforcement.py (add 6 new test cases)")
print()
print("Verification commands:")
print("  python -m pytest tests/test_enforcement.py -v")
print("  python -m pytest tests/test_enforcement.py --cov=src/verdity/enforcement --cov-report=term-missing")
print("  python -m pytest tests/ -v --cov=src/verdity --cov-report=term-missing")