# OWASP Top 10:2025 — mapped to where it actually bites

Extends `craft-security`. **This is the 2025 edition, not 2021** — two categories
are new, SSRF was absorbed into A01, and misconfiguration moved up to A02. Code
written against the older list will check the wrong things.

| | Category | Where it lands here | Owner |
|---|---|---|---|
| **A01** | **Broken Access Control** (incl. SSRF) | by-id endpoints, aggregates, scope filtering — **the largest category and this codebase's most repeated real defect** | `references/authorization.md` |
| **A02** | Security Misconfiguration | settings, `DEBUG`, allowed hosts, CORS, default credentials, verbose errors | below + `dj-settings` |
| **A03** | Software Supply Chain Failures *(new)* | dependencies, lockfiles, CI, build artifacts, an unpinned action | below + `craft-git` |
| **A04** | Cryptographic Failures | secrets at rest, transport, weak hashing, tokens | `references/secrets-errors.md` |
| **A05** | Injection (SQL, XSS, command, template) | forms, raw SQL, `mark_safe`, subprocess, redirects | below + `references/untrusted-input.md` |
| **A06** | Insecure Design | the flaw is the design, not the code — a missing limit, a trusted client, a workflow with no gate | `data-model-consultant`, `planner` |
| **A07** | Authentication Failures | login, session, password reset, tokens, lockout | below |
| **A08** | Software or Data Integrity Failures | unsigned updates, deserialisation, mutable audit trails | below |
| **A09** | Security Logging and Alerting Failures | nothing logged, or everything logged including secrets | below |
| **A10** | Mishandling of Exceptional Conditions *(new)* | fail-open error paths, swallowed exceptions, stack traces to the caller | `references/secrets-errors.md` |

## A05 — Injection, in a framework that mostly protects you

The ORM parameterises, and templates autoescape. So injection here arrives
through the places that **opt out**:

- **`mark_safe` / `|safe` on anything a user can influence.** Every use is a
  decision about whether that value can contain attacker text. Treat each as a
  small review, not a formatting fix.
- **Raw SQL** — `.raw()`, `.extra()`, a cursor. Parameters, never f-strings. And
  if the SQL itself is generated or user-supplied, see
  `references/untrusted-input.md`: parse it, and forbid joins.
- **`order_by(request.GET[...])`** — an unvalidated field name is an information
  leak and an error surface. Allow-list.
- **Command execution** built from input — argument list, never a shell string.
- **A redirect target from a parameter** — validate against known paths.
- **A filename from an upload or archive** — reduce to its base name.

**Stored XSS is the one the framework does not stop:** an uploaded `.html` or
`.svg` served from your own origin runs as first-party script
(`references/files.md`). Autoescaping never sees it.

## A02 — Misconfiguration

`DEBUG` off in production; `ALLOWED_HOSTS` real; secure and httponly cookies;
`SameSite`; HSTS; no default credentials anywhere; error pages that do not carry
a traceback. **The framework's own deployment check is the cheapest audit you
will ever run** — run it, and read what it says rather than silencing items.

Middleware order is security-relevant: a check placed after the thing it guards
does not run.

**"It's only localhost" is not a security boundary.** A mutating endpoint on a
dev tool bound to `0.0.0.0`, reachable via a CORS-simple request (a bodyless
POST, or `Content-Type: text/plain`) with `Access-Control-Allow-Origin: *`, is
reachable from *any page the user's browser happens to load* — no preflight, no
consent. A drive-by page can POST to `127.0.0.1` and inject data or trigger an
action (seen: injecting fake events, shutting the server down). Gate every
mutating route on same-origin (or origin-absent, i.e. server-to-server) — never
on the assumption that only the local user can reach a local port.

The origin check alone is not enough once the same tool intentionally binds a
non-loopback interface (e.g. `0.0.0.0`) so a phone or other LAN device can view
it: a non-browser client (curl, a script, LAN malware) can omit or forge the
Origin header just as easily as a same-machine caller can legitimately omit it,
so origin-absent cannot distinguish the two. Gate mutating routes on the
**loopback source address** (`client_address[0]` in `{127.0.0.1, ::1, 127.*}`)
instead — a TCP source address cannot be spoofed on an established connection
without being on-path. Keep the Origin check too, as defense-in-depth against a
same-machine browser CSRF (a hostile page loaded locally has a loopback source
address but a foreign Origin). Result: GET stays open on the LAN, writes are
loopback-only. Verified: POST via the LAN IP → 403; GET via the LAN IP → 200.

## A03 — Supply chain *(new in 2025)*

A pinned lockfile, and a scan against a vulnerability database on a schedule.
**Use the deterministic scanner, not a model** — it reads your lockfile and
queries a real database in seconds, exactly, for free. The agent's job is only to
decide whether a finding applies to how you actually use the package.

Pin CI actions to a commit, not a moving tag. Anything a build downloads and
executes is part of the application.

## A07 — Authentication

Rate-limit login and password reset. A reset token is single-use and expires.
Session fixed on privilege change. **Do not reveal whether an account exists** —
the response for a wrong password and an unknown user is the same one.

Auth email is part of the minimal core and must not depend on an optional
notification feature — locking someone out of account recovery to enforce a
feature flag is a self-inflicted outage.

## A08 — Integrity

Anything that is evidence — signed, posted, approved, counted — is corrected by a
**new** record, never by editing the old one. A mutable audit trail is not an
audit trail.

Never deserialise untrusted data into objects (`pickle`, `yaml.load`). Signing
payloads: see `craft-code/references/constants.md` for why a `None` in the
payload breaks tamper evidence.

## A09 — Logging and alerting

The two failures are opposite and both common: **nothing is logged**, so a
swallowed side effect is indistinguishable from one that was never meant to run;
or **everything is logged**, including tokens and request bodies.

Log the security-relevant events — authentication, authorisation failures,
privilege changes, exports — with enough to reconstruct and never enough to
replay. And someone has to *see* them; a log nobody reads is not detection.

## A10 — Mishandling exceptional conditions *(new in 2025)*

**A guard that fails open is worse than no guard**, because it is trusted. When a
permission check, a token validation or a scope resolution cannot complete, the
answer is *deny*, not *allow*.

`except: pass` is in this category, not merely untidy: it converts a security
failure into silence. And an exception message returned to the caller leaks
schema (`references/secrets-errors.md`).

## Using this

Not a checklist to run once. Each writer agent carries the two or three
categories its surface actually exposes; `appsec-reviewer` works the whole list
against a diff when the change warrants it.
