#!/usr/bin/env python3
"""Ticket attachments → what the model can actually READ.

Why: a ticket's meaning routinely lives in its files, not its sentence. #18536
("otvaranje projekta za SUF") is three vague lines of text plus FIVE attachments
— a screenshot and four .xlsx table dumps spread over the comment thread — and
the reader used to see NONE of them: only `original.attachment_url` was fetched,
and only when it was an image. So the "predlog upita" was built on the weakest
tenth of the evidence.

This module is the ONE owner of "turn a ticket's attachments into model input":

  * `collect(ticket)` walks `original.attachment_url` AND every
    `comments[].attachments[]`, fetches each (SSRF-guarded, bounded, fail-safe),
    and returns
        {"files":   [{"mime","data"}]  inline parts (images + PDFs) for gemini_client.call,
         "digest":  str                a TEXT rendering of the tabular/text files
                                       (xlsx/xlsm/csv/txt/md/json/docx), capped,
         "listing": [{...}]            one entry per attachment: where it came from,
                                       what it is, and whether it was read}
  * `fetch_bytes(url)` is the single fetch door (http(s) only, public hosts only,
    bounded read, never raises).
  * a small on-disk CACHE keyed by URL (helpdesk attachment URLs are immutable),
    so a Rescan / repeated analyse never re-downloads the same 5 MB workbook.

Everything here is TOTAL: a bad URL, a private host, a timeout, an oversize
body, an unreadable workbook — each degrades to "listed but not read" and the
ticket text stays the primary source. Nothing in this module can sink an
analysis. Attachment CONTENT is untrusted data; the callers frame it that way.
"""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, unquote

HERE = Path(__file__).resolve().parent
BRAIN = HERE.parents[1]

ATTACH_TIMEOUT_S = 10.0                       # never hang an analysis on a slow URL
ATTACH_MAX_BYTES = 8 * 1024 * 1024            # per-file download cap; oversize -> omit
MAX_FILES = 8                                 # inline parts per call (images + PDFs)
MAX_INLINE_TOTAL = 20 * 1024 * 1024           # total inline bytes per call
DIGEST_PER_FILE_CHARS = 9000                  # text rendering cap per file
DIGEST_TOTAL_CHARS = 36000                    # text rendering cap per ticket
XLSX_MAX_ROWS = 40                            # rows shown per sheet
XLSX_MAX_COLS = 30
XLSX_MAX_SHEETS = 6
XLSX_SCAN_ROWS = 3000                         # rows scanned per sheet (bounded work on huge dumps)

CACHE_ENV = "AGENT_VIEW_ATTACH_CACHE"         # tests point this at a temp dir; "off" disables
DEFAULT_CACHE_DIR = BRAIN / "agent_view" / "attachment_cache"

# Magic-byte signatures are the authoritative "what is it" gate — stronger than a
# Content-Type header or a URL extension (a mislabeled HTML error page never
# passes as an image), and they yield the exact mime gemini_client needs.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".sql", ".ini", ".cfg"}
_XLSX_EXT = {".xlsx", ".xlsm"}
_DOCX_EXT = {".docx"}
#: ODF, PowerPoint and RTF were classified "other" -- listed by name, never
#: read. All three are stdlib-reachable: ODF and pptx are ZIP+XML, RTF is
#: text with control words. The idea is anydoc's (firecrawl, MIT); the
#: implementation is ours, because a Rust wheel for three formats is a worse
#: trade than sixty lines that cannot fail to install.
_ODF_EXT = {".odt", ".ods", ".odp"}
_PPTX_EXT = {".pptx"}
_RTF_EXT = {".rtf"}


# --------------------------------------------------------------------------- #
#  Sniffing
# --------------------------------------------------------------------------- #
def sniff_image_mime(data) -> str:
    """The image mime for `data` from its magic bytes, or "" when the bytes are
    not one of the supported image formats."""
    if not isinstance(data, (bytes, bytearray)):
        return ""
    for sig, mime in _IMAGE_MAGIC:
        if data.startswith(sig):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _is_zip(data) -> bool:
    return isinstance(data, (bytes, bytearray)) and data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def url_name(url: str, name: str = "") -> str:
    """A human-readable file name for an attachment: the helpdesk `name` when
    given, else the URL's basename (unquoted)."""
    n = (name or "").strip()
    if n:
        return n
    try:
        return unquote(Path(urlparse(url).path).name) or url
    except Exception:            # noqa: BLE001
        return url


def _ext(url: str, name: str = "") -> str:
    return Path(url_name(url, name)).suffix.lower()


