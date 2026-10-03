"""
Workstreams - Parallel development workstreams with monitoring and subagent coordination.
"""

from .models import (
    WorkstreamConfig,
    WorkstreamsConfig,
    WorkstreamStatus,
    SubagentEvent,
)
from .config import load_config, save_config
from .manager import WorkstreamsManager
from .subagent_client import (
    subagent_report,
    subagent_started,
    subagent_progress,
    subagent_completed,
    subagent_failed,
    subagent_error,
)

__version__ = "0.4.17"
__all__ = [
    "WorkstreamConfig",
    "WorkstreamsConfig",
    "WorkstreamStatus",
    "SubagentEvent",
    "load_config",
    "save_config",
    "WorkstreamsManager",
    "subagent_report",
    "subagent_started",
    "subagent_progress",
    "subagent_completed",
    "subagent_failed",
    "subagent_error",
]
