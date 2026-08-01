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
    not verdict.accepted and "keyword" in verdict.reason,
    verdict.reason,
)

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


if __name__ == "__main__":
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    for ok, name, detail in _results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {name}{suffix}")
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)
