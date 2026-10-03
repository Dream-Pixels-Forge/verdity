"""
Subagent client helper for reporting events.
"""

from __future__ import annotations

from typing import Optional, Dict, Any

from .models import SubagentEvent
from .event_log import get_event_log


def subagent_report(
    project: str,
    workstream_id: int,
    subagent: str,
    issue: int,
    event_type: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Client function for subagents to report events (cross-process)."""
    event_log = get_event_log(project)
    event = SubagentEvent(
        workstream_id=workstream_id,
        subagent=subagent,
        issue=issue,
        event_type=event_type,
        message=message,
        data=data or {},
    )
    event_log.append(event)
    return True


def subagent_started(project: str, workstream_id: int, subagent: str, issue: int, prompt: str = "") -> bool:
    """Report subagent started."""
    return subagent_report(project, workstream_id, subagent, issue, "started", f"Started {subagent} for issue #{issue}", {"prompt": prompt})


def subagent_progress(project: str, workstream_id: int, subagent: str, issue: int, message: str, data: Optional[Dict] = None) -> bool:
    """Report subagent progress."""
    return subagent_report(project, workstream_id, subagent, issue, "progress", message, data)


def subagent_completed(project: str, workstream_id: int, subagent: str, issue: int, message: str, data: Optional[Dict] = None) -> bool:
    """Report subagent completed."""
    return subagent_report(project, workstream_id, subagent, issue, "completed", message, data)


def subagent_failed(project: str, workstream_id: int, subagent: str, issue: int, message: str, data: Optional[Dict] = None) -> bool:
    """Report subagent failed."""
    return subagent_report(project, workstream_id, subagent, issue, "failed", message, data)


def subagent_error(project: str, workstream_id: int, subagent: str, issue: int, message: str, data: Optional[Dict] = None) -> bool:
    """Report subagent error."""
    return subagent_report(project, workstream_id, subagent, issue, "error", message, data)
