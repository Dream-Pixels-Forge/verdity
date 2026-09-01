"""
Verdity — AI Pull Request Reviewer, Production Agent System
Root package.

v0.3.0 Features:
- MCP Server exposure (Model Context Protocol)
- Full-codebase context indexing
- Agentic fix mode
- Custom review rules (.verdity/rules.yml)
"""

__version__ = "0.3.0"

__all__ = [
    "__version__",
    "MCPServer",
    "create_mcp_server",
    "ReviewRules",
]
