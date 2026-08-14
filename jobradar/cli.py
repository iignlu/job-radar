"""Orchestration: fetch → dedup → filter → deliver.

Two behaviours in here are load-bearing and easy to get wrong, so they are
called out at their implementation sites: the first-run arm-don't-fire branch,
and the overflow deferral.
"""

import argparse
import html as _html

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
    parser.add_argument(
        "--probe-jsearch", action="store_true",
        help="test JSearch parameters one at a time and exit (costs ~5 requests)",
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

    if config.ENABLE_JSEARCH:
        sources.append(
            JSearchSource(
                api_key=config.env("RAPIDAPI_KEY"),
                queries=config.QUERIES,
                country=config.COUNTRY,
                date_posted=args.date_posted or config.DATE_POSTED,
                job_requirements=config.JOB_REQUIREMENTS,
            )
        )
    else:
        _log.info("jsearch disabled in config — running on the free sources only")
    return sources


def chat_id_for_run(configured: str, store, token: str, dry_run: bool = False,
                    resolver=resolve_chat_id) -> str:
    """Settle on a chat id, in descending order of durability.

    1. the TELEGRAM_CHAT_ID secret — set once, never expires
    2. the value cached in the state file — survives getUpdates expiry
    3. getUpdates — only works within ~24h of you last messaging the bot

    Step 2 exists because step 3 alone is not a foundation. Telegram retains
    updates for roughly a day, so the first quiet day left the bot with no way
    to address anyone: four consecutive scheduled runs aborted here, and
    because a bot that sends nothing looks exactly like a quiet week, nobody
    noticed until the fifth.

    Whatever is settled on is written back to the store, so the id is cached
    even when it came from the secret — that keeps the fallback warm if the
    secret is ever rotated away.

    Raises ChatIdUnavailable when a send is expected and no id can be found.
    """
    chat_id = configured
    if chat_id:
        _log.info("using TELEGRAM_CHAT_ID from the environment")
    elif store.chat_id:
        chat_id = store.chat_id
        _log.info("using chat id %s cached in %s", chat_id, store.path)
    elif token and not dry_run:
        chat_id = resolver(token)
        _log.info("resolved TELEGRAM_CHAT_ID=%s from getUpdates", chat_id)
        _log.info("set it as a repository secret to skip this lookup in future")

    if chat_id:
        store.chat_id = chat_id
    return chat_id


def record_silence(store, delivered: bool) -> int:
    """Update the silent-run counter; return the count when it is time to speak.

    Returns 0 when the bot should stay quiet, otherwise the number of
    consecutive silent runs that have just elapsed. Resets the counter when it
    fires, so a long outage produces a periodic note rather than one message
    per run for the rest of time.
    """
    if delivered:
        store.silent_runs = 0
        return 0

    store.silent_runs += 1
    if store.silent_runs < config.HEARTBEAT_AFTER_SILENT_RUNS:
        return 0

    elapsed = store.silent_runs
    store.silent_runs = 0
    return elapsed


def heartbeat_message(silent_runs: int, examined: int, yields) -> str:
    """The "still alive, nothing to send" note.

    Names what was checked, so that "nothing matched your filters" is visibly
    different from "nothing worked" — the distinction the receiving end could
    not previously make, and the reason two outages were caught by a person
    rather than by the system.
    """
    checked = ", ".join(
        f"{name}: {'unreachable' if count is None else count}"
        for name, count in yields
    ) or "none configured"

    return (
        "🟢 <b>Still watching — nothing new to send</b>\n\n"
        f"No new match in the last {silent_runs} check(s). "
        f"The most recent one looked at {examined} posting(s).\n\n"
        f"Sources — {_html.escape(checked)}\n\n"
        f"Next checks: {_html.escape(config.SCHEDULE_HUMAN)}.\n\n"
        "This note only appears when the bot has been quiet, so silence "
        "never has to mean guessing whether it broke."
    )


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

    # --- Telegram chat id. Reported in the same precedence the run uses, so
    # this output answers "where is the bot getting its chat id from today?"
    chat_id = config.env("TELEGRAM_CHAT_ID", required=False)
    cached = SeenStore(config.STATE_PATH, max_keys=config.MAX_SEEN_KEYS).chat_id
    if chat_id:
        print(f"OK    TELEGRAM_CHAT_ID set explicitly ({chat_id})")
    elif cached:
        print(f"OK    TELEGRAM_CHAT_ID not set, using the id cached in "
              f"{config.STATE_PATH} ({cached})")
        print("      Set it as a secret so it does not depend on the state file.")
    elif token:
        try:
            resolved = resolve_chat_id(token)
            print(f"OK    TELEGRAM_CHAT_ID not set, but resolved from getUpdates: {resolved}")
            print("      Set it as a secret: getUpdates only keeps ~24h of history,")
            print("      so this lookup stops working the first quiet day.")
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

    if args.probe_jsearch:
        try:
            JSearchSource(
                api_key=config.env("RAPIDAPI_KEY"),
                queries=config.QUERIES,
                country=config.COUNTRY,
                date_posted=args.date_posted or config.DATE_POSTED,
                job_requirements=config.JOB_REQUIREMENTS,
            ).probe()
        except config.MissingSetting as exc:
            _log.error("%s", exc)
            return 2
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

    # Loaded before the chat-id block because the store is where a previously
    # resolved chat id lives.
    store = SeenStore(config.STATE_PATH, max_keys=config.MAX_SEEN_KEYS)

    try:
        chat_id = chat_id_for_run(chat_id, store, token, dry_run=args.dry_run)
    except ChatIdUnavailable as exc:
        _log.error("%s", exc)
        return 2

    # TELEGRAM_ADMIN_CHAT_ID is optional and only matters once TELEGRAM_CHAT_ID
    # points at a shared channel: it keeps operational noise going to the
    # person who maintains the bot rather than to everyone subscribed.
    telegram = Telegram(
        token, chat_id,
        admin_chat_id=config.env("TELEGRAM_ADMIN_CHAT_ID", required=False),
        dry_run=args.dry_run,
    )

    # ---- fetch -----------------------------------------------------------
    fetched = []
    budget = 0
    yields: list[tuple[str, int | None]] = []
    for source in sources:
        budget += source.request_cost
        try:
            postings = source.fetch()
        except Exception as exc:
            _log.error("source %s failed entirely: %s", source.name, exc)
            yields.append((source.name, None))
            continue
        fetched.extend(postings)
        yields.append((source.name, len(postings)))

    # Per-source accounting on one line. Without it the run log reports a
    # single total, and a source that quietly stops contributing is invisible
    # for as long as the others cover for it — which is how a dead JSearch
    # went unnoticed while ATS and LinkedIn supplied 300 postings a run.
    _log.info(
        "source yield: %s",
        ", ".join(
            f"{name}={'FAILED' if count is None else count}"
            for name, count in yields
        ),
    )
    _log.info("fetched %d posting(s) using ~%d API request(s)", len(fetched), budget)

    if not fetched:
        _log.error(
            "every source returned nothing. That is not a quiet week — check "
            "the per-source line above and run --doctor."
        )

    # ---- dedup -----------------------------------------------------------
    # Two queries overlap heavily; the same posting also gets syndicated to
    # several publishers. First occurrence wins.
    unique: dict[str, object] = {}
    for job in fetched:
        unique.setdefault(job.key, job)
    jobs = list(unique.values())
    if len(jobs) != len(fetched):
        _log.info("deduped %d -> %d posting(s)", len(fetched), len(jobs))

    # ---- first run -------------------------------------------------------
    # CRITICAL: on a first run every posting in the window looks "new", so
    # sending them individually means fifty notifications in ten minutes and a
    # muted bot. Instead: mark everything seen, send one summary, stop. From
    # the next run on, "new" means genuinely new.
    if store.first_run:
        matched = sum(1 for job in jobs if evaluate(job).accepted)
        store.add_all(job.key for job in jobs)
        telegram.send_admin(
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

    # ---- heartbeat -------------------------------------------------------
    # A run that sends nothing is indistinguishable, from the receiving end,
    # from a run that never happened or one that died before sending. Both
    # outages so far were reported by a human noticing the quiet. So: count
    # consecutive silent runs, and after a day of them, break the silence on
    # purpose. The message doubles as a health report — it names what was
    # checked, so "nothing matched" is visibly different from "nothing worked".
    silent_for = record_silence(store, delivered=bool(to_send or deferred))
    if silent_for:
        telegram.send_admin(heartbeat_message(silent_for, len(jobs), yields))
        _log.info("sent heartbeat after %d silent run(s)", silent_for)

    if args.dry_run:
        _log.info("dry run — state not written, nothing sent")
    else:
        store.save()

    _log.info("done: %d message(s) delivered", telegram.sent)
    return 0
