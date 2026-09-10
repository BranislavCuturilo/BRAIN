#!/usr/bin/env python3
"""Windows DPAPI encrypt-at-rest, standard library only (ctypes, no pip).

The mail config stores an account password. We never keep it in plaintext on
disk: `encrypt()` wraps it with the Windows Data Protection API bound to the
CURRENT USER, so the ciphertext is useless on another account or machine, and
`decrypt()` unwraps it only in-process at connect time. No key to manage — the
OS holds it against the logged-in user.

`CRYPTPROTECT_UI_FORBIDDEN` is set so a call can never block on a UI prompt
(this runs under a windowless server); it fails cleanly instead.

Windows-only by construction. On any other platform the two entry points raise
a clear RuntimeError rather than importing a DLL that does not exist — so the
module still *imports* everywhere, and only *using* it off-Windows fails. The
plaintext is never logged, never placed in an exception message.
"""
from __future__ import annotations

import base64
import ctypes
import sys

# DPAPI: bound to the current user (the default — NOT CRYPTPROTECT_LOCAL_MACHINE),
# and never allowed to raise a UI prompt from a headless process.
_CRYPTPROTECT_UI_FORBIDDEN = 0x01

# wintypes is avoided on purpose: importing it (and WinDLL below) only happens
# after the platform guard, so this module imports cleanly on non-Windows and the
# Windows-only failure surfaces at call time as a RuntimeError, per the contract.


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


_FUNCS: dict = {}


def _funcs():
    """The three configured DPAPI/kernel entry points, loaded once. Raises a
    clear RuntimeError off-Windows, before any WinDLL access (which would itself
    raise an opaque AttributeError there)."""
    if sys.platform != "win32":
        raise RuntimeError(
            "agent_view mail encryption uses the Windows Data Protection API and "
            f"runs on Windows only; current platform is {sys.platform!r}")
    if not _FUNCS:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_p = ctypes.POINTER(_DATA_BLOB)
        lpvoid = ctypes.c_void_p
        crypt32.CryptProtectData.restype = ctypes.c_int
        crypt32.CryptProtectData.argtypes = [blob_p, ctypes.c_wchar_p, blob_p,
                                             lpvoid, lpvoid, ctypes.c_uint32,
                                             blob_p]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        crypt32.CryptUnprotectData.argtypes = [blob_p, lpvoid, blob_p, lpvoid,
                                               lpvoid, ctypes.c_uint32, blob_p]
        kernel32.LocalFree.restype = lpvoid
        kernel32.LocalFree.argtypes = [lpvoid]
        _FUNCS["protect"] = crypt32.CryptProtectData
        _FUNCS["unprotect"] = crypt32.CryptUnprotectData
        _FUNCS["free"] = kernel32.LocalFree
    return _FUNCS["protect"], _FUNCS["unprotect"], _FUNCS["free"]


def _in_blob(raw: bytes):
    """A DATA_BLOB over `raw`, plus the backing buffer the caller MUST keep alive
    until the API call returns (the blob only borrows its memory)."""
    size = len(raw)
    buf = ctypes.create_string_buffer(raw, size) if size else ctypes.create_string_buffer(1)
    blob = _DATA_BLOB(size, ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def encrypt(plaintext: str) -> str:
    """DPAPI-protect a string and return the blob as base64. Bound to the current
    Windows user. Raises RuntimeError on failure (message carries an error code,
    never the plaintext)."""
    protect, _unprotect, free = _funcs()
    blob_in, _keep = _in_blob(plaintext.encode("utf-8"))
    blob_out = _DATA_BLOB()
    if not protect(ctypes.byref(blob_in), None, None, None, None,
                   _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise RuntimeError("DPAPI CryptProtectData failed (error %d)"
                           % ctypes.get_last_error())
    try:
        blob = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        free(blob_out.pbData)
    return base64.b64encode(blob).decode("ascii")


def decrypt(b64: str) -> str:
    """Inverse of `encrypt`: base64 → DPAPI-unprotect → UTF-8 string. Raises
    RuntimeError if the blob was made by a different user/machine or is corrupt
    (message carries an error code, never any secret)."""
    _protect, unprotect, free = _funcs()
    blob_in, _keep = _in_blob(base64.b64decode(b64.encode("ascii")))
    blob_out = _DATA_BLOB()
    if not unprotect(ctypes.byref(blob_in), None, None, None, None,
                     _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise RuntimeError("DPAPI CryptUnprotectData failed (error %d)"
                           % ctypes.get_last_error())
    try:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        free(blob_out.pbData)
    return raw.decode("utf-8")
