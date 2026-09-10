"""Acme Helpdesk (helpdesk.example.com) source adapter — stdlib urllib, no deps.

The single outbound door to the helpdesk from the sync side. Mirrors the
endpoints the MCP server exposes
(Ticketing-System3/static/mcp/acme_helpdesk_mcp.py) but without the `requests`
dependency, so a plain `python sync.py` needs nothing installed. Credentials
come from the environment (HELPDESK_URL / HELPDESK_TOKEN) — never a file, and
never written to a log or an exception message.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import TicketSourceError, register

API_PREFIX = "/api/v1"
#: Attachment caps, mirrored from the helpdesk serializer so a bad payload is
#: refused before it costs a round trip (and before half a comment exists).
#: A set that exceeds them is SPLIT ACROSS COMMENTS by the caller
#: (writeback.plan_chunks) — never trimmed here, and never sent as one comment
#: the server will reject (2026-08-21: 13 approved screenshots on DEMO#05513 were
#: refused whole, so the customer got nothing).
ATTACH_MAX_FILES = 5
ATTACH_MAX_BYTES = 5 * 1024 * 1024
#: OURS, not the server's: a budget for one multipart body. The serializer caps
#: each FILE at 5 MB and declares no total, so five of them is a 25 MB request
#: that no proxy in front of the helpdesk is known to accept. Chunking is free,
#: so we spend a comment rather than a round trip. Never below ATTACH_MAX_BYTES:
#: a lone file within the per-file cap must always fit in a comment of its own.
ATTACH_MAX_REQUEST_BYTES = ATTACH_MAX_BYTES
CRLF = chr(13) + chr(10)        # multipart is CRLF-delimited, by spec
SEP = "--"
ATTACH_FIELD = "files"          # the API accepts `files[]` too; we send one name
ATTACH_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif", "pdf": "application/pdf"}
TIMEOUT_S = 30.0
#: Web route to a ticket's full view, used for the "Otvori na helpdesk-u" link.
#: The API returns no per-ticket web URL, so it is built from the base host.
#: VERIFY this path against the Ticketing-System2 front routes before the UI
#: relies on the link — it is a single constant to change if it is wrong.
TICKET_WEB_PATH = "/tickets/{id}"


def writes_blocked() -> str:
    """The reason this process must NOT write to the helpdesk, or "".

    2026-08-20, twice in one hour: a subagent's test shelled out to
    `writeback.py --close`, the child inherited HELPDESK_URL/HELPDESK_TOKEN and
    posted a real comment on live ticket #08597 (id 599); an hour later a
    "does the guard work?" check called `add_comment` directly and posted
    another (id 600). Both had to be deleted from the production database by
    hand. So the refusal lives HERE, in the one outbound door, and it triggers
    on anything that smells of a test or a rehearsal:

      * BRAIN_HELPDESK_READONLY=1     - set it around anything experimental;
      * PYTEST_CURRENT_TEST           - a pytest run;
      * argv[0] is a test_*.py        - this repo's own runner convention;
      * BRAIN_NO_SEND=1               - the generic "no side effects" switch.

    A subprocess started BY a test inherits the env, so a test that shells out
    must pass BRAIN_HELPDESK_READONLY=1 on (or scrub the credentials) - the
    rule and this incident are written up in craft-testing.
    """
    for var in ("BRAIN_HELPDESK_READONLY", "BRAIN_NO_SEND"):
        v = str(os.environ.get(var) or "").strip().lower()
        if v not in ("", "0", "false", "no"):
            return f"{var} is set"
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "a pytest run"
    argv0 = os.path.basename((sys.argv[0] if sys.argv else "") or "")
    if argv0.startswith("test_") and argv0.endswith(".py"):
        return f"the caller is a test ({argv0})"
    return ""


class HelpdeskError(TicketSourceError):
    # Derives from the registry's base so `sync.py` can catch a failed pull
    # without importing THIS module. The name stays: every caller and every
    # test already uses it, and renaming it would be churn with no gain.

    """A helpdesk call failed. Carries a status/kind but never the token, the
    query string, or a response body that could contain either.

    `ambiguous` carries the ONE thing the message cannot: might the request have
    been PROCESSED? True only for a transport or parse failure AFTER the request
    left this machine. A pre-flight refusal (no credentials, the write guard, an
    attachment the caps reject) and a real HTTP status are both DEFINITE — the
    write did not happen — which is why the default is False and a new raise
    site is definite unless it says otherwise.

    The sent log spells the two apart as "unknown" (a send that may be live —
    go look at the helpdesk) and "fail" (it did not land). Getting it backwards
    sends someone hunting for a comment that cannot exist: until 2026-08-21 the
    attachment-count refusal, which raises before any socket is opened, was
    logged "unknown"."""

    def __init__(self, *args, ambiguous: bool = False):
        super().__init__(*args)
        self.ambiguous = bool(ambiguous)


def _seg(value) -> str:
    """URL-encode a path segment. A ticket_id comes from tiketi.json keys, which
    a user can hand-edit — encoding stops a crafted id (a `/`, `?`, `..`) from
    reaching a different endpoint than intended."""
    return urllib.parse.quote(str(value), safe="")


@register("acme_helpdesk")
class AcmeHelpdesk:
    def __init__(self, url: str = "", token: str = ""):
        self.base = (url or os.environ.get("HELPDESK_URL", "")).rstrip("/")
        self.token = token or os.environ.get("HELPDESK_TOKEN", "")
        if not self.base:
            raise HelpdeskError("HELPDESK_URL is not set")
        if not self.token:
            raise HelpdeskError("HELPDESK_TOKEN is not set")

    # -- the single outbound door ------------------------------------------
    def _get(self, path: str, params: dict | None = None):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(f"{self.base}{API_PREFIX}{path}{qs}", method="GET")
        req.add_header("Authorization", f"Token {self.token}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Status only. The path is safe to show (no id secrets); the body
            # and the Authorization header are not, so neither is surfaced.
            raise HelpdeskError(f"GET {path} -> HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # The request left (or may have): the server's side is unknown.
            raise HelpdeskError(f"GET {path} failed: {exc.__class__.__name__}",
                                ambiguous=True) from None

    # -- reads (Phase 3: pull) ---------------------------------------------
    def list_active(self, module=None, category=None, priority=None):
        """The engineer's active tickets (thin list shape — no body/comments)."""
        params = {}
        if module:
            params["module"] = module
        if category:
            params["category"] = category
        if priority:
            params["priority"] = priority
        data = self._get("/engineer/tickets/active/", params or None)
        return (data or {}).get("tickets") or []

    def get_detail(self, ticket_id):
        """Full ticket: adds description, attachment_url and the comment thread."""
        return self._get(f"/engineer/tickets/{_seg(ticket_id)}/")

    def list_modules(self):
        """`[{id, name}, ...]` — used to turn a module NAME into the id the
        active-tickets filter expects."""
        data = self._get("/modules/")
        if isinstance(data, dict):
            return data.get("modules") or data.get("results") or []
        return data or []

    # -- writes (Phase 6: write-back) — IRREVERSIBLE, one call each ---------
    def _write(self, method: str, path: str, body: dict):
        blocked = writes_blocked()
        if blocked:
            raise HelpdeskError(f"helpdesk write refused: {blocked}")
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self.base}{API_PREFIX}{path}", data=data, method=method)
        req.add_header("Authorization", f"Token {self.token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            # 404 = the token's engineer does not own this ticket (queryset
            # filter), 400 = already closed / validation. Status only, no body.
            raise HelpdeskError(f"{method} {path} -> HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # AMBIGUOUS: the request left this machine and the helpdesk may have
            # processed it. The caller must not retry it blind.
            raise HelpdeskError(f"{method} {path} failed: {exc.__class__.__name__}",
                                ambiguous=True) from None

    def attachment_limits(self) -> dict:
        """What ONE comment can carry. The chunker (writeback.plan_chunks) reads
        the caps from HERE instead of importing the constants, so a second
        adapter with different limits needs no change on the writeback side."""
        return {"max_files": ATTACH_MAX_FILES, "max_bytes": ATTACH_MAX_BYTES,
                "max_request_bytes": ATTACH_MAX_REQUEST_BYTES}

    def attachment_check(self, f) -> tuple:
        """`(size_in_bytes, reason)` for ONE attachment: `reason` is why the
        helpdesk would refuse it (type, unreadable, over the per-file cap), else
        "". ONE definition of the per-file rules, asked twice — by
        `_write_multipart` before it builds a body, and by the chunker before it
        posts the FIRST of several comments. The second call is the point: a
        file that can never land is then an honest failure up front, instead of
        half a delivered set with a hole in it."""
        fp = Path(str((f or {}).get("path") or ""))
        name = str((f or {}).get("name") or fp.name or "prilog")
        ext = fp.suffix.lower().lstrip(".")
        if ext not in ATTACH_TYPES:
            return 0, f"attachment type not allowed: .{ext}"
        try:
            size = fp.stat().st_size
        except OSError as exc:
            return 0, f"attachment unreadable ({exc.__class__.__name__})"
        if size > ATTACH_MAX_BYTES:
            return size, f"attachment too large: {name}"
        return size, ""

    def add_comment(self, ticket_id, text, files=None):
        """POST a comment, optionally with attachments. NOT idempotent — the API
        has no dedup key, so a retry double-posts; the caller claims the draft
        before calling, never retries.

        `files` is `[{"path": <local file>, "name": <what the customer sees>}]`
        (the before/after screenshots of a visual change). With files the request
        is multipart under the field `files`; the API is the side that accepts
        `files[]` as well (those two spellings disagreed once already). Caps
        mirror the server's: 5 files, 5 MB each, images
        and PDF — refused HERE too, so a bad payload never becomes a 400 after
        the comment text was already accepted.

        ONE comment, ONE request: a set larger than the caps is split across
        several comments by the caller (writeback.plan_chunks), because only the
        caller can claim each comment before sending it. Reaching this method
        with an over-cap set is a caller bug and is refused, not trimmed."""
        path = f"/engineer/tickets/{_seg(ticket_id)}/comment/"
        if not files:
            return self._write("POST", path, {"comment": text})
        return self._write_multipart("POST", path, {"comment": text}, files)

    def _write_multipart(self, method: str, path: str, fields: dict, files: list):
        """The multipart twin of `_write`: same guard, same curated errors, the
        body assembled by hand (stdlib only, no `requests` in this repo).

        EVERY refusal below happens before a socket is opened, so all of them are
        DEFINITE (`ambiguous` stays False) — the caller logs "did not land", and
        nobody is sent to look for a comment that cannot exist. The pre-flight
        guards are grouped FIRST, ahead of any body assembly, so adding one
        cannot accidentally land after work has begun."""
        blocked = writes_blocked()
        if blocked:
            raise HelpdeskError(f"helpdesk write refused: {blocked}")
        if len(files) > ATTACH_MAX_FILES:
            raise HelpdeskError(f"too many attachments ({len(files)} > {ATTACH_MAX_FILES})")
        total = 0
        for f in files:
            size, reason = self.attachment_check(f)
            if reason:
                raise HelpdeskError(reason)
            total += size
        if total > ATTACH_MAX_REQUEST_BYTES:
            raise HelpdeskError(f"attachments too large for one comment "
                                f"({total} > {ATTACH_MAX_REQUEST_BYTES} bytes)")
        parts, boundary = [], "----brainshots" + uuid.uuid4().hex
        for k, v in (fields or {}).items():
            parts.append((SEP + boundary + CRLF
                          + 'Content-Disposition: form-data; name="' + str(k) + '"'
                          + CRLF + CRLF + str(v) + CRLF).encode("utf-8"))
        for f in files:
            fp = Path(str((f or {}).get("path") or ""))
            name = str((f or {}).get("name") or fp.name or "prilog")
            ext = fp.suffix.lower().lstrip(".")
            try:
                blob = fp.read_bytes()
            except OSError as exc:
                # Re-checked on the BYTES we are about to send, not on the stat
                # `attachment_check` took: the file can change in between, and
                # what goes on the wire is what the cap is about.
                raise HelpdeskError(f"attachment unreadable ({exc.__class__.__name__})") from None
            if len(blob) > ATTACH_MAX_BYTES:
                raise HelpdeskError(f"attachment too large: {name}")
            # ONE field name per file. Sending the same bytes under both
            # spellings would create TWO attachments per screenshot; the API is
            # the side that accepts either name.
            head = (SEP + boundary + CRLF
                    + 'Content-Disposition: form-data; name="' + ATTACH_FIELD
                    + '"; filename="' + name + '"' + CRLF
                    + 'Content-Type: ' + ATTACH_TYPES[ext] + CRLF + CRLF)
            parts.append(head.encode("utf-8") + blob + CRLF.encode("ascii"))
        parts.append((SEP + boundary + SEP + CRLF).encode("utf-8"))
        data = b"".join(parts)
        req = urllib.request.Request(f"{self.base}{API_PREFIX}{path}", data=data, method=method)
        req.add_header("Authorization", f"Token {self.token}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raise HelpdeskError(f"{method} {path} -> HTTP {exc.code}") from None
        except Exception as exc:                       # noqa: BLE001
            # Same as `_write`: the body left this machine, the outcome is not
            # ours to declare.
            raise HelpdeskError(f"{method} {path} failed: {exc.__class__.__name__}",
                                ambiguous=True) from None

    def close(self, ticket_id, resolution):
        """Close with a resolution. Fires the helpdesk's notifications/webhooks;
        an already-closed ticket returns HTTP 400."""
        return self._write("PATCH", f"/engineer/tickets/{_seg(ticket_id)}/close/",
                            {"resolution": resolution})

    def set_estimate(self, ticket_id, hours):
        return self._write("PATCH", f"/engineer/tickets/{_seg(ticket_id)}/estimate/",
                            {"hours": hours})

    def create_ticket(self, payload: dict) -> dict:
        """POST a new ticket. `payload` carries the API's own field names
        (ticket_title, ticket_description, module, category, priority,
        assign_to_me) — the caller (writeback.create_ticket_only) builds them,
        this method never guesses one. Returns the 201 body: the full ticket
        detail, the same shape `get_detail`/`to_record` already handle."""
        return self._write("POST", "/engineer/tickets/", payload)

    def edit_ticket(self, ticket_id, fields: dict) -> dict:
        """PATCH any subset of ticket_title/ticket_description/module/category/
        priority on an existing ticket. `fields` carries the API's own field
        names, same rule as `create_ticket`."""
        return self._write("PATCH", f"/engineer/tickets/{_seg(ticket_id)}/edit/", fields)

    # -- map the API shape onto the canonical tiketi.json shape ------------
    def to_record(self, detail: dict) -> dict:
        """One ticket-detail response → `original` (immutable evidence) +
        `comments` + `url` + a raw `helpdesk` block. Interpretation (title,
        status, ordering) is the human's layer and is NOT set here."""
        tid = str(detail.get("ticket_id") or "").strip()
        customer = detail.get("customer") or {}
        engineer = detail.get("engineer") or {}
        category = detail.get("category") or {}
        module = detail.get("module") or {}
        cust_id, eng_id = customer.get("id"), engineer.get("id")

        # F9: every rater who scored the ticket (customer/tester/admin), plus the
        # average. Tolerant of the OLD single-field shape (`rating`/
        # `rating_comment`/`rated_at`, no `ratings`/`rating_avg` at all) — the
        # helpdesk has not shipped F9 yet, so both must keep working.
        raw_ratings = detail.get("ratings")
        ratings = []
        if isinstance(raw_ratings, list):
            for r in raw_ratings:
                if not isinstance(r, dict):
                    continue
                ratings.append({
                    "role": r.get("role") or "",
                    "rater": r.get("rater") or "",
                    "rating": r.get("rating"),
                    "comment": r.get("comment") or "",
                    "rated_at": r.get("rated_at") or "",
                })
        rating_avg = detail.get("rating_avg")
        rating_avg = (rating_avg if isinstance(rating_avg, (int, float))
                      and not isinstance(rating_avg, bool) else None)
        customer_rating = next((r for r in ratings if r["role"] == "customer"), None)
        if ratings or rating_avg is not None or "ratings" in detail or "rating_avg" in detail:
            # new shape: the API DROPS the flat fields, so `rating`/
            # `rating_comment`/`rated_at` below are DERIVED, never read off them.
            rating_value = customer_rating["rating"] if customer_rating else rating_avg
            rating_comment = customer_rating["comment"] if customer_rating else ""
            rated_at = customer_rating["rated_at"] if customer_rating else ""
        else:
            # pre-F9 shape (not deployed yet — tolerate absence): the only source.
            rating_value = detail.get("rating")
            rating_comment = detail.get("rating_comment") or ""
            rated_at = detail.get("rated_at") or ""

        def role_of(user):
            uid = (user or {}).get("id")
            if uid is not None and uid == cust_id:
                return "customer"
            if uid is not None and uid == eng_id:
                return "engineer"
            return "other"           # the API has no role field; infer by id

        comments = []
        for c in (detail.get("comments") or []):
            user = c.get("user") or {}
            att = c.get("attachment_url")
            comments.append({
                "id": c.get("id"),
                "author": user.get("full_name") or user.get("email") or "",
                "author_role": role_of(user),
                "at": c.get("created_on") or "",
                "body": c.get("text") or "",
                "attachments": [{"name": "", "url": att}] if att else [],
            })

        return {
            "id": tid,
            "original": {
                "title": detail.get("ticket_title") or "",
                "description": detail.get("ticket_description") or "",
                "category": category.get("name") or "",
                "created": detail.get("created_on") or "",
                "customer": customer.get("full_name") or customer.get("email") or "",
                "attachment_url": detail.get("attachment_url") or "",
            },
            "comments": comments,
            "url": f"{self.base}{TICKET_WEB_PATH.format(id=_seg(tid))}" if tid else "",
            "helpdesk": {
                "priority": (detail.get("priority") or "").lower(),
                "status": detail.get("status") or "",
                "is_closed": bool(detail.get("is_closed")),
                "module": module.get("name") or "",
                "module_id": module.get("id"),
                "deadline": detail.get("deadline") or "",
                "estimated_time": detail.get("estimated_time"),
                "resolution_steps": detail.get("resolution_steps") or "",
                "closed_at": detail.get("closed_at"),
                # authoritative count from the server; falls back to len(comments)
                "comment_count": (detail.get("comment_count")
                                  if isinstance(detail.get("comment_count"), int)
                                  else len(comments)),
                # F8/F9: the post-close rating(s). `rating` is back-compat for an
                # F8 reader — the CUSTOMER's own rating when they rated, else the
                # average; absent entirely (pre-F8, or nobody has rated yet) ->
                # None/""/[], never a KeyError. `ratings`/`rating_avg` are F9: every
                # rater (customer/tester/admin) and the mean of them.
                "rating": rating_value,
                "rating_comment": rating_comment,
                "rated_at": rated_at,
                "ratings": ratings,
                "rating_avg": rating_avg,
            },
        }
