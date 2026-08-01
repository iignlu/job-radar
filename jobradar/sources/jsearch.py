"""JSearch (RapidAPI) — Google for Jobs.

Why this and not LinkedIn directly: JSearch indexes the Google for Jobs
aggregate, which already includes LinkedIn, Indeed and Glassdoor listings.
LinkedIn has no public jobs API, and its internal endpoints ban accounts that
touch them. One legitimate aggregator beats three brittle scrapers.
"""

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

    @property
    def request_cost(self) -> int:
        """One request per query, since num_pages=1 is a single billed call."""
        return len(self.queries)

    def _headers(self) -> dict:
        return {"X-RapidAPI-Key": self.api_key, "X-RapidAPI-Host": API_HOST}

    def _params(self, query: str) -> dict:
        params = {
            "query": query,
            "page": 1,
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

            data = payload.get("data") or []
            _log.info("query %r -> %d posting(s)", query, len(data))
            for item in data:
                try:
                    jobs.append(self._to_job(item))
                except Exception as exc:  # one malformed record, not a dead run
                    _log.warning("skipping malformed posting: %s", exc)
        return jobs

    def _to_job(self, item: dict) -> Job:
        return Job(
            title=(item.get("job_title") or "").strip(),
            company=(item.get("employer_name") or "Unknown").strip(),
            url=item.get("job_apply_link") or "",
            source=self.name,
            description=item.get("job_description") or "",
            city=item.get("job_city"),
            country=item.get("job_country"),
            publisher=item.get("job_publisher"),
            employment_type=item.get("job_employment_type"),
            is_remote=bool(item.get("job_is_remote")),
            posted_at=item.get("job_posted_at_datetime_utc"),
            native_id=item.get("job_id"),
            raw=item,
        )
