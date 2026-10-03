"""
File-based event log for cross-process subagent communication.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional, List

from .models import SubagentEvent


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
                    f.write(json.dumps(event.__dict__) + "\n")
            finally:
                self._release_lock()
        else:
            with open(self.events_file, "a") as f:
                f.write(json.dumps(event.__dict__) + "\n")

    def get_events(
        self,
        workstream_id: Optional[int] = None,
        since: Optional[datetime] = None,
        event_type: Optional[str] = None,
    ) -> List[SubagentEvent]:
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
                    if event_type and event.event_type != event_type:
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
