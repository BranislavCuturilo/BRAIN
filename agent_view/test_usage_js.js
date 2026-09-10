// node test_usage_js.js -- the pure parts of web/usage.js: the countdown text the
// operator reads instead of "5h", and the thresholds shared with the status line.
"use strict";
const assert = require("node:assert/strict");
const u = require("./web/usage.js");

const now = Date.parse("2026-09-07T12:00:00Z");
const at = (min) => new Date(now + min * 60000).toISOString();

assert.equal(u.claudeFmtLeft(at(161), now), "reset za 2 h 41 min", "hours and minutes");
assert.equal(u.claudeFmtLeft(at(41), now), "reset za 41 min", "minutes only");
assert.equal(u.claudeFmtLeft(at(0.4), now), "reset za 1 min", "rounded up, never 0 min");
assert.equal(u.claudeFmtLeft(at(-1), now), "reset upravo sad", "passed");
assert.equal(u.claudeFmtLeft(at(60 * 72), now), "reset za 3 d", "days when far");
assert.equal(u.claudeFmtLeft("", now), "", "unknown");
assert.equal(u.claudeFmtLeft("garbage", now), "", "unparseable");

assert.equal(u.claudeLevel(46), "", "normal");
assert.equal(u.claudeLevel(80), "warn", "warn from 80");
assert.equal(u.claudeLevel(95), "bad", "bad from 95");

assert.equal(u.claudeAge(20), "sad");
assert.equal(u.claudeAge(190), "pre 3 min");
assert.equal(u.claudeAge(7300), "pre 2 h");
assert.equal(u.claudeDur(7_500_000), "2 h 5 min");
assert.equal(u.claudeDur(30_000), "", "under a minute is nothing");
assert.equal(u.claudeCtxSize(1_000_000), "1M");
assert.equal(u.claudeCtxSize(200_000), "200k");


// --- the decision, not the buttons -------------------------------------------
// Both commands spend the WEEKLY window. Offering them while that window is
// itself nearly gone would be advice that makes the situation worse, so this
// is a table rather than a threshold, and the table is what gets tested.
const W = (five, week) => ({five_hour: {percent: five}, seven_day: {percent: week}});

let a = u.claudeAdvice(W(30, 20), true);
assert.equal(a.why, "", "ordinary work: nothing is offered at all");
assert.deepEqual(a.offer, []);

a = u.claudeAdvice(W(85, 20), true);
assert.equal(a.level, "warn", "5h nearly spent, weekly has room");
assert.deepEqual(a.offer, ["limit-reset", "low-priority"], "both, reset first");

a = u.claudeAdvice(W(85, 20), false);
assert.deepEqual(a.offer, ["low-priority"], "no limit-reset unless the account is offered it");

a = u.claudeAdvice(W(85, 70), true);
assert.equal(a.level, "bad");
assert.deepEqual(a.offer, [], "weekly is tight too: offer NOTHING, both would spend it");
assert.match(a.why, /odmogle/, "and say why");

a = u.claudeAdvice(W(20, 90), true);
assert.equal(a.level, "bad");
assert.deepEqual(a.offer, [], "weekly alone nearly spent: no command helps");
assert.match(a.why, /Nijedna komanda ne pomaže/);

assert.deepEqual(u.claudeAdvice({}, true).offer, [], "no windows -> no advice");
assert.equal(u.claudeAdvice(null, true).why, "", "null -> silent, not a crash");
assert.equal(u.CLAUDE_NEAR, 80);
assert.equal(u.CLAUDE_WEEKLY_TIGHT, 60);

console.log("OK 28 checks");
