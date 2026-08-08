#!/usr/bin/env python3
"""JSearch response-parsing tests — run with `python tests/test_jsearch.py`.

No pytest: the project is standard-library-only, and that has to include its
own test run or CI would need a pip install.

These are all about one thing: telling "no jobs today" apart from "we parsed
the response wrong". Those two look identical from the run log, and confusing
them has now cost this project two separate multi-run outages.
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.sources.jsearch import JSearchSource  # noqa: E402

_results: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((bool(condition), name, detail))


def source() -> JSearchSource:
    return JSearchSource(api_key="x", queries=["q"])


posting = {"job_title": "Graduate Software Engineer", "job_id": "abc"}

# --- response shapes -------------------------------------------------------

# v1: data is a bare list.
check("v1 flat list is read",
      len(source()._records({"data": [posting]})) == 1)

# v2: data is an object nesting the postings one level down.
check("v2 nested data.jobs is read",
      len(source()._records({"data": {"jobs": [posting]}})) == 1)

# THE REGRESSION: a sibling empty list must not win over the real postings.
# {"filters": [], "jobs": [...]} previously matched `filters` first and
# reported zero postings while the jobs sat one key over — a silent outage
# indistinguishable from a quiet day.
records = source()._records({"data": {"filters": [], "jobs": [posting]}})
check("an empty sibling key does not shadow the postings",
      len(records) == 1, f"got {len(records)}")

# Same, with the empty key named something plausible.
records = source()._records({"data": {"results": [], "jobs": [posting, posting]}})
check("a populated key beats an empty one regardless of order",
      len(records) == 2, f"got {len(records)}")

# When several keys are populated, the conventionally-named one wins.
records = source()._records(
    {"data": {"related": [{"x": 1}], "jobs": [posting, posting, posting]}}
)
check("a conventionally-named key wins when several qualify",
      len(records) == 3, f"got {len(records)}")

# A genuinely empty response is still empty — the fix must not invent jobs.
check("a genuinely empty response reads as zero",
      source()._records({"data": {"jobs": []}}) == [])
check("an empty flat list reads as zero",
      source()._records({"data": []}) == [])

# Unparseable shapes report zero rather than raising.
check("data as a string does not raise", source()._records({"data": "nope"}) == [])
check("a missing data key does not raise", source()._records({}) == [])
check("an object with no lists does not raise",
      source()._records({"data": {"message": "quota exceeded"}}) == [])

# Non-dict entries are dropped rather than crashing _to_job later.
check("non-dict records are filtered out",
      len(source()._records({"data": [posting, "junk", None]})) == 1)

# --- stable ids ------------------------------------------------------------
# job_id is base64 of "<identity>:<request-context>"; only the first half is
# stable. Getting this wrong re-alerts every job on every run.
encoded = base64.b64encode(b"job-identity-123:request-context-abc").decode()
other = base64.b64encode(b"job-identity-123:different-context-xyz").decode()
check("the same posting yields the same id across requests",
      source()._stable_id(encoded) == source()._stable_id(other))
check("the id is the identity half",
      source()._stable_id(encoded) == "job-identity-123")
check("a non-base64 id passes through", source()._stable_id("plain-id") == "plain-id")
check("an empty id is None", source()._stable_id("") is None)

# --- zero-yield warning ----------------------------------------------------
# fetch() must flag "every query answered, nothing came back" rather than
# letting it pass as an ordinary quiet run.
import logging  # noqa: E402


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def fetch_with(payload):
    """Run fetch() against a canned payload, capturing log records."""
    from jobradar import http
    from jobradar.sources import jsearch as module

    capture = Capture()
    module._log.addHandler(capture)
    original = http.get_json
    http.get_json = lambda *a, **k: payload
    try:
        src = JSearchSource(api_key="x", queries=["one", "two"])
        jobs = src.fetch()
    finally:
        http.get_json = original
        module._log.removeHandler(capture)
    return jobs, capture.records


jobs, records = fetch_with({"data": {"jobs": []}})
warnings = [r for r in records if r.levelno >= logging.WARNING]
check("an all-zero fetch returns no jobs", jobs == [])
check("an all-zero fetch warns rather than passing silently",
      len(warnings) == 1, f"{len(warnings)} warning(s)")
check("the warning names the parameters to check",
      warnings and "date_posted" in warnings[0].getMessage(),
      warnings[0].getMessage() if warnings else "no warning")

jobs, records = fetch_with({"data": {"jobs": [posting]}})
warnings = [r for r in records if r.levelno >= logging.WARNING]
check("a productive fetch returns jobs", len(jobs) == 2, f"got {len(jobs)}")
check("a productive fetch does not warn", not warnings,
      warnings[0].getMessage() if warnings else "")


if __name__ == "__main__":
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    for ok, name, detail in _results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {name}{suffix}")
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)
