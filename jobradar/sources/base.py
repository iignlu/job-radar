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

    # Every route to applying, normalised to {publisher, url, is_direct}.
    # `is_direct` means the link goes to the employer's own careers page or ATS
    # rather than an aggregator — which is the difference between filling in a
    # form and being asked to create an account first.
    apply_options: list = field(default_factory=list)

    raw: dict = field(default_factory=dict, repr=False)

    @property
    def direct_url(self) -> str | None:
        """The employer's own apply link, if any source offered one."""
        for option in self.apply_options:
            if option.get("is_direct") and option.get("url"):
                return option["url"]
        return None

    @property
    def best_url(self) -> str:
        """Where to send someone who wants to apply.

        Prefers the employer's own link: aggregator links increasingly sit
        behind a signup or paywall, and a posting you cannot apply to is worth
        nothing regardless of how well it matched.
        """
        return self.direct_url or self.url

    @property
    def alternate_options(self) -> list:
        """Apply routes other than `best_url`, newest-first order preserved."""
        best = self.best_url
        return [o for o in self.apply_options if o.get("url") and o["url"] != best]

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
    def age_days(self) -> float | None:
        """Days since posting, or None when the source gave no usable date.

        None is meaningfully different from "old": plenty of postings carry no
        timestamp at all, and treating those as stale would silently discard
        them. Callers decide what unknown means; the filters let it through.
        """
        if not self.posted_at:
            return None
        from datetime import datetime, timezone

        try:
            stamp = datetime.fromisoformat(str(self.posted_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)

        seconds = (datetime.now(timezone.utc) - stamp).total_seconds()
        # Clamp: a posting dated slightly in the future (timezone rounding at
        # the source) is brand new, not invalid.
        return max(0.0, seconds / 86400)

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
