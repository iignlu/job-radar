#!/usr/bin/env python3
"""Filter tests — run with `python tests/test_filters.py`.

No pytest: the project is standard-library-only, and that has to include its
own test run or CI would need a pip install.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.filters import evaluate, min_years_required  # noqa: E402
from jobradar.sources.base import Job  # noqa: E402

_results: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((bool(condition), name, detail))


def job(title, description="", **kwargs) -> Job:
    kwargs.setdefault("company", "Acme")
    kwargs.setdefault("url", "https://example.com/job")
    kwargs.setdefault("source", "jsearch")
    return Job(title=title, description=description, **kwargs)


# 1 — accepts a graduate role
verdict = evaluate(job(
    "Graduate Software Engineer",
    "Join our 2026 graduate programme. You will work in Python and SQL.",
))
check("accepts graduate role", verdict.accepted, verdict.reason)

# 2 — accepts a junior analyst
verdict = evaluate(job(
    "Junior Data Analyst",
    "Entry level position building dashboards in Power BI for our Riyadh team.",
))
check("accepts junior analyst", verdict.accepted, verdict.reason)

# 3 — accepts an Arabic posting
verdict = evaluate(job(
    "مطور برمجيات حديث التخرج",
    "نبحث عن خريج جديد للانضمام إلى فريق التطوير لدينا في الرياض.",
))
check("accepts Arabic posting", verdict.accepted, verdict.reason)

# 4 — rejects Senior by title (layer 1)
verdict = evaluate(job(
    "Senior Software Engineer",
    "We are looking for a software engineer to join a graduate-friendly team.",
))
check(
    "rejects 'Senior ...' by title",
    not verdict.accepted and "senior" in verdict.reason,
    verdict.reason,
)

# 5 — rejects Lead by title (layer 1)
verdict = evaluate(job(
    "Lead Backend Developer",
    "Junior-friendly environment, Python and React.",
))
check(
    "rejects 'Lead ...' by title",
    not verdict.accepted and "lead" in verdict.reason,
    verdict.reason,
)

# 6 — rejects an explicit multi-year demand (layer 4)
verdict = evaluate(job(
    "Software Engineer",
    "Minimum 5 years of experience in backend development is required.",
))
check(
    "rejects 'minimum 5 years'",
    not verdict.accepted and "5 years" in verdict.reason,
    verdict.reason,
)

# 7 — rejects an unrelated field (layer 3)
verdict = evaluate(job(
    "Marketing Specialist",
    "Own our social media calendar and campaign reporting.",
))
check(
    "rejects unrelated field (marketing)",
    not verdict.accepted and "role in the title" in verdict.reason,
    verdict.reason,
)

# 7b — the real corpus. Every title below came from a live ATS run. Employer
# boards repeat company boilerplate on every posting, so a description-based
# match accepted Sales Executive and Customer Care Advisor; these lock in that
# the role must come from the title while تمهير/graduate only enrich it.
_BOILERPLATE = ("Join our graduate programme. We hire fresh grads and interns. "
                "Our stack includes Python and SQL. Associate level, rotational.")
_CORPUS = [
    ("Database Administrator - Tamheer Program", True),
    ("Graduate Development Program Engineer", True),
    ("Junior Data Engineer: Build Scalable Data Pipelines", True),
    ("Systems Engineer | KSA", True),
    ("Full Stack Engineer (Fresh Graduate - Saudi)", True),
    # تمهير runs across every major, so it must not qualify a posting alone.
    ("Marketing Specialist ( Tamheer ) x 3", False),
    ("E-Commerce Talent Curator ( Tamheer program )", False),
    ("Financial Planning & Analysis Junior (Tamheer Program)", False),
    ("Sales Executive", False),
    ("Customer Care Advisor (Voice)", False),
    ("Fraud Investigator", False),
    ("Compliance Associate - Builders Program", False),
    ("Junior eDiscovery Project Coordinator | Data-Driven Tech", False),
    ("Entry-Level Engineer: Hands-On Site & Design", False),
]
for _title, _want in _CORPUS:
    _v = evaluate(job(_title, _BOILERPLATE))
    check(f"corpus: {'keep' if _want else 'drop'} {_title[:44]}",
          _v.accepted == _want, _v.reason)

# 8 — year-parsing table
YEAR_CASES = [
    ("5+ years of experience", 5),
    ("3-5 years of experience", 3),          # range -> floor
    ("2 yrs in a similar role", 2),
    ("at least 5 years", 5),
    ("خبرة 3 سنوات في التطوير", 3),
    ("no experience needed", 0),
    ("Founded 30 years ago", 0),             # implausible -> ignored
    ("1 year contract, 2+ years experience", 1),  # minimum wins
]
for text, expected in YEAR_CASES:
    got = min_years_required(text)
    check(f"years: {text!r} -> {expected}", got == expected, f"got {got}")

# 9 — key stability across case
a = job("Software Engineer", company="Acme", city="Riyadh")
b = job("SOFTWARE ENGINEER", company="ACME", city="RIYADH")
check("key stable across case", a.key == b.key, f"{a.key} vs {b.key}")

# 10 — native_id takes precedence over the content hash
with_id = job("Software Engineer", native_id="abc123", city="Riyadh")
check(
    "native_id precedence",
    with_id.key == "jsearch:abc123" and not a.key.startswith("jsearch:"),
    f"{with_id.key} / {a.key}",
)


# 11 — JSearch job_ids carry a per-request suffix that must not reach the key.
# These two ids are real, captured from consecutive live runs: same posting,
# different suffix. Before the fix they produced different keys and the job was
# alerted twice.
from jobradar.sources.jsearch import JSearchSource  # noqa: E402

import base64  # noqa: E402

_src = JSearchSource("key", ["q"])


def _fake_id(identity: str, context: str) -> str:
    """Build an id the way JSearch does: base64("<identity>:<context>")."""
    return base64.b64encode(f"{identity}:{context}".encode()).decode()


_run1 = _fake_id("RBa7oJ_YUE5VPcBSAAAAAA==", "EswBCowBQUppVDR0SjRrbmcz")
_run2 = _fake_id("RBa7oJ_YUE5VPcBSAAAAAA==", "EssBCowBQUppVDR0S0duRTA5")

check(
    "request context stripped from job_id",
    _src._stable_id(_run1) == _src._stable_id(_run2) == "RBa7oJ_YUE5VPcBSAAAAAA==",
    f"{_src._stable_id(_run1)} vs {_src._stable_id(_run2)}",
)

_a = _src._to_job({"job_id": _run1, "job_title": "Grad Engineer", "employer_name": "X"})
_b = _src._to_job({"job_id": _run2, "job_title": "Grad Engineer", "employer_name": "X"})
check("same posting across runs -> same key", _a.key == _b.key, f"{_a.key} vs {_b.key}")

check(
    "distinct postings keep distinct keys",
    _src._stable_id(_fake_id("AAA==", "ctx")) != _src._stable_id(_fake_id("BBB==", "ctx")),
)

# Real id captured from a live run — the exact value that caused a duplicate.
_REAL = ("UkJhN29KX1lVRTVWUGNCU0FBQUFBQT09OkVzd0JDb3dCUVVwcFZEUjBTalJyYm1jelJqQnlZbmRx"
         "TFZGNVlWSjRXRFp6Ym1GRUxUaFViVXhpVFRreFlXUXpWRzVYY0RNMmNqaEJRMlpaUjBGVlNVRnE")
check("real captured id reduces to its identity half",
      _src._stable_id(_REAL) == "RBa7oJ_YUE5VPcBSAAAAAA==", _src._stable_id(_REAL))

check("non-base64 id passes through unchanged", _src._stable_id("plain-id-123") == "plain-id-123")
check("missing job_id -> None, falls back to content hash",
      _src._stable_id(None) is None and _src._stable_id("") is None)


# 12 — signature footer
from jobradar import config as _config  # noqa: E402
from jobradar.notify import MAX_BODY, with_signature  # noqa: E402

_signed = with_signature("body")
check("signature appended", _signed.endswith("<i>— Abdullah Alshehri's Job Radar</i>"), _signed)
check("apostrophe stays literal, not &#x27;", "&#x27;" not in _signed, _signed)

_original = _config.SIGNATURE
try:
    _config.SIGNATURE = ""
    check("empty signature appends nothing", with_signature("body") == "body")

    _config.SIGNATURE = "A & B <script>"
    check("signature is HTML-escaped",
          "A &amp; B &lt;script&gt;" in with_signature("x"), with_signature("x"))

    # Telegram rejects >4096 outright, so the footer must fit inside the cap.
    _config.SIGNATURE = _original
    check("oversized body still fits the cap", len(with_signature("x" * 9000)) <= MAX_BODY)
finally:
    _config.SIGNATURE = _original


# 13 — apply routing: prefer the employer's own link over aggregators
_gated = {"publisher": "LinkedIn", "url": "https://li/1", "is_direct": False}
_direct = {"publisher": "Acme Careers", "url": "https://acme.sa/1", "is_direct": True}

_j = job("Graduate Engineer", url="https://li/1", apply_options=[_gated, _direct])
check("direct_url finds the employer link", _j.direct_url == "https://acme.sa/1", _j.direct_url)
check("best_url prefers direct over aggregator", _j.best_url == "https://acme.sa/1", _j.best_url)
check("alternates exclude the chosen link",
      [o["url"] for o in _j.alternate_options] == ["https://li/1"])

_only_gated = job("Graduate Engineer", url="https://li/2", apply_options=[_gated])
check("no direct route -> falls back to the aggregator link",
      _only_gated.direct_url is None and _only_gated.best_url == "https://li/2")

_bare = job("Graduate Engineer", url="https://x/3")
check("no apply_options at all -> still returns a link", _bare.best_url == "https://x/3")

# The source must fold the top-level link in, and read its own direct flag.
_parsed = _src._to_job({
    "job_id": "abc", "job_title": "Grad Engineer", "employer_name": "Acme",
    "job_apply_link": "https://acme.sa/x", "job_apply_is_direct": True,
    "apply_options": [{"publisher": "Indeed", "apply_link": "https://in/x", "is_direct": False}],
})
check("source marks top-level link direct when flagged",
      _parsed.best_url == "https://acme.sa/x", _parsed.best_url)
check("source keeps the aggregator as an alternate",
      [o["publisher"] for o in _parsed.alternate_options] == ["Indeed"],
      _parsed.alternate_options)
check("malformed apply_options ignored, not fatal",
      _src._to_job({"job_id": "z", "job_title": "T", "employer_name": "E",
                    "job_apply_link": "https://z", "apply_options": "nonsense"}).best_url
      == "https://z")


# 14 — ATS sources: every link must be direct, and HTML must not reach filters
from jobradar.sources.ats import ATSSource, PROVIDERS, _plain  # noqa: E402

check("ATS costs no API quota", ATSSource([]).request_cost == 0)
check("all confirmed providers have an adapter",
      all(p in PROVIDERS for _c, p, _s in _config.ATS_BOARDS),
      [p for _c, p, _s in _config.ATS_BOARDS if p not in PROVIDERS])

check("HTML is stripped before filtering",
      _plain("<p>Join our <b>graduate</b> team</p>") == "Join our graduate team",
      _plain("<p>Join our <b>graduate</b> team</p>"))
check("HTML entities are unescaped",
      _plain("Python &amp; SQL") == "Python & SQL", _plain("Python &amp; SQL"))

_ats = ATSSource([("Tamara", "greenhouse", "tamara")])
_, _, _gh_map = PROVIDERS["greenhouse"]
_gh = _ats._to_job("Tamara", "greenhouse", "tamara", _gh_map, {
    "id": 4567, "title": "Graduate Software Engineer",
    "absolute_url": "https://boards.greenhouse.io/tamara/jobs/4567",
    "content": "<p>Join our <b>graduate</b> programme. Python and SQL.</p>",
    "location": {"name": "Riyadh"}, "updated_at": "2026-08-01T10:00:00Z",
})
check("greenhouse posting maps to a Job", _gh.title == "Graduate Software Engineer", _gh.title)
check("ATS link is marked direct", _gh.direct_url == _gh.url and _gh.best_url == _gh.url,
      f"{_gh.direct_url} / {_gh.url}")
check("ATS description reaches filters as plain text",
      "graduate" in _gh.haystack and "<b>" not in _gh.haystack)
check("ATS job passes the filters", evaluate(_gh).accepted, evaluate(_gh).reason)
check("ATS key is namespaced by source", _gh.key.startswith("ats:greenhouse:"), _gh.key)

# Nested location objects must not blow up or leak dicts into the message.
_, _, _sr_map = PROVIDERS["smartrecruiters"]
_sr = _ats._to_job("Almosafer", "smartrecruiters", "almosafer", _sr_map, {
    "id": "abc", "name": "Junior Data Analyst",
    "location": {"city": "Riyadh", "country": "sa", "remote": True},
    "releasedDate": "2026-08-01T09:00:00Z",
})
check("smartrecruiters builds an apply URL",
      _sr.url == "https://jobs.smartrecruiters.com/almosafer/abc", _sr.url)
check("nested location is flattened", _sr.city == "Riyadh" and _sr.is_remote is True)

check("a posting with no URL yields no fake apply route",
      _ats._to_job("X", "greenhouse", "x", _gh_map, {"id": 1, "title": "T"}).apply_options == [])


# 15 — freshness (layer 2)
from datetime import datetime, timedelta, timezone  # noqa: E402


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


_FRESH_DESC = "Graduate role. Python and SQL."
check("age_days is None without a timestamp",
      job("Data Analyst", _FRESH_DESC).age_days is None)
check("age_days reads an ISO timestamp",
      abs(job("Data Analyst", _FRESH_DESC, posted_at=_ago(5)).age_days - 5) < 0.01)
check("age_days handles a Z suffix",
      job("Data Analyst", _FRESH_DESC, posted_at="2026-08-01T00:00:00Z").age_days is not None)
check("a future-dated posting is treated as brand new",
      job("Data Analyst", _FRESH_DESC, posted_at=_ago(-2)).age_days == 0.0)
check("an unparseable date does not crash",
      job("Data Analyst", _FRESH_DESC, posted_at="last Tuesday").age_days is None)

check("a 3-day-old posting is kept",
      evaluate(job("Data Analyst", _FRESH_DESC, posted_at=_ago(3))).accepted)
check("a 13-day-old posting is kept (inside the 14-day window)",
      evaluate(job("Data Analyst", _FRESH_DESC, posted_at=_ago(13))).accepted)
_stale = evaluate(job("Data Analyst", _FRESH_DESC, posted_at=_ago(40)))
check("a 40-day-old posting is dropped",
      not _stale.accepted and "days ago" in _stale.reason, _stale.reason)
check("a posting with no date is kept, not assumed stale",
      evaluate(job("Data Analyst", _FRESH_DESC)).accepted)

_original_age = _config.MAX_AGE_DAYS
try:
    _config.MAX_AGE_DAYS = 0
    check("MAX_AGE_DAYS=0 disables the check",
          evaluate(job("Data Analyst", _FRESH_DESC, posted_at=_ago(400))).accepted)
finally:
    _config.MAX_AGE_DAYS = _original_age


if __name__ == "__main__":
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    for ok, name, detail in _results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {name}{suffix}")
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)
