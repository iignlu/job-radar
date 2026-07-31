"""Logging setup.

One place so every module gets the same format, and so the GitHub Actions log
reads top-to-bottom as a narrative of the run.
"""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"
_DATEFMT = "%H:%M:%S"


def setup(verbose: bool = False) -> logging.Logger:
    """Configure root logging and hand back the package logger."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=_FORMAT,
        datefmt=_DATEFMT,
        stream=sys.stdout,
        force=True,
    )
    return logging.getLogger("jobradar")


def get(name: str) -> logging.Logger:
    """Child logger, e.g. get(__name__)."""
    return logging.getLogger(name)
