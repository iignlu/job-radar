"""The shared shape every source produces.

The whole point of this module: a source's job is to normalise. Once a posting
is a `Job`, the rest of the pipeline is source-agnostic, which is what makes
adding an ATS source later a purely additive change.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Job:
    """One posting, normalised."""

    title: str
    company: str
    url: str
    source: str
    description: str = ""
    city: str | None = None
    country: str | None = None
    publisher: str | None = None
    employment_type: str | None = None
    is_remote: bool = False
    posted_at: str | None = None          # ISO 8601 string, or None if unknown
    native_id: str | None = None          # upstream's own id, when it has one
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """Stable dedup key.

        Prefer the upstream id — it is the only identifier that survives the
        same posting being re-titled or re-listed. Fall back to a content hash
        so sources without ids still dedup, and lowercase the inputs so a
        publisher flipping to title case does not resurrect a job we have
        already sent.
        """
        if self.native_id:
            return f"{self.source}:{self.native_id}"
        seed = f"{self.title}|{self.company}|{self.city or ''}".lower()
        return "h:" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]

    @property
    def haystack(self) -> str:
        """Lowercased title + description — what the keyword layers search."""
        return f"{self.title}\n{self.description}".lower()

    @property
    def location(self) -> str:
        """Best available human location."""
        return self.city or self.country or ""


class Source(ABC):
    """A place postings come from."""

    name: str = "source"

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return every posting this source currently offers.

        Implementations must be forgiving: one upstream hiccup should cost us
        one query's results, never the whole run.
        """

    @property
    @abstractmethod
    def request_cost(self) -> int:
        """How many upstream API requests one `fetch()` consumes.

        Used for the quota arithmetic in the README and the run summary. Free
        tiers are small enough that this is worth tracking explicitly.
        """
