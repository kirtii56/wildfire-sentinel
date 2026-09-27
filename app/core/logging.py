"""Logging setup. Call configure_logging() from entry points only."""

from __future__ import annotations

import logging
import sys

LOGGER_NAME = "wildfire_sentinel"


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    return logger


def get_logger(suffix: str) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME).getChild(suffix)
