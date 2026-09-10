#!/usr/bin/env python3
"""tracked_config — the ONE reader/writer of agent_view.config.json. Offline,
temp files only: every call passes an explicit path, so the real config on this
machine is never touched.

  python test_tracked_config.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tracked_config as tc  # noqa: E402

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + detail) if (detail and not ok) else ""))


def test_absent_vs_unreadable():
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "cfg.json"
        check("absent -> load() is {}", tc.load(fp) == {})
        check("absent -> load_quiet() is {}", tc.load_quiet(fp) == {})
        fp.write_text("<<<<<<< HEAD\n{\"a\":1}\n=======\n{\"a\":2}\n>>>>>>> x\n", encoding="utf-8")
        try:
            tc.load(fp)
            check("unreadable -> load() raises", False)
        except tc.TrackedConfigError:
            check("unreadable -> load() raises", True)
        check("unreadable -> load_quiet() is {} (readers degrade)", tc.load_quiet(fp) == {})
        try:
            tc.save({"x": 1}, fp)
            check("unreadable -> save() REFUSES (never rewrites from {})", False)
        except tc.TrackedConfigError:
            check("unreadable -> save() REFUSES (never rewrites from {})", True)
        check("unreadable -> the file is left exactly as it was",
              fp.read_text(encoding="utf-8").startswith("<<<<<<< HEAD"))
        # positive twin of the refusal: a READABLE file with the same keys is written
        fp.write_text(json.dumps({"a": 1}), encoding="utf-8")
        tc.save({"x": 1}, fp)
        check("readable -> save() writes and keeps a", json.loads(fp.read_text(encoding="utf-8")) == {"a": 1, "x": 1})


def test_save_preserves_other_keys_and_update_mutates():
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "cfg.json"
        fp.write_text(json.dumps({"gemini_api_key": "K", "monitor": {"url": "u", "token": "t"}}), encoding="utf-8")
        tc.save({"customer_profiles": False}, fp)
        d = json.loads(fp.read_text(encoding="utf-8"))
        check("save: other keys preserved", d.get("gemini_api_key") == "K" and d.get("monitor", {}).get("token") == "t")
        check("save: the patch landed", d.get("customer_profiles") is False)

        def _m(cfg):
            cfg.setdefault("mail_accounts", []).append({"id": "a"})
            return cfg
        tc.update(_m, fp)
        d = json.loads(fp.read_text(encoding="utf-8"))
        check("update: mutator applied, keys kept", d.get("mail_accounts") == [{"id": "a"}] and d.get("gemini_api_key") == "K")
        text = fp.read_text(encoding="utf-8")
        check("write: indent=2 (one serialisation for every writer)", '\n  "gemini_api_key"' in text)
        check("write: no temp file left behind", not fp.with_name(fp.name + ".tmp").exists())
        check("write: lock dir released", not fp.with_name(fp.name + ".lock").exists())


def test_lock_blocks_a_second_writer_until_released():
    with tempfile.TemporaryDirectory() as tmp:
        fp = Path(tmp) / "cfg.json"
        fp.write_text("{}", encoding="utf-8")
        tc.LOCK_TIMEOUT_S = 0.3
        with tc._Lock(fp):
            try:
                tc.save({"x": 1}, fp)
                check("lock: a second writer times out while held", False)
            except TimeoutError:
                check("lock: a second writer times out while held", True)
        tc.LOCK_TIMEOUT_S = 10.0
        tc.save({"x": 2}, fp)
        check("lock: after release the write goes through", json.loads(fp.read_text(encoding="utf-8")).get("x") == 2)


def main():
    for fn in (test_absent_vs_unreadable, test_save_preserves_other_keys_and_update_mutates,
               test_lock_blocks_a_second_writer_until_released):
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    print(f"\n{passed}/{len(_results)} checks passed")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
