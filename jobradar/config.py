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

# JSearch-side pre-filter, e.g. "under_3_years_experience,no_experience".
#
# OFF by default. On search-v2 it returned zero results for every query — a
# full month of Saudi graduate roles came back empty with it set, and the
# filter layers in filters.py already do this job locally and more carefully.
# An empty response is indistinguishable from "no jobs today", so a server-side
# filter that silently matches nothing is worse than no filter at all.
#
# Set it back to "under_3_years_experience,no_experience" if you want to shrink
# responses and have confirmed it actually returns results.
JOB_REQUIREMENTS = ""

# --------------------------------------------------------------------------
# ATS watchlist — companies whose own job boards we want to poll
# --------------------------------------------------------------------------

# Why this exists: aggregator apply links increasingly sit behind a signup or
# a paid plan. A company's own ATS board is the opposite — the link IS the
# employer's form, it costs no API quota, and roles usually appear there hours
# before Google indexes them.
#
# Selection: Saudi employers with a strong reputation as places to work, biased
# toward those that actually hire software and data graduates. Sourced from
# LinkedIn Top Companies KSA, Great Place to Work KSA, and the Saudi tech
# scene. See README for the reasoning.
#
# The second element is a CANDIDATE board slug, not a confirmed one. Slugs are
# usually the company name lowercased, but plenty are not, and a wrong slug is
# indistinguishable from a company with no open roles. Run tools/probe_ats.py
# (or the probe-ats workflow) to find out which are real before wiring any of
# these into build_sources().
ATS_COMPANIES = [
    # --- Saudi tech & startups: most likely to run Greenhouse/Lever/Ashby
    ("Tamara", "tamara"),
    ("Tabby", "tabby"),
    ("Foodics", "foodics"),
    ("Salla", "salla"),
    ("Unifonic", "unifonic"),
    ("Jahez", "jahez"),
    ("Nana", "nana"),
    ("Lean Technologies", "leantech"),
    ("Sary", "sary"),
    ("Mrsool", "mrsool"),
    ("Zid", "zid"),
    ("Rasan", "rasan"),
    ("Hakbah", "hakbah"),
    ("Lucidya", "lucidya"),
    ("stc pay", "stcpay"),
    ("Almosafer", "almosafer"),
    ("Floward", "floward"),
    ("Geidea", "geidea"),
    ("HungerStation", "hungerstation"),
    ("Ninja", "ninja"),
    ("Moyasar", "moyasar"),
    ("PayTabs", "paytabs"),
    ("Tarabut", "tarabut"),
    ("Sadad", "sadad"),

    # --- National tech & Vision 2030 giga-projects
    ("Elm", "elm"),
    ("Thiqah", "thiqah"),
    ("Takamol Holding", "takamol"),
    ("SDAIA", "sdaia"),
    ("Aramco Digital", "aramcodigital"),
    ("Cyberani", "cyberani"),
    ("NEOM", "neom"),
    ("Saudi Aramco", "aramco"),
    ("SABIC", "sabic"),
    ("Ma'aden", "maaden"),
    ("stc", "stc"),
    ("Mobily", "mobily"),
    ("Zain KSA", "zain"),
    ("Red Sea Global", "redseaglobal"),
    ("Qiddiya", "qiddiya"),
    ("Diriyah Company", "diriyah"),
    ("ROSHN", "roshn"),
    ("PIF", "pif"),

    # --- Banking & insurance: large in-house data/engineering teams
    ("Al Rajhi Bank", "alrajhibank"),
    ("Saudi National Bank", "snb"),
    ("Riyad Bank", "riyadbank"),
    ("Alinma Bank", "alinma"),
    ("Tawuniya", "tawuniya"),
    ("Bupa Arabia", "bupa"),
    ("SIDF", "sidf"),

    # --- Consulting: heavy graduate intake
    ("Accenture Middle East", "accenture"),
]

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
# Presentation
# --------------------------------------------------------------------------

# Aggregator apply links (LinkedIn, Indeed, ZipRecruiter…) increasingly sit
# behind a signup or a paid plan, so a posting can match perfectly and still be
# unapplicable. When a posting offers the employer's own careers page or ATS,
# lead with that instead.
PREFER_DIRECT_APPLY = True

# Also list the other routes, so a gated primary link is not a dead end.
# 0 switches the alternates line off.
MAX_ALTERNATE_APPLY_LINKS = 3

# Footer appended to every Telegram message. Set to "" to switch it off.
# Plain text — it is HTML-escaped before sending, so apostrophes, & and < are
# all safe to write literally here.
SIGNATURE = "Abdullah Alshehri's Job Radar"

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
