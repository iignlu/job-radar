"""JSearch (RapidAPI) — Google for Jobs.

Why this and not LinkedIn directly: JSearch indexes the Google for Jobs
aggregate, which already includes LinkedIn, Indeed and Glassdoor listings.
LinkedIn has no public jobs API, and its internal endpoints ban accounts that
touch them. One legitimate aggregator beats three brittle scrapers.
"""

import base64

from .. import config, http, log
from ..http import HttpError
from .base import Job, Source

_log = log.get(__name__)

API_HOST = "jsearch.p.rapidapi.com"
API_BASE = f"https://{API_HOST}"


def api_url() -> str:
    """Full search URL, with the path read from config at call time.

    RapidAPI renames this path between versions ("search" -> "search-v2"), and
    a rename surfaces as a 404 rather than anything that looks like a version
    problem, so it is a config value rather than a constant.
    """
    return f"{API_BASE}/{config.JSEARCH_ENDPOINT.lstrip('/')}"


# Kept as a module attribute for callers that just want the current URL.
API_URL = api_url()


class JSearchSource(Source):
    name = "jsearch"

    def __init__(
        self,
        api_key: str,
        queries: list[str],
        country: str = "sa",
        date_posted: str = "today",
        job_requirements: str | None = None,
        num_pages: int = 1,
    ):
        self.api_key = api_key
        self.queries = list(queries)
        self.country = country
        self.date_posted = date_posted
        self.job_requirements = job_requirements
        self.num_pages = num_pages
        self._shape_logged = False
        self._nesting_logged = False
        self._options_logged = False

    @property
    def request_cost(self) -> int:
        """One request per query, since num_pages=1 is a single billed call."""
        return len(self.queries)

    def _headers(self) -> dict:
        return {"X-RapidAPI-Key": self.api_key, "X-RapidAPI-Host": API_HOST}

    def _params(self, query: str) -> dict:
        # Exactly the params search-v2 documents, and nothing else. v1 also
        # took `page`; sending it to v2 is at best ignored, so it is gone.
        params = {
            "query": query,
            "num_pages": self.num_pages,
            "country": self.country,
            "date_posted": self.date_posted,
        }
        if self.job_requirements:
            params["job_requirements"] = self.job_requirements
        return params

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        answered = 0
        for query in self.queries:
            try:
                payload = http.get_json(
                    api_url(), params=self._params(query), headers=self._headers()
                )
            except HttpError as exc:
                # One bad query must not kill the run — the other query may
                # still be perfectly good. Auth and quota failures are logged
                # loudly because they mean every query will fail too.
                if exc.is_auth_failure:
                    _log.error("query %r rejected: API key invalid or not subscribed (%s)", query, exc)
                elif exc.is_quota_failure:
                    _log.error("query %r rejected: rate limited or monthly quota exhausted (%s)", query, exc)
                else:
                    _log.error("query %r failed, skipping: %s", query, exc)
                continue

            answered += 1
            records = self._records(payload)
            _log.info("query %r -> %d posting(s)", query, len(records))
            for item in records:
                try:
                    jobs.append(self._to_job(item))
                except Exception as exc:  # one malformed record, not a dead run
                    _log.warning("skipping malformed posting: %s", exc)

        # Every query answered, none produced a posting. That is possible on a
        # quiet day but it is also what a broken parameter looks like, and the
        # two are indistinguishable from the outside — which is precisely how
        # an earlier outage stayed hidden for four runs. Say so at WARNING so
        # it stands out in a log full of INFO, and name the next step.
        if answered and not jobs:
            _log.warning(
                "jsearch: %d quer%s answered but returned 0 postings between "
                "them (params: country=%s date_posted=%s%s). If this repeats "
                "run `python -m jobradar --probe-jsearch` to test the "
                "parameters one at a time.",
                answered, "y" if answered == 1 else "ies",
                self.country, self.date_posted,
                f" job_requirements={self.job_requirements}"
                if self.job_requirements else "",
            )
        return jobs

    def probe(self) -> None:
        """Vary one parameter at a time and report what comes back.

        Exists because "0 postings" carries no information about *why*. A
        quiet day, a renamed field, a parameter the endpoint no longer honours
        and an exhausted quota all look identical from the run log, and the
        only way to tell them apart is to change one thing at a time and look.

        Costs one request per row below, so it is a deliberate command rather
        than something a scheduled run does.
        """
        query = self.queries[0] if self.queries else "software engineer"
        base = {"query": query, "num_pages": 1, "country": self.country,
                "date_posted": self.date_posted}

        trials = [
            ("what the bot sends today", dict(base)),
            ("date_posted=week", dict(base, date_posted="week")),
            ("date_posted=all", dict(base, date_posted="all")),
            ("date_posted=all, bare query 'software engineer'",
             dict(base, query="software engineer", date_posted="all")),
            ("date_posted=all, no country param",
             {"query": f"{query} in Saudi Arabia", "num_pages": 1,
              "date_posted": "all"}),
        ]

        _log.info("probing %s — %d request(s)", api_url(), len(trials))
        for label, params in trials:
            try:
                payload = http.get_json(
                    api_url(), params=params, headers=self._headers(), retries=1
                )
            except HttpError as exc:
                _log.error("%-46s ERROR %s", label, exc)
                continue

            data = payload.get("data")
            if isinstance(data, dict):
                shape = ", ".join(
                    f"{k}={len(v) if isinstance(v, list) else type(v).__name__}"
                    for k, v in data.items()
                )
                shape = f"data{{{shape}}}"
            elif isinstance(data, list):
                shape = f"data=list[{len(data)}]"
            else:
                shape = f"data={type(data).__name__}"

            count = len(self._records(payload))
            _log.info("%-46s -> %2d posting(s) | top-level=%s | %s",
                      label, count, list(payload)[:8], shape)

            # The first row that actually returns something is the answer, so
            # show one title as proof the records are real postings.
            if count:
                first = self._records(payload)[0]
                _log.info("%-46s    e.g. %r", "", str(
                    self._pick(first, "job_title", "title", default="?"))[:70])

    def _records(self, payload: dict) -> list[dict]:
        """Pull the posting list out of a response.

        v1 returned `data` as a list of postings. search-v2 nests them one
        level deeper, and iterating the wrapper object silently yields its
        string keys instead — which shows up as "'str' object has no attribute
        'get'" rather than anything that points at the response shape. So:
        accept both layouts, and when neither matches, log the shape rather
        than a stack trace, because the shape is the only thing you need.
        """
        data = payload.get("data")

        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            # Pick the key holding the postings. Order matters: an empty list
            # is accepted only as a last resort, because a response like
            # {"filters": [], "jobs": [...]} would otherwise match `filters`
            # first and report zero postings while the jobs sat one key over.
            populated = [
                (key, value) for key, value in data.items()
                if isinstance(value, list) and value and isinstance(value[0], dict)
            ]
            empty = [
                (key, value) for key, value in data.items()
                if isinstance(value, list) and not value
            ]
            candidates = populated or empty
            if not candidates:
                _log.error(
                    "data is an object with keys %s, none holding a list of postings",
                    list(data)[:20],
                )
                return []

            # Prefer a conventionally-named key when several qualify.
            key, records = next(
                (pair for pair in candidates if pair[0] in ("jobs", "results", "data")),
                candidates[0],
            )
            if not self._nesting_logged:
                _log.info("postings are nested under data.%s (%d record(s); "
                          "data keys: %s)", key, len(records), list(data)[:20])
                self._nesting_logged = True
        elif isinstance(payload.get("jobs"), list):
            records = payload["jobs"]
        else:
            _log.error(
                "unexpected response shape: top-level keys %s, data is %s",
                list(payload)[:20], type(data).__name__,
            )
            return []

        records = [item for item in records if isinstance(item, dict)]

        # One-time field dump: if the upstream renames fields, this names them
        # in the log instead of leaving every posting silently half-empty.
        if records and not self._shape_logged:
            _log.info("posting fields: %s", ", ".join(sorted(records[0])[:30]))
            self._shape_logged = True

        return records

    @staticmethod
    def _stable_id(job_id) -> str | None:
        """Reduce a JSearch job_id to the part that identifies the posting.

        job_id is base64. Decoded, it is "<job-identity>:<request-context>",
        and only the first half is stable — the second half changes on every
        request. Observed live: the same posting came back minutes apart as
        two different ids that decoded to the same identity but different
        context, so dedup missed it and the job was alerted twice.

        Left unfixed this re-notifies every job on every run, which is the one
        failure mode that makes the whole bot worth muting.

        Anything that is not base64, or decodes without a colon, is passed
        through untouched — a future id format should degrade to today's
        behaviour rather than to an exception.
        """
        if not job_id:
            return None
        raw = str(job_id)
        try:
            decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        except Exception:
            return raw
        identity, sep, _context = decoded.partition(":")
        return identity if (sep and identity) else raw

    @staticmethod
    def _pick(item: dict, *names, default=None):
        """First non-empty value among `names`.

        v1 prefixed every field with `job_`/`employer_`; v2 does not always.
        Listing both spellings costs one tuple and avoids a whole class of
        silently-blank alerts when a field is renamed.
        """
        for name in names:
            value = item.get(name)
            if value not in (None, "", [], {}):
                return value
        return default

    def _apply_options(self, item: dict) -> list:
        """Normalise every apply route the posting offers.

        Written defensively and logged once, because guessing at an upstream
        structure is exactly what produced the duplicate-alert bug: the shape
        is reported rather than assumed.
        """
        raw = item.get("apply_options")
        if not isinstance(raw, list):
            return []

        options = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if not self._options_logged:
                _log.info("apply_options fields: %s", ", ".join(sorted(entry)))
                self._options_logged = True
            url = self._pick(entry, "apply_link", "url", "link")
            if not url:
                continue
            options.append({
                "publisher": self._pick(entry, "publisher", "name", "source"),
                "url": url,
                "is_direct": bool(self._pick(entry, "is_direct", "direct", default=False)),
            })

        # Employer-run routes first, so best_url picks one without re-scanning.
        options.sort(key=lambda o: not o["is_direct"])
        return options

    def _to_job(self, item: dict) -> Job:
        pick = self._pick
        employer = item.get("employer") if isinstance(item.get("employer"), dict) else {}
        location = item.get("location") if isinstance(item.get("location"), dict) else {}

        options = self._apply_options(item)
        primary = pick(item, "job_apply_link", "apply_link", "job_url", "url", default="")

        # The top-level link has its own is_direct flag; fold it in so a posting
        # with no apply_options array still reports a direct route correctly.
        if primary and not any(o["url"] == primary for o in options):
            options.append({
                "publisher": pick(item, "job_publisher", "publisher"),
                "url": primary,
                "is_direct": bool(pick(item, "job_apply_is_direct", "is_direct", default=False)),
            })
            options.sort(key=lambda o: not o["is_direct"])

        return Job(
            title=str(pick(item, "job_title", "title", default="")).strip(),
            company=str(
                pick(item, "employer_name", "company_name", "company",
                     default=employer.get("name") or "Unknown")
            ).strip(),
            url=primary,
            apply_options=options,
            source=self.name,
            description=pick(item, "job_description", "description", default=""),
            city=pick(item, "job_city", "city", default=location.get("city")),
            country=pick(item, "job_country", "country", default=location.get("country")),
            publisher=pick(item, "job_publisher", "publisher", "source"),
            employment_type=pick(item, "job_employment_type", "employment_type"),
            is_remote=bool(pick(item, "job_is_remote", "is_remote", default=False)),
            posted_at=pick(
                item, "job_posted_at_datetime_utc", "job_posted_at",
                "posted_at_datetime_utc", "posted_at",
            ),
            native_id=self._stable_id(pick(item, "job_id", "id")),
            raw=item,
        )
