"""Seen-posting state.

A single JSON file, committed back to the repo by the workflow. That is the
whole persistence layer — no database, no external store, and the commit
history doubles as an audit log of what the bot noticed and when.
"""

import json
from pathlib import Path

from . import log

_log = log.get(__name__)


class SeenStore:
    """Append-ordered set of job keys, capped at `max_keys`.

    Order matters: it is what lets us trim the *oldest* keys when the file
    grows past the cap, rather than trimming arbitrary ones.
    """

    def __init__(self, path: str | Path, max_keys: int = 4000):
        self.path = Path(path)
        self.max_keys = max_keys
        self.keys: list[str] = []
        self._index: set[str] = set()

        # Resolved Telegram chat id, cached here so it survives getUpdates
        # expiry. Telegram keeps only ~24h of updates, so a bot that derives
        # its chat id every run stops working the first quiet day — which is
        # exactly what happened: four consecutive runs aborted before sending.
        self.chat_id: str = ""

        # first_run drives the "do not spam on day one" branch in cli.py.
        # A corrupt file counts as a first run: we cannot tell what was already
        # sent, and re-arming quietly beats firing fifty notifications.
        self.first_run = True

        if not self.path.is_file():
            _log.info("no state file at %s — this is a first run", self.path)
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            keys = payload["keys"]
            if not isinstance(keys, list):
                raise TypeError("keys is not a list")
        except Exception as exc:
            _log.warning("state file %s unreadable (%s) — treating as first run", self.path, exc)
            return

        self.keys = [str(k) for k in keys]
        self._index = set(self.keys)
        self.chat_id = str(payload.get("chat_id") or "")
        self.first_run = False
        _log.info("loaded %d seen key(s) from %s", len(self.keys), self.path)

    def __contains__(self, key: str) -> bool:
        return key in self._index

    def __len__(self) -> int:
        return len(self.keys)

    def add(self, key: str) -> bool:
        """Record one key. Returns False if it was already known."""
        if key in self._index:
            return False
        self.keys.append(key)
        self._index.add(key)
        return True

    def add_all(self, keys) -> int:
        """Record many keys, returning how many were new."""
        return sum(1 for key in keys if self.add(key))

    def _trim(self) -> None:
        if len(self.keys) > self.max_keys:
            dropped = len(self.keys) - self.max_keys
            self.keys = self.keys[-self.max_keys:]
            self._index = set(self.keys)
            _log.info("trimmed %d oldest key(s) to stay under %d", dropped, self.max_keys)

    def save(self) -> None:
        from datetime import datetime, timezone

        self._trim()
        payload = {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(self.keys),
            # Not a credential: the chat id is useless without the bot token,
            # which stays in Actions secrets and never lands in the repo.
            "chat_id": self.chat_id,
            "keys": self.keys,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _log.info("saved %d key(s) to %s", len(self.keys), self.path)
