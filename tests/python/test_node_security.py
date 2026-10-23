"""Node.js security floor: the version check, the News item, and the pins that must agree.

    python tests/run.py node_security      (no network)

Node.js security release of 2026-10-21 (CVE-2026-111497 Brotli RCE and 9 more) fixed 22.23.4,
24.22.1 and 26.12.1. Javis runs Claude Code, Codex and npx MCP servers on the machine's Node.js,
so three places carry that floor: the Docker image pin, install.sh and server/node_security.py.
This test keeps them in step and checks the News item only appears on an unpatched machine.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401
import asyncio
import json
import os
import re
import sys
import tempfile

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-node-sec-"))
import node_security  # noqa: E402

_fails = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  [{detail}]"))
    if not ok:
        _fails.append(name)


ns = node_security

# --- parse + classify -------------------------------------------------------------------------
check("parse_version reads `node --version` output", ns.parse_version("v22.12.0\n") == (22, 12, 0))
check("parse_version rejects garbage", ns.parse_version("command not found") is None)
cases = {
    "v18.20.4": "eol", "v20.18.0": "eol", "v22.12.0": "outdated", "v22.23.3": "outdated",
    "v22.23.4": "ok", "v22.30.0": "ok", "v23.11.0": "eol", "v24.22.0": "outdated",
    "v24.22.1": "ok", "v25.9.0": "eol", "v26.12.0": "outdated", "v26.12.1": "ok", "v27.0.0": "ok",
}
for text, want in cases.items():
    got = ns.classify(ns.parse_version(text))
    check(f"classify {text} = {want}", got == want, got)
check("no Node.js at all is not flagged", ns.classify(None) == "ok")

# --- News item ----------------------------------------------------------------------------------
check("patched machine gets no notice", ns.notice((22, 23, 4), "native") is None)
check("machine without Node.js gets no notice", ns.notice(None, "native") is None)
item = ns.notice((22, 12, 0), "windows")
check("unpatched machine gets a high-priority security notice",
      bool(item) and item["kind"] == "security" and item["priority"] == "high", item)
check("notice names the installed version", "v22.12.0" in (item or {}).get("summary", ""), item)
check("notice id is scoped under the advisory id",
      str((item or {}).get("id", "")).startswith(ns.ADVISORY_ID + ":"), item)
check("notice links the official advisory", (item or {}).get("cta", {}).get("url") == ns.ADVISORY_URL)
check("Windows notice says how to upgrade with winget", "winget" in (item or {}).get("body", ""))
docker_item = ns.notice((20, 18, 0), "docker")
check("Docker notice says to update Javis, not Node.js by hand",
      "update.sh" in (docker_item or {}).get("body", ""), docker_item)
_dash = re.compile("[–—]")
for mode in ("docker", "windows", "native"):
    for v in ((22, 1, 0), (20, 1, 0)):
        it = ns.notice(v, mode)
        check(f"no long dashes in {mode} notice for {v}", not _dash.search(json.dumps(it, ensure_ascii=False)))

# --- the three pins agree -----------------------------------------------------------------------
dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
m = re.search(r"^FROM node:(\d+)\.(\d+)\.(\d+)-", dockerfile, re.M)
pin = tuple(int(x) for x in m.groups()) if m else None
check("Dockerfile pins an exact Node.js release (a floating tag can come from a stale cache)", bool(pin), m)
check("Dockerfile Node.js pin is patched", bool(pin) and ns.classify(pin) == "ok", pin)

install = (ROOT / "install.sh").read_text(encoding="utf-8")
floors = dict(re.findall(r"(\d+)\) echo (\d+\.\d+\.\d+);;", install))
want = {str(k): ".".join(map(str, v)) for k, v in ns.PATCHED.items()}
check("install.sh node_floor matches node_security.PATCHED", floors == want, (floors, want))

ann = json.loads((ROOT / "ANNOUNCEMENTS.json").read_text(encoding="utf-8"))
ids = [a.get("id") for a in ann.get("announcements", [])]
check("ANNOUNCEMENTS.json carries the broadcast for older installs", ns.ADVISORY_ID in ids, ids)

# --- the feed replaces the broadcast with the machine's own result ------------------------------
import main  # noqa: E402


async def _feed(version):
    async def fake_changelog(*a, **k):
        return {"releases": [], "latest": "0", "current": "0"}

    async def fake_announcements():
        broadcast = main._parse_announcements((ROOT / "ANNOUNCEMENTS.json").read_text(encoding="utf-8"))
        return broadcast, None

    old = (main.changelog_index, main._load_community_announcements, ns.node_version)
    main.changelog_index, main._load_community_announcements = fake_changelog, fake_announcements
    ns.node_version = lambda: version
    main._NOTIFICATION_CACHE.update({"at": 0.0, "data": None})
    try:
        return await main._notifications_data()
    finally:
        main.changelog_index, main._load_community_announcements, ns.node_version = old
        main._NOTIFICATION_CACHE.update({"at": 0.0, "data": None})


def _node_items(data):
    return [i for i in data["items"] if str(i.get("id", "")).startswith(ns.ADVISORY_ID)]


patched = _node_items(asyncio.run(_feed((22, 23, 4))))
check("patched machine: broadcast dropped, no Node.js item in News", patched == [], patched)
unpatched = _node_items(asyncio.run(_feed((22, 12, 0))))
check("unpatched machine: exactly one Node.js item, the machine-specific one",
      len(unpatched) == 1 and "v22.12.0" in unpatched[0]["summary"], unpatched)
check("parser keeps the security kind", main._parse_announcements(json.dumps(
    {"announcements": [{"id": "x", "title": "t", "kind": "security"}]}))[0]["kind"] == "security")

print()
if _fails:
    print(f"{len(_fails)} test FAILED: " + ", ".join(_fails))
    sys.exit(1)
print("All node_security tests passed.")
