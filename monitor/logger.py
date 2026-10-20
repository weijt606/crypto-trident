"""Structured logging to both the console and logs/trident.log (TRIDENT_LOG_DIR overrides)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from core.config import log_dir

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


class RedactingFormatter(logging.Formatter):
    """Redact the fully formatted message, including exception traceback text."""

    def format(self, record: logging.LogRecord) -> str:
        from execution.common import redact

        return redact(super().format(record))


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(RedactingFormatter(_FORMAT))
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir() / "trident.log", maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(RedactingFormatter(_FORMAT))
    logger.addHandler(file_handler)
    return logger
