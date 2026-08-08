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
# CONFIRMED boards — verified live by tools/probe_ats.py, each returning real
# postings. These are polled on every run. They cost nothing against the
# JSearch quota, and every apply link is the employer's own form.
#
# Confirmed means "the board returned at least one job". Provider behaviour on
# a bad slug varies too much to infer existence from an HTTP 200, and a board
# with no jobs is unusable anyway.
ATS_BOARDS = [
    ("Lucidya", "workable", "lucidya"),
    ("Tamara", "greenhouse", "tamara"),
    ("Foodics", "workable", "foodics"),
    ("Salla", "workable", "salla"),
    ("Unifonic", "recruitee", "unifonic"),
    ("Bupa Arabia", "workable", "bupa"),
    ("Almosafer", "smartrecruiters", "almosafer"),
    ("Lean Technologies", "ashby", "leantech"),
]

ENABLE_ATS = True

# JSearch is the only metered source and the only one that has ever gone
# quiet on us. Set this to False to run on the free sources alone — the ATS
# boards and your LinkedIn alerts supply ~300 postings a run between them and
# produce every alert you currently receive, so switching it off costs less
# than it sounds like. Left on by default: when JSearch works it reaches
# postings the other two never see.
ENABLE_JSEARCH = True

# --------------------------------------------------------------------------
# LinkedIn, via your own job-alert emails
# --------------------------------------------------------------------------

# LinkedIn has no public jobs API, and scripting the endpoints its site uses
# violates the User Agreement and gets blocked. A job alert you asked LinkedIn
# to send you is a different thing entirely: it arrives in your inbox by
# design, and reading your own mail breaks nothing.
#
# Needs IMAP_USER and IMAP_PASSWORD. On Gmail that means an App Password
# (Google Account -> Security -> 2-Step Verification -> App passwords), never
# your account password. Leave either unset and this source stays off.
IMAP_HOST = "imap.gmail.com"
IMAP_MAILBOX = "INBOX"
LINKEDIN_ALERT_SENDER = "jobalerts-noreply@linkedin.com"

# How far back to read. Alerts repeat roles, and dedup handles the overlap.
LINKEDIN_ALERT_DAYS = 7

# Sources whose postings skip the filter layers entirely.
#
# A LinkedIn alert is the result of a saved search you already tuned — role,
# location, experience level. Running our own keyword filters over it would
# only discard things LinkedIn already decided were relevant, and the alert
# mail carries no description for the layers to read anyway. Dedup and the
# per-run cap still apply, so these cannot spam.
PRE_FILTERED_SOURCES = ["linkedin-email"]

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
# WHAT THE JOB IS. Matched against the TITLE only, and required — a posting
# with no role term in its title is not in this field, whatever its description
# says.
#
# Title-only is the important part. Descriptions carry company boilerplate: on
# an employer's ATS board every posting mentions the graduate programme and
# half of them list Python somewhere, so description matching accepted Customer
# Care Advisor, Sales Executive and Fraud Investigator. A title does not lie
# about what the job is.
MUST_MATCH_ROLE = [
    # software
    "software engineer", "software developer", "software development",
    "web developer", "full stack", "fullstack", "frontend", "front-end",
    "backend", "back-end", "mobile developer", "ios developer",
    "android developer", "developer", "programmer",
    # "engineer" is never listed bare: it matched "Entry-Level Engineer:
    # Hands-On Site & Design", a civil role. Every accepted use is qualified by
    # something that identifies the discipline.
    "graduate engineer", "trainee engineer", "development engineer",
    "development program engineer", "platform engineer", "solutions engineer",
    # data
    "data analyst", "data engineer", "data scientist", "data science",
    "database administrator", "business intelligence", "bi developer",
    "power bi", "analytics engineer", "machine learning", "ml engineer",
    "artificial intelligence", "ai engineer",
    # platform / quality / security
    "devops", "sre", "cloud engineer", "qa engineer", "quality assurance",
    "test engineer", "automation engineer", "cybersecurity",
    "security engineer", "systems engineer", "network engineer",
    # named technologies, which only appear in a title on a technical role
    "python", "java", "javascript", "typescript", "react", "laravel", ".net",
    "sql",
    # Arabic
    "مطور", "مبرمج", "مهندس برمجيات", "محلل بيانات", "مهندس بيانات",
    "تقنية المعلومات", "أمن سيبراني", "ذكاء اصطناعي", "علوم البيانات",
]

# WHO THE JOB IS FOR. Enrichment only — these explain why something matched but
# can no longer qualify a posting on their own.
#
# تمهير in particular: the Tamheer programme runs across every major, so on its
# own it matches marketing, HR and finance placements just as readily as
# technical ones. Same for graduate, junior and associate. Pairing them with a
# role term is what keeps "Database Administrator - Tamheer Program" and drops
# "Marketing Specialist (Tamheer)".
MUST_MATCH_LEVEL = [
    "graduate", "fresh grad", "junior", "entry level", "entry-level",
    "trainee", "intern", "co-op", "associate", "rotational",
    "تمهير", "حديث التخرج", "خريج", "متدرب",
]

# Kept as the union so anything reading MUST_MATCH still sees every term.
MUST_MATCH = MUST_MATCH_ROLE + MUST_MATCH_LEVEL

