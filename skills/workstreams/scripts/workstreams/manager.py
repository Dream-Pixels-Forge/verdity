"""
Core Workstreams Manager.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from .models import WorkstreamConfig, WorkstreamsConfig
from .config import load_config, save_config
from .event_log import get_event_log, EventLog
from .notifier import Notifier
from .multiplexer import get_multiplexer
from .dashboard import LiveDashboard
from .subagent_client import subagent_report


class WorkstreamsManager:
    """Main manager for workstreams."""

    def __init__(self, config: WorkstreamsConfig):
        self.config = config
        self.base_path = Path(config.base_path).resolve()
        self.config_file = self.base_path / ".workstreams.yaml"
        self.event_log = get_event_log(config.project)
        self.notifier = Notifier(config.project)

    def init_project(self, force: bool = False, num_workstreams: int = 4) -> bool:
        """Initialize workstreams for a project."""
        if self.config_file.exists() and not force:
            print(f"Configuration already exists at {self.config_file}. Use --force to overwrite.")
            return False

        # Create default workstreams if none defined
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
        save_config(self.config, self.config_file)
        print(f"Initialized {len(self.config.workstreams)} workstreams for project '{self.config.project}'")
        return True

    def _create_workstream(self, ws: WorkstreamConfig):
        """Create a workstream (worktree or branch)."""
        ws_path = self.base_path / ws.path
        ws_path.parent.mkdir(parents=True, exist_ok=True)

        if self.config.mode == "worktree":
            branch = ws.branch
            try:
                subprocess.run(
                    ["git", "worktree", "add", str(ws_path), branch],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError:
                subprocess.run(
                    ["git", "branch", branch, self.config.base_branch],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "worktree", "add", str(ws_path), branch],
                    check=True,
                )
        else:
            ws_path.mkdir(parents=True, exist_ok=True)

        # Create logs directory
        (ws_path / "logs").mkdir(exist_ok=True)

    def start(self, workstream_id: Optional[int] = None, command: Optional[str] = None):
        """Start workstreams in multiplexer."""
        workstreams = self.config.workstreams
        if workstream_id:
            workstreams = [ws for ws in workstreams if ws.id == workstream_id]

        multiplexer = get_multiplexer(self.config.multiplexer, self.config)
        multiplexer.start(workstreams, command)

    def attach(self, multiplexer: Optional[str] = None, session: Optional[str] = None):
        """Attach to existing multiplexer session."""
        mux_name = multiplexer or self.config.multiplexer
        sess = session or f"workstreams-{self.config.project}"
        multiplexer = get_multiplexer(mux_name, self.config)
        multiplexer.attach(sess)

    def status(self, workstream_id: Optional[int] = None, verbose: bool = False, live: bool = False):
        """Show status of workstreams."""
        if live:
            dashboard = LiveDashboard(self)
            dashboard.refresh_rate = 2
            dashboard.run()
        else:
            workstreams = self.config.workstreams
            if workstream_id:
                workstreams = [ws for ws in workstreams if ws.id == workstream_id]
            self._print_status(workstreams, verbose)

    def _print_status(self, workstreams: List, verbose: bool):
        for ws in self.config.workstreams:
            print(f"\n=== Workstream {ws.id}: {ws.name} ===")
            print(f"  Path: {ws.path}")
            print(f"  Branch: {ws.branch}")
            print(f"  Command: {ws.command}")

            ws_path = self.base_path / ws.path
            if ws_path.exists():
                result = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=ws_path,
                    capture_output=True,
                    text=True,
                )
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
                    subprocess.run(
                        ["git", "rebase", f"origin/{self.config.base_branch}"],
                        cwd=ws_path,
                        check=True,
                    )
                else:
                    subprocess.run(["git", "fetch", "origin"], cwd=ws_path, check=True)
                    subprocess.run(
                        ["git", "merge", f"origin/{self.config.base_branch}"],
                        cwd=ws_path,
                        check=True,
                    )
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
        subagent_report(
            self.config.project,
            workstream_id,
            subagent,
            issue,
            "started",
            f"Dispatched {subagent} for issue #{issue}",
            {"prompt": prompt},
        )

        # Write to workstream log
        ws_path = self.base_path / ws.path
        log_file = ws_path / "logs" / "worker.log"
        log_file.parent.mkdir(exist_ok=True)
        with open(log_file, "a") as f:
            f.write(f"[{self._now()}] DISPATCH: Dispatched {subagent} for issue #{issue}\n")

        # Notify
        self.notifier.send(
            f"Workstream {ws.name}",
            f"Subagent {subagent} started on issue #{issue}"
        )

        print(f"Dispatched {subagent} to workstream {ws.name} for issue {issue}")

        # If using tmux, send command to pane
        if self.config.multiplexer == "tmux":
            multiplexer = get_multiplexer("tmux", self.config)
            multiplexer.send_command(workstream_id, prompt or f"Work on issue #{issue} as {subagent}")

    def _now(self) -> str:
        from datetime import datetime, UTC
        return datetime.now(UTC).isoformat()

    def run(self, workstream_id: int, command: str) -> bool:
        """Run a command in a workstream."""
        ws = next((ws for ws in self.config.workstreams if ws.id == workstream_id), None)
        if not ws:
            print(f"Workstream {workstream_id} not found")
            return False

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

    def get_events(
        self,
        workstream_id: Optional[int] = None,
        since_minutes: int = 10,
        event_type: Optional[str] = None,
    ):
        """Get subagent events."""
        from datetime import datetime, UTC, timedelta
        since = datetime.now(UTC) - timedelta(minutes=since_minutes)
        return self.event_log.get_events(workstream_id, since, event_type)


# Import sys for run method
import sys
