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

from jobradar.cli import chat_id_for_run  # noqa: E402
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


if __name__ == "__main__":
    passed = sum(1 for ok, _, _ in _results if ok)
    total = len(_results)
    for ok, name, detail in _results:
        flag = "PASS" if ok else "FAIL"
        suffix = f"  ({detail})" if detail and not ok else ""
        print(f"[{flag}] {name}{suffix}")
    print(f"\n{passed}/{total} tests passed")
    sys.exit(0 if passed == total else 1)
