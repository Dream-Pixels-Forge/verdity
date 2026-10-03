"""
Configuration loading and saving.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml

from .models import WorkstreamConfig, WorkstreamsConfig


def load_config(config_path: Path) -> WorkstreamsConfig:
    """Load workstreams configuration from YAML file."""
    if not config_path.exists():
        return WorkstreamsConfig(project="myproject")

    with open(config_path) as f:
        data = yaml.safe_load(f)

    workstreams = [
        WorkstreamConfig(
            id=ws["id"],
            name=ws["name"],
            path=ws["path"],
            branch=ws["branch"],
            command=ws["command"],
            env=ws.get("env", {}),
        )
        for ws in data.get("workstreams", [])
    ]

    return WorkstreamsConfig(
        project=data.get("project", "myproject"),
        multiplexer=data.get("multiplexer", "tmux"),
        layout=data.get("layout", "even-horizontal"),
        base_branch=data.get("base_branch", "master"),
        mode=data.get("mode", "worktree"),
        workstreams=workstreams,
        shared_deps=data.get("shared_deps", []),
    )


def save_config(config: WorkstreamsConfig, config_path: Path):
    """Save configuration to YAML file."""
    config_dict = {
        "project": config.project,
        "multiplexer": config.multiplexer,
        "layout": config.layout,
        "base_branch": config.base_branch,
        "mode": config.workstreams[0].mode if config.workstreams else "worktree",
        "shared_deps": config.shared_deps,
        "workstreams": [
            {
                "id": ws.id,
                "name": ws.name,
                "path": ws.path,
                "branch": ws.branch,
                "command": ws.command,
                "env": ws.env,
            }
            for ws in config.workstreams
        ],
    }
    with open(config_path, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False)
