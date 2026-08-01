"""Every tunable in the project.

If you want to change what the bot considers a match, you change it here and
nowhere else. The filter code reads these at call time, so tests can override
them without reimporting anything.
"""

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Search — what we ask JSearch for
# --------------------------------------------------------------------------

# Two queries, deliberately. Each one costs an API request on every run, and
# the free tier is ~200/month (see the quota table in the README). Broad-but-few
# beats narrow-but-many: the keyword layers below do the real precision work.
QUERIES = [
    "software engineer graduate Saudi Arabia",
    "junior developer OR data analyst Riyadh",
]

COUNTRY = "sa"

# JSearch's search path. Lives here because RapidAPI has renamed it before:
# it was "search", and became "search-v2". When a run starts failing with
# HTTP 404 {"message": "Endpoint '/...' does not exist"}, the path moved again
# — open the API's Code Snippets panel on RapidAPI, read the new path, and
# change this one string.
JSEARCH_ENDPOINT = "search-v2"

# One of: all | today | 3days | week | month. `today` suits a 3×/day schedule —
# anything wider re-fetches the same postings we already marked seen.
DATE_POSTED = "today"

# JSearch-side pre-filter. Cheaper than filtering locally because it shrinks
# the response before it reaches us, but it is not reliable enough to trust on
# its own — plenty of senior roles still come back tagged this way.
JOB_REQUIREMENTS = "under_3_years_experience,no_experience"

# --------------------------------------------------------------------------
# Matching — the keyword layers
# --------------------------------------------------------------------------

# A posting must contain at least one of these to be considered at all.
# Two families: seniority signals ("this is aimed at someone new") and stack
# signals ("this is the kind of work you do"). Either family alone is enough,
# which is intentional — a "Data Analyst" posting with no seniority word is
# still worth a look.
#
# Matching is plain lowercased substring, so short terms bleed: "intern" also
# fires on "internal" and "international". That is a deliberate trade under
# the same reasoning as the years parser below — a false accept costs you three
# seconds of reading, a false reject costs you a job you never saw.
MUST_MATCH = [
    # seniority / early-career
    "graduate", "fresh grad", "junior", "entry level", "entry-level",
    "trainee", "intern", "co-op", "associate", "rotational",
    "تمهير", "حديث التخرج", "خريج", "متدرب",
    # stack / discipline
    "software engineer", "software developer", "full stack", "fullstack",
    "frontend", "front-end", "backend", "back-end", "web developer",
    "data analyst", "data engineer", "business intelligence",
    "bi developer", "power bi", "react", "laravel", "python", "sql",
    "javascript", "typescript",
    "مطور", "مهندس برمجيات", "محلل بيانات",
]

# Checked against the TITLE only. This is the layer that actually removes
# senior roles — a job description will happily mention "senior stakeholders"
# or "reports to the lead", but a title does not lie about its own level.
EXCLUDE_TITLE = [
    "senior", "sr.", "lead", "principal", "staff engineer", "manager",
    "head of", "director", "architect", "chief", "vp", "expert",
    "أول", "رئيس", "مدير", "خبير",
]

# Checked against the description. Catches the postings that pass the title
# check but demand a decade of experience in the body.
EXCLUDE_BODY = [
    "10+ years", "9+ years", "8+ years", "7+ years", "6+ years", "5+ years",
    "at least 5 years",
]

# Anything demanding strictly more than this many years is dropped.
MAX_YEARS_EXPERIENCE = 3

# Empty list means "anywhere in COUNTRY". Add e.g. ["riyadh", "jeddah"] to
# narrow. Matched case-insensitively against city, falling back to country.
CITIES: list[str] = []

# When CITIES is set, still allow remote postings through.
ALLOW_REMOTE = True

# Ceiling per run. Overflow is deferred, not dropped — see cli.py.
MAX_MESSAGES_PER_RUN = 12

# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

STATE_PATH = Path(os.environ.get("JOBRADAR_STATE", "seen.json"))

# ~4000 keys is a few months of history at this volume and keeps seen.json
# small enough that committing it back to git every run stays cheap.
MAX_SEEN_KEYS = 4000

# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader — KEY=value, # comments, optional quotes.

    Deliberately not python-dotenv: that would be a dependency, and this is
    forty lines of parsing we fully control. Existing environment variables
    always win, so GitHub Actions secrets are never shadowed by a stray local
    file.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


class MissingSetting(RuntimeError):
    """A required credential is not set anywhere."""


def env(name: str, required: bool = True, default: str = "") -> str:
    """Read a setting from the environment."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if required:
        raise MissingSetting(
            f"{name} is not set. Put it in .env for local runs, or add it as a "
            f"GitHub Actions secret for scheduled runs."
        )
    return default
