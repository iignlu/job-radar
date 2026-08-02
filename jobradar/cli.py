"""Orchestration: fetch → dedup → filter → deliver.

Two behaviours in here are load-bearing and easy to get wrong, so they are
called out at their implementation sites: the first-run arm-don't-fire branch,
and the overflow deferral.
"""

import argparse

from . import config, log
from .filters import Verdict, evaluate
from .notify import ChatIdUnavailable, Telegram, describe_bot, resolve_chat_id
from .sources.ats import ATSSource
from .sources.demo import DemoSource
from .sources.linkedin_email import from_config as linkedin_from_config
from .sources.jsearch import JSearchSource
from .state import SeenStore

_log = log.get(__name__)

DATE_CHOICES = ["all", "today", "3days", "week", "month"]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m jobradar",
        description="Graduate job alerts for Saudi Arabia, delivered to Telegram.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print matches instead of sending them, and do not write state",
    )
    parser.add_argument(
        "--show-rejected", action="store_true",
        help="log every rejected posting with the layer that dropped it",
    )
    parser.add_argument(
        "--date-posted", choices=DATE_CHOICES, default=None,
        help=f"override config.DATE_POSTED (default: {config.DATE_POSTED})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help=f"max messages this run (default: {config.MAX_MESSAGES_PER_RUN})",
    )
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--demo", action="store_true",
        help="use built-in fixture postings instead of the API; no key needed",
    )
    parser.add_argument(
        "--doctor", action="store_true",
        help="check credentials and connectivity, then exit",
    )
    parser.add_argument(
        "--dump-linkedin", action="store_true",
        help="print the structure of your LinkedIn alert emails and exit",
    )
    parser.add_argument(
        "--resolve-chat-id", action="store_true",
        help="print your Telegram chat id (from getUpdates) and exit",
    )
    return parser.parse_args(argv)


def build_sources(args) -> list:
    """Assemble the sources for this run.

    THIS IS THE EXTENSION POINT. Everything downstream consumes `Job`, so a new
    source is purely additive — write a `Source` subclass that normalises into
    `Job` and append it here.

    Next up: ATS sources. Workday, Greenhouse and Lever all expose public,
    unauthenticated JSON endpoints per company board, which means no API key
    and no quota. They complement JSearch rather than duplicating it — an
    aggregator sees what companies syndicate, an ATS sees everything the
    company posts, often hours earlier.
    """
    if args.demo:
        # Fixtures — no key, no network, no quota consumed.
        return [DemoSource()]

    sources = []

    # Employer ATS boards first: free, and every link is the company's own
    # application form rather than an aggregator that may demand an account.
    if config.ENABLE_ATS and config.ATS_BOARDS:
        sources.append(ATSSource(config.ATS_BOARDS))

    # LinkedIn, read out of your own alert emails. Silently absent unless
    # IMAP credentials are configured, so nothing breaks without them.
    linkedin = linkedin_from_config(dump=getattr(args, "dump_linkedin", False))
    if linkedin:
        sources.append(linkedin)

    sources.append(
        JSearchSource(
            api_key=config.env("RAPIDAPI_KEY"),
            queries=config.QUERIES,
            country=config.COUNTRY,
            date_posted=args.date_posted or config.DATE_POSTED,
            job_requirements=config.JOB_REQUIREMENTS,
        )
    )
    return sources


def _sort_key(pair):
    """Newest first. Postings with no timestamp sort last under reverse=True."""
    job, _verdict = pair
    return job.posted_at or ""


