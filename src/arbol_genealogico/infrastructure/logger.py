from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from arbol_genealogico.config.paths import project_root


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging to console and ``logs/arbol.log``."""
    logs_dir = project_root() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        logs_dir / "arbol.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
