#!/usr/bin/env python3
"""Dead-man's switch for job-radar.

The alert workflow can report its own failures — but only if it runs. On
6 August GitHub cancelled a scheduled run after fifteen minutes without ever
allocating a runner. No step executed, so nothing could raise an alarm, and
the outage looked exactly like a quiet evening.

This runs on a separate schedule and asks one question the alert workflow
cannot ask about itself: when did the bot last do anything at all? seen.json
is rewritten on every successful run, so its `updated` timestamp is a
liveness signal that does not depend on the alert workflow being healthy.

Both workflows would have to fail at the same time to hide an outage, which
is a much smaller target than one workflow failing alone.

Standard library only, like the rest of the project. Run:

    python tools/watchdog.py            # check, alert on staleness
    python tools/watchdog.py --dry-run  # check, print, never send
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import config, http, log  # noqa: E402

_log = log.get("watchdog")

SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


def last_updated(path: Path) -> datetime | None:
    """When the state file was last written, or None if unreadable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.error("cannot read %s: %s", path, exc)
        return None

    raw = payload.get("updated")
    if not raw:
        _log.error("%s has no 'updated' field", path)
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        _log.error("cannot parse 'updated' value %r: %s", raw, exc)
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def chat_id() -> str:
    """The secret if set, else the id cached in the state file.

    Same order the bot itself uses. The fallback matters: an expired chat id
    was the original outage, and an alarm that shares that failure mode is
    not an alarm.
    """
    configured = config.env("TELEGRAM_CHAT_ID", required=False)
    if configured:
        return configured
    try:
        return str(json.loads(
            Path(config.STATE_PATH).read_text(encoding="utf-8")
        ).get("chat_id") or "")
    except Exception:
        return ""


def alert(body: str, dry_run: bool) -> bool:
    if dry_run:
        print("--- would send ---")
        print(body)
        return True

    token = config.env("TELEGRAM_TOKEN", required=False)
    target = chat_id()
    if not (token and target):
        _log.error("no Telegram credentials — cannot raise the alarm")
        return False
    try:
        http.post_json(
            SEND_URL.format(token=token),
            {"chat_id": target, "text": body, "parse_mode": "HTML",
             "disable_web_page_preview": True},
        )
    except http.HttpError as exc:
        _log.error("could not send watchdog alert: %s", exc)
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Alert if job-radar has gone quiet.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the verdict instead of sending it")
    parser.add_argument("--max-hours", type=float,
                        default=config.WATCHDOG_MAX_SILENCE_HOURS,
                        help="hours of silence to tolerate")
    args = parser.parse_args(argv)
    log.setup()
    config.load_dotenv()

    path = Path(config.STATE_PATH)
    stamp = last_updated(path)

    if stamp is None:
        alert(
            "🔴 <b>job-radar watchdog</b>\n\n"
            f"Could not read {path} — the bot's state file is missing or "
            "corrupt, which means it is not running normally.",
            args.dry_run,
        )
        return 1

    hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
    _log.info("state file last written %.1fh ago (threshold %.1fh)",
              hours, args.max_hours)

    if hours <= args.max_hours:
        print(f"OK — job-radar wrote state {hours:.1f}h ago")
        return 0

    # Report in whole days once it has been going on that long: "62.4h" is
    # harder to act on than "2 days".
    span = f"{hours:.0f} hours" if hours < 48 else f"{hours / 24:.1f} days"
    alert(
        "🔴 <b>job-radar has gone quiet</b>\n\n"
        f"No successful run in {span} — expected one every few hours on "
        f"{config.SCHEDULE_HUMAN}.\n\n"
        "This usually means the scheduled workflow is failing or is not "
        "starting at all. Check the Actions tab.\n\n"
        "Nothing is lost: unsent matches are never marked seen, so they "
        "arrive on the next successful run.",
        args.dry_run,
    )
    # Non-zero so the watchdog run itself goes red in the Actions tab, giving
    # a second, independent signal alongside the Telegram message.
    return 1


if __name__ == "__main__":
    sys.exit(main())
