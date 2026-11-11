"""The GitHub backup carries only the primary account's brains, never `.users/` (multi-user).

Reported in docs/dev/multi-user-QA.md 13.4: the backup synced all of BRAINS_DIR, including `.users/<uuid>/`, where
the other accounts' brains live. Every member's notes went to the admin's repo, and a restore could write them back.

Law after the fix:
  1. A file under `.users/` is out of scope for the backup: not pushed, not pruned, not applied back.
  2. The primary account's brains are backed up as before.

Run: python tests/run.py backup_skips_other_accounts (needs git, uses a local file:// remote, no network).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401
import git_brain as gb

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


skip = gb._backup_skip
check("a member's brain file is skipped", skip(".users/3d8e77f3-ec19-4117-bf7e-e48acb74744f/Shop/wiki/note.md"))
check("the primary account's note is still backed up", not skip("Shop/wiki/note.md"))
check("a brain merely named users is still backed up", not skip("users/wiki/note.md"))

if not gb.has_git():
    print("SKIP integration: git is not installed")
    sys.exit(1 if _fails else 0)

TMP = Path(tempfile.mkdtemp(prefix="jv-users-out-")).resolve()
REMOTE = TMP / "remote.git"
subprocess.run(["git", "init", "--bare", str(REMOTE)], capture_output=True)
URL = "file:///" + str(REMOTE).replace("\\", "/").lstrip("/")
B, M, T = TMP / "brains", TMP / "mirror", TMP / "trash"
MEMBER = ".users/3d8e77f3-ec19-4117-bf7e-e48acb74744f/Bob Brain/wiki/secret.md"


def w(rel, text):
    p = B / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def remote_files():
    r = subprocess.run(["git", "--git-dir", str(REMOTE), "ls-tree", "-r", "--name-only", "main"],
                       capture_output=True, text=True)
    return set((r.stdout or "").splitlines())


w("Shop/wiki/note.md", "admin knowledge")
w(MEMBER, "bob's private note")
r = gb.sync_brains(str(B), str(M), URL, "x", "main", trash_dir=str(T), protected_names=set())
files = remote_files()
check("sync ok", bool(r.get("ok") and r.get("pushed")))
check("the admin's note is pushed", "Shop/wiki/note.md" in files)
check("no member file reaches the repo", not any(f.startswith(".users/") for f in files))
check("the member's file is untouched locally", (B / MEMBER).read_text(encoding="utf-8") == "bob's private note")

# A second sync after the member edits their note: still nothing goes up, and the local file is not replaced.
w(MEMBER, "bob's note, edited")
r = gb.sync_brains(str(B), str(M), URL, "x", "main", trash_dir=str(T), protected_names=set())
check("second sync ok", bool(r.get("ok")))
check("still no member file in the repo", not any(f.startswith(".users/") for f in remote_files()))
check("the member's edit stays", (B / MEMBER).read_text(encoding="utf-8") == "bob's note, edited")

print()
if _fails:
    print(f"{len(_fails)} FAIL: " + ", ".join(_fails))
    sys.exit(1)
print("ALL PASS")