def doctor() -> int:
    """Check every credential independently and report precisely what is wrong.

    Written so the output tells you the next action, not just that something
    failed — a 401 from RapidAPI and a 429 from RapidAPI need opposite fixes.
    """
    from .http import HttpError
    from .sources.jsearch import API_HOST, api_url
    from . import http as _http

    problems = 0

    # --- Telegram token
    token = config.env("TELEGRAM_TOKEN", required=False)
    if not token:
        print("FAIL  TELEGRAM_TOKEN is not set (get one from @BotFather)")
        problems += 1
    else:
        try:
            bot = describe_bot(token)
            print(f"OK    TELEGRAM_TOKEN valid — bot is @{bot.get('username', '?')}")
        except HttpError as exc:
            print(f"FAIL  TELEGRAM_TOKEN rejected by Telegram: {exc}")
            problems += 1
            token = ""

    # --- Telegram chat id
    chat_id = config.env("TELEGRAM_CHAT_ID", required=False)
    if chat_id:
        print(f"OK    TELEGRAM_CHAT_ID set explicitly ({chat_id})")
    elif token:
        try:
            resolved = resolve_chat_id(token)
            print(f"OK    TELEGRAM_CHAT_ID not set, but resolved from getUpdates: {resolved}")
            print("      Set it as a secret to avoid resolving it every run.")
        except (ChatIdUnavailable, HttpError) as exc:
            print(f"FAIL  could not resolve a chat id: {exc}")
            problems += 1

    # --- RapidAPI. Costs one request, so it is called out explicitly.
    key = config.env("RAPIDAPI_KEY", required=False)
    if not key:
        print("FAIL  RAPIDAPI_KEY is not set (rapidapi.com → JSearch → Basic plan)")
        problems += 1
    else:
        try:
            payload = _http.get_json(
                api_url(),
                params={"query": "software engineer", "page": 1, "num_pages": 1,
                        "country": config.COUNTRY, "date_posted": "week"},
                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": API_HOST},
                retries=1,
            )
            count = len(payload.get("data") or [])
            print(f"OK    RAPIDAPI_KEY valid — test query returned {count} posting(s)")
            print("      (that check consumed 1 of your ~200 monthly requests)")
        except HttpError as exc:
            if exc.is_auth_failure:
                print(f"FAIL  RAPIDAPI_KEY rejected — bad key, or not subscribed to "
                      f"JSearch on rapidapi.com ({exc.status})")
            elif exc.is_quota_failure:
                print(f"FAIL  RAPIDAPI_KEY rate limited or monthly quota exhausted ({exc.status})")
            elif exc.status == 404:
                print(f"FAIL  endpoint '/{config.JSEARCH_ENDPOINT}' does not exist — RapidAPI "
                      f"renamed the path. Read the current one from the API's Code Snippets "
                      f"panel and update config.JSEARCH_ENDPOINT ({exc.status})")
            else:
                print(f"FAIL  RapidAPI request failed: {exc}")
            problems += 1

    print()
    print("all checks passed" if not problems else f"{problems} problem(s) found")
    return 0 if not problems else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    log.setup(verbose=args.verbose)
    config.load_dotenv()

    if args.doctor:
        return doctor()

    if args.dump_linkedin:
        source = linkedin_from_config(dump=True)
        if not source:
            _log.error("IMAP_USER and IMAP_PASSWORD are not set — nothing to read")
            return 2
        found = source.fetch()
        _log.info("extracted %d posting(s)", len(found))
        for job in found[:15]:
            print(f"  {job.native_id}  {job.title}")
        return 0

    if args.resolve_chat_id:
        try:
            print(resolve_chat_id(config.env("TELEGRAM_TOKEN")))
            return 0
        except (ChatIdUnavailable, config.MissingSetting) as exc:
            _log.error("%s", exc)
            return 2

    limit = args.limit if args.limit is not None else config.MAX_MESSAGES_PER_RUN

    # Telegram credentials are only strictly needed when we might actually
    # send, so --dry-run works with nothing but a RapidAPI key.
    try:
        sources = build_sources(args)
        token = config.env("TELEGRAM_TOKEN", required=not args.dry_run)
        chat_id = config.env("TELEGRAM_CHAT_ID", required=False)
    except config.MissingSetting as exc:
        _log.error("%s", exc)
        return 2

    # Chat id is optional config: if it is absent we derive it from the bot's
    # own updates. One less secret to set up, and it works identically on the
    # Actions runner. Explicitly-set values always win.
    if not chat_id and token and not args.dry_run:
        try:
            chat_id = resolve_chat_id(token)
            _log.info("resolved TELEGRAM_CHAT_ID=%s from getUpdates", chat_id)
            _log.info("set it as a repository secret to skip this lookup in future")
        except ChatIdUnavailable as exc:
            _log.error("%s", exc)
            return 2

    telegram = Telegram(token, chat_id, dry_run=args.dry_run)

    # ---- fetch -----------------------------------------------------------
    fetched = []
    budget = 0
    for source in sources:
        budget += source.request_cost
        try:
            fetched.extend(source.fetch())
        except Exception as exc:
            _log.error("source %s failed entirely: %s", source.name, exc)

    _log.info("fetched %d posting(s) using ~%d API request(s)", len(fetched), budget)

    # ---- dedup -----------------------------------------------------------
    # Two queries overlap heavily; the same posting also gets syndicated to
    # several publishers. First occurrence wins.
    unique: dict[str, object] = {}
    for job in fetched:
        unique.setdefault(job.key, job)
    jobs = list(unique.values())
    if len(jobs) != len(fetched):
        _log.info("deduped %d -> %d posting(s)", len(fetched), len(jobs))

    store = SeenStore(config.STATE_PATH, max_keys=config.MAX_SEEN_KEYS)

    # ---- first run -------------------------------------------------------
    # CRITICAL: on a first run every posting in the window looks "new", so
    # sending them individually means fifty notifications in ten minutes and a
    # muted bot. Instead: mark everything seen, send one summary, stop. From
    # the next run on, "new" means genuinely new.
    if store.first_run:
        matched = sum(1 for job in jobs if evaluate(job).accepted)
        store.add_all(job.key for job in jobs)
        telegram.send_raw(
            "👋 <b>job-radar is armed</b>\n"
            f"Marked {len(jobs)} existing posting(s) as seen, "
            f"{matched} of which matched your filters.\n"
            "No individual alerts this run — from the next one you'll get "
            "one message per new match."
        )
        if not args.dry_run:
            store.save()
        else:
            _log.info("dry run — state not written")
        _log.info("first run complete: %d seen, %d would have matched", len(jobs), matched)
        return 0

    # ---- filter ----------------------------------------------------------
    fresh = [job for job in jobs if job.key not in store]
    _log.info("%d posting(s) not seen before", len(fresh))

    matches, rejected = [], []
    for job in fresh:
        # A pre-filtered source skips only the keyword layer — the upstream
        # search already chose the field. Seniority, freshness and the
        # on-site/full-time rules still apply: those are your requirements,
        # and no upstream knows them.
        verdict = evaluate(job, trust_source=job.source in config.PRE_FILTERED_SOURCES)
        (matches if verdict.accepted else rejected).append((job, verdict))

    if args.show_rejected:
        for job, verdict in rejected:
            _log.info("REJECT  %-55s | %s", (job.title or "?")[:55], verdict.reason)

    for job, verdict in matches:
        _log.info("ACCEPT  %-55s | %s", (job.title or "?")[:55], verdict.reason)

    _log.info("%d accepted, %d rejected", len(matches), len(rejected))

    # ---- deliver ---------------------------------------------------------
    # CRITICAL: overflow is deferred, not dropped. Sorted newest-first, we send
    # up to `limit` and deliberately do NOT mark the remainder seen, so they
    # come back through the pipeline on the next run and arrive then. Marking
    # them would silently discard real matches.
    matches.sort(key=_sort_key, reverse=True)
    to_send = matches[:limit]
    deferred = matches[limit:]

    for job, verdict in to_send:
        telegram.send_job(job, verdict.reason)

    if deferred:
        telegram.send_raw(
            f"… {len(deferred)} more match(es) deferred to the next run "
            f"(cap is {limit} per run)."
        )
        _log.info("deferred %d match(es) to the next run", len(deferred))

    # Everything we examined gets marked seen EXCEPT the deferred overflow.
    store.add_all(job.key for job, _ in to_send)
    store.add_all(job.key for job, _ in rejected)

    if args.dry_run:
        _log.info("dry run — state not written, nothing sent")
    else:
        store.save()

    _log.info("done: %d message(s) delivered", telegram.sent)
    return 0
