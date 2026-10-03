"""
Zellij multiplexer implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from .base import MultiplexerBase


class ZellijMultiplexer(MultiplexerBase):
    """Zellij-based workstream management."""

    def start(self, workstreams: List, command: Optional[str] = None):
        session_name = f"workstreams-{self.config.project}"

        # Create or attach to session
        subprocess.run(["zellij", "attach", session_name, "--create"], check=False)

        for ws in workstreams:
            # Create tab for workstream
            tab_name = ws.name
            subprocess.run(["zellij", "action", "new-tab", "--name", tab_name])

            # Run command in tab
            if ws.command:
                subprocess.run(
                    ["zellij", "run", "--", "bash", "-c", f"cd {ws.path} && {ws.command}"]
                )

        print(f"Started {len(workstreams)} workstream(s) in zellij session '{session_name}'")

    def attach(self, session: str):
        subprocess.run(["zellij", "attach", session])

    def send_command(self, workstream_id: int, command: str):
        # Zellij doesn't have easy programmatic pane targeting
        # This would need zellij's API or plugin system
        print(f"Zellij send_command not fully implemented for workstream {workstream_id}")
