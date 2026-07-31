"""Tiny JSON-over-HTTP client built on urllib.

Exists so the project can stay dependency-free. `requests` would be nicer to
read, but it would mean a pip install in CI, and this whole design is built
around not having one.

Retry policy: 429 and 5xx are retried with exponential backoff, because they
mean "try again later". Every other 4xx is raised immediately — a 401 is a bad
key and a 403 is an exhausted quota, and hammering either one just burns
requests we do not have to spare.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import log

_log = log.get(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0


class HttpError(Exception):
    """Any non-success HTTP outcome, including transport failures.

    `status` is 0 when the request never got far enough to receive one
    (DNS failure, refused connection, timeout).
    """

    def __init__(self, status: int, reason: str, body: str = ""):
        self.status = status
        self.reason = reason
        self.body = body
        detail = body.strip().replace("\n", " ")[:300]
        super().__init__(f"HTTP {status} {reason}{': ' + detail if detail else ''}")

    @property
    def is_auth_failure(self) -> bool:
        """401/403 — a bad key or a blocked/exhausted subscription."""
        return self.status in (401, 403)

    @property
    def is_quota_failure(self) -> bool:
        """429 — rate limited or out of monthly quota."""
        return self.status == 429


def _should_retry(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def _request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    payload: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
) -> dict:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    body = None
    hdrs = dict(headers or {})
    hdrs.setdefault("Accept", "application/json")
    hdrs.setdefault("User-Agent", "job-radar/0.1 (+https://github.com/)")
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    delay = backoff
    last_error: HttpError | None = None

    for attempt in range(1, retries + 1):
        request = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}

        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so it has to be caught first.
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover - body already consumed
                detail = ""
            last_error = HttpError(exc.code, str(exc.reason), detail)
            if not _should_retry(exc.code) or attempt == retries:
                raise last_error
            _log.warning(
                "%s %s -> %s, retry %d/%d in %.1fs",
                method, _redact(url), exc.code, attempt, retries, delay,
            )

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = HttpError(0, str(reason))
            if attempt == retries:
                raise last_error
            _log.warning(
                "%s %s -> %s, retry %d/%d in %.1fs",
                method, _redact(url), reason, attempt, retries, delay,
            )

        except json.JSONDecodeError as exc:
            # A body that is not JSON is not going to become JSON on retry.
            raise HttpError(0, f"malformed JSON response: {exc}") from exc

        time.sleep(delay)
        delay *= 2

    raise last_error or HttpError(0, "request failed")


def _redact(url: str) -> str:
    """Strip the bot token out of Telegram URLs before logging them."""
    if "/bot" in url:
        head, _, tail = url.partition("/bot")
        return f"{head}/bot<redacted>/{tail.partition('/')[2]}"
    return url


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> dict:
    return _request("GET", url, params=params, headers=headers,
                    timeout=timeout, retries=retries)


def post_json(url: str, payload: dict, *, headers: dict | None = None,
              timeout: float = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> dict:
    return _request("POST", url, payload=payload, headers=headers,
                    timeout=timeout, retries=retries)
