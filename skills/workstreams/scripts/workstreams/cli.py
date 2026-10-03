"""
CLI entry point.
"""

from __future__ import annotations

import argparse
from datetime import datetime, UTC, timedelta
from pathlib import Path

from .config import load_config
from .manager import WorkstreamsManager
from .event_log import get_event_log


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser."""
    parser = argparse.ArgumentParser(description="Workstreams - Parallel development workstreams")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize workstreams")
    init_parser.add_argument("--project", required=True, help="Project name")
    init_parser.add_argument("--workstreams", type=int, default=4, help="Number of workstreams")
    init_parser.add_argument(
        "--multiplexer",
        choices=["tmux", "zellij", "tmuxp", "wezterm", "kitty"],
        default="tmux",
    )
    init_parser.add_argument(
        "--layout",
        choices=["even-horizontal", "even-vertical", "main-horizontal", "tiled", "tabs"],
        default="even-horizontal",
    )
    init_parser.add_argument("--base-branch", default="master")
    init_parser.add_argument("--mode", choices=["worktree", "branch"], default="worktree")
    init_parser.add_argument("--shared-deps", nargs="+", default=[])
    init_parser.add_argument("--force", action="store_true")

    # start
    start_parser = subparsers.add_parser("start", help="Start workstreams")
    start_parser.add_argument("--workstream", type=int, help="Specific workstream to start")
    start_parser.add_argument("--cmd", help="Override command", dest="command_override")

    # status
    status_parser = subparsers.add_parser("status", help="Show workstream status")
    status_parser.add_argument("--workstream", type=int)
    status_parser.add_argument("--verbose", action="store_true")
    status_parser.add_argument("--live", action="store_true")

    # monitor
    monitor_parser = subparsers.add_parser("monitor", help="Live monitoring dashboard")
    monitor_parser.add_argument("--refresh", type=int, default=2, help="Refresh interval in seconds")

    # attach
    attach_parser = subparsers.add_parser("attach", help="Attach to existing multiplexer session")
    attach_parser.add_argument("--multiplexer", choices=["tmux", "zellij"], default="tmux")
    attach_parser.add_argument("--session", help="Session name")

    # assign
    assign_parser = subparsers.add_parser("assign", help="Assign issues to workstream")
    assign_parser.add_argument("--workstream", type=int, required=True)
    assign_parser.add_argument("--issue", type=int, action="append", help="Issue number(s)")

    # sync
    sync_parser = subparsers.add_parser("sync", help="Sync workstream with base branch")
    sync_parser.add_argument("--workstream", type=int)
    sync_parser.add_argument("--rebase", action="store_true")

    # pr
    pr_parser = subparsers.add_parser("pr", help="Create PR from workstream")
    pr_parser.add_argument("--workstream", type=int, required=True)
    pr_parser.add_argument("--title", required=True)
    pr_parser.add_argument("--body", default="")
    pr_parser.add_argument("--base", default="main")
    pr_parser.add_argument("--draft", action="store_true")

    # merge
    merge_parser = subparsers.add_parser("merge", help="Merge workstream PR")
    merge_parser.add_argument("--workstream", type=int, required=True)
    merge_parser.add_argument("--method", choices=["merge", "squash", "rebase"], default="squash")
    merge_parser.add_argument("--delete-branch", action="store_true")
    merge_parser.add_argument("--auto", action="store_true")

    # dispatch
    dispatch_parser = subparsers.add_parser("dispatch", help="Dispatch subagent to workstream")
    dispatch_parser.add_argument("--workstream", type=int, required=True)
    dispatch_parser.add_argument(
        "--subagent",
        choices=[
            "backend-api", "frontend-ui", "database-schema", "testing",
            "documentation", "security", "code-quality", "devops",
        ],
    )
    dispatch_parser.add_argument("--issue", type=int, required=True)
    dispatch_parser.add_argument("--prompt", help="Custom prompt")

    # run
    run_parser = subparsers.add_parser("run", help="Run command in workstream")
    run_parser.add_argument("--workstream", type=int, required=True)
    run_parser.add_argument("--cmd", required=True, dest="run_command")

    # logs
    logs_parser = subparsers.add_parser("logs", help="Show workstream logs")
    logs_parser.add_argument("--workstream", type=int, required=True)
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.add_argument("--lines", type=int, default=100)

    # notify
    notify_parser = subparsers.add_parser("notify", help="Send notification to other terminals")
    notify_parser.add_argument("--title", required=True)
    notify_parser.add_argument("--message", required=True)
    notify_parser.add_argument("--urgency", choices=["low", "normal", "critical"], default="normal")

    # tail
    tail_parser = subparsers.add_parser("tail", help="Tail logs from all workstreams")
    tail_parser.add_argument("--workstream", type=int)
    tail_parser.add_argument("--follow", action="store_true", default=True)

    # events
    events_parser = subparsers.add_parser("events", help="Show subagent events")
    events_parser.add_argument("--workstream", type=int)
    events_parser.add_argument("--since", type=int, default=10, help="Minutes back")
    events_parser.add_argument(
        "--type",
        choices=["started", "progress", "completed", "failed", "error"],
        help="Filter by event type",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Load config
    config_path = Path.cwd() / ".workstreams.yaml"
    config = load_config(config_path)

    # Update config with CLI args
    if hasattr(args, "project"):
        config.project = args.project
    if hasattr(args, "multiplexer"):
        config.multiplexer = args.multiplexer
    if hasattr(args, "layout"):
        config.layout = args.layout
    if hasattr(args, "base_branch"):
        config.base_branch = args.base_branch

    manager = WorkstreamsManager(config)

    # Execute command
    if args.command == "init":
        manager.init_project(force=args.force, num_workstreams=args.workstreams)
    elif args.command == "start":
        manager.start(workstream_id=args.workstream, command=args.command_override)
    elif args.command == "status":
        manager.status(workstream_id=args.workstream, verbose=args.verbose, live=args.live)
    elif args.command == "monitor":
        manager.monitor(refresh=args.refresh)
    elif args.command == "attach":
        manager.attach(multiplexer=args.multiplexer, session=args.session)
    elif args.command == "assign":
        manager.assign(args.workstream, args.issue)
    elif args.command == "sync":
        manager.sync(workstream_id=args.workstream, rebase=args.rebase)
    elif args.command == "pr":
        manager.pr(args.workstream, args.title, args.body, args.base, args.draft)
    elif args.command == "merge":
        manager.merge(args.workstream, args.method, args.delete_branch, args.auto)
    elif args.command == "dispatch":
        manager.dispatch(args.workstream, args.subagent, args.issue, args.prompt)
    elif args.command == "logs":
        manager.logs(args.workstream, args.follow, args.lines)
    elif args.command == "run":
        manager.run(args.workstream, args.run_command)
    elif args.command == "notify":
        manager.notify(args.title, args.message, args.urgency)
    elif args.command == "tail":
        manager.tail_logs(workstream_id=args.workstream, follow=args.follow)
    elif args.command == "events":
        event_log = get_event_log(config.project)
        since = datetime.now(UTC) - timedelta(minutes=args.since)
        events = event_log.get_events(
            workstream_id=args.workstream,
            since=since,
            event_type=args.type,
        )
        for e in events:
            print(f"[{e.timestamp[11:19]}] [{e.subagent}] #{e.issue} - {e.event_type}: {e.message}")
    elif args.command == "cleanup":
        pass


if __name__ == "__main__":
    main()