def classify(data: bytes, url: str, name: str = "") -> str:
    """One of: image, pdf, xlsx, docx, text, other. Bytes first (magic), then the
    file name — a zip is xlsx/docx only if the name says so."""
    if sniff_image_mime(data):
        return "image"
    if data[:5] == b"%PDF-":
        return "pdf"
    ext = _ext(url, name)
    if _is_zip(data):
        if ext in _XLSX_EXT:
            return "xlsx"
        if ext in _DOCX_EXT:
            return "docx"
        if ext in _ODF_EXT:
            return "odf"
        if ext in _PPTX_EXT:
            return "pptx"
        return "other"
    if ext in _RTF_EXT or data[:5] == b"{\\rtf":
        return "rtf"
    if ext in _TEXT_EXT:
        return "text"
    # bytes that decode cleanly as UTF-8 and look printable count as text
    try:
        s = data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "other"
    if s and sum(ch.isprintable() or ch in "\r\n\t" for ch in s) / max(len(s), 1) > 0.95:
        return "text"
    return "other"


# --------------------------------------------------------------------------- #
#  Fetch — the ONE door. SSRF-guarded, bounded, never raises.
# --------------------------------------------------------------------------- #
def url_host_is_public(u: str) -> bool:
    """SSRF guard: True only when the URL's host resolves EXCLUSIVELY to public IPs,
    so a fetch can never be steered at loopback / private / link-local internal
    services on this machine or LAN. Fails CLOSED — a resolution failure or a
    single non-public address returns False (the caller then omits the file).
    (Defence-in-depth: a helpdesk assigns attachment URLs on its own media host.
    It does not defend against DNS rebinding, which would need
    connect-to-resolved-IP.)"""
    try:
        host = urlparse(u).hostname
        if not host:
            return False
        infos = socket.getaddrinfo(host, None)
    except Exception:                          # noqa: BLE001 — any resolve error -> fail closed
        return False
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


