"""
Dynamic version management for Verdity.

Reads version from pyproject.toml (single source of truth) and provides it via verdity.__version__.
This avoids hardcoding version in multiple places.
"""

import sys
from pathlib import Path

# Cache the version to avoid re-reading the file on every import
_cached_version: str | None = None


def _read_version_from_pyproject() -> str:
    """Read version from pyproject.toml."""
    # Find the project root (where pyproject.toml is)
    current_dir = Path(__file__).parent.parent.parent
    pyproject_path = current_dir / "pyproject.toml"

    if not pyproject_path.exists():
        # Fallback for installed packages
        pyproject_path = Path(sys.prefix) / "share" / "verdity" / "pyproject.toml"
        if not pyproject_path.exists():
            return "0.0.0+unknown"

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return "0.0.0+no-toml"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")


def get_version() -> str:
    """Get the version, caching it for subsequent calls."""
    global _cached_version
    if _cached_version is None:
        _cached_version = _read_version_from_pyproject()
    return _cached_version


# For backward compatibility, expose version as module attribute
__version__ = get_version()
