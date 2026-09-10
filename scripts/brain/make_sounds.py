#!/usr/bin/env python3
"""Generate the agent sounds, rather than downloading anyone's.

Game audio is copyrighted; these are synthesised from scratch, so they are
yours outright with nothing to attribute and nothing to take down. Short,
pitched, quick-decay motifs in the spirit of a unit acknowledging an order.

  make_sounds.py            write into ~/.claude/sounds
  make_sounds.py --force    overwrite existing files

Anything already in that folder is left alone by default: a file you dropped
in yourself must never be replaced by a regenerated one.

Design notes, since a beep is easy and a *recognisable* beep is not:
  - three harmonics with falling amplitude, so it reads as an instrument
    rather than a test tone;
  - a fast attack and an exponential decay, because a square envelope clicks;
  - each family gets an interval it owns, so the family is identifiable
    without being memorised: rising = starting, resolving = finished,
    minor second = something needs attention.
"""
from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

OUT = Path.home() / ".claude" / "sounds"
RATE = 22050

# family -> (start motif, done motif). Frequencies in Hz, duration in seconds.
MOTIFS = {
    "read":     ([(880, .07), (1175, .09)],              # quick rising blip
                 [(1175, .07), (880, .10)]),
    "write":    ([(587, .07), (784, .09)],               # a fourth up
                 [(784, .06), (988, .06), (1175, .11)]), # resolving triad
    "security": ([(440, .09), (415, .12)],               # minor second, uneasy
                 [(622, .07), (466, .13)]),
    "review":   ([(698, .07), (880, .08)],
                 [(880, .06), (698, .06), (587, .12)]),
    "research": ([(523, .08), (659, .08), (784, .09)],   # thoughtful climb
                 [(784, .07), (1047, .12)]),
    "default":  ([(659, .08)], [(523, .10)]),
}


def tone(freq: float, seconds: float) -> list[float]:
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        t = i / RATE
        # Exponential decay: a square envelope clicks audibly at the edges.
        env = math.exp(-4.5 * t / max(seconds, 1e-6))
        # Three harmonics, falling amplitude - reads as an instrument.
        s = (math.sin(2 * math.pi * freq * t)
             + 0.35 * math.sin(4 * math.pi * freq * t)
             + 0.12 * math.sin(6 * math.pi * freq * t))
        out.append(env * s / 1.47)
    return out


def motif(parts: list[tuple[float, float]]) -> list[float]:
    samples: list[float] = []
    for freq, dur in parts:
        samples.extend(tone(freq, dur))
    # A short fade at the very end kills the trailing click on some drivers.
    fade = min(220, len(samples))
    for i in range(fade):
        samples[-1 - i] *= i / fade
    return samples


def write_wav(path: Path, samples: list[float]) -> None:
    frames = b"".join(
        struct.pack("<h", max(-32767, min(32767, int(s * 22000))))
        for s in samples)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(RATE)
        fh.writeframes(frames)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    written = kept = 0
    for family, (start, done) in MOTIFS.items():
        for phase, parts in (("start", start), ("done", done)):
            path = OUT / f"{family}-{phase}.wav"
            if path.exists() and not args.force:
                kept += 1
                continue
            write_wav(path, motif(parts))
            written += 1
    print(f"{written} written, {kept} left alone -> {OUT}")
    print("agent_sound.py picks these up automatically; drop your own "
          "<family>-<phase>.wav there to override.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
