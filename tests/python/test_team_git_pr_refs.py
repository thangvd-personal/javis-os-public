"""Tests for resolving git PR refs and authenticated HEAD verification in Team.

Verifies:
1. github_repos.prompt_block includes the authenticated HEAD SHA of the PR and instructions.
2. GET /teams/{tid}/tree returns pr_number, pr_url, and head_sha.
3. POST /teams/{tid}/git/sync (pull) fetches +refs/pull/*/head:refs/remotes/origin/pr/* into local git.
4. Agents can inspect PR HEAD offline using git rev-parse origin/pr/<pr_number> without exit 128.
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-pr-state-")
BR = Path(tempfile.mkdtemp(prefix="javis-pr-brain-"))
(BR / "agents").mkdir()
(BR / "agents" / "devops.md").write_text("---\nname: DevOps\nrole: Kỹ sư DevOps\n---\nBạn là DevOps.\n", encoding="utf-8")

# Create mock bare origin and mock clone/worktree
BARE_DIR = Path(tempfile.mkdtemp(prefix="mock-bare-"))
CLONE_DIR = Path(tempfile.mkdtemp(prefix="mock-clone-"))
WT_DIR = Path(tempfile.mkdtemp(prefix="mock-wt-"))

# Init bare repo
os.system(f"git init --bare {BARE_DIR} > /dev/null 2>&1")
# Clone from bare
os.system(f"git clone {BARE_DIR} {CLONE_DIR} > /dev/null 2>&1")
os.system(f"cd {CLONE_DIR} && git config user.name 'Test' && git config user.email 'test@test.com'")
(CLONE_DIR / "README.md").write_text("# Initial Commit\n", encoding="utf-8")
os.system(f"cd {CLONE_DIR} && git add README.md && git commit -m 'Initial commit' > /dev/null 2>&1")
os.system(f"cd {CLONE_DIR} && git branch -M main && git push origin main > /dev/null 2>&1")

# Create a PR branch and commit
os.system(f"cd {CLONE_DIR} && git checkout -b feature/pr1 > /dev/null 2>&1")
(CLONE_DIR / "feature.txt").write_text("Feature 1\n", encoding="utf-8")
os.system(f"cd {CLONE_DIR} && git add feature.txt && git commit -m 'Feature 1 commit' > /dev/null 2>&1")
pr_sha = os.popen(f"cd {CLONE_DIR} && git rev-parse HEAD").read().strip()
os.system(f"cd {CLONE_DIR} && git push origin feature/pr1 > /dev/null 2>&1")

# Push the PR ref to bare repo to simulate GitHub PR ref: refs/pull/1/head
os.system(f"cd {CLONE_DIR} && git push origin feature/pr1:refs/pull/1/head > /dev/null 2>&1")

# Init worktree from main
os.system(f"cd {CLONE_DIR} && git checkout main > /dev/null 2>&1")
os.system(f"git clone {BARE_DIR} {WT_DIR} > /dev/null 2>&1")
os.system(f"cd {WT_DIR} && git config user.name 'Test' && git config user.email 'test@test.com'")

import main
import github_work
import github_repos
from starlette.testclient import TestClient

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(extra)[:300] + "]") if extra and not cond else ""))
    if not cond:
        fails.append(name)


async def _quiet(ev):
    return None

main._CHAT_RUNTIME.publish = _quiet
main.cfgmod.gate_active = lambda: False
client = TestClient(main.app, base_url="http://127.0.0.1")
H = {"origin": "http://127.0.0.1"}
b = str(BR)

# Create team
res = client.post("/teams", params={"brain": b}, headers=H, json={
    "name": "Team PR Test",
    "description": "Testing PR refs in team",
    "members": ["devops"],
    "leader_slug": "devops",
    "client_req_id": "r_pr1"
})
check("team created successfully", res.status_code == 200, res.text)
tid = res.json()["team"]["team_id"]
sid = res.json()["team"]["session_id"]

# Setup github_work row with PR #1 and pr_sha
github_work.st().ensure(sid, "acme/repo",
    branch="feature/pr1",
    worktree=str(WT_DIR),
    base_branch="main",
    head_sha=pr_sha,
    pr_number=1,
    pr_url="https://github.com/acme/repo/pull/1",
    ci="no_ci",
    integ="ready"
)

# 1. Test _work_state_lines & prompt_block
fake_repos = [{"full_name": "acme/repo", "worktree": str(WT_DIR), "branch": "feature/pr1"}]
lines = github_repos._work_state_lines(sid, fake_repos)
state_text = "\n".join(lines)
check("work_state_lines includes HEAD SHA trên GitHub", pr_sha in state_text, state_text)
check("work_state_lines includes PR #1 url", "https://github.com/acme/repo/pull/1" in state_text, state_text)

# 2. Test GET /teams/{tid}/tree returns PR info
# Mock session_repos to return acme/repo
orig_session_repos = github_repos.session_repos
github_repos.session_repos = lambda s, r: [{
    "full_name": "acme/repo",
    "repo_id": "r1",
    "clone": str(CLONE_DIR),
    "worktree": str(WT_DIR),
    "branch": "feature/pr1",
    "default_branch": "main",
    "clone_url": str(BARE_DIR),
}]

tree_res = client.get(f"/teams/{tid}/tree?kind=worktree", params={"brain": b}, headers=H)
check("teams_tree returns 200", tree_res.status_code == 200, tree_res.text)
tree_json = tree_res.json()
check("teams_tree returns pr_number 1", tree_json.get("pr_number") == 1, str(tree_json))
check("teams_tree returns head_sha", tree_json.get("head_sha") == pr_sha, str(tree_json))

# 3. Test POST /teams/{tid}/git/sync (pull) fetches PR refs
sync_res = client.post(f"/teams/{tid}/git/sync", params={"brain": b}, headers=H, json={
    "action": "pull",
    "kind": "worktree",
    "repo": "acme/repo"
})
check("teams_git_sync pull succeeds", sync_res.status_code == 200 and sync_res.json().get("ok"), sync_res.text)

# 4. Verify local git has origin/pr/1
rev = os.popen(f"cd {WT_DIR} && git rev-parse origin/pr/1 2>/dev/null").read().strip()
check("local git resolved origin/pr/1 to exact PR SHA", rev == pr_sha, f"expected {pr_sha}, got {rev}")

# 5. Test javis_github plugin tool with status and pr
import importlib.util
gh_spec = importlib.util.spec_from_file_location("javis_github_plugin", ROOT / "system/plugins/javis-github/plugin.py")
gh_plug = importlib.util.module_from_spec(gh_spec)
gh_spec.loader.exec_module(gh_plug)

class MockCtx:
    vault_root = str(BR)

# Tool status using raw sid
stat_out = gh_plug._chay({"op": "status", "chat_id": sid}, MockCtx())
check("javis_github status returns HEAD sha with raw sid", pr_sha in stat_out, stat_out)

# Tool status using repo name without chat_id
stat_repo_out = gh_plug._chay({"op": "status", "repo": "acme/repo"}, MockCtx())
check("javis_github status auto-resolves with repo name", pr_sha in stat_repo_out, stat_repo_out)

# Tool op=pr returns json with head_sha
pr_json_out = gh_plug._chay({"op": "pr", "repo": "acme/repo"}, MockCtx())
check("javis_github op=pr returns json containing head_sha", f'"head_sha": "{pr_sha}"' in pr_json_out, pr_json_out)

github_repos.session_repos = orig_session_repos

if fails:
    print(f"\nFAILED {len(fails)} checks: {fails}")
    exit(1)
else:
    print("\nALL PR REF & HEAD SHA TESTS PASSED!")
