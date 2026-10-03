import subprocess
import sys

# Run the enforcement tests
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