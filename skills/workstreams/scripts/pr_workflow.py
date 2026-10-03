#!/usr/bin/env python3
"""
Workstreams PR Workflow Helper

Automates the PR-driven development workflow for workstreams:
1. Creates GitHub issue from task description
2. Creates feature branch
3. Runs TDD cycle (test -> implement -> test)
4. Formats and lints code
5. Creates PR with proper formatting
"""

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def run_cmd(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run command and return result."""
    result = subprocess.run(cmd, check=check, capture_output=capture, text=True)
    return result


def slugify(text: str) -> str:
    """Convert text to slug."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', text.lower())
    return slug.strip('-')


def run_cmd(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run command and return result."""
    result = subprocess.run(cmd, check=check, capture_output=capture, text=True)
    return result


def create_issue(title: str, body: str, labels: list[str]) -> str:
    """Create GitHub issue and return issue number."""
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    for label in labels:
        cmd.extend(["--label", label])
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create issue: {result.stderr}")
    # Extract issue number from URL
    url = result.stdout.strip()
    issue_num = url.split("/")[-1]
    return issue_num


def create_branch(issue_num: str, slug: str) -> str:
    """Create feature branch from master."""
    branch = f"feature/{issue_num}-{slug}"
    run_cmd(["git", "checkout", "master"])
    run_cmd(["git", "pull", "origin", "master"])
    run_cmd(["git", "checkout", "-b", branch])
    return branch


def run_tdd_cycle(test_file: str) -> bool:
    """Run TDD cycle: test -> implement -> test."""
    # Run tests to see failures
    result = run_cmd(["pytest", test_file, "-v", "--tb=short"], check=False)
    if result.returncode == 0:
        print("Tests already passing - no implementation needed")
        return True
    
    print("Tests failing as expected. Implement the feature...")
    print("After implementation, run: pytest", test_file, "-v")
    return False


def run_tests() -> bool:
    """Run full test suite."""
    result = run_cmd(["pytest", "tests/", "-q"], check=False)
    return result.returncode == 0


def format_and_lint() -> bool:
    """Run ruff format and lint."""
    result = run_cmd(["ruff", "format", "src/", "tests/"], check=False)
    if result.returncode != 0:
        return False
    result = run_cmd(["ruff", "check", "src/", "tests/"], check=False)
    return result.returncode == 0


def create_pr(issue_num: str, title: str, body: str) -> str:
    """Create PR linked to issue."""
    cmd = [
        "gh", "pr", "create",
        "--title", title,
        "--body", body,
        "--label", "needs-review"
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create PR: {result.stderr}")
    return result.stdout.strip()


def slugify(text: str) -> str:
    """Convert text to slug."""
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', text.lower())
    return slug.strip('-')


def main():
    parser = argparse.ArgumentParser(description="PR-Driven Development Helper for Workstreams")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Create issue
    create_parser = subparsers.add_parser("create-issue", help="Create GitHub issue")
    create_parser.add_argument("--title", required=True, help="Issue title")
    create_parser.add_argument("--body", required=True, help="Issue body")
    create_parser.add_argument("--labels", nargs="+", default=["enhancement"], help="Labels")

    # Create branch
    branch_parser = subparsers.add_parser("create-branch", help="Create feature branch")
    branch_parser.add_argument("--issue", required=True, help="Issue number")
    branch_parser.add_argument("--slug", required=True, help="Branch slug")

    # TDD cycle
    tdd_parser = subparsers.add_parser("tdd", help="Run TDD cycle")
    tdd_parser.add_argument("--test-file", required=True, help="Test file path")

    # Full workflow
    workflow_parser = subparsers.add_parser("workflow", help="Run full PR workflow")
    workflow_parser.add_argument("--title", required=True, help="Feature title")
    workflow_parser.add_argument("--body", required=True, help="Feature description")
    workflow_parser.add_argument("--labels", nargs="+", default=["enhancement"], help="Labels")

    args = parser.parse_args()

    if args.command == "create-issue":
        issue_num = create_issue(args.title, args.body, args.labels)
        print(f"Created issue #{issue_num}")

    elif args.command == "create-branch":
        branch = create_branch(args.issue, args.slug)
        print(f"Created branch: {branch}")

    elif args.command == "tdd":
        run_tdd_cycle(args.test_file)

    elif args.command == "workflow":
        # Full PR-driven workflow
        slug = slugify(args.title)
        
        print(f"1. Creating issue...")
        issue_num = create_issue(args.title, args.body, args.labels)
        print(f"   Created issue #{issue_num}")

        print(f"2. Creating branch...")
        branch = create_branch(issue_num, slug)
        print(f"   Created branch: {branch}")

        print(f"3. TDD cycle - write tests first!")
        print(f"   Run: pytest tests/test_<module>.py -v --tb=short")
        print(f"   After implementation: pytest tests/test_<module>.py -v")

        print(f"4. Format & lint...")
        if format_and_lint():
            print("   ✓ Formatting and linting passed")
        else:
            print("   ✗ Formatting/linting failed - fix before commit")
            sys.exit(1)

        print(f"5. Running tests...")
        if run_tests():
            print("   ✓ All tests passing")
        else:
            print("   ✗ Tests failing - fix before PR")
            sys.exit(1)

        print(f"6. Creating PR...")
        pr_body = f"## Changes\n- Implemented {args.title}\n## Linked Issue\nCloses #{issue_num}"
        pr_url = create_pr(issue_num, f"feat(#{issue_num}): {args.title}", pr_body)
        print(f"   Created PR: {pr_url}")
        
        print(f"\n✅ Workflow complete! CI will run on PR.")


if __name__ == "__main__":
    import re
    import subprocess
    main()