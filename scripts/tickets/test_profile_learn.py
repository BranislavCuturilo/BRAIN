#!/usr/bin/env python3
"""Offline tests for profile_learn.py - the paid (AI) half of the customer-profile
learning (plan F4). A fake Gemini call throughout; no network, no real key, no
real profiles.json, no real tickets_store. Run: python test_profile_learn.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_TMP = Path(tempfile.mkdtemp(prefix="profilelearn_"))
os.environ["AGENT_VIEW_TICKET_PROFILES"] = str(_TMP / "profiles_dir")   # never the real store

import store            # noqa: E402
import ticket_reader    # noqa: E402
import profile_learn    # noqa: E402

# The migration source must NEVER be the real per-machine file on this dev
# machine - every test that does not explicitly exercise migration points it at
# a path that does not exist.
ticket_reader.OLD_PROFILES_PATH = _TMP / "no_such_old_profiles.json"

FAILS = []


def ck(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label
          + (("  -- " + str(detail)) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #
def _fresh_root() -> Path:
    root = _TMP / f"store_{len(FAILS)}_{id(object())}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_ticket(root, tid="100", module="TEST", customer="Ana Anic",
                  closed=False, real_need="", desc="opis problema", comments=None):
    t = {"title": "t", "original": {"title": "t", "description": desc,
                                    "category": "Bug", "customer": customer},
        "comments": comments or [], "helpdesk": {"is_closed": closed}}
    if closed:
        t["status"] = "done"
    if real_need:
        t["reading"] = {"real_need": real_need}
    fp = Path(root) / (module + ".json")
    d = store.load(fp) or {"project": {"name": module}, "tickets": {}, "rev": 0}
    d.setdefault("tickets", {})[tid] = t
    d["rev"] = int(d.get("rev") or 0) + 1
    store.atomic_write_json(fp, d)
    return t


def _fresh_profiles():
    fp = ticket_reader.profiles_path()
    try:
        fp.unlink()
    except FileNotFoundError:
        pass


class FakeGemini:
    def __init__(self, reply=None, raises=None):
        self.reply, self.raises = reply, raises
        self.calls = []

    def __call__(self, prompt, *, json_out=True, api_key="", model=""):
        self.calls.append({"prompt": prompt, "json_out": json_out,
                           "api_key": api_key, "model": model})
        if self.raises is not None:
            raise self.raises
        return self.reply


GOOD_REPLY = {"style": "kratko i jasno", "tone": "direktan",
             "habits": ["uvek prilaze sliku", "pise na srpskom"],
             "vocabulary": {"SUF": "sistem unosa faktura"},
             "satisfaction_note": "zadovoljan brzim odgovorima"}


# --------------------------------------------------------------------------- #
def test_disabled_no_call():
    root = _fresh_root()
    _write_ticket(root)
    _fresh_profiles()
    gem = FakeGemini(reply=GOOD_REPLY)
    rep = profile_learn.learn_on_close(str(root), "TEST", "100", enabled=False,
                                       gemini_call=gem, log=lambda *_a: None)
    ck("disabled: no call attempted", gem.calls == [], str(gem.calls))
    ck("disabled: ok False, reason disabled",
       rep == {"ok": False, "skipped_reason": "disabled", "creator": ""}, str(rep))


def test_enabled_merges_and_keeps_history_and_sample_count():
    root = _fresh_root()
    _write_ticket(root, real_need="korisniku treba X")
    _fresh_profiles()
    profiles = ticket_reader.load_profiles()
    profiles["creators"]["ana anic"] = {
        "creator": "Ana Anic", "sample_count": 3, "updated": "2026-01-01T00:00:00",
        "history": [{"ticket": "1", "module": "TEST", "scope": "ui", "outcome": "closed",
                     "hours": 2.0, "rating": None, "at": "2026-01-01T00:00:00"}],
        "style": "old style", "tone": "old tone", "habits": [], "vocabulary": {},
        "satisfaction": {"note": "", "rating_avg": None}, "ai_summary_at": None}
    ticket_reader.save_profiles(profiles)

    gem = FakeGemini(reply=GOOD_REPLY)
    rep = profile_learn.learn_on_close(str(root), "TEST", "100", enabled=True,
                                       api_key="", gemini_call=gem, log=lambda *_a: None)
    ck("enabled: exactly ONE Gemini call", len(gem.calls) == 1, str(len(gem.calls)))
    ck("enabled: ok True, creator resolved", rep == {"ok": True, "skipped_reason": None,
                                                     "creator": "Ana Anic"}, str(rep))
    prof = ticket_reader.load_profiles()["creators"]["ana anic"]
    ck("merge: style/tone overwritten from the reply",
       prof.get("style") == "kratko i jasno" and prof.get("tone") == "direktan", str(prof))
    ck("merge: habits/vocabulary/satisfaction_note carried",
       prof.get("habits") == GOOD_REPLY["habits"]
       and prof.get("vocabulary") == GOOD_REPLY["vocabulary"]
       and prof.get("satisfaction", {}).get("note") == GOOD_REPLY["satisfaction_note"], str(prof))
    ck("merge: ai_summary_at stamped", bool(prof.get("ai_summary_at")), str(prof))
    ck("merge: history/sample_count are NOT touched by the AI half",
       prof.get("sample_count") == 3 and prof.get("history") and prof["history"][0]["ticket"] == "1",
       str(prof))
    sent = gem.calls[0]["prompt"]
    ck("prompt: closed ticket data rides the call (title/description/real_need/outcome)",
       "korisniku treba X" in sent and "closed" in sent and "opis problema" in sent, sent[:300])
    ck("prompt: model pinned to MODEL_LITE (the cheap tier)",
       gem.calls[0]["model"] in ("", None) or "lite" in gem.calls[0]["model"], str(gem.calls[0]))


def test_hard_cap_1200_chars():
    root = _fresh_root()
    _write_ticket(root)
    _fresh_profiles()
    big_habit = "h" * 300
    reply = {"style": "s" * 200, "tone": "t" * 200,
            "habits": [big_habit] * 8,
            "vocabulary": {f"w{i}": "m" * 100 for i in range(20)},
            "satisfaction_note": "n" * 200}
    gem = FakeGemini(reply=reply)
    rep = profile_learn.learn_on_close(str(root), "TEST", "100", enabled=True,
                                       gemini_call=gem, log=lambda *_a: None)
    ck("cap: call succeeded", rep.get("ok") is True, str(rep))
    prof = ticket_reader.load_profiles()["creators"]["ana anic"]
    total = (len(prof.get("style") or "") + len(prof.get("tone") or "")
            + len(prof.get("satisfaction", {}).get("note") or "")
            + sum(len(h) for h in prof.get("habits") or [])
            + sum(len(k) + len(v) for k, v in (prof.get("vocabulary") or {}).items()))
    ck("cap: combined length stays within SUMMARY_CHARS_CAP",
       total <= profile_learn.SUMMARY_CHARS_CAP, str(total))


def test_junk_answer_skipped_profile_unchanged():
    root = _fresh_root()
    _write_ticket(root)
    _fresh_profiles()
    profiles = ticket_reader.load_profiles()
    profiles["creators"]["ana anic"] = {"creator": "Ana Anic", "style": "kept",
                                        "sample_count": 1, "history": []}
    ticket_reader.save_profiles(profiles)

    for label, gem in (("non-object reply", FakeGemini(reply=["not", "an", "object"])),
                       ("empty reply", FakeGemini(reply={})),
                       ("raising call", FakeGemini(raises=RuntimeError("boom")))):
        rep = profile_learn.learn_on_close(str(root), "TEST", "100", enabled=True,
                                           gemini_call=gem, log=lambda *_a: None)
        ck(f"junk ({label}): ok False, skipped_reason set",
           rep.get("ok") is False and rep.get("skipped_reason"), str(rep))
        prof = ticket_reader.load_profiles()["creators"]["ana anic"]
        ck(f"junk ({label}): the OLD profile is left untouched (never blanked)",
           prof.get("style") == "kept", str(prof))


def test_unknown_ticket_and_module_and_no_creator():
    root = _fresh_root()
    _write_ticket(root, customer="")            # no creator on the ticket
    _fresh_profiles()
    gem = FakeGemini(reply=GOOD_REPLY)
    r1 = profile_learn.learn_on_close(str(root), "TEST", "999", enabled=True,
                                      gemini_call=gem, log=lambda *_a: None)
    ck("unknown ticket: skipped, no call", r1.get("skipped_reason") == "unknown ticket"
       and gem.calls == [], str(r1))
    r2 = profile_learn.learn_on_close(str(root), "NOPE", "100", enabled=True,
                                      gemini_call=gem, log=lambda *_a: None)
    ck("unknown module: skipped, no call", r2.get("skipped_reason") == "unknown module"
       and gem.calls == [], str(r2))
    r3 = profile_learn.learn_on_close(str(root), "TEST", "100", enabled=True,
                                      gemini_call=gem, log=lambda *_a: None)
    ck("no creator on the ticket: skipped, no call", r3.get("skipped_reason") == "no creator"
       and gem.calls == [], str(r3))


def test_learn_batch_one_bad_ticket_does_not_sink_the_rest():
    root = _fresh_root()
    _write_ticket(root, tid="1", customer="Ana Anic")
    _write_ticket(root, tid="2", customer="Bob Bobic")
    _fresh_profiles()
    gem = FakeGemini(reply=GOOD_REPLY)
    rep = profile_learn.learn_batch(str(root), [("TEST", "1"), ("TEST", "999"), ("TEST", "2")],
                                    enabled=True, gemini_call=gem, log=lambda *_a: None)
    ck("batch: 2 ran, 1 skipped (unknown ticket)", rep == {"ran": 2, "skipped": 1}, str(rep))
    ck("batch: exactly 2 Gemini calls", len(gem.calls) == 2, str(len(gem.calls)))


def test_batch_disabled_skips_everything_without_a_call():
    gem = FakeGemini(reply=GOOD_REPLY)
    rep = profile_learn.learn_batch("unused", [("TEST", "1"), ("TEST", "2")],
                                    enabled=False, gemini_call=gem)
    ck("batch disabled: nothing ran, nothing called",
       rep == {"ran": 0, "skipped": 2} and gem.calls == [], str(rep))


# --------------------------------------------------------------------------- #
#  Store plumbing F4 depends on
# --------------------------------------------------------------------------- #
def test_non_module_files_contains_profiles_json():
    ck("store: profiles.json is excluded from the module scan",
       "profiles.json" in store.NON_MODULE_FILES, str(store.NON_MODULE_FILES))


def test_migration_from_old_path():
    old = _TMP / "migration_old_profiles.json"
    old.write_text(json.dumps({"version": 1, "creators": {
        "carla caric": {"creator": "Carla Caric", "sample_count": 5, "tone": "friendly"}}}),
        encoding="utf-8")
    orig_old = ticket_reader.OLD_PROFILES_PATH
    fresh_dir = _TMP / f"migrated_{id(old)}"
    orig_env = os.environ.get("AGENT_VIEW_TICKET_PROFILES")
    try:
        ticket_reader.OLD_PROFILES_PATH = old
        os.environ["AGENT_VIEW_TICKET_PROFILES"] = str(fresh_dir)
        new_fp = ticket_reader.profiles_path()
        ck("migration: the new tracked path starts empty", not new_fp.exists(), str(new_fp))
        loaded = ticket_reader.load_profiles()
        ck("migration: the old creator is present after the FIRST load",
           loaded.get("creators", {}).get("carla caric", {}).get("sample_count") == 5,
           str(loaded))
        ck("migration: the new file now exists on disk", new_fp.exists(), str(new_fp))
        ck("migration: the OLD file is left in place, untouched", old.exists()
           and json.loads(old.read_text(encoding="utf-8"))["creators"]["carla caric"]["tone"] == "friendly",
           "")
        loaded2 = ticket_reader.load_profiles()
        ck("migration: idempotent - a second load still sees the migrated creator",
           loaded2.get("creators", {}).get("carla caric", {}).get("sample_count") == 5, "")
    finally:
        ticket_reader.OLD_PROFILES_PATH = orig_old
        if orig_env is not None:
            os.environ["AGENT_VIEW_TICKET_PROFILES"] = orig_env
        else:
            os.environ.pop("AGENT_VIEW_TICKET_PROFILES", None)



def test_style_and_tone_are_capped_per_field():
    out = profile_learn._merge_reply({"style": "S" * 5000, "tone": "T" * 5000,
                                      "habits": [], "vocabulary": {}, "satisfaction_note": ""}, {})
    ck("cap: style <= FIELD_CHARS_CAP", len(out["style"]) <= profile_learn.FIELD_CHARS_CAP, len(out["style"]))
    ck("cap: tone <= FIELD_CHARS_CAP", len(out["tone"]) <= profile_learn.FIELD_CHARS_CAP, len(out["tone"]))
    ck("cap: satisfaction always carries note", "note" in out["satisfaction"], out["satisfaction"])


def test_two_device_files_merge_per_creator_newest_wins_history_union():
    _fresh_profiles()
    d = ticket_reader.profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "laptop.json").write_text(json.dumps({"version": 2, "creators": {"ana anic": {
        "creator": "Ana Anic", "style": "old-style", "updated": "2026-08-01T10:00:00", "sample_count": 3,
        "history": [{"module": "TEST", "ticket": "1", "at": "2026-08-01T10:00:00"}]}}}), encoding="utf-8")
    (d / "desktop.json").write_text(json.dumps({"version": 2, "creators": {"ana anic": {
        "creator": "Ana Anic", "style": "new-style", "updated": "2026-08-05T10:00:00", "sample_count": 2,
        "history": [{"module": "TEST", "ticket": "2", "at": "2026-08-05T10:00:00"}]}}}), encoding="utf-8")
    prof = ticket_reader.load_profiles()["creators"]["ana anic"]
    ck("merge: the newer device's scalar wins", prof["style"] == "new-style", prof.get("style"))
    ck("merge: history is the union", sorted(h["ticket"] for h in prof["history"]) == ["1", "2"], prof["history"])
    ck("merge: sample_count is the max", prof["sample_count"] == 3, prof["sample_count"])
    ticket_reader.save_profiles({"creators": {"ana anic": prof}})
    ck("merge: save writes THIS device's file only",
       ticket_reader.profiles_path().is_file() and ticket_reader.profiles_path().name == store.device_id() + ".json")
    for f in ("laptop.json", "desktop.json"):
        (d / f).unlink()


def test_cli_gate_reads_the_toggle_and_bridges_the_key():
    orig = profile_learn._hud_config
    for cfg, want in (({"customer_profiles": False}, False), ({"customer_profiles": True}, True), ({}, True)):
        profile_learn._hud_config = lambda cfg=cfg: cfg
        ck("gate: config %r -> enabled %s" % (cfg, want), profile_learn.enabled_by_config() is want)
    profile_learn._hud_config = lambda: {"gemini_api_key": "k-test"}
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEYS"):
        os.environ.pop(k, None)
    ok = profile_learn.ensure_gemini_env()
    ck("gate: the config key is bridged into the env", ok and os.environ.get("GEMINI_API_KEY") == "k-test")
    os.environ.pop("GEMINI_API_KEY", None)
    profile_learn._hud_config = orig

def main():
    for fn in (test_style_and_tone_are_capped_per_field,
               test_two_device_files_merge_per_creator_newest_wins_history_union,
               test_cli_gate_reads_the_toggle_and_bridges_the_key,
               test_disabled_no_call,
               test_enabled_merges_and_keeps_history_and_sample_count,
               test_hard_cap_1200_chars,
               test_junk_answer_skipped_profile_unchanged,
               test_unknown_ticket_and_module_and_no_creator,
               test_learn_batch_one_bad_ticket_does_not_sink_the_rest,
               test_batch_disabled_skips_everything_without_a_call,
               test_non_module_files_contains_profiles_json,
               test_migration_from_old_path):
        try:
            fn()
        except Exception as exc:
            ck(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    total = len(FAILS)
    status = "ALL PASSED" if not total else f"{total} FAILED"
    print("\n" + status)
    return 0 if not total else 1


if __name__ == "__main__":
    raise SystemExit(main())
