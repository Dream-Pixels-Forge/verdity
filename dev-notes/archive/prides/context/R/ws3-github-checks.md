# Workstream 3: Enhanced GitHub Checks API

## Task
Enhance GitHub Checks API with inline annotations, markdown summaries, action buttons, and auto-fix suggestions.

## Current State
- `src/verdity/github_client.py` - Has basic `create_check_run` and `update_check_run`
- Tests exist in `tests/test_github_client.py` and `tests/test_enforcement.py`
- Current coverage: 12 lines missed (92% coverage)

## Required Enhancements

### 1. Inline Annotations (Line-Level Feedback)
```python
# GitHub Checks API supports annotations array
annotations = [
    {
        "path": f.file,
        "start_line": f.line_start,
        "end_line": f.line_end,
        "annotation_level": "failure" if f.severity in (Severity.CRITICAL, Severity.HIGH) else "warning",
        "message": f.summary,
        "title": f.concern.value
    }
    for f in findings
]
```

### 2. Rich Markdown Summary
```python
output = {
    "title": "Verdity Code Review",
    "summary": f"## Summary\n\nFound **{len(findings)} issues** in this PR.",
    "text": generate_detailed_markdown(findings),
    "annotations": annotations[:50]  # GitHub limit
}
```

### 3. Action Buttons
```python
# GitHub Checks supports actions
actions = [
    {
        "label": "Re-run Verdity",
        "description": "Re-run the review on this commit",
        "identifier": "rerun-verdity"
    },
    {
        "label": "Dismiss Findings",
        "description": "Dismiss all findings for this PR",
        "identifier": "dismiss-findings"
    }
]
```

### 4. Auto-Fix Suggestions in Output
```python
# Include suggested fixes in check output
if finding.suggested_fix:
    annotations.append({
        "path": f.file,
        "start_line": f.line_start,
        "end_line": f.line_end,
        "annotation_level": "notice",
        "message": f"Suggested fix: {f.suggested_fix}",
        "title": "Auto-fix available"
    })
```

## Implementation Tasks

### 1. Update `create_check_run` in `src/verdity/github_client.py`
- Add `output` parameter support
- Add `actions` parameter support
- Properly format annotations array

### 2. Update `update_check_run` in `src/verdity/github_client.py`
- Support updating output with annotations
- Support updating conclusion based on findings

### 3. Add Helper Functions
```python
def create_annotations(findings: list[Finding]) -> list[dict]:
    # Convert findings to GitHub annotations format

def create_check_output(findings: list[Finding]) -> dict:
    # Create rich output with summary, text, annotations
```

### 4. Update Orchestrator/Router Integration
- Call `create_check_run` at start of review
- Call `update_check_run` with findings at end
- Handle both success and failure conclusions

## Testing Requirements
- Unit tests for annotation creation
- Unit tests for markdown generation
- Integration tests with mock GitHub API
- Test annotation limit (50 max)
- Test action button handling

## Files to Modify
- `src/verdity/github_client.py` - Main implementation
- `tests/test_github_client.py` - Add tests for new features
- `tests/test_enforcement.py` - Integration tests