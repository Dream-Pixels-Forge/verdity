"""
Data models for workstreams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Optional, List, Dict, Any


@dataclass
class WorkstreamConfig:
    """Configuration for a single workstream."""
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
    """Project-level configuration."""
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
