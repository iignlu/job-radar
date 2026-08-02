#!/usr/bin/env python3
"""Find out which ATS each company on the watchlist actually uses.

Run this before wiring any ATS source into build_sources(). A candidate slug
that is wrong returns the same "nothing here" as a company with no openings,
so guessing produces a source that silently finds nothing forever — the exact
failure mode that hid the JSearch outage for four runs.

    python tools/probe_ats.py            # probe every company in config
    python tools/probe_ats.py tabby elm  # probe specific slugs

Standard library only, like the rest of the project. Each board is public and
unauthenticated; this makes one cheap GET per company per provider and consumes
no API quota anywhere.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import ATS_COMPANIES  # noqa: E402

TIMEOUT = 15
UA = {"User-Agent": "job-radar-probe/0.1"}

# Each entry: provider -> (url template, function extracting the job count).
PROVIDERS = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        lambda d: len(d.get("jobs") or []),
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{slug}?mode=json",
        lambda d: len(d) if isinstance(d, list) else 0,
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        lambda d: len(d.get("jobs") or []),
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        lambda d: d.get("totalFound", len(d.get("content") or [])),
    ),
    "recruitee": (
        "https://{slug}.recruitee.com/api/offers/",
        lambda d: len(d.get("offers") or []),
    ),
    "workable": (
        "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        lambda d: len(d.get("jobs") or []),
    ),
}


def probe(provider: str, slug: str):
    """Return (provider, slug, job_count) when the board exists, else None."""
    template, count_of = PROVIDERS[provider]
    url = template.format(slug=urllib.parse.quote(slug))
    request = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            json.JSONDecodeError):
        return None

    try:
        count = count_of(payload)
    except Exception:
        return None

    # A board that exists but is empty is still a hit worth recording: the slug
    # is right, the company just is not hiring today.
    return (provider, slug, count) if count is not None else None


def main(argv=None):
    argv = argv or sys.argv[1:]
    if argv:
        targets = [(s, s) for s in argv]
    else:
        targets = ATS_COMPANIES

    jobs = [(name, slug, provider)
            for name, slug in targets
            for provider in PROVIDERS]

    print(f"probing {len(targets)} companies across {len(PROVIDERS)} providers "
          f"({len(jobs)} requests)\n")

    hits = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(probe, p, slug): (name, slug)
                   for name, slug, p in jobs}
        for future in futures:
            result = future.result()
            if result:
                name, slug = futures[future]
                hits.setdefault((name, slug), []).append(result)

    if not hits:
        print("no boards matched. Either the slugs are wrong or these companies "
              "do not use the probed providers.")
    else:
        print(f"{'COMPANY':<26} {'SLUG':<16} {'PROVIDER':<16} JOBS")
        print("-" * 68)
        for (name, slug), found in sorted(hits.items()):
            for provider, _s, count in sorted(found):
                print(f"{name:<26} {slug:<16} {provider:<16} {count}")

    missing = [n for n, s in targets if (n, s) not in hits]
    print(f"\n{len(hits)} matched, {len(missing)} unmatched")
    if missing:
        print("unmatched: " + ", ".join(missing))
        print("\nUnmatched means the candidate slug did not resolve — the company "
              "may use a different slug, a provider not probed here (Workday, "
              "Oracle, SuccessFactors, Taleo), or its own careers system.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
