#!/usr/bin/env python3
"""Offline tests for worklog.py - pairing, share, open intervals, two devices
merged, heartbeats + auto-close (reap), and what hours_by_ticket refuses to count. Every test runs against a
FRESH TEMP DIR; the real store is never opened and nothing here reaches the
network. Run: python test_worklog.py"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import worklog  # noqa: E402

FAILS = []


def ck(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def _ev(root, device, **kw):
    """One raw event with an explicit clock and device, so durations are exact
    and the two-device cases can be built without a second machine."""
    kw.setdefault("device", device)
    worklog.append_event(root, kw)


def _pair(root, device, module, ticket, work_id, start, end, share=1.0,
          kind="claude", auto_closed=False):
    _ev(root, device, ev="start", work_id=work_id, module=module, ticket=ticket,
        at=start, share=share, kind=kind)
    _ev(root, device, ev="end", work_id=work_id, module=module, ticket=ticket,
        at=end, share=share, kind=kind, auto_closed=auto_closed)


# ------------------------------------------------------------------ pairing
def test_start_end_pairing():
    with tempfile.TemporaryDirectory() as root:
        worklog.start(root, "w1", [{"module": "INV", "ticket": "94313"}], kind="claude")
        ck("pairing: one open interval after start",
           len(worklog.open_intervals(root)) == 1)
        worklog.end(root, "w1")
        ivs = worklog.intervals(root)
        ck("pairing: still exactly one interval", len(ivs) == 1)
        ck("pairing: it is complete", ivs[0]["end"] is not None)
        ck("pairing: carries module/ticket/kind",
           (ivs[0]["module"], ivs[0]["ticket"], ivs[0]["kind"]) == ("INV", "94313", "claude"))
        ck("pairing: a second end writes nothing (nothing is open)",
           worklog.end(root, "w1") == [])


def test_repeated_sessions_on_one_ticket_pair_fifo():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "a", "INV", "1", "w1", "2026-08-18T09:00:00", "2026-08-18T10:00:00")
        _pair(root, "a", "INV", "1", "w2", "2026-08-18T11:00:00", "2026-08-18T11:30:00")
        ivs = worklog.intervals(root)
        ck("repeat: two intervals for the same ticket", len(ivs) == 2)
        ck("repeat: hours are summed",
           abs(worklog.hours_by_ticket(root)[("INV", "1")] - 1.5) < 1e-9)


def test_end_of_one_work_id_leaves_the_other_open():
    with tempfile.TemporaryDirectory() as root:
        worklog.start(root, "w1", [{"module": "INV", "ticket": "1"}])
        worklog.start(root, "w2", [{"module": "INV", "ticket": "2"}])
        worklog.end(root, "w1")
        ck("end: closes only its own work_id",
           [iv["work_id"] for iv in worklog.open_intervals(root)] == ["w2"])


def test_end_can_close_a_subset():
    with tempfile.TemporaryDirectory() as root:
        worklog.start(root, "w1", [{"module": "INV", "ticket": "1"},
                                   {"module": "INV", "ticket": "2"}])
        worklog.end(root, "w1", tickets=[{"module": "INV", "ticket": "1"}])
        left = [(iv["module"], iv["ticket"]) for iv in worklog.open_intervals(root)]
        ck("end: a subset closes only the named ticket", left == [("INV", "2")])


def test_end_without_a_known_start_writes_nothing():
    with tempfile.TemporaryDirectory() as root:
        wrote = worklog.end(root, "never-started")
        ck("end: no start -> no orphan end event",
           wrote == [] and worklog.read_events(root) == [])


# -------------------------------------------------------------------- share
def test_share_defaults_to_one_over_n():
    with tempfile.TemporaryDirectory() as root:
        items = [{"module": "INV", "ticket": str(i)} for i in (1, 2, 3)]
        evs = worklog.start(root, "w1", items)
        ck("share: one event per ticket", len(evs) == 3)
        ck("share: default is 1/N", all(abs(e["share"] - 1 / 3) < 1e-9 for e in evs))
        ck("share: an explicit share wins",
           worklog.start(root, "w2", items, share=0.5)[0]["share"] == 0.5)


def test_share_scales_the_measured_hours():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "a", "INV", "1", "w1", "2026-08-18T09:00:00",
              "2026-08-18T12:00:00", share=1 / 3)
        ck("share: 3 h at share 1/3 counts as 1 h",
           abs(worklog.hours_by_ticket(root)[("INV", "1")] - 1.0) < 1e-9)


# ------------------------------------------------------------- two devices
def test_two_device_files_are_merged_on_read():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "desktop", "INV", "1", "w1", "2026-08-18T09:00:00",
              "2026-08-18T10:00:00")
        _pair(root, "laptop", "INV", "2", "w2", "2026-08-18T08:00:00",
              "2026-08-18T08:30:00")
        files = sorted(p.name for p in worklog.dir_path(root).glob("*.jsonl"))
        ck("devices: one file per device", files == ["desktop.jsonl", "laptop.jsonl"])
        hours = worklog.hours_by_ticket(root)
        ck("devices: both device histories are visible",
           abs(hours[("INV", "1")] - 1.0) < 1e-9 and abs(hours[("INV", "2")] - 0.5) < 1e-9)
        ats = [e["at"] for e in worklog.read_events(root)]
        ck("devices: merged events are sorted by time", ats == sorted(ats))
        ck("devices: an interval names the machine that did the work",
           {iv["device"] for iv in worklog.intervals(root)} == {"desktop", "laptop"})


def test_an_interval_started_on_one_device_can_be_ended_on_the_other():
    with tempfile.TemporaryDirectory() as root:
        _ev(root, "desktop", ev="start", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T09:00:00", share=1.0, kind="claude")
        _ev(root, "laptop", ev="end", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T10:00:00", share=1.0, kind="claude", auto_closed=False)
        ivs = worklog.intervals(root)
        ck("devices: a cross-device pair closes", len(ivs) == 1 and bool(ivs[0]["end"]))
        ck("devices: device stays the one that STARTED it", ivs[0]["device"] == "desktop")


def test_a_corrupt_line_does_not_hide_the_rest():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "a", "INV", "1", "w1", "2026-08-18T09:00:00", "2026-08-18T10:00:00")
        fp = worklog.path(root, "a")
        truncated = '{"ev": "start", "trunc' + "\n"
        fp.write_text(fp.read_text(encoding="utf-8") + truncated, encoding="utf-8")
        ck("corrupt: the good interval still reads",
           abs(worklog.hours_by_ticket(root)[("INV", "1")] - 1.0) < 1e-9)


# -------------------------------------------------------------------- hours
def test_hours_exclude_open_and_auto_closed():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "a", "INV", "1", "w1", "2026-08-18T09:00:00", "2026-08-18T10:00:00")
        _pair(root, "a", "INV", "2", "w2", "2026-08-18T09:00:00",
              "2026-08-18T13:00:00", auto_closed=True)
        _ev(root, "a", ev="start", work_id="w3", module="INV", ticket="3",
            at="2026-08-18T14:00:00", share=1.0, kind="claude")
        hours = worklog.hours_by_ticket(root)
        ck("hours: the complete operator-closed interval counts",
           abs(hours[("INV", "1")] - 1.0) < 1e-9)
        ck("hours: an auto_closed interval is excluded", ("INV", "2") not in hours)
        ck("hours: an open interval is excluded", ("INV", "3") not in hours)
        ck("hours: auto_closed is included on request",
           abs(worklog.hours_by_ticket(root, include_auto_closed=True)[("INV", "2")] - 4.0) < 1e-9)


def test_a_backwards_or_unparsable_interval_is_dropped():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "a", "INV", "1", "w1", "2026-08-18T10:00:00", "2026-08-18T09:00:00")
        _pair(root, "a", "INV", "2", "w2", "when-i-started", "when-i-stopped")
        ck("hours: a negative or unparsable interval contributes nothing",
           worklog.hours_by_ticket(root) == {})


def test_append_event_never_raises_and_writes_one_line():
    with tempfile.TemporaryDirectory() as root:
        ck("append: a non-dict is refused, not raised",
           worklog.append_event(root, "nope") is False)
        ck("append: an unserialisable payload returns False",
           worklog.append_event(root, {"ev": "start", "at": object()}) is False)
        worklog.append_event(root, {"ev": "start", "work_id": "w", "module": "M",
                                    "ticket": "1", "at": "2026-08-18T09:00:00",
                                    "share": 1.0, "kind": "manual", "device": "a"})
        text = worklog.path(root, "a").read_text(encoding="utf-8")
        ck("append: exactly one line", text.count("\n") == 1)
        ck("append: it is one JSON object", isinstance(json.loads(text.strip()), dict))


# ------------------------------------------------------------ heartbeat + reap
def test_touch_lifts_last_touch_on_every_interval_of_the_work():
    with tempfile.TemporaryDirectory() as root:
        _ev(root, "a", ev="start", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T09:00:00", share=0.5, kind="claude")
        _ev(root, "a", ev="start", work_id="w1", module="INV", ticket="2",
            at="2026-08-18T09:00:00", share=0.5, kind="claude")
        _ev(root, "a", ev="start", work_id="w2", module="INV", ticket="3",
            at="2026-08-18T09:00:00", share=1.0, kind="claude")
        worklog.touch(root, "w1", at="2026-08-18T09:20:00")
        by_ticket = {iv["ticket"]: iv for iv in worklog.intervals(root)}
        ck("touch: an interval with no heartbeat reports its start",
           by_ticket["3"]["last_touch"] == "2026-08-18T09:00:00")
        ck("touch: ONE heartbeat lifts every interval of that work",
           by_ticket["1"]["last_touch"] == by_ticket["2"]["last_touch"] == "2026-08-18T09:20:00")
        ck("touch: it never lifts another work's interval",
           by_ticket["3"]["last_touch"] == "2026-08-18T09:00:00")
        ck("touch: open intervals expose last_touch",
           all("last_touch" in iv for iv in worklog.open_intervals(root)))
        ck("touch: a heartbeat is not an interval", len(worklog.intervals(root)) == 3)


def test_touch_after_the_end_does_not_move_a_closed_interval():
    with tempfile.TemporaryDirectory() as root:
        _pair(root, "a", "INV", "1", "w1", "2026-08-18T09:00:00", "2026-08-18T10:00:00")
        worklog.touch(root, "w1", at="2026-08-18T11:00:00")
        iv = worklog.intervals(root)[0]
        ck("touch: a heartbeat after the end is ignored",
           iv["last_touch"] == "2026-08-18T09:00:00" and iv["end"] == "2026-08-18T10:00:00")


def test_a_touch_written_on_another_device_is_merged():
    with tempfile.TemporaryDirectory() as root:
        _ev(root, "desktop", ev="start", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T09:00:00", share=1.0, kind="claude")
        _ev(root, "laptop", ev="touch", work_id="w1", at="2026-08-18T09:15:00")
        files = sorted(p.name for p in worklog.dir_path(root).glob("*.jsonl"))
        ck("touch: it lands in the writing device's own file",
           files == ["desktop.jsonl", "laptop.jsonl"])
        ck("touch: a heartbeat from the other device still lifts the interval",
           worklog.intervals(root)[0]["last_touch"] == "2026-08-18T09:15:00")


def test_end_honours_an_explicit_timestamp():
    with tempfile.TemporaryDirectory() as root:
        _ev(root, "a", ev="start", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T09:00:00", share=1.0, kind="claude")
        wrote = worklog.end(root, "w1", at="2026-08-18T09:30:00")
        ck("end: the explicit `at` is what is written",
           len(wrote) == 1 and wrote[0]["at"] == "2026-08-18T09:30:00")
        ck("end: and it is what the interval is priced on",
           abs(worklog.hours_by_ticket(root)[("INV", "1")] - 0.5) < 1e-9)


def test_reap_closes_an_idle_interval_at_its_last_heartbeat():
    with tempfile.TemporaryDirectory() as root:
        _ev(root, "a", ev="start", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T09:00:00", share=1.0, kind="claude")
        worklog.touch(root, "w1", at="2026-08-18T09:40:00")
        ck("reap: nothing is stale 10 min after the last heartbeat",
           worklog.reap(root, now="2026-08-18T09:50:00") == [])
        ends = worklog.reap(root, now="2026-08-18T10:30:00")
        ck("reap: idle past the threshold closes it", len(ends) == 1)
        iv = worklog.intervals(root)[0]
        ck("reap: it ends AT the last heartbeat, not at now",
           iv["end"] == "2026-08-18T09:40:00")
        ck("reap: the end is marked auto_closed", iv["auto_closed"] is True)
        ck("reap: an auto-closed interval is out of the calibration hours",
           worklog.hours_by_ticket(root) == {})
        ck("reap: it is idempotent - a second pass writes nothing",
           worklog.reap(root, now="2026-08-18T10:30:00") == [])


def test_reap_caps_an_interval_that_outlives_the_cap_however_alive_it_looks():
    with tempfile.TemporaryDirectory() as root:
        _ev(root, "a", ev="start", work_id="w1", module="INV", ticket="1",
            at="2026-08-18T09:00:00", share=1.0, kind="claude")
        for hh in ("09:30", "10:30", "11:30", "12:30", "13:20"):
            worklog.touch(root, "w1", at=f"2026-08-18T{hh}:00")
        ends = worklog.reap(root, now="2026-08-18T13:30:00")
        iv = worklog.intervals(root)[0]
        ck("reap: a still-beating interval past the cap is closed", len(ends) == 1)
        ck("reap: it ends at start + cap_hours", iv["end"] == "2026-08-18T13:00:00")
        ck("reap: capped is auto_closed too", iv["auto_closed"] is True)
        ck("reap: capped hours are excluded by default",
           worklog.hours_by_ticket(root) == {}
           and abs(worklog.hours_by_ticket(root, include_auto_closed=True)[("INV", "1")]
                   - float(worklog.CAP_HOURS)) < 1e-9)


def test_reap_closes_every_ticket_of_a_multi_ticket_work_and_leaves_fresh_ones():
    with tempfile.TemporaryDirectory() as root:
        for tid in ("1", "2"):
            _ev(root, "a", ev="start", work_id="w1", module="INV", ticket=tid,
                at="2026-08-18T09:00:00", share=0.5, kind="claude")
        _ev(root, "a", ev="start", work_id="w2", module="INV", ticket="3",
            at="2026-08-18T10:25:00", share=1.0, kind="claude")
        ends = worklog.reap(root, now="2026-08-18T10:30:00")
        ck("reap: both tickets of the idle work are closed", len(ends) == 2)
        left = [(iv["work_id"], iv["ticket"]) for iv in worklog.open_intervals(root)]
        ck("reap: the fresh work stays open", left == [("w2", "3")])

def main() -> int:
    for fn in (test_start_end_pairing, test_repeated_sessions_on_one_ticket_pair_fifo,
               test_end_of_one_work_id_leaves_the_other_open, test_end_can_close_a_subset,
               test_end_without_a_known_start_writes_nothing,
               test_share_defaults_to_one_over_n, test_share_scales_the_measured_hours,
               test_two_device_files_are_merged_on_read,
               test_an_interval_started_on_one_device_can_be_ended_on_the_other,
               test_a_corrupt_line_does_not_hide_the_rest,
               test_hours_exclude_open_and_auto_closed,
               test_a_backwards_or_unparsable_interval_is_dropped,
               test_append_event_never_raises_and_writes_one_line,
               test_touch_lifts_last_touch_on_every_interval_of_the_work,
               test_touch_after_the_end_does_not_move_a_closed_interval,
               test_a_touch_written_on_another_device_is_merged,
               test_end_honours_an_explicit_timestamp,
               test_reap_closes_an_idle_interval_at_its_last_heartbeat,
               test_reap_caps_an_interval_that_outlives_the_cap_however_alive_it_looks,
               test_reap_closes_every_ticket_of_a_multi_ticket_work_and_leaves_fresh_ones):
        try:
            fn()
        except Exception as exc:                       # noqa: BLE001
            ck(f"{fn.__name__} (raised): {type(exc).__name__}: {exc}", False)
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
