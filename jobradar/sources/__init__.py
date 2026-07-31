"""Job sources.

Each source knows how to talk to exactly one upstream and normalises whatever
it gets back into `base.Job`. Nothing downstream of here — filters, state,
notify — knows or cares where a posting came from.
"""

from .base import Job, Source
from .jsearch import JSearchSource

__all__ = ["Job", "Source", "JSearchSource"]
