"""Orchestration: fetch → dedup → filter → deliver.

Two behaviours in here are load-bearing and easy to get wrong, so they are
called out at their implementation sites: the first-run arm-don't-fire branch,
and the overflow deferral.
"""

import argparse

from . import config, log
from .filters import evaluate
from .notify import Telegram
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
    sources = [
        JSearchSource(
            api_key=config.env("RAPIDAPI_KEY"),
            queries=config.QUERIES,
            country=config.COUNTRY,
            date_posted=args.date_posted or config.DATE_POSTED,
            job_requirements=config.JOB_REQUIREMENTS,
        )
    ]
    return sources


def _sort_key(pair):
    """Newest first. Postings with no timestamp sort last under reverse=True."""
    job, _verdict = pair
    return job.posted_at or ""


def main(argv=None) -> int:
    args = parse_args(argv)
    log.setup(verbose=args.verbose)
    config.load_dotenv()

    limit = args.limit if args.limit is not None else config.MAX_MESSAGES_PER_RUN

    # Telegram credentials are only strictly needed when we might actually
    # send, so --dry-run works with nothing but a RapidAPI key.
    try:
        sources = build_sources(args)
        token = config.env("TELEGRAM_TOKEN", required=not args.dry_run)
        chat_id = config.env("TELEGRAM_CHAT_ID", required=not args.dry_run)
    except config.MissingSetting as exc:
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
        verdict = evaluate(job)
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
