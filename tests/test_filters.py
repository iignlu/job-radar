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


if __name__ == "__main__":
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    for ok, name, detail in _results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {name}{suffix}")
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)
