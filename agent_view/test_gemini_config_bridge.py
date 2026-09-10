#!/usr/bin/env python3
"""Offline tests for the config->env model bridge in server.py (Feature 1).

server._publish_gemini_model mirrors _publish_gemini_keys: it copies the config's
model names into GEMINI_MODEL / _LITE / _PRO so gemini_client (which reads them
from the env at import) uses them. No network. Run:  python test_gemini_config_bridge.py
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server      # noqa: E402  (also puts scripts/tickets on sys.path for gemini_client)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  -- {detail}" if detail and not cond else ""))


def _clear_env():
    for k in ("GEMINI_MODEL", "GEMINI_MODEL_LITE", "GEMINI_MODEL_PRO", "GEMINI_MODEL_CAPS"):
        os.environ.pop(k, None)


def test_bridge_sets_all_three():
    _clear_env()
    orig = server.load_config
    server.load_config = lambda: {"gemini_model": "gemini-2.5-pro",
                                  "gemini_model_lite": "gemini-flash-lite-latest",
                                  "gemini_model_pro": "gemini-2.5-pro"}
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    check("bridge: GEMINI_MODEL set from config",
          os.environ.get("GEMINI_MODEL") == "gemini-2.5-pro", os.environ.get("GEMINI_MODEL"))
    check("bridge: GEMINI_MODEL_LITE set from config",
          os.environ.get("GEMINI_MODEL_LITE") == "gemini-flash-lite-latest")
    check("bridge: GEMINI_MODEL_PRO set from config",
          os.environ.get("GEMINI_MODEL_PRO") == "gemini-2.5-pro")


def test_explicit_env_wins():
    _clear_env()
    os.environ["GEMINI_MODEL"] = "already-set-by-user"
    orig = server.load_config
    server.load_config = lambda: {"gemini_model": "gemini-2.5-pro"}
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    check("bridge: an explicit GEMINI_MODEL env is NOT overridden by config",
          os.environ.get("GEMINI_MODEL") == "already-set-by-user", os.environ.get("GEMINI_MODEL"))


def test_missing_config_key_leaves_env_unset():
    _clear_env()
    orig = server.load_config
    server.load_config = lambda: {"gemini_model": "gemini-2.5-pro"}   # only the one
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    check("bridge: a config without _lite/_pro leaves those env vars unset",
          "GEMINI_MODEL_LITE" not in os.environ and "GEMINI_MODEL_PRO" not in os.environ)


def test_gemini_client_resolves_bridged_model():
    # The bridge is only useful if gemini_client actually picks the env up. It
    # reads MODEL at import, so reload after the bridge to prove the resolution.
    _clear_env()
    orig = server.load_config
    server.load_config = lambda: {"gemini_model": "gemini-2.5-pro"}
    try:
        server._publish_gemini_model()
        import gemini_client
        importlib.reload(gemini_client)
        check("resolve: gemini_client.MODEL == the bridged config model",
              gemini_client.MODEL == "gemini-2.5-pro", gemini_client.MODEL)
    finally:
        server.load_config = orig
        _clear_env()
        import gemini_client
        importlib.reload(gemini_client)          # restore the default-env module state


def test_caps_bridge_from_dict():
    _clear_env()
    orig = server.load_config
    server.load_config = lambda: {"gemini_model_caps": {"model-a": 5, "model-b": 10}}
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    val = os.environ.get("GEMINI_MODEL_CAPS")
    check("caps bridge: GEMINI_MODEL_CAPS set from a config dict",
          val is not None and "model-a=5" in val and "model-b=10" in val, val)


def test_caps_bridge_from_string():
    _clear_env()
    orig = server.load_config
    server.load_config = lambda: {"gemini_model_caps": "model-a=5,model-b=10"}
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    check("caps bridge: GEMINI_MODEL_CAPS set from a config string",
          os.environ.get("GEMINI_MODEL_CAPS") == "model-a=5,model-b=10")


def test_caps_bridge_explicit_env_wins():
    _clear_env()
    os.environ["GEMINI_MODEL_CAPS"] = "already-set=1"
    orig = server.load_config
    server.load_config = lambda: {"gemini_model_caps": {"model-a": 5}}
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    check("caps bridge: an explicit GEMINI_MODEL_CAPS env is NOT overridden by config",
          os.environ.get("GEMINI_MODEL_CAPS") == "already-set=1")


def test_caps_bridge_missing_key_leaves_env_unset():
    _clear_env()
    orig = server.load_config
    server.load_config = lambda: {"gemini_model": "gemini-2.5-pro"}   # no caps key
    try:
        server._publish_gemini_model()
    finally:
        server.load_config = orig
    check("caps bridge: a config without gemini_model_caps leaves the env unset",
          "GEMINI_MODEL_CAPS" not in os.environ)


def main():
    for fn in (test_bridge_sets_all_three, test_explicit_env_wins,
               test_missing_config_key_leaves_env_unset,
               test_gemini_client_resolves_bridged_model,
               test_caps_bridge_from_dict, test_caps_bridge_from_string,
               test_caps_bridge_explicit_env_wins,
               test_caps_bridge_missing_key_leaves_env_unset):
        try:
            fn()
        except Exception as exc:
            check(fn.__name__ + " (raised)", False, f"{type(exc).__name__}: {exc}")
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
