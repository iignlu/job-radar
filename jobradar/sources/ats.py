"""Employer ATS boards — Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable.

Why this exists: an aggregator's apply link increasingly leads to a signup wall
or a paid plan, so a posting can match perfectly and still be unapplicable. An
ATS board is the employer's own hiring system, so every link here IS the
company's application form. These endpoints are also public and unmetered, so
this source costs nothing against the JSearch quota, and roles usually appear
on a company's board before an aggregator indexes them.

Only boards confirmed by tools/probe_ats.py belong in config.ATS_BOARDS. A
guessed slug returns the same empty result as a company with no openings, which
is a source that silently finds nothing forever.
"""

import html as _html
import re

from .. import http, log
from ..http import HttpError
from .base import Job, Source

_log = log.get(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain(text) -> str:
    """HTML description -> plain text.

    Several providers return HTML. The filter layers scan for keywords and
    parse years out of the description, and markup both hides matches (a term
    split by a <b>) and invents them, so it has to come off before filtering.
    """
    if not text:
        return ""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", str(text)))).strip()


def _pick(item: dict, *names, default=None):
    for name in names:
        value = item.get(name)
        if value not in (None, "", [], {}):
            return value
    return default


def _nested(item: dict, key: str, *names, default=None):
    """Read a field from a nested object, e.g. location.city."""
    inner = item.get(key)
    return _pick(inner, *names, default=default) if isinstance(inner, dict) else default


# --------------------------------------------------------------------------
# Per-provider adapters: (url template, list extractor, job mapper)
# --------------------------------------------------------------------------
# Each mapper returns the kwargs for a Job. Written with _pick so a renamed
# field degrades to a blank rather than an exception — the same defensive
# stance that made the JSearch v2 migration survivable.

def _greenhouse(company, slug, item):
    return dict(
        native_id=str(_pick(item, "id", default="")),
        title=_pick(item, "title", default=""),
        url=_pick(item, "absolute_url", "url", default=""),
        description=_plain(_pick(item, "content", "description")),
        city=_nested(item, "location", "name"),
        posted_at=_pick(item, "updated_at", "first_published", "created_at"),
    )


def _lever(company, slug, item):
    return dict(
        native_id=str(_pick(item, "id", default="")),
        title=_pick(item, "text", "title", default=""),
        url=_pick(item, "hostedUrl", "applyUrl", default=""),
        description=_plain(_pick(item, "descriptionPlain", "description")),
        city=_nested(item, "categories", "location"),
        employment_type=_nested(item, "categories", "commitment"),
        posted_at=_pick(item, "createdAt"),
    )


def _ashby(company, slug, item):
    return dict(
        native_id=str(_pick(item, "id", default="")),
        title=_pick(item, "title", default=""),
        url=_pick(item, "jobUrl", "applyUrl", default=""),
        description=_plain(_pick(item, "descriptionPlain", "descriptionHtml")),
        city=_pick(item, "location"),
        employment_type=_pick(item, "employmentType"),
        is_remote=bool(_pick(item, "isRemote", default=False)),
        posted_at=_pick(item, "publishedAt", "updatedAt"),
    )


def _smartrecruiters(company, slug, item):
    job_id = str(_pick(item, "id", default=""))
    return dict(
        native_id=job_id,
        title=_pick(item, "name", "title", default=""),
        url=f"https://jobs.smartrecruiters.com/{slug}/{job_id}" if job_id else "",
        description=_plain(_pick(item, "jobAd", "description")),
        city=_nested(item, "location", "city"),
        country=_nested(item, "location", "country"),
        is_remote=bool(_nested(item, "location", "remote", default=False)),
        posted_at=_pick(item, "releasedDate", "createdOn"),
    )


def _recruitee(company, slug, item):
    return dict(
        native_id=str(_pick(item, "id", default="")),
        title=_pick(item, "title", default=""),
        url=_pick(item, "careers_apply_url", "careers_url", default=""),
        description=_plain(
            f"{_pick(item, 'description', default='')} "
            f"{_pick(item, 'requirements', default='')}"
        ),
        city=_pick(item, "city", "location"),
        country=_pick(item, "country"),
        employment_type=_pick(item, "employment_type_code", "employment_type"),
        is_remote=bool(_pick(item, "remote", default=False)),
        posted_at=_pick(item, "published_at", "created_at"),
    )


def _workable(company, slug, item):
    shortlink = _pick(item, "shortlink", "url", "application_url", default="")
    return dict(
        native_id=str(_pick(item, "id", "shortcode", default="")),
        title=_pick(item, "title", default=""),
        url=shortlink,
        description=_plain(_pick(item, "description", "requirements")),
        city=_nested(item, "location", "city") or _pick(item, "city"),
        country=_nested(item, "location", "country") or _pick(item, "country"),
        is_remote=bool(_nested(item, "location", "workplaceType") == "remote"
                       or _pick(item, "remote", default=False)),
        employment_type=_pick(item, "employment_type", "type"),
        posted_at=_pick(item, "published_on", "created_at"),
    )


PROVIDERS = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        lambda d: d.get("jobs") or [], _greenhouse,
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{slug}?mode=json",
        lambda d: d if isinstance(d, list) else [], _lever,
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        lambda d: d.get("jobs") or [], _ashby,
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
        lambda d: d.get("content") or [], _smartrecruiters,
    ),
    "recruitee": (
        "https://{slug}.recruitee.com/api/offers/",
        lambda d: d.get("offers") or [], _recruitee,
    ),
    "workable": (
        "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        lambda d: d.get("jobs") or [], _workable,
    ),
}


