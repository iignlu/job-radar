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

            records = self._records(payload)
            _log.info("query %r -> %d posting(s)", query, len(records))
            for item in records:
                try:
                    jobs.append(self._to_job(item))
                except Exception as exc:  # one malformed record, not a dead run
                    _log.warning("skipping malformed posting: %s", exc)
        return jobs

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
            records = None
            for key, value in data.items():
                if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                    if not self._shape_logged:
                        _log.info("postings are nested under data.%s", key)
                    records = value
                    break
            if records is None:
                _log.error(
                    "data is an object with keys %s, none holding a list of postings",
                    list(data)[:20],
                )
                return []
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

    def _to_job(self, item: dict) -> Job:
        pick = self._pick
        employer = item.get("employer") if isinstance(item.get("employer"), dict) else {}
        location = item.get("location") if isinstance(item.get("location"), dict) else {}

        return Job(
            title=str(pick(item, "job_title", "title", default="")).strip(),
            company=str(
                pick(item, "employer_name", "company_name", "company",
                     default=employer.get("name") or "Unknown")
            ).strip(),
            url=pick(item, "job_apply_link", "apply_link", "job_url", "url", default=""),
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
