#!/usr/bin/env python3
"""Offline tests for sent_log.py - the entry contract, newest-first reads, the
two filters, and counts over successful sends only. Temp dirs throughout; the
real store is never opened and nothing here reaches the network.
Run: python test_sent_log.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sent_log  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _add(root, **kw):
    kw.setdefault("device", "desktop")
    return sent_log.append(root, kw)


def test_the_entry_carries_every_field():
    with tempfile.TemporaryDirectory() as root:
        ok = _add(root, at="2026-08-18T09:00:00", module="INV", ticket="1",
                  kind="comment", preview="x" * 300, approved_by="operator",
                  http="ok", pre_image=None)
        ck("entry: append returns True", ok is True)
        e = sent_log.read(root)[0]
        ck("entry: every contract field is present",
           set(e) == {"at", "module", "ticket", "kind", "preview", "approved_by",
                      "http", "error", "pre_image", "device"})
        ck("entry: preview is cut to 120 chars", len(e["preview"]) == 120)
        ck("entry: error defaults to null", e["error"] is None)
        ck("entry: pre_image defaults to null", e["pre_image"] is None)
        ck("entry: device is recorded", e["device"] == "desktop")


def test_a_missing_or_odd_field_is_normalised_not_dropped():
    with tempfile.TemporaryDirectory() as root:
        _add(root, module="INV", ticket=94313, kind="close")
        e = sent_log.read(root)[0]
        ck("normalise: ticket is a string", e["ticket"] == "94313")
        ck("normalise: at is stamped when missing", bool(e["at"]))
        ck("normalise: an unknown http value reads as fail", e["http"] == "fail")
        ck("normalise: a non-dict entry is refused, not raised",
           sent_log.append(root, "nope") is False)


def test_an_unserialisable_pre_image_is_kept_as_text():
    with tempfile.TemporaryDirectory() as root:
        ck("pre_image: an odd object does not lose the record",
           _add(root, module="INV", ticket="1", kind="estimate", http="ok",
                pre_image=object()) is True)
        e = sent_log.read(root)[0]
        ck("pre_image: it is stored as a string", isinstance(e["pre_image"], str))
        ck("pre_image: the row is still one JSON object",
           isinstance(json.loads(sent_log.path(root, "desktop")
                                 .read_text(encoding="utf-8").strip()), dict))


def test_read_is_newest_first_and_filters():
    with tempfile.TemporaryDirectory() as root:
        _add(root, at="2026-08-18T09:00:00", module="INV", ticket="1",
             kind="comment", http="ok")
        _add(root, at="2026-08-18T11:00:00", module="DEMO", ticket="2",
             kind="close", http="ok")
        _add(root, at="2026-08-18T10:00:00", module="INV", ticket="2",
             kind="estimate", http="ok")
        ats = [e["at"] for e in sent_log.read(root)]
        ck("read: newest first", ats == sorted(ats, reverse=True))
        ck("read: limit applies after sorting", len(sent_log.read(root, limit=2)) == 2)
        ck("read: module filter",
           {e["module"] for e in sent_log.read(root, module="INV")} == {"INV"})
        ck("read: ticket filter narrows within a module",
           [e["kind"] for e in sent_log.read(root, module="INV", ticket="2")] == ["estimate"])
        ck("read: a missing directory reads as empty",
           sent_log.read(str(Path(root) / "nope")) == [])


def test_two_devices_are_merged_on_read():
    with tempfile.TemporaryDirectory() as root:
        sent_log.append(root, {"at": "2026-08-18T09:00:00", "module": "INV",
                               "ticket": "1", "kind": "comment", "http": "ok",
                               "device": "desktop"})
        sent_log.append(root, {"at": "2026-08-18T10:00:00", "module": "INV",
                               "ticket": "1", "kind": "comment", "http": "ok",
                               "device": "laptop"})
        files = sorted(p.name for p in sent_log.dir_path(root).glob("*.jsonl"))
        ck("devices: one file per device", files == ["desktop.jsonl", "laptop.jsonl"])
        ck("devices: counts see both", sent_log.counts(root)[("INV", "1")]["comment"] == 2)


def test_counts_only_count_successful_sends():
    with tempfile.TemporaryDirectory() as root:
        _add(root, module="INV", ticket="1", kind="comment", http="ok")
        _add(root, module="INV", ticket="1", kind="comment", http="ok")
        _add(root, module="INV", ticket="1", kind="close", http="fail",
             error="PATCH close -> HTTP 400")
        _add(root, module="INV", ticket="1", kind="nonsense", http="ok")
        _add(root, module="DEMO", ticket="1", kind="estimate", http="ok")
        counts = sent_log.counts(root)
        ck("counts: successful comments are counted",
           counts[("INV", "1")]["comment"] == 2)
        ck("counts: a failed send is NOT counted",
           counts[("INV", "1")]["close"] == 0)
        ck("counts: every kind is always present",
           set(counts[("INV", "1")]) == {"comment", "close", "estimate", "create", "edit"})
        ck("counts: an unknown kind is ignored, not invented",
           "nonsense" not in counts[("INV", "1")])
        ck("counts: keys are (module, ticket) and do not bleed across modules",
           counts[("DEMO", "1")]["estimate"] == 1 and counts[("INV", "1")]["estimate"] == 0)
        ck("counts: a ticket with no sends has no key", ("DEMO", "9") not in counts)
        ck("counts: zero() is a fresh dict per caller",
           sent_log.zero() == {"comment": 0, "close": 0, "estimate": 0, "create": 0, "edit": 0}
           and sent_log.zero() is not sent_log.zero())


def test_http_unknown_is_kept_and_not_counted():
    with tempfile.TemporaryDirectory() as root:
        sent_log.append(root, {"module": "M", "ticket": "1", "kind": "close", "http": "unknown"})
        sent_log.append(root, {"module": "M", "ticket": "1", "kind": "close", "http": "weird"})
        rows = sorted(r["http"] for r in sent_log.read(root))
        ck("unknown kept, weird -> fail", rows == ["fail", "unknown"])
        ck("neither counted", sent_log.counts(root) == {})


def main() -> int:
    for fn in (test_http_unknown_is_kept_and_not_counted,
               test_the_entry_carries_every_field,
               test_a_missing_or_odd_field_is_normalised_not_dropped,
               test_an_unserialisable_pre_image_is_kept_as_text,
               test_read_is_newest_first_and_filters,
               test_two_devices_are_merged_on_read,
               test_counts_only_count_successful_sends):
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            ck(f"{fn.__name__} (raised): {type(exc).__name__}: {exc}", False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
