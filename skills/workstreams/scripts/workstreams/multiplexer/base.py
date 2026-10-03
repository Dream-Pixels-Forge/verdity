"""
Base multiplexer interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional


class MultiplexerBase(ABC):
    """Base class for terminal multiplexers."""

    def __init__(self, config):
        self.config = config

    @abstractmethod
    def start(self, workstreams: List, command: Optional[str] = None):
        """Start workstreams in multiplexer."""
        pass

    @abstractmethod
    def attach(self, session: str):
        """Attach to existing session."""
        pass

    @abstractmethod
    def send_command(self, workstream_id: int, command: str):
        """Send command to workstream pane/tab."""
        pass
