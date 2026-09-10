#!/usr/bin/env python3
"""The CLIENT half of comment attachments: the multipart body this repo builds
for `add_comment(..., files=[...])`, and the caps it refuses before spending a
round trip.

NOTHING REACHES THE NETWORK: `urllib.request.urlopen` is replaced by a spy that
captures the request and returns a canned response — a real call is impossible
here. The outbound write guard (`writes_blocked`) is stubbed off ON PURPOSE for
these cases, and one case asserts the guard is what stops a test that forgets to
(the 2026-08-20 incident: two real comments on a live ticket).

  python test_comment_attachments_client.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from adapters import acme_helpdesk as hd  # noqa: E402

FAILS = []
CRLF = chr(13) + chr(10)


def ck(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILS.append(name)


class _Spy:
    """Captures the request instead of sending it."""
    def __init__(self):
        self.last = None

    def __call__(self, req, timeout=None):
        self.last = req
        body = io.BytesIO(b'{"id": 1, "attachments": []}')
        body.__enter__ = lambda: body                       # urlopen is used as a context manager
        body.__exit__ = lambda *a: False
        body.read = lambda: b'{"id": 1, "attachments": []}'
        return body


def _png(dirpath, name="a.png", size=64):
    fp = Path(dirpath) / name
    fp.write_bytes(b"\x89PNG" + b"0" * size)
    return {"path": str(fp), "name": name}


def main() -> int:
    spy = _Spy()
    urllib.request.urlopen = spy
    hd.os.environ["HELPDESK_URL"] = "https://example.invalid"
    hd.os.environ["HELPDESK_TOKEN"] = "not-a-real-token"
    adapter = hd.AcmeHelpdesk()

    # The guard must refuse first — this file IS a test_*.py.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            adapter.add_comment("1", "x", files=[_png(tmp)])
            ck("the write guard refuses a test by default", False, "the call went through")
        except hd.HelpdeskError as exc:
            ck("the write guard refuses a test by default", "refused" in str(exc), str(exc))

    real_guard, hd.writes_blocked = hd.writes_blocked, lambda: ""     # deliberate, spy is in place

    with tempfile.TemporaryDirectory() as tmp:
        # 1. text-only keeps the old JSON path
        spy.last = None
        adapter.add_comment("08597", "samo tekst")
        req = spy.last
        ck("no files: still the JSON path", req.get_header("Content-type") == "application/json",
           req.get_header("Content-type"))
        ck("no files: body carries the comment", b'"comment"' in req.data and "samo tekst".encode() in req.data)

        # 2. with files: multipart, one part per file, correct field name and type
        spy.last = None
        files = [_png(tmp, "pre.png"), _png(tmp, "posle.png")]
        adapter.add_comment("08597", "Slike:", files=files)
        req = spy.last
        ctype = req.get_header("Content-type") or ""
        body = req.data
        ck("files: multipart content type with a boundary",
           ctype.startswith("multipart/form-data; boundary=----brainshots"), ctype)
        boundary = ctype.split("boundary=", 1)[1]
        ck("files: the comment text is a form field",
           ('name="comment"' + CRLF + CRLF + "Slike:").encode("utf-8") in body)
        ck("files: one part per file, under the field the API declares",
           body.count(b'name="files"; filename=') == 2, body.count(b'name="files"; filename='))
        ck("files: each part names the customer-facing file name",
           b'filename="pre.png"' in body and b'filename="posle.png"' in body)
        ck("files: the image content type rides along", body.count(b"Content-Type: image/png") == 2)
        ck("files: the body is CRLF-delimited and closed with the terminator",
           body.endswith(("--" + boundary + "--" + CRLF).encode("utf-8")), body[-60:])
        ck("files: the bytes of the file are in the body", b"\x89PNG" in body)

        # 3. caps refused BEFORE the request (never half a comment), and every
        #    one of those refusals is DEFINITE: nothing left this machine, so the
        #    sent log must not say "may have landed" (2026-08-21, DEMO#05513).
        big = Path(tmp) / "big.png"
        big.write_bytes(b"\x89PNG" + b"0" * (hd.ATTACH_MAX_BYTES + 1))
        for label, payload, needle in (
                ("too many files", [_png(tmp, "%d.png" % i) for i in range(6)], "too many"),
                ("bad extension", [{"path": str(Path(tmp) / "x.exe"), "name": "x.exe"}], "not allowed"),
                ("unreadable file", [{"path": str(Path(tmp) / "nope.png"), "name": "nope.png"}], "unreadable"),
                ("file over the size cap", [{"path": str(big), "name": "big.png"}], "too large")):
            spy.last = None
            try:
                adapter.add_comment("08597", "x", files=payload)
                ck("refused: %s" % label, False, "the call went through")
            except hd.HelpdeskError as exc:
                ck("refused: %s" % label, needle in str(exc) and spy.last is None, str(exc))
                ck("refused: %s is DEFINITE, not ambiguous" % label,
                   exc.ambiguous is False, "ambiguous=%r" % exc.ambiguous)

        # 4. the caps are readable by the CALLER, which is what lets it split an
        #    over-cap set across comments instead of losing it.
        lim = adapter.attachment_limits()
        ck("limits: the caller sees the same caps the sender enforces",
           lim == {"max_files": hd.ATTACH_MAX_FILES, "max_bytes": hd.ATTACH_MAX_BYTES,
                   "max_request_bytes": hd.ATTACH_MAX_REQUEST_BYTES}, str(lim))
        ck("limits: a lone file within the per-file cap always fits one comment",
           lim["max_request_bytes"] >= lim["max_bytes"], str(lim))
        ok_file = _png(tmp, "ok.png")
        ck("check: a good file reports its size and no reason",
           adapter.attachment_check(ok_file) == (Path(ok_file["path"]).stat().st_size, ""),
           str(adapter.attachment_check(ok_file)))
        for label, payload, needle in (
                ("bad extension", {"path": str(Path(tmp) / "x.exe"), "name": "x.exe"}, "not allowed"),
                ("missing file", {"path": str(Path(tmp) / "nope.png"), "name": "nope.png"}, "unreadable"),
                ("over the cap", {"path": str(big), "name": "big.png"}, "too large")):
            ck("check: %s is reported, not raised" % label,
               needle in adapter.attachment_check(payload)[1],
               str(adapter.attachment_check(payload)))

        # 5. a TRANSPORT failure is the one ambiguous case: the request left.
        def _boom(req, timeout=None):
            raise urllib.error.URLError("connection reset")

        urllib.request.urlopen = _boom
        try:
            adapter.add_comment("08597", "x", files=[_png(tmp, "t.png")])
            ck("transport failure is AMBIGUOUS", False, "the call did not raise")
        except hd.HelpdeskError as exc:
            ck("transport failure is AMBIGUOUS", exc.ambiguous is True, str(exc))
        try:
            adapter.add_comment("08597", "samo tekst")
            ck("transport failure is AMBIGUOUS on the text path too", False, "did not raise")
        except hd.HelpdeskError as exc:
            ck("transport failure is AMBIGUOUS on the text path too",
               exc.ambiguous is True, str(exc))
        urllib.request.urlopen = spy

    hd.writes_blocked = real_guard
    print(("\n%d failed" % len(FAILS)) if FAILS else "\nall checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
