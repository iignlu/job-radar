"""The five filter layers.

Ordered cheapest-first: a string scan of the title costs nothing, a regex sweep
of a 4000-character description costs more, so the layers that reject the most
for the least effort run first. Every layer returns a human-readable reason,
because "why was this dropped" is the question you actually ask when tuning.
"""

import re
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str


# Matches "5+ years", "3-5 years", "2 yrs", "3 سنوات", "1 year".
# The capture group is deliberately the FIRST number of a range: "3-5 years"
# means the floor is 3, and the floor is what decides whether you can apply.
_YEARS_RE = re.compile(
    r"""
    (?P<low>\d{1,2})                      # the number we keep
    \s*
    (?:                                   # optional range / plus marker
        \+
      | \s*(?:-|–|—|to|إلى)\s*\d{1,2}\s*\+?
    )?
    \s*
    (?:years?|yrs?\.?|سنوات|سنة|أعوام|عام)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Above this, the number is not an experience requirement — it is a company
# age, a headcount, a founding year fragment.
_IMPLAUSIBLE_YEARS = 25


def min_years_required(text: str) -> int:
    """Smallest plausible years-of-experience figure in `text`, or 0.

    Why the MINIMUM and not the maximum: descriptions are full of numbers that
    have nothing to do with the bar for applying — "3 year contract", "2 years
    of company growth", "5 years since we launched". Taking the maximum lets
    any one of those reject a posting you were qualified for. Taking the
    minimum means the occasional over-demanding role slips through to your
    phone, where it costs you three seconds to dismiss. A false rejection costs
    an opportunity; a false acceptance costs three seconds. The asymmetry is
    the whole argument.
    """
    if not text:
        return 0
    found = [
        int(match.group("low"))
        for match in _YEARS_RE.finditer(text)
        if int(match.group("low")) <= _IMPLAUSIBLE_YEARS
    ]
    return min(found) if found else 0


def evaluate(job) -> Verdict:
    """Run the five layers against a Job."""

    # Layer 1 — title exclusions. The single highest-yield check: this is what
    # removes senior roles, and a title is the one field that reliably states
    # the level of the job.
    title = (job.title or "").lower()
    for term in config.EXCLUDE_TITLE:
        if term in title:
            return Verdict(False, f"title contains excluded term '{term}'")

    # Layer 2 — body exclusions. Explicit multi-year demands that survived the
    # title check.
    body = (job.description or "").lower()
    for term in config.EXCLUDE_BODY:
        if term in body:
            return Verdict(False, f"description demands '{term}'")

    # Layer 3 — must-match. Nothing relevant in title or description means this
    # is not our field at all.
    haystack = job.haystack
    hits = [term for term in config.MUST_MATCH if term in haystack]
    if not hits:
        return Verdict(False, "no early-career or stack keyword matched")
    reason = ", ".join(hits[:4])

    # Layer 4 — parsed experience requirement. Costs a regex sweep, so it runs
    # after the cheap string checks have already thinned the field.
    years = min_years_required(job.description or "")
    if years > config.MAX_YEARS_EXPERIENCE:
        return Verdict(False, f"requires {years} years experience (max {config.MAX_YEARS_EXPERIENCE})")

    # Layer 5 — geography. Skipped entirely when CITIES is empty, which is the
    # default: COUNTRY already scoped the search.
    if config.CITIES:
        location = (job.location or "").lower()
        in_city = any(city.lower() in location for city in config.CITIES)
        if not in_city and not (config.ALLOW_REMOTE and job.is_remote):
            return Verdict(False, f"location '{job.location}' outside CITIES")

    return Verdict(True, reason)