# Checked against the TITLE only. This is the layer that actually removes
# senior roles — a job description will happily mention "senior stakeholders"
# or "reports to the lead", but a title does not lie about its own level.
EXCLUDE_TITLE = [
    # too senior
    "senior", "sr.", "lead", "principal", "staff engineer", "manager",
    "head of", "director", "architect", "chief", "vp", "expert",
    "أول", "رئيس", "مدير", "خبير",
    # "engineer" is in MUST_MATCH_ROLE so that Systems Engineer and Graduate
    # Development Program Engineer pass; these keep the other engineering
    # disciplines out. Seen live: "Entry-Level Engineer: Hands-On Site &
    # Design" was a civil role that matched on seniority alone.
    "site engineer", "civil", "mechanical", "electrical", "chemical",
    "structural", "piping", "hse", "sales engineer", "field engineer",
    "process engineer", "maintenance engineer", "petroleum",
]

# Checked against the description. Catches the postings that pass the title
# check but demand a decade of experience in the body.
EXCLUDE_BODY = [
    "10+ years", "9+ years", "8+ years", "7+ years", "6+ years", "5+ years",
    "at least 5 years",
]

# Anything demanding strictly more than this many years is dropped.
MAX_YEARS_EXPERIENCE = 3

# Drop postings older than this many days. 0 disables the check.
#
# 14 rather than 7, deliberately. This barely touches the JSearch side, where
# DATE_POSTED already limits results to the last day — it exists for the ATS
# boards, which list every open role regardless of age. And there, age means
# much less: a role still on a company's own board has not been closed by
# anyone, so a ten-day-old Tamheer posting is as live as a one-day-old one.
# Seven days would also punish the Saudi week, where a Thursday posting sits
# through Friday and Saturday before you could realistically apply.
#
# Postings with no date at all are kept. An unknown date is not evidence of
# staleness, and rejecting on it would silently drop good roles — the same
# asymmetry as the years parser: a false rejection costs an opportunity.
MAX_AGE_DAYS = 14

# --------------------------------------------------------------------------
# Working arrangement
# --------------------------------------------------------------------------

# On-site only: drop anything flagged remote, or whose title says remote or
# hybrid. Only the title is scanned, not the description — a description
# saying "this role is not remote" would otherwise reject itself.
EXCLUDE_REMOTE = True

REMOTE_MARKERS = [
    "remote", "work from home", "wfh", "hybrid", "telecommute",
    "عن بعد", "عن بُعد", "هجين", "هجينة",
]

# Full-time only. Applied ONLY when the source states a type — most ATS
# boards omit it, and rejecting on silence would discard most of the board.
REQUIRE_FULL_TIME = True

# INTERN and TRAINEE are deliberately allowed: تمهير and graduate programmes
# are routinely tagged that way despite being full-time hours, and excluding
# them would drop exactly the roles this bot exists to find. Remove them from
# this set if you want strictly permanent positions.
FULL_TIME_TYPES = {
    "FULLTIME", "FULL_TIME", "FULL-TIME", "FULL TIME", "PERMANENT",
    "INTERN", "INTERNSHIP", "TRAINEE", "APPRENTICESHIP",
}

# Empty list means "anywhere in COUNTRY". Add e.g. ["riyadh", "jeddah"] to
# narrow. Matched case-insensitively against city, falling back to country.
CITIES: list[str] = []

# When CITIES is set, still allow remote postings through.
ALLOW_REMOTE = True

# Ceiling per run. Overflow is deferred, not dropped — see cli.py.
MAX_MESSAGES_PER_RUN = 12

# --------------------------------------------------------------------------
# Liveness — making silence mean one thing instead of five
# --------------------------------------------------------------------------
#
# The bot's normal output on a quiet day is nothing at all. That is also its
# output when the chat id has expired, when a run never got a runner, when a
# source has died, and when it is simply the weekend. Five different states,
# one indistinguishable symptom — which is why both outages so far were
# spotted by a human noticing the quiet rather than by the system.
#
# After this many consecutive runs that delivered nothing, say so out loud.
# 3 is one full working day: quiet enough not to nag, frequent enough that a
# genuine outage surfaces the same day it starts.
HEARTBEAT_AFTER_SILENT_RUNS = 3

# Plain-language schedule, quoted in the heartbeat so "when should I next hear
# from you?" is answered in the message itself. Keep in step with the cron in
# .github/workflows/jobs.yml — they are two statements of the same fact.
SCHEDULE_HUMAN = "09:00, 14:00 and 19:00 Riyadh time, Sunday–Thursday"

# Watchdog threshold, in hours. A run that never starts cannot report its own
# failure — GitHub cancelled one after fifteen minutes without ever giving it
# a runner, and no step ran, so nothing could raise the alarm. The watchdog
# workflow runs on its own schedule and checks how long ago the state file was
# last written; anything past this is silence that has gone on too long.
#
# 8 hours, checked after the second run of the working day: by then two runs
# should have written state, so a fresh file is ~1.5h old and a stale one is
# unambiguous. Both workflows would have to fail together to hide an outage.
WATCHDOG_MAX_SILENCE_HOURS = 8

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
