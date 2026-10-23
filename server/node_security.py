"""Warn the owner when this machine's Node.js misses the latest Node.js security release.

Javis runs Claude Code, Codex and every npx-based MCP server on the machine's own Node.js, so a
Node.js vulnerability reaches all of them. The Docker image pins a patched release; a native or
Windows install uses whatever Node.js the owner installed, and Javis cannot upgrade it for them
(that needs sudo, Homebrew or winget). So this module compares `node --version` with the first
patched release of each supported line and builds ONE News item that says what to do.

When Node.js ships the next security release: update `PATCHED`, `ADVISORY_*` and the Dockerfile
pin together (tests/python/test_node_security.py checks they agree).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time

import localefmt
from winproc import kwargs_no_window

# Node.js security release of 2026-10-21: 10 CVEs, among them CVE-2026-111497 (Brotli decoder
# out-of-bounds write, remote code execution through fetch() and zlib).
ADVISORY_ID = "security-nodejs-2026-10"
ADVISORY_DATE = "2026-10-21"
ADVISORY_URL = "https://nodejs.org/en/blog/vulnerability/october-2026-security-releases"

# First patched release of every line still supported. Older majors and odd (non-LTS) majors
# below the newest known line are end of life and get no fix at all.
PATCHED = {22: (22, 23, 4), 24: (24, 22, 1), 26: (26, 12, 1)}

_CACHE_TTL = 600
_cache = {"at": 0.0, "version": None}


def parse_version(text: str):
    """'v22.12.0' -> (22, 12, 0). None for anything that is not a Node.js version line."""
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def classify(version) -> str:
    """ok | outdated (supported line, patch missing) | eol (line gets no fix)."""
    if not version:
        return "ok"
    major = version[0]
    if major > max(PATCHED):
        return "ok"
    floor = PATCHED.get(major)
    if floor is None:
        return "eol"
    return "ok" if tuple(version) >= floor else "outdated"


def node_version():
    """Version of the `node` on PATH, cached 10 minutes. None when Node.js is absent or silent.

    Blocking (subprocess): call it through asyncio.to_thread from the event loop."""
    now = time.monotonic()
    if _cache["at"] and now - _cache["at"] < _CACHE_TTL:
        return _cache["version"]
    version = None
    node = shutil.which("node")
    if node:
        try:
            out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5,
                                 **kwargs_no_window())
            version = parse_version(out.stdout)
        except (OSError, subprocess.SubprocessError):
            version = None
    _cache.update({"at": now, "version": version})
    return version


def _fix_steps(mode: str) -> str:
    if mode == "docker":
        return localefmt.chu(
            "Cập nhật Javis lên bản mới nhất (trang Cài đặt, mục Cập nhật, hoặc chạy ./update.sh trên VPS). "
            "Ảnh Docker mới đã có Node.js 22.23.4.",
            "Update Javis to the latest version (Settings, Updates, or ./update.sh on the VPS). "
            "The new Docker image ships Node.js 22.23.4.")
    if mode == "windows":
        return localefmt.chu(
            "Mở PowerShell và chạy: winget upgrade OpenJS.NodeJS.LTS (hoặc tải bản LTS ở nodejs.org). "
            "Xong thì khởi động lại Javis.",
            "Open PowerShell and run: winget upgrade OpenJS.NodeJS.LTS (or download the LTS from nodejs.org). "
            "Then restart Javis.")
    return localefmt.chu(
        "Cài Node.js 22.23.4, 24.22.1, 26.12.1 hoặc mới hơn, ví dụ: brew upgrade node (Mac), "
        "sudo apt-get install --only-upgrade nodejs (Ubuntu dùng NodeSource), nvm install 22 (nvm). "
        "Hoặc chạy lại install.sh. Xong thì khởi động lại Javis.",
        "Install Node.js 22.23.4, 24.22.1, 26.12.1 or newer, for example: brew upgrade node (Mac), "
        "sudo apt-get install --only-upgrade nodejs (Ubuntu with NodeSource), nvm install 22 (nvm). "
        "Or run install.sh again. Then restart Javis.")


def notice(version, mode: str):
    """The News item for this machine, or None when its Node.js is patched or absent."""
    status = classify(version)
    if status == "ok":
        return None
    shown = "v" + ".".join(str(x) for x in version)
    if status == "eol":
        summary = localefmt.chu(
            "Máy đang chạy Node.js {v}. Dòng này đã hết hỗ trợ nên không có bản vá cho 10 lỗ hổng "
            "công bố ngày 21/10/2026, trong đó có lỗi Brotli cho phép chạy mã từ xa.",
            "This machine runs Node.js {v}. That line is end of life and gets no fix for the 10 "
            "vulnerabilities published on 2026-10-21, including a Brotli flaw that allows remote code execution.",
            v=shown)
    else:
        summary = localefmt.chu(
            "Máy đang chạy Node.js {v}, chưa có bản vá ngày 21/10/2026 cho 10 lỗ hổng, trong đó có "
            "lỗi Brotli cho phép chạy mã từ xa.",
            "This machine runs Node.js {v}, without the 2026-10-21 fix for 10 vulnerabilities, "
            "including a Brotli flaw that allows remote code execution.",
            v=shown)
    body = localefmt.chu(
        "Claude Code, Codex và các MCP chạy bằng npx đều dùng Node.js này. ",
        "Claude Code, Codex and npx-based MCP servers all run on this Node.js. ") + _fix_steps(mode)
    return {
        "id": f"{ADVISORY_ID}:{shown}",
        "kind": "security",
        "title": localefmt.chu("Node.js trên máy cần cập nhật bản vá bảo mật",
                               "Node.js on this machine needs a security update"),
        "summary": summary,
        "body": body,
        "published_at": ADVISORY_DATE,
        "expires_at": "",
        "priority": "high",
        "cta": {"label": localefmt.chu("Xem thông báo của Node.js →", "Read the Node.js advisory →"),
                "url": ADVISORY_URL},
    }
