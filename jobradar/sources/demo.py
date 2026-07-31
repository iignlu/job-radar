"""A fixture source, for `--demo`.

Two jobs: it lets you exercise the whole pipeline — dedup, all five filter
layers, message rendering — with no credentials and no network, and it is a
worked example of the extension point described in cli.build_sources().

Adding a real source looks exactly like this: subclass Source, normalise
whatever the upstream gives you into Job, return a list.
"""

from .base import Job, Source

# Deliberately mixed: some should pass, some should be caught by each of the
# five layers, so `--demo --show-rejected` demonstrates every rejection path.
_FIXTURES = [
    {
        "title": "Graduate Software Engineer",
        "company": "Elm Company",
        "desc": "Join our 2026 graduate programme in Riyadh. You will work "
                "across Python and SQL with a mentor. No prior experience required.",
        "city": "Riyadh", "remote": False, "hours": 2,
    },
    {
        "title": "Junior Data Analyst",
        "company": "stc",
        "desc": "Entry level role building Power BI dashboards. 1-2 years of "
                "experience welcome but not required.",
        "city": "Riyadh", "remote": False, "hours": 5,
    },
    {
        "title": "مطور برمجيات حديث التخرج",
        "company": "أرامكو الرقمية",
        "desc": "نبحث عن خريج جديد للانضمام إلى فريق التطوير. خبرة 1 سنة أو أقل.",
        "city": "Dhahran", "remote": False, "hours": 9,
    },
    {
        "title": "Frontend Developer (React) - Trainee",
        "company": "Tamara",
        "desc": "Trainee position. React and TypeScript. Fully remote within KSA.",
        "city": None, "remote": True, "hours": 14,
    },
    {
        "title": "Senior Backend Engineer",
        "company": "Noon",
        "desc": "We need a backend engineer with deep Python expertise.",
        "city": "Riyadh", "remote": False, "hours": 3,
    },
    {
        "title": "Data Engineer",
        "company": "SABIC",
        "desc": "You will own our pipelines. At least 5 years of experience "
                "with distributed systems is required.",
        "city": "Jubail", "remote": False, "hours": 7,
    },
    {
        "title": "Software Developer",
        "company": "Lucid",
        "desc": "Minimum 6 years building production services in Java.",
        "city": "Riyadh", "remote": False, "hours": 11,
    },
    {
        "title": "Marketing Coordinator",
        "company": "Jahez",
        "desc": "Own the social calendar and campaign reporting for our brand.",
        "city": "Riyadh", "remote": False, "hours": 4,
    },
]


class DemoSource(Source):
    name = "demo"

    @property
    def request_cost(self) -> int:
        """Costs nothing — it never leaves the process."""
        return 0

    def fetch(self) -> list[Job]:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        jobs = []
        for index, row in enumerate(_FIXTURES):
            posted = (now - timedelta(hours=row["hours"])).isoformat()
            jobs.append(
                Job(
                    title=row["title"],
                    company=row["company"],
                    url=f"https://example.com/apply/{index}",
                    source=self.name,
                    description=row["desc"],
                    city=row["city"],
                    country="Saudi Arabia",
                    publisher="LinkedIn" if index % 2 == 0 else "Indeed",
                    employment_type="FULLTIME",
                    is_remote=row["remote"],
                    posted_at=posted,
                    native_id=f"demo{index}",
                )
            )
        return jobs
