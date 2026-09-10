# Seam: Django + htmx (with a JSON API alongside)

Server still renders; htmx swaps fragments of the page instead of reloading it.
Most of the SSR seam still applies — read `django-ssr.md` first; this file covers
only what changes.

Frequently there is *also* a JSON API on the same backend, serving mobile clients
or integrations. **That is a second seam sharing one backend**, and the mistakes
below mostly come from treating the two as one.

| Question | Answer in this seam |
|---|---|
| State | Server + URL, as SSR. htmx does not introduce a client store — if you find yourself building one, the seam is wrong. |
| Identity | Session cookie for htmx (same as SSR). Token auth for the JSON API. **Two mechanisms, one backend.** |
| CSRF | htmx must send the token on every non-`GET`. Configure it once globally; per-request is where it gets forgotten. |
| Validation | Server only, once — unchanged. |
| Errors | A fragment response containing the errors, swapped into the same target. Non-2xx responses are **not swapped by default**. |
| i18n | Server-side, unchanged. A fragment must render in the request's language, which means the language must be resolvable on a fragment request too. |
| Build | None for htmx. |

## What actually goes wrong here

**The CSRF token is missing on htmx requests.** Configure it globally at the body
element rather than per-request; the per-request form is correct until the one
place someone forgets.

**An error response is silently dropped.** htmx does not swap non-2xx responses
unless told to. A failed validation therefore produces *nothing at all* — the
user clicks save, nothing happens, no error. Either return the errors with a 2xx
and encode failure in the fragment, or configure the response handling for error
codes explicitly. Decide once, for the whole app.

**A fragment view returns a full page.** It renders inside the target, producing
a page nested in a page. Fragment views need their own thin templates, and it is
worth making that visible in the naming so it cannot be confused.

**Authorization is skipped because "it is only a fragment".** A fragment endpoint
is an endpoint: same by-id authorization through the same helper as everything
else (`craft-security`). Fragment views are exactly the kind of small,
template-less view that gets forgotten in a security sweep.

**Two auth mechanisms, one set of views.** Session-authenticated htmx and
token-authenticated API traffic must not accidentally share a permission path
where only one of them was considered. Keep the API surface separate from the
fragment surface, even when they read the same service.

**Duplicated business logic between the fragment view and the API view.** Both
call the same service and differ only in rendering. When they start diverging,
that divergence is the bug — the service is the single implementation
(`craft-code`).

## Boundary between the two surfaces

The JSON API answers the seven questions independently. Keep it explicit which
endpoints are fragments (session, HTML, CSRF) and which are API (token, JSON,
no CSRF) — a URL prefix is enough. Ambiguity here is what produces an endpoint
that is authenticated one way and authorized for the other.
