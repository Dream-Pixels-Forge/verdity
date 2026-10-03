"""
Cross-terminal notifications.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional, List, Dict


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
            subprocess.run(
                ["notify-send", "-u", urgency, title, message],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass

        # Write to notification file for other instances to pick up
        try:
            notif = {
                "title": title,
                "message": message,
                "urgency": urgency,
                "timestamp": datetime.now(UTC).isoformat(),
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
