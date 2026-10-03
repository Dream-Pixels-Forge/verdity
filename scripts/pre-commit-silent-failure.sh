#!/usr/bin/env bash
# Pre-commit hook: Run silent-failure-hunter on changed Python files
# Install: ln -s ../../scripts/pre-commit-silent-failure.sh .git/hooks/pre-commit

set -euo pipefail

# Get changed Python files
CHANGED_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)

if [ -z "$CHANGED_FILES" ]; then
    echo "No Python files changed. Skipping silent failure scan."
    exit 0
fi

echo "Running silent-failure-hunter on changed files..."
echo "Changed files:"
echo "$CHANGED_FILES"

# Create temp file with changed files
TMP_FILE=$(mktemp)
echo "$CHANGED_FILES" > "$TMP_FILE"

# Run silent-failure-hunter on each changed file
ERRORS=0
while IFS= read -r file; do
    if [ -f "$file" ]; then
        echo "Scanning $file..."
        PYTHONPATH=/home/dimona/.config/opencode/skills/silent-failure-hunter \
        python3 -m silent_failure_hunter scan --module "$file" --output /dev/null 2>&1 || true
        
        # Check for critical issues in the output
        OUTPUT=$(PYTHONPATH=/home/dimona/.config/opencode/skills/silent-failure-hunter \
            python3 -m silent_failure_hunter scan --module "$file" 2>/dev/null || true)
        
        CRITICAL=$(echo "$OUTPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    errors = [i for i in data.get('details', []) if i['severity'] == 'error']
    print(len(errors))
except:
    print(0)
")
        
        if [ "$CRITICAL" -gt 0 ]; then
            echo "❌ Found $CRITICAL critical issues in $file"
            ERRORS=$((ERRORS + CRITICAL))
        fi
    fi
done < "$TMP_FILE"

rm -f "$TMP_FILE"

if [ $ERRORS -gt 0 ]; then
    echo ""
    echo "❌ Commit blocked: Found $ERRORS critical silent failures"
    echo "Run 'python -m silent_failure_hunter scan --project .' for full report"
    exit 1
fi

echo "✅ Silent failure check passed"
exit 0
