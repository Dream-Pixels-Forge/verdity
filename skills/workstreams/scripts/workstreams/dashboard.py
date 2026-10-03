"""
Live monitoring dashboard.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, UTC, timedelta
from typing import List, Optional, TYPE_CHECKING
from pathlib import Path

from .models import WorkstreamStatus, SubagentEvent
from .event_log import get_event_log

if TYPE_CHECKING:
    from .manager import WorkstreamsManager


class LiveDashboard:
    """Terminal-based live monitoring dashboard."""

    def __init__(self, manager: "WorkstreamsManager"):
        self.manager = manager
        self.running = False
        self.refresh_rate = 2
        self.width = 120

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
            print("\n\033[?25h\033[0m", end="")

    def _render(self):
        """Render dashboard."""
        print("\033[2J\033[H\033[?25l", end="")

        now = datetime.now().strftime("%H:%M:%S")
        print(f"\033[1;36m{'='*self.width}\033[0m")
        print(f"\033[1;36m  WORKSTREAMS MONITOR  |  {self.manager.config.project}  |  {now}  |  Ctrl+C to exit\033[0m")
        print(f"\033[1;36m{'='*self.width}\033[0m")

        statuses = self._get_all_statuses()

        total = len(statuses)
        active = sum(1 for s in statuses if s.pid is not None)
        dirty = sum(1 for s in statuses if s.git_status != "clean")
        alerts = sum(len(s.alerts) for s in statuses)
        print(f"  Total: {total}  |  Active: {active}  |  Dirty: {dirty}  |  Alerts: {alerts}")
        print(f"\033[90m{'-'*self.width}\033[0m")

        print(f"  {'ID':>3}  {'NAME':<15}  {'BRANCH':<20}  {'STATUS':<12}  {'LAST COMMIT':<30}  {'ACTIVITY'}")
        print(f"\033[90m{'-'*self.width}\033[0m")

        for status in statuses:
            self._print_workstream_row(status)

        all_alerts = []
        for s in statuses:
            for alert in s.alerts:
                all_alerts.append(f"[{s.name}] {alert}")
        if all_alerts:
            print(f"\n\033[1;33m  RECENT ALERTS:\033[0m")
            for alert in all_alerts[-5:]:
                print(f"  \033[33m⚠\033[0m  {alert}")

        events = self._get_recent_events()
        if events:
            print(f"\n\033[1;35m  SUBAGENT ACTIVITY:\033[0m")
            for event in events[-10:]:
                color = self._event_color(event.event_type)
                print(f"  \033[{color}m{event.timestamp[11:19]}\033[0m  [{event.subagent}]  {event.message}")

    def _get_all_statuses(self) -> List[WorkstreamStatus]:
        statuses = []
        for ws in self.manager.config.workstreams:
            statuses.append(self._get_workstream_status(ws))
        return statuses

    def _get_workstream_status(self, ws) -> WorkstreamStatus:
        ws_path = self.manager.base_path / ws.path
        git_status = "unknown"
        last_commit = ""
        last_activity = ""
        pid = None

        if ws_path.exists():
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=ws_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                git_status = "dirty" if result.stdout.strip() else "clean"

            result = subprocess.run(
                ["git", "log", "-1", "--format=%h %s"],
                cwd=ws_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                last_commit = result.stdout.strip()[:50]

            try:
                result = subprocess.run(
                    ["pgrep", "-f", f"{ws.path}"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    pid = int(result.stdout.strip().split()[0])
            except Exception:
                pass

        log_tail = []
        log_file = ws_path / "logs" / "worker.log"
        if log_file.exists():
            try:
                result = subprocess.run(
                    ["tail", "-5", str(log_file)],
                    capture_output=True,
                    text=True,
                )
                log_tail = result.stdout.strip().split("\n")
            except Exception:
                pass

        alerts = []
        if log_file.exists():
            try:
                result = subprocess.run(
                    ["grep", "-i", "error\\|fail\\|warning", str(log_file)],
                    capture_output=True,
                    text=True,
                )
                if result.stdout:
                    alerts = result.stdout.strip().split("\n")[-3:]
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
        if status.git_status == "clean":
            status_color = "32"
        elif status.git_status == "dirty":
            status_color = "33"
        else:
            status_color = "31"

        name = status.name[:14]
        branch = status.branch[:19]
        commit = status.last_commit[:29]

        print(
            f"  {status.id:>3}  \033[1m{name:<15}\033[0m  "
            f"{branch:<20}  \033[{status_color}m{status.git_status:<12}\033[0m  "
            f"{commit:<30}  {status.last_activity}"
        )

    def _get_recent_events(self) -> List[SubagentEvent]:
        event_log = get_event_log(self.manager.config.project)
        since = datetime.now(UTC) - timedelta(minutes=10)
        return event_log.get_events(since=since)

    def _event_color(self, event_type: str) -> str:
        colors = {
            "started": "36",
            "progress": "34",
            "completed": "32",
            "failed": "31",
            "error": "31",
        }
        return colors.get(event_type, "37")
