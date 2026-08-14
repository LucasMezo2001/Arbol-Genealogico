from __future__ import annotations

import os
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent  # .../src/arbol_genealogico/config
_REPO_ROOT_ANCHOR = _PACKAGE_DIR.parents[2]


def project_root() -> Path:
    """Return the project root directory.

    Resolution order:
    1. ``ARBOL_ROOT`` environment variable (set in Docker/CI).
    2. Anchor derived from ``__file__``.
    """
    env_root = os.environ.get("ARBOL_ROOT")
    if env_root:
        return Path(env_root)
    return _REPO_ROOT_ANCHOR


def resolve_path(p: str | Path) -> Path:
    """Resolve a path relative to :func:`project_root`, or return it unchanged if absolute."""
    path = Path(p)
    if path.is_absolute():
        return path
    return project_root() / path
