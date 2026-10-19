"""Structured logging to both the console and logs/trident.log (TRIDENT_LOG_DIR overrides)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from core.config import log_dir

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir() / "trident.log", maxBytes=10_000_000, backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(file_handler)
    return logger
