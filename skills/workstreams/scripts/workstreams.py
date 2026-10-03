#!/usr/bin/env python3
"""
Workstreams CLI - Main entry point for managing parallel development workstreams.

Supports tmux, zellij, and other terminal multiplexers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC, timedelta
import yaml


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class WorkstreamConfig:
    id: int
    name: str
    path: str
    branch: str
    command: str
    env: dict = field(default_factory=dict)
    multiplexer: str = "tmux"
    layout: str = "even-horizontal"
    shared_deps: List[str] = field(default_factory=list)
    mode: str = "worktree"  # worktree|branch


@dataclass
class WorkstreamsConfig:
    project: str
    multiplexer: str = "tmux"
    layout: str = "even-horizontal"
    base_branch: str = "master"
    mode: str = "worktree"
    workstreams: List[WorkstreamConfig] = field(default_factory=list)
    shared_deps: List[str] = field(default_factory=list)
    base_path: str = "."


@dataclass
class WorkstreamStatus:
    """Real-time status of a workstream."""
    id: int
    name: str
    branch: str
    path: str
    git_status: str = "unknown"
    last_commit: str = ""
    last_activity: str = ""
    pid: Optional[int] = None
    command: str = ""
    log_tail: List[str] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)


@dataclass
class SubagentEvent:
    """Event from a subagent."""
    workstream_id: int
    subagent: str
    issue: int
    event_type: str  # started, progress, completed, failed, error
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# File-Based Event Log (Cross-Process)
# =============================================================================

class EventLog:
    """File-based event log for cross-process subagent communication."""

    def __init__(self, project: str):
        self.project = project
        self.log_dir = Path.home() / ".workstreams" / project
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.log_dir / "events.jsonl"
        self.lock_file = self.log_dir / "events.lock"

    def _acquire_lock(self, timeout: float = 1.0) -> bool:
        """Simple file-based lock."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Try to create lock file exclusively
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.01)
        return False

    def _release_lock(self):
        """Release lock."""
        try:
            self.lock_file.unlink()
        except Exception:
            pass

    def append(self, event: SubagentEvent):
        """Append event to log file."""
        if self._acquire_lock():
            try:
                with open(self.events_file, "a") as f:
                    f.write(json.dumps(asdict(event)) + "\n")
            finally:
                self._release_lock()
        else:
            # Fallback: append without lock (rare race condition)
            with open(self.events_file, "a") as f:
                f.write(json.dumps(asdict(event)) + "\n")

    def get_events(self, workstream_id: Optional[int] = None, since: Optional[datetime] = None) -> List[SubagentEvent]:
        """Get filtered events."""
        if not self.events_file.exists():
            return []

        events = []
        with open(self.events_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = SubagentEvent(**data)
                    if workstream_id and event.workstream_id != workstream_id:
                        continue
                    if since and datetime.fromisoformat(event.timestamp) < since:
                        continue
                    events.append(event)
                except Exception:
                    continue
        return events

    def clear(self):
        """Clear event log."""
        if self.events_file.exists():
            self.events_file.unlink()


# Global event log instance
_event_log: Optional[EventLog] = None


def get_event_log(project: str) -> EventLog:
    """Get or create global event log."""
    global _event_log
    if _event_log is None or _event_log.project != project:
        _event_log = EventLog(project)
    return _event_log


# =============================================================================
# Cross-Terminal Notifier
# =============================================================================

class Notifier:
    """Send notifications to other terminals."""

    def __init__(self, project: str):
        self.project = project
        self.notify_dir = Path.home() / ".workstreams" / project
        self.notify_dir.mkdir(parents=True, exist_ok=True)
        self.notify_file = self.notify_dir / "notifications.jsonl"

    def send(self, title: str, message: str, urgency: str = "normal"):
        """Send notification via desktop notification and/or file."""
        # Try desktop notification
        try:
            subprocess.run(["notify-send", "-u", urgency, title, message], check=False, capture_output=True)
        except Exception:
            pass

        # Write to notification file for other instances to pick up
        try:
            notif = {
                "title": title,
                "message": message,
                "urgency": urgency,
                "timestamp": datetime.now(UTC).isoformat()
            }
            with open(self.notify_file, "a") as f:
                f.write(json.dumps(notif) + "\n")
        except Exception:
            pass

    def get_notifications(self, since: Optional[datetime] = None) -> List[Dict]:
        """Get pending notifications."""
        if not self.notify_file.exists():
            return []

        notifs = []
        with open(self.notify_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    notif = json.loads(line)
                    if since and datetime.fromisoformat(notif["timestamp"]) < since:
                        continue
                    notifs.append(notif)
                except Exception:
                    continue
        return notifs

    def clear_notifications(self):
        """Clear notification file."""
        if self.notify_file.exists():
            self.notify_file.unlink()


# =============================================================================
# Live Dashboard
# =============================================================================

class LiveDashboard:
    """Terminal-based live monitoring dashboard."""

    def __init__(self, manager: 'WorkstreamsManager'):
        self.manager = manager
        self.running = False
        self.refresh_rate = 2  # seconds
        self.width = 120
        self.height = 40

    def run(self):
        """Run the live dashboard."""
        self.running = True
        try:
            while self.running:
                self._render()
                time.sleep(self.refresh_rate)
        except KeyboardInterrupt:
            pass
        finally:
            print("\n\033[?25h\033[0m", end="")  # Show cursor, reset colors

    def _render(self):
        """Render dashboard."""
        # Clear screen and move to top
        print("\033[2J\033[H\033[?25l", end="")  # Clear, home, hide cursor

        # Header
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\033[1;36m{'='*self.width}\033[0m")
        print(f"\033[1;36m  WORKSTREAMS MONITOR  |  {self.manager.config.project}  |  {now}  |  Press Ctrl+C to exit\033[0m")
        print(f"\033[1;36m{'='*self.width}\033[0m")

        # Get status for all workstreams
        statuses = self._get_all_statuses()

        # Summary bar
        total = len(statuses)
        active = sum(1 for s in statuses if s.pid is not None)
        dirty = sum(1 for s in statuses if s.git_status != "clean")
        alerts = sum(len(s.alerts) for s in statuses)
        print(f"  Total: {total}  |  Active: {active}  |  Dirty: {dirty}  |  Alerts: {alerts}")
        print(f"\033[90m{'-'*self.width}\033[0m")

        # Workstream table
        print(f"  {'ID':>3}  {'NAME':<15}  {'BRANCH':<20}  {'STATUS':<12}  {'LAST COMMIT':<30}  {'ACTIVITY'}")
        print(f"\033[90m{'-'*self.width}\033[0m")

        for status in statuses:
            self._print_workstream_row(status)

        # Recent alerts
        all_alerts = []
        for s in statuses:
            for alert in s.alerts:
                all_alerts.append(f"[{s.name}] {alert}")
        if all_alerts:
            print(f"\n\033[1;33m  RECENT ALERTS:\033[0m")
            for alert in all_alerts[-5:]:
                print(f"  \033[33m⚠\033[0m  {alert}")

        # Recent subagent events
        events = self._get_recent_events()
        if events:
            print(f"\n\033[1;35m  SUBAGENT ACTIVITY:\033[0m")
            for event in events[-10:]:
                color = self._event_color(event.event_type)
                print(f"  \033[{color}m{event.timestamp[11:19]}\033[0m  [{event.subagent}]  {event.message}")

    def _get_all_statuses(self) -> List[WorkstreamStatus]:
        """Get status for all workstreams."""
        statuses = []
        for ws in self.manager.config.workstreams:
            statuses.append(self._get_workstream_status(ws))
        return statuses

    def _get_workstream_status(self, ws: WorkstreamConfig) -> WorkstreamStatus:
        """Get detailed status for one workstream."""
        ws_path = self.manager.base_path / ws.path
        git_status = "unknown"
        last_commit = ""
        last_activity = ""
        pid = None

        if ws_path.exists():
            # Git status
            result = subprocess.run(["git", "status", "--short"], cwd=ws_path, capture_output=True, text=True)
            if result.returncode == 0:
                git_status = "dirty" if result.stdout.strip() else "clean"

            # Last commit
            result = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=ws_path, capture_output=True, text=True)
            if result.returncode == 0:
                last_commit = result.stdout.strip()[:50]

            # Check for running processes
            try:
                result = subprocess.run(
                    ["pgrep", "-f", f"{ws.path}"],
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    pid = int(result.stdout.strip().split()[0])
            except Exception:
                pass

        # Get recent log tail
        log_tail = []
        log_file = ws_path / "logs" / "worker.log"
        if log_file.exists():
            try:
                result = subprocess.run(["tail", "-5", str(log_file)], capture_output=True, text=True)
                log_tail = result.stdout.strip().split('\n')
            except Exception:
                pass

        # Get alerts from log
        alerts = []
        if log_file.exists():
            try:
                result = subprocess.run(
                    ["grep", "-i", "error\\|fail\\|warning", str(log_file)],
                    capture_output=True, text=True
                )
                if result.stdout:
                    alerts = result.stdout.strip().split('\n')[-3:]
            except Exception:
                pass

        return WorkstreamStatus(
            id=ws.id,
            name=ws.name,
            branch=ws.branch,
            path=ws.path,
            git_status=git_status,
            last_commit=last_commit,
            last_activity=last_activity,
            pid=pid,
            command=ws.command,
            log_tail=log_tail,
            alerts=alerts,
        )

    def _print_workstream_row(self, status: WorkstreamStatus):
        """Print one workstream row."""
        if status.git_status == "clean":
            status_color = "32"  # green
        elif status.git_status == "dirty":
            status_color = "33"  # yellow
        else:
            status_color = "31"  # red

        name = status.name[:14]
        branch = status.branch[:19]
        commit = status.last_commit[:29]

        print(f"  {status.id:>3}  \033[1m{name:<15}\033[0m  {branch:<20}  \033[{status_color}m{status.git_status:<12}\033[0m  {commit:<30}  {status.last_activity}")

    def _get_recent_events(self) -> List[SubagentEvent]:
        """Get recent subagent events from event log."""
        event_log = get_event_log(self.manager.config.project)
        since = datetime.now(UTC) - timedelta(minutes=10)
        return event_log.get_events(since=since)

    def _event_color(self, event_type: str) -> str:
        colors = {
            "started": "36",    # cyan
            "progress": "34",   # blue
            "completed": "32",  # green
            "failed": "31",     # red
            "error": "31",      # red
        }
        return colors.get(event_type, "37")


# =============================================================================
# Workstreams Manager (Enhanced)
# =============================================================================

class WorkstreamsManager:
    def __init__(self, config: WorkstreamsConfig):
        self.config = config
        self.base_path = Path(config.base_path).resolve()
        self.config_file = self.base_path / ".workstreams.yaml"
        self.event_log = get_event_log(config.project)
        self.notifier = Notifier(config.project)

    def init_project(self, force: bool = False, num_workstreams: int = 4):
        """Initialize workstreams for a project."""
        if self.config_file.exists() and not force:
            print(f"Configuration already exists at {self.config_file}. Use --force to overwrite.")
            return False

        # If no workstreams defined, create defaults based on --workstreams argument
        if not self.config.workstreams:
            for i in range(1, num_workstreams + 1):
                ws = WorkstreamConfig(
                    id=i,
                    name=f"ws{i}",
                    path=f"ws{i}",
                    branch=f"ws/{i}",
                    command="",
                    env={},
                )
                self.config.workstreams.append(ws)

        # Create worktrees/branches
        for ws in self.config.workstreams:
            self._create_workstream(ws)

        # Write config
        self._write_config()
        print(f"Initialized {len(self.config.workstreams)} workstreams for project '{self.config.project}'")
        return True

    def _create_workstream(self, ws: WorkstreamConfig):
        """Create a workstream (worktree or branch)."""
        ws_path = self.base_path / ws.path
        ws_path.parent.mkdir(parents=True, exist_ok=True)

        if self.config.mode == "worktree":
            branch = ws.branch
            try:
                subprocess.run(["git", "worktree", "add", str(ws_path), branch], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                subprocess.run(["git", "branch", branch, self.config.base_branch], check=True, capture_output=True)
                subprocess.run(["git", "worktree", "add", str(ws_path), branch], check=True)
        else:
            ws_path.mkdir(parents=True, exist_ok=True)

        # Create logs directory
        (ws_path / "logs").mkdir(exist_ok=True)

    def _write_config(self):
        """Write configuration to .workstreams.yaml"""
        config_dict = {
            "project": self.config.project,
            "multiplexer": self.config.multiplexer,
            "layout": self.config.layout,
            "base_branch": self.config.base_branch,
            "mode": self.config.workstreams[0].mode if self.config.workstreams else "worktree",
            "shared_deps": self.config.shared_deps,
            "workstreams": [
                {
                    "id": ws.id,
                    "name": ws.name,
                    "path": ws.path,
                    "branch": ws.branch,
                    "command": ws.command,
                    "env": ws.env,
                }
                for ws in self.config.workstreams
            ]
        }
        with open(self.config_file, 'w') as f:
            yaml.dump(config_dict, f, default_flow_style=False)

    def start(self, workstream_id: Optional[int] = None, command: Optional[str] = None):
        """Start workstreams in multiplexer."""
        if self.config.multiplexer == "tmux":
            self._start_tmux(workstream_id, command)
        elif self.config.multiplexer == "zellij":
            self._start_zellij(workstream_id, command)
        else:
            print(f"Unsupported multiplexer: {self.config.multiplexer}")

    def _start_tmux(self, workstream_id: Optional[int] = None, command: Optional[str] = None):
        """Start workstreams in tmux."""
        session_name = f"workstreams-{self.config.project}"

        result = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
        if result.returncode != 0:
            tmux_cmd = [
                "tmux", "new-session", "-d", "-s", session_name,
                "-c", str(self.base_path)
            ]
            subprocess.run(tmux_cmd, check=True)

        workstreams = self.config.workstreams
        if workstream_id:
            workstreams = [ws for ws in workstreams if ws.id == workstream_id]

        layout = self.config.layout
        if layout == "even-horizontal":
            self._setup_even_horizontal_tmux(workstreams)
        elif layout == "even-vertical":
            self._setup_even_vertical_tmux(workstreams)
        elif layout == "main-horizontal":
            self._setup_main_horizontal_tmux(workstreams)
        elif layout == "tiled":
            self._setup_tiled_tmux(workstreams)

        for i, ws in enumerate(workstreams):
            pane = f"workstreams-{self.config.project}:{i}"
            cmd = command or ws.command
            if cmd:
                tmux_cmd = ["tmux", "send-keys", "-t", pane, cmd, "Enter"]
                subprocess.run(tmux_cmd)

        print(f"Started {len(workstreams)} workstream(s) in tmux session '{session_name}'")

    def _setup_even_horizontal_tmux(self, workstreams):
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(["tmux", "rename-window", "-t", f"workstreams-{self.config.project}", ws.name])
            else:
                subprocess.run(["tmux", "split-window", "-h", "-t", f"workstreams-{self.config.project}", "-c", str(self.base_path / ws.path)])
            subprocess.run(["tmux", "select-pane", "-t", f"workstreams-{self.config.project}.{i}", "-T", ws.name])

    def _setup_even_vertical_tmux(self, workstreams):
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(["tmux", "rename-window", "-t", f"workstreams-{self.config.project}", ws.name])
            else:
                subprocess.run(["tmux", "split-window", "-v", "-t", f"workstreams-{self.config.project}", "-c", str(self.base_path / ws.path)])
            subprocess.run(["tmux", "select-pane", "-t", f"workstreams-{self.config.project}.{i}", "-T", ws.name])

    def _setup_main_horizontal_tmux(self, workstreams):
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(["tmux", "rename-window", "-t", f"workstreams-{self.config.project}", ws.name])
            elif i == 1:
                subprocess.run(["tmux", "split-window", "-h", "-t", f"workstreams-{self.config.project}", "-c", str(self.base_path / ws.path)])
            else:
                subprocess.run(["tmux", "split-window", "-v", "-t", f"workstreams-{self.config.project}.1", "-c", str(self.base_path / ws.path)])
            subprocess.run(["tmux", "select-pane", "-t", f"workstreams-{self.config.project}.{i}", "-T", ws.name])

    def _setup_tiled_tmux(self, workstreams):
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(["tmux", "rename-window", "-t", f"workstreams-{self.config.project}", ws.name])
            elif i == 1:
                subprocess.run(["tmux", "split-window", "-h", "-t", f"workstreams-{self.config.project}", "-c", str(self.base_path / ws.path)])
            elif i == 2:
                subprocess.run(["tmux", "split-window", "-v", "-t", f"workstreams-{self.config.project}.0", "-c", str(self.base_path / ws.path)])
            else:
                subprocess.run(["tmux", "split-window", "-v", "-t", f"workstreams-{self.config.project}.1", "-c", str(self.base_path / ws.path)])
            subprocess.run(["tmux", "select-pane", "-t", f"workstreams-{self.config.project}.{i}", "-T", ws.name])

    def _start_zellij(self, workstream_id: Optional[int] = None, command: Optional[str] = None):
        """Start workstreams in zellij."""
        session_name = f"workstreams-{self.config.project}"
        subprocess.run(["zellij", "attach", session_name, "--create"], check=False)

        for ws in self.config.workstreams:
            if workstream_id and ws.id != workstream_id:
                continue

            tab_name = ws.name
            subprocess.run(["zellij", "action", "new-tab", "--name", tab_name])

            if ws.command:
                subprocess.run(["zellij", "run", "--", "bash", "-c", f"cd {ws.path} && {ws.command}"])

    def attach(self, multiplexer: Optional[str] = None, session: Optional[str] = None):
        """Attach to existing multiplexer session."""
        mux = multiplexer or self.config.multiplexer
        sess = session or f"workstreams-{self.config.project}"

        if mux == "tmux":
            subprocess.run(["tmux", "attach", "-t", sess])
        elif mux == "zellij":
            subprocess.run(["zellij", "attach", sess])
        else:
            print(f"Unsupported multiplexer: {mux}")

    def status(self, workstream_id: Optional[int] = None, verbose: bool = False, live: bool = False):
        """Show status of workstreams."""
        if live:
            dashboard = LiveDashboard(self)
            dashboard.run()
        else:
            workstreams = self.config.workstreams
            if workstream_id:
                workstreams = [ws for ws in workstreams if ws.id == workstream_id]
            self._print_status(workstreams, verbose)

    def _print_status(self, workstreams, verbose: bool):
        for ws in self.config.workstreams:
            print(f"\n=== Workstream {ws.id}: {ws.name} ===")
            print(f"  Path: {ws.path}")
            print(f"  Branch: {ws.branch}")
            print(f"  Command: {ws.command}")

            ws_path = self.base_path / ws.path
            if ws_path.exists():
                result = subprocess.run(["git", "status", "--short"], cwd=ws_path, capture_output=True, text=True)
                if result.stdout.strip():
                    print(f"  Git status: {result.stdout.strip()}")
                else:
                    print("  Git status: clean")

    def assign(self, workstream_id: int, issue_numbers: List[int]):
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return
        print(f"Assigned issues {issue_numbers} to workstream {ws.name}")

    def sync(self, workstream_id: Optional[int] = None, rebase: bool = False):
        workstreams = self.config.workstreams
        if workstream_id:
            workstreams = [ws for ws in self.config.workstreams if ws.id == workstream_id]

        for ws in workstreams:
            ws_path = self.base_path / ws.path
            if ws_path.exists():
                print(f"Syncing {ws.name}...")
                if rebase:
                    subprocess.run(["git", "fetch", "origin"], cwd=ws_path, check=True)
                    subprocess.run(["git", "rebase", f"origin/{self.config.base_branch}"], cwd=ws_path, check=True)
                else:
                    subprocess.run(["git", "fetch", "origin"], cwd=ws_path, check=True)
                    subprocess.run(["git", "merge", f"origin/{self.config.base_branch}"], cwd=ws_path, check=True)
                print(f"  Synced {ws.name}")

    def pr(self, workstream_id: int, title: str, body: str = "", base: str = "main", draft: bool = False):
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return

        ws_path = self.base_path / ws.path
        subprocess.run(["git", "push", "origin", ws.branch], cwd=ws_path, check=True)

        cmd = ["gh", "pr", "create", "--title", title, "--body", body, "--base", base, "--head", ws.branch]
        if draft:
            cmd.append("--draft")
        result = subprocess.run(cmd, cwd=ws_path, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Created PR: {result.stdout.strip()}")
        else:
            print(f"Failed to create PR: {result.stderr}")

    def merge(self, workstream_id: int, method: str = "squash", delete_branch: bool = False, auto: bool = False):
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return

        cmd = ["gh", "pr", "merge", ws.branch, "--method", method]
        if delete_branch:
            cmd.append("--delete-branch")
        if auto:
            cmd.append("--auto")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"Merged {ws.name}")
            if delete_branch:
                ws_path = self.base_path / ws.path
                subprocess.run(["git", "worktree", "remove", str(ws_path)], check=False)
        else:
            print(f"Failed to merge: {result.stderr}")

    def logs(self, workstream_id: int, follow: bool = False, lines: int = 100):
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return

        ws_path = self.base_path / ws.path
        if not ws_path.exists():
            print(f"Workstream path {ws_path} does not exist")
            return

        log_file = ws_path / "logs" / "worker.log"
        if follow:
            subprocess.run(["tail", "-f", "-n", str(lines), str(log_file)], cwd=ws_path)
        else:
            subprocess.run(["tail", "-n", str(lines), str(log_file)], cwd=ws_path)

    def dispatch(self, workstream_id: int, subagent: str, issue: int, prompt: Optional[str] = None):
        """Dispatch a subagent to work on an issue."""
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return

        # Log the dispatch event
        event = SubagentEvent(
            workstream_id=workstream_id,
            subagent=subagent,
            issue=issue,
            event_type="started",
            message=f"Dispatched {subagent} for issue #{issue}",
            data={"prompt": prompt}
        )
        self.event_log.append(event)

        # Write to workstream log
        ws_path = self.base_path / ws.path
        log_file = ws_path / "logs" / "worker.log"
        log_file.parent.mkdir(exist_ok=True)
        with open(log_file, "a") as f:
            f.write(f"[{event.timestamp}] DISPATCH: {event.message}\n")

        # Notify
        self.notifier.send(
            f"Workstream {ws.name}",
            f"Subagent {subagent} started on issue #{issue}"
        )

        print(f"Dispatched {subagent} to workstream {ws.name} for issue {issue}")

        # If using tmux, send command to pane
        if self.config.multiplexer == "tmux":
            pane = f"workstreams-{self.config.project}:{workstream_id-1}"
            cmd = prompt or f"Work on issue #{issue} as {subagent}"
            full_cmd = f"cd {ws.path} && echo '>>> {cmd}' && {cmd}"
            subprocess.run(["tmux", "send-keys", "-t", pane, full_cmd, "Enter"])

    def run(self, workstream_id: int, command: str):
        """Run a command in a workstream."""
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return

        ws_path = self.base_path / ws.path
        result = subprocess.run(command, shell=True, cwd=ws_path, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return result.returncode == 0

    def monitor(self, refresh: int = 2):
        """Start live monitoring dashboard."""
        dashboard = LiveDashboard(self)
        dashboard.refresh_rate = refresh
        dashboard.run()

    def notify(self, title: str, message: str, urgency: str = "normal"):
        """Send notification."""
        self.notifier.send(title, message, urgency)

    def tail_logs(self, workstream_id: Optional[int] = None, follow: bool = True):
        """Tail logs from all workstreams (or one)."""
        workstreams = self.config.workstreams
        if workstream_id:
            workstreams = [ws for ws in workstreams if ws.id == workstream_id]

        if len(workstreams) == 1:
            self.logs(workstreams[0].id, follow=follow)
        else:
            print("Multi-tail not yet implemented for multiple workstreams. Use --workstream.")


# =============================================================================
# Subagent Client Helper (for subagents to report back)
# =============================================================================

def subagent_report(project: str, workstream_id: int, subagent: str, issue: int,
                   event_type: str, message: str, data: Dict[str, Any] = None):
    """Client function for subagents to report events (cross-process)."""
    event_log = get_event_log(project)
    event = SubagentEvent(
        workstream_id=workstream_id,
        subagent=subagent,
        issue=issue,
        event_type=event_type,
        message=message,
        data=data or {}
    )
    event_log.append(event)
    return True


# =============================================================================
# Config Loading
# =============================================================================

def load_config(config_path: Path) -> WorkstreamsConfig:
    """Load workstreams configuration from YAML file."""
    if not config_path.exists():
        return WorkstreamsConfig(project="myproject")

    with open(config_path) as f:
        data = yaml.safe_load(f)

    workstreams = [
        WorkstreamConfig(
            id=ws["id"],
            name=ws["name"],
            path=ws["path"],
            branch=ws["branch"],
            command=ws["command"],
            env=ws.get("env", {}),
        )
        for ws in data.get("workstreams", [])
    ]

    return WorkstreamsConfig(
        project=data.get("project", "myproject"),
        multiplexer=data.get("multiplexer", "tmux"),
        layout=data.get("layout", "even-horizontal"),
        base_branch=data.get("base_branch", "master"),
        mode=data.get("mode", "worktree"),
        workstreams=workstreams,
        shared_deps=data.get("shared_deps", []),
    )


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Workstreams - Parallel development workstreams")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize workstreams")
    init_parser.add_argument("--project", required=True, help="Project name")
    init_parser.add_argument("--workstreams", type=int, default=4, help="Number of workstreams")
    init_parser.add_argument("--multiplexer", choices=["tmux", "zellij", "tmuxp", "wezterm", "kitty"], default="tmux")
    init_parser.add_argument("--layout", choices=["even-horizontal", "even-vertical", "main-horizontal", "tiled", "tabs"], default="even-horizontal")
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

    # monitor (NEW)
    monitor_parser = subparsers.add_parser("monitor", help="Live monitoring dashboard")
    monitor_parser.add_argument("--refresh", type=int, default=2, help="Refresh interval in seconds")

    # attach (NEW)
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
    dispatch_parser.add_argument("--subagent", choices=["backend-api", "frontend-ui", "database-schema", "testing", "documentation", "security", "code-quality", "devops"])
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

    # notify (NEW)
    notify_parser = subparsers.add_parser("notify", help="Send notification to other terminals")
    notify_parser.add_argument("--title", required=True)
    notify_parser.add_argument("--message", required=True)
    notify_parser.add_argument("--urgency", choices=["low", "normal", "critical"], default="normal")

    # tail (NEW)
    tail_parser = subparsers.add_parser("tail", help="Tail logs from all workstreams")
    tail_parser.add_argument("--workstream", type=int)
    tail_parser.add_argument("--follow", action="store_true", default=True)

    # events (NEW)
    events_parser = subparsers.add_parser("events", help="Show subagent events")
    events_parser.add_argument("--workstream", type=int)
    events_parser.add_argument("--since", type=int, default=10, help="Minutes back")

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
        events = event_log.get_events(workstream_id=args.workstream, since=since)
        for e in events:
            print(f"[{e.timestamp[11:19]}] [{e.subagent}] #{e.issue} - {e.event_type}: {e.message}")
    elif args.command == "cleanup":
        pass


if __name__ == "__main__":
    main()
