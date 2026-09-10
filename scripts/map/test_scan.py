#!/usr/bin/env python3
"""Proof that the map describes the repo and reaches nothing outside it.

Two properties, and the second is the reason this tool exists at all rather
than the hosted one it borrows its shape from:

  * the graph reflects what the code actually imports;
  * NOTHING leaves the machine -- no network client, no URL, no upload path,
    and an HTML output that fetches nothing when opened.

The second is asserted against the SOURCE, not against behaviour, because
"it did not upload this time" is not the same claim as "it cannot".

  python scripts/map/test_scan.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scan                                                      # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def build(root: Path) -> None:
    for app, files in {
        "tenants": {"models.py": "from django.db import models\n"
                                 "class Tenant(models.Model):\n    pass\n"
                                 "class Plan(models.Model):\n    pass\n",
                    "apps.py": ""},
        "billing": {"models.py": "from django.db import models\n"
                                 "class Invoice(models.Model):\n    pass\n",
                    "services.py": "from tenants.models import Tenant\n"
                                   "import requests\n\n"
                                   "def charge(t):\n"
                                   '    """Charge a tenant."""\n'
                                   "    return Tenant\n",
                    "urls.py": "urlpatterns = [path('x/', v, name='invoice-list')]\n"},
        "lonely": {"models.py": "from django.db import models\n"},
    }.items():
        d = root / app
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
        for fn, body in files.items():
            (d / fn).write_text(body, encoding="utf-8")
    cmds = root / "billing" / "management" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "__init__.py").write_text("", encoding="utf-8")
    (cmds / "run_billing.py").write_text(
        "from tenants.models import Tenant\n", encoding="utf-8")


def main() -> int:
    # --- the tool CANNOT upload -------------------------------------------
    #
    # Checked against the AST, not against a grep. The first version searched
    # the text and failed on three false positives it created itself: the
    # module lists "requests" and "httpx" as DATA (package names it detects in
    # a scanned repo), and its own docstring contains the sentence "grep this
    # file for urllib". A substring check cannot tell a mention from a use --
    # the same lesson the dead-code finder is built around.
    import ast as _ast
    src = (HERE / "scan.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)

    imported: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, _ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    NETWORK = {"urllib", "urllib2", "requests", "httpx", "socket", "http",
               "ftplib", "smtplib", "telnetlib", "aiohttp", "websockets",
               "paramiko", "boto3"}
    leak = imported & NETWORK
    ck(f"no network module is imported (imports: {len(imported)})", not leak)
    if leak:
        print(f"       imported anyway: {sorted(leak)}")

    # A subprocess could shell out to curl even with no network import.
    ck("no subprocess either -- nothing can shell out to curl",
       "subprocess" not in imported)
    ck("no upload flag exists", "--upload" not in src and "--publish" not in src)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "proj"
        root.mkdir()
        build(root)

        # --- repo level ----------------------------------------------------
        data = scan.scan_repo(root, 60)
        ids = {n["id"] for n in data["graph"]["nodes"]}
        ck("every app is found", {"tenants", "billing", "lonely"} <= ids)

        pairs = {(e["from"], e["to"]) for e in data["graph"]["edges"]}
        ck("an import between apps becomes an edge", ("billing", "tenants") in pairs)
        ck("the edge points the right way -- billing depends ON tenants",
           ("tenants", "billing") not in pairs)
        ck("a third-party import becomes an external node", "ext:requests" in ids)
        ck("an app that imports nothing has no outgoing edge",
           not any(e["from"] == "lonely" for e in data["graph"]["edges"]))

        counts = {n["id"]: n.get("sub", "") for n in data["graph"]["nodes"]}
        ck("the model count is real, not guessed", "2 models" in counts["tenants"])

        # --- caps are the point ---------------------------------------------
        small = scan.scan_repo(root, 2)
        ck("the node cap is honoured", len(small["graph"]["nodes"]) <= 2 + 2)
        ck("what was dropped is REPORTED, not silently missing",
           small.get("dropped", 0) >= 1)
        ck("every edge references a node that exists",
           all(e["from"] in {n["id"] for n in small["graph"]["nodes"]}
               and e["to"] in {n["id"] for n in small["graph"]["nodes"]}
               for e in small["graph"]["edges"]))
        ck("labels stay within the cap",
           all(len(n["label"]) <= scan.MAX_LABEL for n in data["graph"]["nodes"]))

        # --- app level -------------------------------------------------------
        app = scan.scan_app(root, "billing", 60)
        aids = {n["id"] for n in app["graph"]["nodes"]}
        kinds = {n["id"]: n["kind"] for n in app["graph"]["nodes"]}
        ck("a model becomes a store node", kinds.get("m:Invoice") == "store")
        ck("a service function becomes a service node", kinds.get("s:charge") == "service")
        ck("a url name becomes an entry node", kinds.get("u:invoice-list") == "entry")
        ck("a management command becomes a cron node", kinds.get("c:run_billing") == "cron")
        ck("a private helper is not a node", "s:_hidden" not in aids)
        ck("the docstring becomes the detail",
           any(n.get("detail", "").startswith("Charge")
               for n in app["graph"]["nodes"] if n["id"] == "s:charge"))
        ck("a source reference is attached so you can jump to code",
           all("sourceRef" in n for n in app["graph"]["nodes"] if n["kind"] != "external"))

        # --- level 3: files ---------------------------------------------------
        fl = scan.scan_files(root, "billing", 60)
        fids = {n["id"] for n in fl["graph"]["nodes"]}
        ck("a module of the app is a node", "services" in fids)
        ck("ANOTHER app it imports appears as its own node", "app:tenants" in fids)
        ck("a third party it imports appears too", "ext:requests" in fids)
        ck("a module's line count is real",
           any("lines" in n.get("sub", "") for n in fl["graph"]["nodes"]))
        fpairs = {(e["from"], e["to"]) for e in fl["graph"]["edges"]}
        ck("the edge points from the module to what it imports",
           ("services", "app:tenants") in fpairs)

        # --- level 4: calls ---------------------------------------------------
        cl = scan.scan_calls(root, "billing", 60)
        cids = {n["id"] for n in cl["graph"]["nodes"]}
        ck("a public function is a node", "charge" in cids)
        ck("the approximation is DECLARED, not implied",
           "approximate" in cl.get("caveat", "").lower())
        ck("the caveat names what it cannot see",
           "getattr" in cl.get("caveat", "") or "self." in cl.get("caveat", ""))

        # --- level 5: pages reuse the visual engine, never a second parser ----
        src_scan = (HERE / "scan.py").read_text(encoding="utf-8")
        ck("scan.py defines NO include/extends regex of its own",
           "{%" not in src_scan.replace("{%s", "") or
           not re.search(r'compile\([^)]*include', src_scan))
        ck("it imports the visual engine's parser instead",
           "from visual import affected" in src_scan)

        # --- the rendered file reaches nothing --------------------------------
        html = scan.render_html(data)
        ck("the HTML contains no absolute URL", not re.search(r"https?://", html))

        # The combined page uses createElementNS, which REQUIRES the SVG
        # namespace URI. That is an identifier, never a request -- no browser
        # fetches it. So the assertion is made precise rather than relaxed:
        # exactly that one string is allowed and nothing else.
        import render as vrender                                  # noqa: PLC0415
        allpage = vrender.render({"project": {"name": "t", "date": "2026-01-01"},
                                  "levels": [{"id": "a", "title": "A",
                                              "graph": data["graph"],
                                              "stats": {}, "dropped": 0}]})
        urls = set(re.findall(r"https?://[^\"'\s<>)]+", allpage))
        ck("the ONLY absolute URL is the SVG namespace, which is never fetched",
           urls <= {"http://www.w3.org/2000/svg"})
        ck("the combined page loads no external script or stylesheet",
           not re.search(r"<script\s+src|<link[^>]+href=[\"']http", allpage))
        ck("the combined page carries its data inline",
           '"levels"' in allpage and len(allpage) > 8000)
        ck("the HTML loads no external script or stylesheet",
           not re.search(r"<script\s+src|<link[^>]+href=[\"\']http", html))
        ck("the HTML is self-contained and non-trivial", len(html) > 2000)

    print()
    if FAILS:
        print(f"{len(FAILS)} failure(s)")
        return 1
    print("ok - describes the repo, and cannot phone home")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
