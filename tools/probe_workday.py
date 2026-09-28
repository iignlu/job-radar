#!/usr/bin/env python3
"""Find out which watchlist companies are reachable on Workday — if any.

Why this exists before any Workday adapter: probe_ats.py confirmed 8 of the 50
companies and left 42 unaccounted for, and the obvious guess is that the large
employers (Aramco, SABIC, stc, the banks) are on Workday. That is a guess. A
Workday adapter built on a guess would return zero forever and look exactly
like a quiet week, which is the failure this project has already paid for twice.

So: measure first, build second. If this finds nothing, an adapter is worthless
and we have saved the work. If it finds three companies, we know which three
and what their URLs are.

The hard part is that Workday needs THREE unknowns per company, not one slug:

    https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

`tenant` is roughly the company name, `N` is the datacentre Workday assigned
that tenant (1, 2, 3, 5, 10, 12 are the common ones), and `site` is whatever
the company named its careers site. Trying every combination is thousands of
requests, so this probes only the single most common site name and lets the
datacentre vary. A company that answers on a different site name will show up
as a miss here; that is a deliberate trade for keeping this cheap, and a
partial answer beats a guess.

Standard library only, like the rest of the project. Every endpoint is public
and unauthenticated, and this consumes no API quota anywhere.

    python tools/probe_workday.py             # probe the whole watchlist
    python tools/probe_workday.py aramco stc  # probe specific tenants
"""

import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar.config import ATS_COMPANIES  # noqa: E402

TIMEOUT = 15

# Workday's job search endpoint wants a POST with a JSON body, unlike every
# other provider in probe_ats.py, which is part of why it needs its own script.
BODY = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0,
                   "searchText": ""}).encode("utf-8")
HEADERS = {
    "User-Agent": "job-radar-probe/0.1",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# The datacentre is assigned per tenant and cannot be derived from the name.
DATACENTRES = [1, 2, 3, 5, 10, 12]

# Only the most common site name. See the module docstring on why this is
# deliberately narrow rather than exhaustive.
SITE = "External"


def probe(tenant: str, datacentre: int):
    """Return (tenant, datacentre, job_count) when the board answers, else None."""
    host = f"{tenant}.wd{datacentre}.myworkdayjobs.com"
    url = f"https://{host}/wday/cxs/{tenant}/{SITE}/jobs"
    request = urllib.request.Request(url, data=BODY, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    # `total` is the authoritative count; jobPostings is the page we asked for.
    postings = payload.get("jobPostings")
    if not isinstance(postings, list):
        return None
    total = payload.get("total")
    count = total if isinstance(total, int) else len(postings)
    return (tenant, datacentre, count)


def candidates(name: str, slug: str):
    """Tenant spellings worth trying for one company.

    Workday tenants are lowercase and alphanumeric. The watchlist slug is the
    best guess; the squashed company name is a second, because a slug chosen
    for Greenhouse ("leantech") is not always the Workday tenant ("lean").
    """
    squashed = "".join(ch for ch in name.lower() if ch.isalnum())
    seen, out = set(), []
    for value in (slug, squashed):
        value = value.strip().lower()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def main(argv=None):
    argv = argv or sys.argv[1:]
    targets = [(t, t) for t in argv] if argv else ATS_COMPANIES

    attempts = [
        (name, tenant, dc)
        for name, slug in targets
        for tenant in candidates(name, slug)
        for dc in DATACENTRES
    ]

    print(f"probing {len(targets)} companies on Workday "
          f"({len(attempts)} requests, site={SITE!r})\n")

    hits = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(probe, tenant, dc): name
                   for name, tenant, dc in attempts}
        for future in futures:
            result = future.result()
            if result:
                tenant, dc, count = result
                # Keep the answer with the most jobs: a tenant can resolve on
                # more than one datacentre, but only one holds the real board.
                name = futures[future]
                if name not in hits or count > hits[name][2]:
                    hits[name] = (tenant, dc, count)

    if not hits:
        print("NO Workday boards found for any company on the watchlist.")
        print()
        print("That is a result, not a failure: it means a Workday adapter would")
        print("add nothing, and the 42 unconfirmed companies are on something")
        print("else (Oracle Cloud, SuccessFactors, Taleo, or an in-house site).")
        return 0

    print(f"{'COMPANY':<26} {'TENANT':<16} {'DC':<5} JOBS")
    print("-" * 60)
    for name in sorted(hits):
        tenant, dc, count = hits[name]
        print(f"{name:<26} {tenant:<16} wd{dc:<3} {count}")
    print()
    print(f"FOUND: {len(hits)} company/companies on Workday")
    print()
    print("Each usable board needs its tenant, datacentre and site wired into")
    print("config.ATS_BOARDS once a Workday adapter exists in sources/ats.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
