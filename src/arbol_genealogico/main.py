from __future__ import annotations

import logging

from arbol_genealogico.infrastructure.logger import configure_logging

configure_logging()

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Application started")
