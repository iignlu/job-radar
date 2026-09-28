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
import html as _html
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

# Anchor text that is chrome, not a job title.
_NON_TITLES = {
    "see job", "view job", "apply", "apply now", "see all jobs", "view all",
    "unsubscribe", "see more jobs", "view job posting",
}

# Badges LinkedIn appends to a card's byline; noise once easy_apply is read.
_BADGES = [
    r"easy apply", r"\d+\s+company alum\w*", r"\d+\s+(?:school )?alum\w*",
    r"actively (?:reviewing|hiring)", r"be an early applicant",
    r"promoted", r"viewed", r"\d+ connections?",
]


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _sent_at(raw) -> str | None:
    """RFC 2822 Date header -> ISO 8601, or None if unparseable."""
    if not raw:
        return None
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw).isoformat()
    except Exception:
        return None


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
                yield (_decode(message.get("Subject")),
                       _sent_at(message.get("Date")),
                       self._body(message))
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
        """Extract {job_id: (url, title)} from one alert body.

        Anchored on the job link rather than on layout: the URL shape has been
        stable for years, while the surrounding table markup is regenerated
        constantly.

        Each job appears TWICE in an alert — once wrapping the company logo in
        a 48px cell, once wrapping the title text. Keeping the first occurrence
        per id therefore kept the logo link, whose anchor holds an <img> and no
        text, and every posting came out untitled. So every occurrence is read
        and the best title among them wins.
        """
        found = {}
        for match in _JOB_LINK_RE.finditer(body):
            job_id = match.group(1)
            url = _html.unescape(match.group(0))

            # Anchor body: from the end of the opening <a ...> tag to </a>.
            # Nested markup is stripped, so a logo anchor reduces to "".
            open_tag_end = body.find(">", match.end())
            if open_tag_end == -1:
                continue
            close = body.find("</a>", open_tag_end)
            inner = body[open_tag_end + 1: close] if close != -1 else ""
            title = _clean(_html.unescape(_TAG_RE.sub(" ", inner)))

            if title.lower() in _NON_TITLES or len(title) > 160:
                title = ""

            # The card's byline sits after the title anchor: "COMPANY · City,
            # Country", sometimes followed by badges like "Easy Apply" or
            # "3 company alumni". Stop at the next job link so one card cannot
            # bleed into the next.
            company = location = ""
            easy_apply = False
            if title and close != -1:
                tail = body[close + 4: close + 1200]
                boundary = tail.find("/jobs/view/")
                if boundary != -1:
                    tail = tail[:boundary]
                byline = _clean(_html.unescape(_TAG_RE.sub(" ", tail)))
                easy_apply = "easy apply" in byline.lower()
                for noise in _BADGES:
                    byline = re.sub(noise, " ", byline, flags=re.IGNORECASE)
                parts = [p.strip() for p in re.split(r"[·•]", byline) if p.strip()]
                if parts:
                    company = parts[0][:120]
                    location = parts[1][:120] if len(parts) > 1 else ""

            previous = found.get(job_id) or {}
            found[job_id] = {
                "url": previous.get("url") or url,
                "title": title or previous.get("title", ""),
                "company": company or previous.get("company", ""),
                "location": location or previous.get("location", ""),
                "easy_apply": easy_apply or previous.get("easy_apply", False),
            }
        return found

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

        for subject, sent_at, body in messages:
            postings = self._postings(body)
            total += len(postings)

            if self.dump:
                _log.info("--- %s -> %d link(s)", subject[:70], len(postings))
                for job_id, card in list(postings.items())[:6]:
                    _log.info("    %s | %s | %s | %s%s",
                              job_id, (card["title"] or "(no title)")[:44],
                              (card["company"] or "?")[:26],
                              (card["location"] or "?")[:24],
                              " | EASY APPLY" if card["easy_apply"] else "")

                # Print the raw markup around the first link. Guessing at the
                # layout is what produced "(no title found)" — this shows what
                # is actually there so the parser is written against evidence.
                first = _JOB_LINK_RE.search(body)
                if first and not any(t for _u, t in postings.values()):
                    window = body[max(0, first.start() - 400): first.end() + 900]
                    condensed = _WS_RE.sub(" ", window.replace("\n", " "))
                    _log.info("    RAW CONTEXT: %s", condensed[:1200])

            for job_id, card in postings.items():
                # Canonical form rather than the tracked link from the mail.
                # The id alone identifies the job, and the alert URL carries
                # ~600 characters of per-recipient tracking that wraps badly
                # and can be truncated into a dead link when forwarded.
                url = f"https://www.linkedin.com/jobs/view/{job_id}/"
                # Easy Apply means the application happens inside LinkedIn —
                # no external site, no second signup. Worth surfacing, since a
                # gated apply link is the main reason a matched job is useless.
                publisher = "LinkedIn ⚡ Easy Apply" if card["easy_apply"] else "LinkedIn"
                jobs.append(Job(
                    # Untitled postings are still sent: this source is not
                    # title-filtered, so a missing title costs presentation,
                    # not correctness. The id at least makes the link openable.
                    title=card["title"] or f"LinkedIn job {job_id}",
                    company=card["company"] or "via LinkedIn alert",
                    url=url,
                    source=self.name,
                    description="",
                    city=card["location"] or None,
                    country="Saudi Arabia",
                    publisher=publisher,
                    # An alert card carries no posting date, so the mail's own
                    # timestamp stands in: LinkedIn sends these when a job is
                    # newly matched, so it dates when the role surfaced. Without
                    # it every LinkedIn job would be ageless and the freshness
                    # layer could never drop one.
                    posted_at=sent_at,
                    native_id=job_id,
                    apply_options=[{"publisher": publisher, "url": url,
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
