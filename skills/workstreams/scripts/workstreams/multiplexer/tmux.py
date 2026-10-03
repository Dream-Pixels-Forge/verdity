"""
Tmux multiplexer implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from .base import MultiplexerBase


class TmuxMultiplexer(MultiplexerBase):
    """Tmux-based workstream management."""

    def start(self, workstreams: List, command: Optional[str] = None):
        session_name = f"workstreams-{self.config.project}"
        base_path = Path(self.config.base_path).resolve()

        # Create session if not exists
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
        )
        if result.returncode != 0:
            tmux_cmd = [
                "tmux", "new-session", "-d", "-s", session_name,
                "-c", str(base_path),
            ]
            subprocess.run(tmux_cmd, check=True)

        # Setup panes based on layout
        layout = self.config.layout
        if layout == "even-horizontal":
            self._setup_even_horizontal(workstreams)
        elif layout == "even-vertical":
            self._setup_even_vertical(workstreams)
        elif layout == "main-horizontal":
            self._setup_main_horizontal(workstreams)
        elif layout == "tiled":
            self._setup_tiled(workstreams)

        # Send commands to each pane
        for i, ws in enumerate(workstreams):
            pane = f"{session_name}:{i}"
            cmd = command or ws.command
            if cmd:
                tmux_cmd = ["tmux", "send-keys", "-t", pane, cmd, "Enter"]
                subprocess.run(tmux_cmd)

        print(f"Started {len(workstreams)} workstream(s) in tmux session '{session_name}'")

    def _setup_even_horizontal(self, workstreams: List):
        session_name = f"workstreams-{self.config.project}"
        base_path = Path(self.config.base_path).resolve()
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(
                    ["tmux", "rename-window", "-t", session_name, ws.name]
                )
            else:
                subprocess.run(
                    ["tmux", "split-window", "-h", "-t", session_name, "-c", str(base_path / ws.path)]
                )
            subprocess.run(
                ["tmux", "select-pane", "-t", f"{session_name}.{i}", "-T", ws.name]
            )

    def _setup_even_vertical(self, workstreams: List):
        session_name = f"workstreams-{self.config.project}"
        base_path = Path(self.config.base_path).resolve()
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(
                    ["tmux", "rename-window", "-t", session_name, ws.name]
                )
            else:
                subprocess.run(
                    ["tmux", "split-window", "-v", "-t", session_name, "-c", str(base_path / ws.path)]
                )
            subprocess.run(
                ["tmux", "select-pane", "-t", f"{session_name}.{i}", "-T", ws.name]
            )

    def _setup_main_horizontal(self, workstreams: List):
        session_name = f"workstreams-{self.config.project}"
        base_path = Path(self.config.base_path).resolve()
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(
                    ["tmux", "rename-window", "-t", session_name, ws.name]
                )
            elif i == 1:
                subprocess.run(
                    ["tmux", "split-window", "-h", "-t", session_name, "-c", str(base_path / ws.path)]
                )
            else:
                subprocess.run(
                    ["tmux", "split-window", "-v", "-t", f"{session_name}.1", "-c", str(base_path / ws.path)]
                )
            subprocess.run(
                ["tmux", "select-pane", "-t", f"{session_name}.{i}", "-T", ws.name]
            )

    def _setup_tiled(self, workstreams: List):
        session_name = f"workstreams-{self.config.project}"
        base_path = Path(self.config.base_path).resolve()
        for i, ws in enumerate(workstreams):
            if i == 0:
                subprocess.run(
                    ["tmux", "rename-window", "-t", session_name, ws.name]
                )
            elif i == 1:
                subprocess.run(
                    ["tmux", "split-window", "-h", "-t", session_name, "-c", str(base_path / ws.path)]
                )
            elif i == 2:
                subprocess.run(
                    ["tmux", "split-window", "-v", "-t", f"{session_name}.0", "-c", str(base_path / ws.path)]
                )
            else:
                subprocess.run(
                    ["tmux", "split-window", "-v", "-t", f"{session_name}.1", "-c", str(base_path / ws.path)]
                )
            subprocess.run(
                ["tmux", "select-pane", "-t", f"{session_name}.{i}", "-T", ws.name]
            )

    def attach(self, session: str):
        subprocess.run(["tmux", "attach", "-t", session])

    def send_command(self, workstream_id: int, command: str):
        session_name = f"workstreams-{self.config.project}"
        pane = f"{session_name}:{workstream_id - 1}"
        subprocess.run(["tmux", "send-keys", "-t", pane, command, "Enter"])
