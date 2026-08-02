"""LinkedIn job alerts, read out of your own inbox over IMAP.

Why this and not the obvious thing: LinkedIn has no public jobs API, and the
endpoints its own site calls are private and defended — scripting them violates
the User Agreement and gets IPs and accounts blocked, which builds you something
that breaks silently and takes your account with it.

A LinkedIn job alert is different. You asked LinkedIn to send it, LinkedIn sends
it to your inbox, and reading your own mail breaks nothing. It also reaches
postings Google never indexed, which is the whole gap this closes.

Standard library only: imaplib and email are both built in.

The parser deliberately reports what it found rather than assuming a layout.
Marketing-email HTML changes without notice, and a parser that silently returns
nothing is indistinguishable from a quiet week — the failure that hid the
JSearch outage for four runs. Run `python -m jobradar --dump-linkedin` to print
the structure of a real alert before tuning.
"""

import email
import imaplib
import re
from email.header import decode_header, make_header

from .. import config, log
from .base import Job, Source

_log = log.get(__name__)

# Job links in alert mail look like .../jobs/view/<id>/ and carry tracking
# query strings; the id is the only stable part.
_JOB_LINK_RE = re.compile(r"https?://[^\s\"'<>]*?/jobs/view/(\d+)[^\s\"'<>]*")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _decode(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return str(value or "")


class LinkedInEmailSource(Source):
    """Reads LinkedIn job-alert mail from an IMAP mailbox."""

    name = "linkedin-email"

    def __init__(self, host, user, password, mailbox="INBOX",
                 sender="jobalerts-noreply@linkedin.com", days=7, dump=False):
        self.host = host
        self.user = user
        self.password = password
        self.mailbox = mailbox
        self.sender = sender
        self.days = days
        self.dump = dump

    @property
    def request_cost(self) -> int:
        """Zero — this is your own mailbox, not a metered API."""
        return 0

    # ---------------------------------------------------------------- IMAP

    def _messages(self):
        """Yield (subject, body_text) for recent alert mail."""
        from datetime import datetime, timedelta

        since = (datetime.now() - timedelta(days=self.days)).strftime("%d-%b-%Y")
        connection = imaplib.IMAP4_SSL(self.host)
        try:
            connection.login(self.user, self.password)
            connection.select(self.mailbox, readonly=True)

            status, data = connection.search(
                None, f'(FROM "{self.sender}" SINCE {since})'
            )
            if status != "OK":
                _log.error("IMAP search failed: %s", status)
                return

            ids = (data[0] or b"").split()
            _log.info("found %d LinkedIn alert message(s) in the last %d day(s)",
                      len(ids), self.days)

            for message_id in ids:
                status, payload = connection.fetch(message_id, "(RFC822)")
                if status != "OK" or not payload or not payload[0]:
                    continue
                message = email.message_from_bytes(payload[0][1])
                yield _decode(message.get("Subject")), self._body(message)
        finally:
            try:
                connection.logout()
            except Exception:
                pass

    @staticmethod
    def _body(message) -> str:
        """Prefer HTML: the plain-text alternative drops the job links."""
        html_part = text_part = ""
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                decoded = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if part.get_content_type() == "text/html":
                html_part += decoded
            elif part.get_content_type() == "text/plain":
                text_part += decoded
        return html_part or text_part

    # ---------------------------------------------------------------- parse

    def _postings(self, body: str):
        """Extract (job_id, url, title) triples from one alert body.

        Anchored on the job link rather than on layout: the URL shape has been
        stable for years, while the surrounding table markup is regenerated
        constantly. Title comes from the anchor text when there is one.
        """
        seen = {}
        for match in _JOB_LINK_RE.finditer(body):
            job_id, url = match.group(1), match.group(0)
            if job_id in seen:
                continue

            # Anchor text immediately after this link, if the markup has one.
            tail = body[match.end(): match.end() + 600]
            anchor = re.search(r">\s*([^<>]{4,120}?)\s*<", tail)
            title = _clean(_TAG_RE.sub(" ", anchor.group(1))) if anchor else ""
            seen[job_id] = (url.replace("&amp;", "&"), title)
        return seen

    def fetch(self) -> list[Job]:
        jobs, total = [], 0
        try:
            messages = list(self._messages())
        except imaplib.IMAP4.error as exc:
            _log.error("IMAP login/select failed: %s", exc)
            return []
        except Exception as exc:
            _log.error("could not read mailbox: %s", exc)
            return []

        for subject, body in messages:
            postings = self._postings(body)
            total += len(postings)

            if self.dump:
                _log.info("--- %s -> %d link(s)", subject[:70], len(postings))
                for job_id, (url, title) in list(postings.items())[:5]:
                    _log.info("    %s | %s", job_id, title[:70] or "(no title found)")

                # Print the raw markup around the first link. Guessing at the
                # layout is what produced "(no title found)" — this shows what
                # is actually there so the parser is written against evidence.
                first = _JOB_LINK_RE.search(body)
                if first and not any(t for _u, t in postings.values()):
                    window = body[max(0, first.start() - 400): first.end() + 900]
                    condensed = _WS_RE.sub(" ", window.replace("\n", " "))
                    _log.info("    RAW CONTEXT: %s", condensed[:1200])

            for job_id, (url, title) in postings.items():
                if not title:
                    # A link we cannot title is not worth alerting on: the
                    # filters run on the title, so it would be rejected anyway
                    # and only add noise to the reject log.
                    continue
                jobs.append(Job(
                    title=title,
                    company=_clean(subject.split(" - ")[-1]) or "via LinkedIn alert",
                    url=url,
                    source=self.name,
                    description="",
                    country="Saudi Arabia",
                    publisher="LinkedIn",
                    native_id=job_id,
                    apply_options=[{"publisher": "LinkedIn", "url": url,
                                    "is_direct": False}],
                ))

        _log.info("linkedin-email -> %d posting(s) from %d message(s)",
                  len(jobs), len(messages))
        if messages and not jobs:
            _log.error(
                "read %d alert message(s) but extracted no postings — the mail "
                "layout has probably changed. Run --dump-linkedin to inspect it.",
                len(messages),
            )
        return jobs


def from_config(dump=False):
    """Build the source from config/env, or None when not configured."""
    user = config.env("IMAP_USER", required=False)
    password = config.env("IMAP_PASSWORD", required=False)
    if not (user and password):
        return None
    return LinkedInEmailSource(
        host=config.IMAP_HOST,
        user=user,
        password=password,
        mailbox=config.IMAP_MAILBOX,
        sender=config.LINKEDIN_ALERT_SENDER,
        days=config.LINKEDIN_ALERT_DAYS,
        dump=dump,
    )