class ATSSource(Source):
    """Polls the employer boards listed in config.ATS_BOARDS."""

    name = "ats"

    def __init__(self, boards):
        self.boards = list(boards)
        self._logged = set()

    @property
    def request_cost(self) -> int:
        """Zero — these endpoints are public and unmetered.

        Deliberately not len(self.boards): request_cost exists to track the
        JSearch quota, and counting free requests against it would make the
        budget arithmetic in the README wrong.
        """
        return 0

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for company, provider, slug in self.boards:
            adapter = PROVIDERS.get(provider)
            if not adapter:
                _log.error("%s: unknown provider %r, skipping", company, provider)
                continue

            template, extract, mapper = adapter
            try:
                payload = http.get_json(template.format(slug=slug), retries=2)
            except HttpError as exc:
                # One dead board must not cost the other seven.
                _log.error("%s (%s/%s) failed, skipping: %s", company, provider, slug, exc)
                continue

            try:
                records = extract(payload)
            except Exception as exc:
                _log.error("%s: unexpected response shape (%s)", company, exc)
                continue

            if records and provider not in self._logged:
                _log.info("%s fields: %s", provider, ", ".join(sorted(records[0])[:25]))
                self._logged.add(provider)

            count = 0
            for item in records:
                if not isinstance(item, dict):
                    continue
                try:
                    jobs.append(self._to_job(company, provider, slug, mapper, item))
                    count += 1
                except Exception as exc:
                    _log.warning("%s: skipping malformed posting: %s", company, exc)

            _log.info("%s (%s) -> %d posting(s)", company, provider, count)

        return jobs

    def _to_job(self, company, provider, slug, mapper, item) -> Job:
        fields = mapper(company, slug, item)
        url = fields.get("url") or ""

        return Job(
            title=str(fields.get("title") or "").strip(),
            company=company,
            url=url,
            source=f"{self.name}:{provider}",
            description=fields.get("description") or "",
            city=fields.get("city"),
            country=fields.get("country") or "Saudi Arabia",
            publisher=company,
            employment_type=fields.get("employment_type"),
            is_remote=bool(fields.get("is_remote")),
            posted_at=fields.get("posted_at"),
            native_id=f"{slug}:{fields.get('native_id')}" if fields.get("native_id") else None,
            # Every link here is the employer's own form, by construction.
            apply_options=[{"publisher": company, "url": url, "is_direct": True}] if url else [],
            raw=item,
        )
