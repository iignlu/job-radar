#!/usr/bin/env python3
"""State tests — run with `python tests/test_state.py`.

No pytest: the project is standard-library-only, and that has to include its
own test run or CI would need a pip install.

The chat-id cases here are regression tests. Four consecutive scheduled runs
failed because the bot re-derived its Telegram chat id from getUpdates every
run, and Telegram only retains ~24h of updates — so the first quiet day killed
it. Caching the id in the state file is what stops that recurring.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobradar import config  # noqa: E402
from jobradar.cli import chat_id_for_run, heartbeat_message, record_silence  # noqa: E402
from jobradar.notify import ChatIdUnavailable, Telegram  # noqa: E402
from jobradar.state import SeenStore  # noqa: E402

_results: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((bool(condition), name, detail))


def store_path(tmp: Path, name: str = "seen.json") -> Path:
    return tmp / name


with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)

    # 1 — a missing file is a first run
    store = SeenStore(store_path(tmp, "absent.json"))
    check("missing file is a first run", store.first_run)
    check("missing file has no cached chat id", store.chat_id == "")

    # 2 — keys round-trip
    store = SeenStore(store_path(tmp, "keys.json"))
    check("add returns True for a new key", store.add("a"))
    check("add returns False for a known key", not store.add("a"))
    check("add_all counts only new keys", store.add_all(["a", "b", "c"]) == 2)
    store.save()
    reloaded = SeenStore(store_path(tmp, "keys.json"))
    check("reloaded store is not a first run", not reloaded.first_run)
    check("keys survive a round trip", reloaded.keys == ["a", "b", "c"])
    check("membership works after reload", "b" in reloaded and "z" not in reloaded)

    # 3 — the chat id round-trips, which is the whole fix
    path = store_path(tmp, "chat.json")
    store = SeenStore(path)
    store.chat_id = "820654816"
    store.add("a")
    store.save()
    check("chat id is persisted", SeenStore(path).chat_id == "820654816")
    check("chat id is written as a string, not an int",
          isinstance(json.loads(path.read_text())["chat_id"], str))

    # 4 — a state file written before the cache existed must still load
    legacy = store_path(tmp, "legacy.json")
    legacy.write_text(json.dumps({"updated": "x", "count": 1, "keys": ["a"]}))
    store = SeenStore(legacy)
    check("a pre-cache state file still loads", not store.first_run)
    check("a pre-cache state file has an empty chat id", store.chat_id == "")

    # 5 — a null or numeric chat id normalises rather than crashing
    odd = store_path(tmp, "odd.json")
    odd.write_text(json.dumps({"keys": ["a"], "chat_id": None}))
    check("a null chat id reads as empty", SeenStore(odd).chat_id == "")
    odd.write_text(json.dumps({"keys": ["a"], "chat_id": 820654816}))
    check("a numeric chat id is coerced to str", SeenStore(odd).chat_id == "820654816")

    # 6 — a corrupt file is a first run, and must not take the process with it
    broken = store_path(tmp, "broken.json")
    broken.write_text("{not json")
    store = SeenStore(broken)
    check("a corrupt file is treated as a first run", store.first_run)
    check("a corrupt file yields no keys", store.keys == [])

    # 7 — trimming drops the OLDEST keys, which is why order is preserved
    path = store_path(tmp, "trim.json")
    store = SeenStore(path, max_keys=3)
    store.add_all(["a", "b", "c", "d", "e"])
    store.chat_id = "820654816"
    store.save()
    reloaded = SeenStore(path, max_keys=3)
    check("trim keeps the newest keys", reloaded.keys == ["c", "d", "e"],
          str(reloaded.keys))
    check("trim rebuilds the membership index",
          "a" not in reloaded and "e" in reloaded)
    check("chat id survives a trim", reloaded.chat_id == "820654816")

    # 8 — chat-id precedence. The regression: with nothing configured and a
    # cached id present, the resolver must not be called at all, because
    # getUpdates is exactly what stopped working.
    calls = []

    def resolver(token):
        calls.append(token)
        return "from-getupdates"

    def fresh(chat_id=""):
        store = SeenStore(store_path(tmp, "precedence.json"))
        store.chat_id = chat_id
        return store

    got = chat_id_for_run("111", fresh("222"), "tok", resolver=resolver)
    check("the configured secret wins over the cache", got == "111")
    check("the secret path never calls getUpdates", calls == [])

    store = fresh("222")
    got = chat_id_for_run("", store, "tok", resolver=resolver)
    check("the cache is used when nothing is configured", got == "222")
    check("the cache path never calls getUpdates", calls == [], str(calls))

    store = fresh("")
    got = chat_id_for_run("", store, "tok", resolver=resolver)
    check("getUpdates is the last resort", got == "from-getupdates")
    check("getUpdates was called exactly once", calls == ["tok"], str(calls))
    check("a freshly resolved id is written back to the store",
          store.chat_id == "from-getupdates")

    store = fresh("222")
    chat_id_for_run("111", store, "tok", resolver=resolver)
    check("a configured id is cached too, so it survives secret rotation",
          store.chat_id == "111")

    # A dry run must not hit the network, and must not fail for want of an id.
    calls.clear()
    got = chat_id_for_run("", fresh(""), "tok", dry_run=True, resolver=resolver)
    check("a dry run resolves to nothing rather than calling getUpdates",
          got == "" and calls == [], str(calls))

    # 9 — the silent-run counter, which is what turns "no news" into a signal
    # instead of an ambiguity. Both outages so far were spotted by a human
    # noticing the quiet; this is the field that lets the bot notice instead.
    path = store_path(tmp, "silent.json")
    store = SeenStore(path)
    check("a fresh store starts with no silent runs", store.silent_runs == 0)
    store.silent_runs = 2
    store.add("a")
    store.save()
    check("the silent-run count is persisted", SeenStore(path).silent_runs == 2)

    legacy = store_path(tmp, "legacy2.json")
    legacy.write_text(json.dumps({"keys": ["a"], "chat_id": "1"}))
    check("a state file written before the counter existed reads as 0",
          SeenStore(legacy).silent_runs == 0)

    for junk in ("nonsense", None, -5, 1.9):
        legacy.write_text(json.dumps({"keys": ["a"], "silent_runs": junk}))
        value = SeenStore(legacy).silent_runs
        check(f"a {junk!r} silent-run value degrades to a sane int",
              isinstance(value, int) and value >= 0, repr(value))

    # 10 — when the bot decides to break its own silence
    threshold = config.HEARTBEAT_AFTER_SILENT_RUNS
    store = SeenStore(store_path(tmp, "beat.json"))

    fired = [record_silence(store, delivered=False) for _ in range(threshold)]
    check("silence below the threshold stays quiet",
          all(f == 0 for f in fired[:-1]), str(fired))
    check("the threshold run speaks up", fired[-1] == threshold, str(fired))
    check("the counter resets after speaking", store.silent_runs == 0)

    # It must not then repeat on every subsequent run — one note per stretch.
    check("the run straight after a heartbeat is quiet again",
          record_silence(store, delivered=False) == 0)

    # Delivering anything resets the count, so a single good run clears it.
    store.silent_runs = threshold - 1
    check("delivering jobs suppresses the heartbeat",
          record_silence(store, delivered=True) == 0)
    check("delivering jobs zeroes the counter", store.silent_runs == 0)

    # The message has to distinguish "nothing matched" from "nothing worked",
    # which is the entire point — so a dead source must be named as dead.
    body = heartbeat_message(3, 311, [("ats", 171), ("linkedin-email", 140),
                                      ("jsearch", 0)])
    check("the heartbeat reports each source", "ats: 171" in body, body)
    check("the heartbeat reports a zero-yield source", "jsearch: 0" in body, body)
    check("the heartbeat says when to expect the next check",
          "Next checks" in body, body)

    body = heartbeat_message(3, 0, [("ats", None)])
    check("a source that threw is named unreachable, not 0",
          "ats: unreachable" in body, body)
    check("a heartbeat with no sources does not crash",
          "none configured" in heartbeat_message(3, 0, []))

    # 11 — sharing the bot's link means strangers now appear in getUpdates.
    # Picking one of them would deliver a stranger your job alerts, so the
    # resolver must refuse to guess rather than choose.
    def updates(*chat_ids):
        return {"result": [{"message": {"chat": {"id": cid}}} for cid in chat_ids]}

    def resolve(payload):
        from jobradar import http
        from jobradar import notify
        original = http.get_json
        http.get_json = lambda *a, **k: payload
        try:
            return notify.resolve_chat_id("tok")
        finally:
            http.get_json = original

    check("a single chat still resolves", resolve(updates(820654816)) == "820654816")
    check("the same chat repeated is still one chat",
          resolve(updates(820654816, 820654816)) == "820654816")

    try:
        resolve(updates(820654816, 999111222))
        check("two different chats must not resolve silently", False,
              "it returned a chat id instead of refusing")
    except ChatIdUnavailable as exc:
        check("two different chats refuse rather than guess", True)
        check("the refusal names both chats so you can pick",
              "820654816" in str(exc) and "999111222" in str(exc), str(exc))

    try:
        resolve({"result": [{"edited_message": {}}]})
        check("updates with no chat id raise", False, "no exception")
    except ChatIdUnavailable:
        check("updates with no chat id raise", True)

    # 12 — operational messages must not land in a shared channel
    sends = []

    class FakeHttp:
        @staticmethod
        def post_json(url, payload, **kwargs):
            sends.append(payload["chat_id"])
            return {}

    from jobradar import http as _http
    from jobradar import notify as _notify
    _original_post = _http.post_json
    _http.post_json = FakeHttp.post_json
    try:
        bot = Telegram("tok", chat_id="@friends_channel",
                       admin_chat_id="820654816", pause=0)
        bot.send_raw("a job")
        bot.send_admin("a failure")
        check("job alerts go to the shared channel", sends[0] == "@friends_channel",
              str(sends))
        check("operational messages go to the admin instead",
              sends[1] == "820654816", str(sends))

        sends.clear()
        solo = Telegram("tok", chat_id="820654816", pause=0)
        solo.send_admin("a failure")
        check("with no admin set, operational messages fall back to the main chat",
              sends == ["820654816"], str(sends))
    finally:
        _http.post_json = _original_post


if __name__ == "__main__":
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    for ok, name, detail in _results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {name}{suffix}")
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)
