"""
Verdity — AI Pull Request Reviewer, Production Agent System
Root package.

v0.4.6 Features:
- Enforcement engine with blocking rules (CEL expressions)
- GitHub Checks API integration
- Approval queue SLA escalation
- Verification gate auto-escalation
- Budget enforcer with specialist-level limits
"""

from ._version import get_version, __version__

__all__ = [
    "MCPServer",
    "ReviewRules",
    "__version__",
    "create_mcp_server",
    "get_version",
]