def fetch_bytes(url, *, timeout: float = ATTACH_TIMEOUT_S,
                max_bytes: int = ATTACH_MAX_BYTES):
    """Fetch an http(s) URL as bytes, or None. TOTAL and fail-safe: a non-http
    scheme, a host that resolves to a private/loopback IP (SSRF guard), a
    timeout, a network error, an empty or oversize body all return None so a
    broken attachment URL can never hang or break a run. The read is bounded to
    max_bytes and each socket recv to `timeout`."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return None                           # only http(s); no file://, data:, ...
    if not url_host_is_public(u):
        return None                           # SSRF guard
    try:
        req = urllib.request.Request(u, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)      # one past the cap -> detect oversize
    except Exception:                         # noqa: BLE001 — any failure -> omit
        return None
    if not data or len(data) > max_bytes:
        return None
    return bytes(data)


def fetch_image(url, *, timeout: float = ATTACH_TIMEOUT_S,
                max_bytes: int = ATTACH_MAX_BYTES):
    """Fetch an image URL as a gemini_client `files=` part {"mime","data"(base64)},
    or None (non-image bytes -> None: the magic-byte gate)."""
    data = fetch_bytes(url, timeout=timeout, max_bytes=max_bytes)
    if data is None:
        return None
    mime = sniff_image_mime(data)
    if not mime:
        return None
    return {"mime": mime, "data": base64.b64encode(data).decode("ascii")}


# --------------------------------------------------------------------------- #
#  Text renderings of non-image files (what the model reads as TEXT).
# --------------------------------------------------------------------------- #
def _cell(v) -> str:
    if v is None:
        return ""
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    return s[:80]


def _sheet_rows(ws):
    """Non-empty rows of a sheet (cells trimmed to XLSX_MAX_COLS), bounded: at
    most XLSX_SCAN_ROWS rows are scanned. Returns (rows, scanned, truncated)."""
    rows, scanned = [], 0
    for row in ws.iter_rows(values_only=True):
        scanned += 1
        cells = [_cell(v) for v in row[:XLSX_MAX_COLS]]
        if any(cells):
            rows.append(cells)
        if scanned >= XLSX_SCAN_ROWS:
            return rows, scanned, True
    return rows, scanned, False


def _fill(rows) -> float:
    if not rows:
        return 0.0
    return sum(sum(1 for c in r if c) for r in rows) / len(rows)


def render_xlsx(data: bytes, cap: int = DIGEST_PER_FILE_CHARS) -> str:
    """Sheets → compact pipe tables. The budget goes to the sheets that carry DATA
    first: sheets are ordered by density (rows × non-empty cells per row), a
    sparse "documenter"/property sheet (avg < 3 filled cells per row — e.g. an
    Access table-definition dump) keeps only its rows with ≥ 3 filled cells (the
    `Name | Type | Size` field lines) and drops the property:value noise. Each
    sheet shows its header + the first XLSX_MAX_ROWS data rows and the count of
    what was not shown. Never raises — an unreadable workbook renders as a
    one-line note."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:                  # noqa: BLE001
        return f"(xlsx nije mogao da se pročita: {type(exc).__name__})"
    sheets = []
    try:
        for ws in wb.worksheets:
            rows, scanned, truncated = _sheet_rows(ws)
            if not rows:
                continue
            fill = _fill(rows)
            sparse = fill < 3.0
            if sparse:
                dense_rows = [r for r in rows if sum(1 for c in r if c) >= 3]
                if dense_rows:
                    rows = dense_rows
            sheets.append((len(rows) * max(fill, 1.0), ws.title, rows, scanned, truncated, sparse))
    except Exception as exc:                  # noqa: BLE001
        sheets.append((0, "?", [[f"(prekinuto čitanje: {type(exc).__name__})"]], 0, False, False))
    finally:
        try:
            wb.close()
        except Exception:                     # noqa: BLE001
            pass
    if not sheets:
        return "(prazan radni list)"
    sheets.sort(key=lambda x: -x[0])
    out = []
    per_sheet = max(cap // min(len(sheets), 3), 1500)
    for si, (_score, title, rows, scanned, truncated, sparse) in enumerate(sheets):
        if si >= XLSX_MAX_SHEETS:
            out.append(f"... (+{len(sheets) - XLSX_MAX_SHEETS} listova)")
            break
        width = max((len(r) for r in rows), default=0)
        while width and all((r[width - 1] if len(r) >= width else "") == "" for r in rows):
            width -= 1
        # exact-duplicate rows collapse (a repeated header line, 150 identical
        # message rows) so the budget shows DISTINCT rows; the count says how many
        shown, seen, dupes = [], set(), 0
        for r in rows:
            k = tuple(r)
            if k in seen:
                dupes += 1
                continue
            seen.add(k)
            shown.append(r)
            if len(shown) >= XLSX_MAX_ROWS:
                break
        more = len(rows) - len(shown) - dupes
        head = (f"## list '{title}' — {len(rows)}{'+' if truncated else ''} "
                f"{'značajnih ' if sparse else ''}redova × {width} kolona")
        if sparse:
            head += " (redak list: prikazane samo linije sa ≥3 popunjene ćelije — npr. definicije polja)"
        block = [head] + ["| " + " | ".join((r + [""] * width)[:width]) + " |" for r in shown]
        if more or truncated or dupes:
            block.append(f"... (+{more}{'+' if truncated else ''} redova nije prikazano"
                         + (f"; {dupes} identičnih redova sažeto" if dupes else "") + ")")
        text = "\n".join(block)
        if len(text) > per_sheet:
            text = text[:per_sheet].rsplit("\n", 1)[0] + "\n... (skraćeno)"
        out.append(text)
    text = "\n".join(out)
    return text[:cap] + ("\n... (skraćeno)" if len(text) > cap else "")


def render_docx(data: bytes, cap: int = DIGEST_PER_FILE_CHARS) -> str:
    """Paragraphs + tables of a .docx as plain text (python-docx when present,
    else a raw document.xml tag-strip). Never raises."""
    try:
        import docx                            # python-docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append("| " + " | ".join(_cell(c.text) for c in row.cells) + " |")
        text = "\n".join(parts)
    except Exception:                          # noqa: BLE001 — fall back to the raw XML
        try:
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            text = re.sub(r"<[^>]+>", "", xml)
        except Exception as exc:               # noqa: BLE001
            return f"(docx nije mogao da se pročita: {type(exc).__name__})"
    text = text.strip()
    return text[:cap] + ("\n... (skraćeno)" if len(text) > cap else "")



def _zip_xml_text(data: bytes, members: list, breaks: tuple) -> str:
    """Text out of a ZIP-and-XML office file. `members` is a list of names or a
    callable that picks them from the namelist, `breaks` are the closing tags
    that mean a line ended. Never raises."""
    import zipfile
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = members(z.namelist()) if callable(members) else members
        for name in names:
            try:
                xml = z.read(name).decode("utf-8", "replace")
            except KeyError:
                continue
            for tag in breaks:
                xml = xml.replace(tag, "\n")
            # Drop the tags, keep what was between them.
            txt = re.sub(r"<[^>]+>", "", xml)
            txt = (txt.replace("&amp;", "&").replace("&lt;", "<")
                      .replace("&gt;", ">").replace("&quot;", '"')
                      .replace("&apos;", "'"))
            out.append("\n".join(ln.strip() for ln in txt.splitlines() if ln.strip()))
    return "\n".join(p for p in out if p)


def render_odf(data: bytes, cap: int = DIGEST_PER_FILE_CHARS) -> str:
    """An OpenDocument text/sheet/presentation as plain text. Never raises.

    A row becomes one line so a spreadsheet reads like the xlsx digest above
    rather than as one run-on paragraph."""
    try:
        text = _zip_xml_text(
            data, ["content.xml"],
            ("</text:p>", "</text:h>", "</table:table-row>", "</draw:frame>"))
    except Exception as exc:                   # noqa: BLE001
        return f"(odf nije mogao da se pročita: {type(exc).__name__})"
    text = text.strip()
    return text[:cap] + ("\n... (skraćeno)" if len(text) > cap else "")


def render_pptx(data: bytes, cap: int = DIGEST_PER_FILE_CHARS) -> str:
    """Slide text of a .pptx, one slide per block, in slide order. Never raises.

    Sorted NUMERICALLY: `slide10.xml` sorts before `slide2.xml` as a string, and
    a deck read out of order is worse than one not read at all -- it looks fine.
    """
    def slides(names):
        got = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        return sorted(got, key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
    try:
        text = _zip_xml_text(data, slides, ("</a:p>", "</a:tr>"))
    except Exception as exc:                   # noqa: BLE001
        return f"(pptx nije mogao da se pročita: {type(exc).__name__})"
    text = text.strip()
    return text[:cap] + ("\n... (skraćeno)" if len(text) > cap else "")


def render_rtf(data: bytes, cap: int = DIGEST_PER_FILE_CHARS) -> str:
    """RTF stripped to its text. Never raises.

    Order matters and is the whole difficulty: the destination groups that carry
    no visible text (fonts, colours, stylesheets, embedded pictures) go FIRST,
    because their contents look like ordinary words once the control words are
    removed -- a font table stripped naively puts "Times New Roman Arial" at the
    top of every document.
    """
    try:
        txt = data.decode("cp1252", "replace")
        # 1. whole destination groups that are never body text
        txt = re.sub(r"{\\\*?\\?(?:fonttbl|colortbl|stylesheet|info|pict|object|"
                     r"themedata|colorschememapping|latentstyles|datastore)"
                     r"(?:[^{}]|{[^{}]*})*}", "", txt)
        # 2. escapes, before the control words that look like them
        txt = txt.replace("\\par", "\n").replace("\\line", "\n").replace("\\tab", "\t")
        txt = re.sub(r"\\'([0-9a-fA-F]{2})",
                     lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), txt)
        txt = txt.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
        # 3. remaining control words, then the braces holding them together
        txt = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", txt)
        txt = txt.replace("{", "").replace("}", "")
        text = "\n".join(ln.strip() for ln in txt.splitlines() if ln.strip())
    except Exception as exc:                   # noqa: BLE001
        return f"(rtf nije mogao da se pročita: {type(exc).__name__})"
    text = text.strip()
    return text[:cap] + ("\n... (skraćeno)" if len(text) > cap else "")


def render_text(data: bytes, cap: int = DIGEST_PER_FILE_CHARS) -> str:
    for enc in ("utf-8", "cp1250", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return "(tekst nije mogao da se dekodira)"
    text = text.strip()
    return text[:cap] + ("\n... (skraćeno)" if len(text) > cap else "")


# --------------------------------------------------------------------------- #
#  Cache — keyed by URL; helpdesk attachment URLs never change content.
# --------------------------------------------------------------------------- #
def cache_dir():
    v = os.environ.get(CACHE_ENV)
    if v and v.strip().lower() == "off":
        return None
    return Path(v) if v else DEFAULT_CACHE_DIR


def _cache_path(url: str):
    d = cache_dir()
    if d is None:
        return None
    return d / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")


def _cache_get(url: str):
    fp = _cache_path(url)
    if fp is None or not fp.exists():
        return None
    try:
        v = json.loads(fp.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) and v.get("kind") else None
    except Exception:                          # noqa: BLE001 — corrupt -> refetch
        return None


def _cache_put(url: str, entry: dict) -> None:
    fp = _cache_path(url)
    if fp is None:
        return
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_name(fp.name + ".tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, fp)
    except Exception:                          # noqa: BLE001 — a cache miss is not an error
        pass


# --------------------------------------------------------------------------- #
#  One attachment → one resolved entry
# --------------------------------------------------------------------------- #
def resolve_one(url: str, name: str = "") -> dict:
    """{url, name, kind, status, mime?, data?(b64), digest?, size}. `status` is
    'read' (content available), 'listed' (fetched but not renderable), or a
    failure word ('unreachable', 'oversize'). Cached by URL. Never raises."""
    url = (url or "").strip()
    display = url_name(url, name)
    hit = _cache_get(url)
    if hit is not None:
        hit["name"] = display
        return hit
    data = fetch_bytes(url)
    if data is None:
        return {"url": url, "name": display, "kind": "other", "status": "unreachable", "size": 0}
    kind = classify(data, url, name)
    entry = {"url": url, "name": display, "kind": kind, "status": "listed", "size": len(data)}
    if kind == "image":
        entry.update(mime=sniff_image_mime(data),
                     data=base64.b64encode(data).decode("ascii"), status="read")
    elif kind == "pdf":
        entry.update(mime="application/pdf",
                     data=base64.b64encode(data).decode("ascii"), status="read")
    elif kind == "xlsx":
        entry.update(digest=render_xlsx(data), status="read")
    elif kind == "docx":
        entry.update(digest=render_docx(data), status="read")
    elif kind == "odf":
        entry.update(digest=render_odf(data), status="read")
    elif kind == "pptx":
        entry.update(digest=render_pptx(data), status="read")
    elif kind == "rtf":
        entry.update(digest=render_rtf(data), status="read")
    elif kind == "text":
        entry.update(digest=render_text(data), status="read")
    _cache_put(url, entry)
    return entry


def iter_attachments(t: dict):
    """Every attachment on a ticket in reading order:
    (source_label, url, name). Source is 'opis' for original.attachment_url and
    'komentar N (author)' for comment files."""
    o = t.get("original") if isinstance(t.get("original"), dict) else {}
    u = o.get("attachment_url")
    if isinstance(u, str) and u.strip():
        yield ("opis tiketa", u.strip(), "")
    for i, c in enumerate(t.get("comments") or [], 1):
        if not isinstance(c, dict):
            continue
        for a in c.get("attachments") or []:
            if isinstance(a, dict) and isinstance(a.get("url"), str) and a["url"].strip():
                yield (f"komentar {i} ({c.get('author') or '?'})", a["url"].strip(), a.get("name") or "")
            elif isinstance(a, str) and a.strip():
                yield (f"komentar {i} ({c.get('author') or '?'})", a.strip(), "")


def collect(t: dict, *, max_files: int = MAX_FILES) -> dict:
    """The model input for a ticket's attachments — see the module docstring.
    Images and PDFs become inline `files` (bounded in count and total bytes);
    tabular/text files become the `digest`; every attachment gets a `listing`
    row so the model (and the human) knows what exists even when it could not
    be read."""
    files, listing, digest_parts = [], [], []
    inline_total = 0
    digest_total = 0
    for k, (source, url, name) in enumerate(iter_attachments(t), 1):
        e = resolve_one(url, name)
        row = {"n": k, "source": source, "url": url, "name": e.get("name") or url,
               "kind": e.get("kind"), "status": e.get("status"), "size": e.get("size", 0)}
        if e.get("status") == "read" and e.get("data"):
            raw_len = len(e["data"]) * 3 // 4
            if len(files) < max_files and inline_total + raw_len <= MAX_INLINE_TOTAL:
                files.append({"mime": e["mime"], "data": e["data"]})
                inline_total += raw_len
                row["inline"] = len(files)          # which inline part it is
            else:
                row["status"] = "listed (limit inline priloga)"
        elif e.get("status") == "read" and e.get("digest"):
            block = (f"\n=== PRILOG {k} — {row['name']} ({row['kind']}, {source}) ===\n"
                     + e["digest"])
            if digest_total + len(block) <= DIGEST_TOTAL_CHARS:
                digest_parts.append(block)
                digest_total += len(block)
            else:
                row["status"] = "listed (limit teksta priloga)"
        listing.append(row)
    return {"files": files, "digest": "\n".join(digest_parts).strip(), "listing": listing}


def listing_text(listing) -> str:
    """The `listing` as lines for a prompt — so the model knows every file that
    exists, which ones ride inline as images/PDFs, and which it only sees named."""
    if not listing:
        return "(nema priloga)"
    lines = []
    for r in listing:
        how = {"read": "pročitan", "unreachable": "nedostupan", "listed": "samo naveden"}.get(
            r.get("status"), r.get("status") or "?")
        if r.get("inline"):
            how = f"priložen kao slika/PDF #{r['inline']}"
        elif r.get("status") == "read":
            how = "sadržaj ispod u tekstu"
        lines.append(f"- prilog {r['n']}: {r['name']} [{r.get('kind')}] — iz: {r['source']} — {how} — {r['url']}")
    return "\n".join(lines)
