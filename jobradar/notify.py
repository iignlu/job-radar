"""Telegram delivery.

Every interpolated value is HTML-escaped before it reaches the message body.
Job titles and company names are attacker-adjacent text — they come from
whoever wrote the posting — and an unescaped '&' alone is enough to make
Telegram reject the whole send with a 400.
"""

import html
import time
from datetime import datetime, timezone

from . import http, log

_log = log.get(__name__)

API_ROOT = "https://api.telegram.org/bot{token}/{method}"
API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram tolerates roughly one message per second to a single chat. 1.2s is
# a deliberate margin — a run sends at most a dozen, so the extra seconds cost
# nothing and a 429 mid-batch would cost the rest of the batch.
SEND_PAUSE_SECONDS = 1.2

# Telegram hard-caps message bodies at 4096 characters.
MAX_BODY = 4000


class ChatIdUnavailable(RuntimeError):
    """getUpdates came back with nothing we can turn into a chat id."""


def describe_bot(token: str) -> dict:
    """getMe — cheap way to prove a token is real before using it."""
    payload = http.get_json(API_ROOT.format(token=token, method="getMe"))
    return payload.get("result") or {}


def resolve_chat_id(token: str) -> str:
    """Derive the chat id from the bot's pending updates.

    Telegram will not hand out a chat id until the chat exists, so this only
    works after you have sent the bot at least one message. Saves the user
    hand-parsing getUpdates JSON, and — because it runs on the Actions runner
    too — means TELEGRAM_CHAT_ID does not have to be configured at all.

    Caveat worth knowing: getUpdates only retains ~24h of history, and long
    polling elsewhere consumes it. Once resolved, setting the id as a secret is
    strictly more robust than resolving it every run.
    """
    payload = http.get_json(API_ROOT.format(token=token, method="getUpdates"))
    results = payload.get("result") or []
    if not results:
        raise ChatIdUnavailable(
            "getUpdates returned an empty result array. Open Telegram, send "
            "your bot any message (e.g. 'hi'), then run this again."
        )

    # result[0] first, as documented, but fall back to scanning every update:
    # a /start arrives as my_chat_member rather than message, and that would
    # otherwise look like a failure.
    for update in results:
        for field in ("message", "edited_message", "channel_post", "my_chat_member"):
            container = update.get(field) or {}
            chat_id = (container.get("chat") or {}).get("id")
            if chat_id is not None:
                return str(chat_id)

    raise ChatIdUnavailable(
        f"got {len(results)} update(s) but none carried a chat id. "
        "Send your bot a plain text message and retry."
    )


def humanise_age(iso: str | None) -> str:
    """'3h ago' from an ISO timestamp; 'recently' when we cannot tell."""
    if not iso:
        return "recently"
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "recently"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)

    seconds = (datetime.now(timezone.utc) - stamp).total_seconds()
    if seconds < 0:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    return f"{int(days / 30)}mo ago"


def format_job(job, reason: str) -> str:
    """Build the HTML body for one posting."""
    esc = html.escape

    lines = [f"🎯 <b>{esc(job.title or 'Untitled role')}</b>"]
    lines.append(f"🏢 {esc(job.company or 'Unknown company')}")

    # Location line: city · employment type · remote flag, skipping blanks so
    # a sparse posting does not render as a row of orphan separators.
    bits = []
    if job.location:
        bits.append(f"📍 {esc(job.location)}")
    if job.employment_type:
        bits.append(esc(job.employment_type.replace("_", " ").title()))
    if job.is_remote:
        bits.append("🌍 Remote")
    if bits:
        lines.append(" · ".join(bits))

    age = humanise_age(job.posted_at)
    if job.publisher:
        lines.append(f"🕐 {esc(age)} · via {esc(job.publisher)}")
    else:
        lines.append(f"🕐 {esc(age)}")

    lines.append(f"✅ <i>{esc(reason)}</i>")

    if job.url:
        lines.append(f'<a href="{esc(job.url, quote=True)}">Apply →</a>')

    body = "\n".join(lines)
    return body[:MAX_BODY]


class Telegram:
    """Sender. In dry-run mode it prints and never touches the network."""

    def __init__(self, token: str, chat_id: str, dry_run: bool = False,
                 pause: float = SEND_PAUSE_SECONDS):
        self.token = token
        self.chat_id = chat_id
        self.dry_run = dry_run
        self.pause = pause
        self.sent = 0

    def send_raw(self, body: str) -> bool:
        """Send one pre-formatted HTML message."""
        if self.dry_run:
            # No pause here: dry runs are for reading output, not pacing it.
            print("\n--- telegram (dry-run) " + "-" * 44)
            print(body)
            print("-" * 66)
            self.sent += 1
            return True

        if not self.token or not self.chat_id:
            _log.error("cannot send: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing")
            return False

        try:
            http.post_json(
                API_TEMPLATE.format(token=self.token),
                {
                    "chat_id": self.chat_id,
                    "text": body,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except http.HttpError as exc:
            _log.error("telegram send failed: %s", exc)
            return False

        self.sent += 1
        time.sleep(self.pause)
        return True

    def send_job(self, job, reason: str) -> bool:
        return self.send_raw(format_job(job, reason))
