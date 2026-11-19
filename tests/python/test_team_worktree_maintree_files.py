"""Tests for adding and reading worktree and main-tree files in Team.

Verifies:
1. Adding worktree files (absolute & relative paths) into Team Files succeeds.
2. Adding main-tree (clone) files into Team Files succeeds.
3. Adding worktree folders into Team Files succeeds and pins folder tree.
4. GET /sessions/{sid}/assets returns worktree and main-tree files with exists=True.
5. GET /files/raw serves worktree and main-tree files correctly.
6. Pinned worktree and main-tree files are loaded into _team_docs_block for member turns.
7. Mentioned file paths in chat messages from worktree/main-tree are auto-loaded into prompt.
8. Unauthorized outside files (e.g. /etc/passwd) are rejected with 400.
"""
import os
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-wt-state-")
BR = Path(tempfile.mkdtemp(prefix="javis-team-wt-brain-"))
(BR / "agents").mkdir()
(BR / "agents" / "lan.md").write_text("---\nname: Lan\nrole: Thành viên\n---\nBạn là Lan.\n", encoding="utf-8")

# Create mock worktree and maintree (clone)
WORKTREE = Path(tempfile.mkdtemp(prefix="javis-team-wt-worktree-"))
MAINTREE = Path(tempfile.mkdtemp(prefix="javis-team-wt-maintree-"))

(WORKTREE / "docs" / "reports").mkdir(parents=True)
report_file = WORKTREE / "docs" / "reports" / "artifact_nghiem_thu_chang_1.md"
report_content = "# Báo cáo nghiệm thu chặng 1\nSHA: f7acf208636d\nKết quả test: 15 passed\nStatus: ĐẠT\n"
report_file.write_text(report_content, encoding="utf-8")

(MAINTREE / "README.md").write_text("# Main-tree README from origin/main\n", encoding="utf-8")

import main
from starlette.testclient import TestClient

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(extra)[:300] + "]") if extra and not cond else ""))
    if not cond:
        fails.append(name)


async def _quiet(ev):
    return None

main._CHAT_RUNTIME.publish = _quiet
main._TEAM_API.deps.spawn = lambda job: job.close() if hasattr(job, "close") else None
main.cfgmod.gate_active = lambda: False
client = TestClient(main.app, base_url="http://127.0.0.1")
H = {"origin": "http://127.0.0.1"}
b = str(BR)

# Create a team
res = client.post("/teams", params={"brain": b}, headers=H, json={
    "name": "Team Worktree Test",
    "description": "Testing worktree files in team",
    "members": ["lan"],
    "leader_slug": "lan",
    "client_req_id": "r_wt1"
})
check("team created successfully", res.status_code == 200, res.text)
tid = res.json()["team"]["team_id"]
sid = res.json()["team"]["session_id"]

# Attach mock worktree and maintree to this team's repo trees
store = main.get_store()
# Register repos for session
orig_team_repo_trees = main._team_repo_trees
main._team_repo_trees = lambda team, root: ([str(WORKTREE)], [str(MAINTREE)])

try:
    # 1. Reject unauthorized outside file
    r_bad = client.post(f"/teams/{tid}/files", params={"brain": b}, headers=H,
                        json={"attachments": [{"path": "/etc/passwd", "name": "passwd"}]})
    check("unauthorized outside file is rejected with 400", r_bad.status_code == 400, r_bad.text)

    # 2. Add worktree file by absolute path
    r_wt = client.post(f"/teams/{tid}/files", params={"brain": b}, headers=H,
                       json={"attachments": [{"path": str(report_file), "name": "artifact_nghiem_thu_chang_1.md"}]})
    check("worktree file added successfully to team files", r_wt.status_code == 200 and len(r_wt.json().get("files", [])) == 1, r_wt.text)

    # 3. Add maintree file
    r_mt = client.post(f"/teams/{tid}/files", params={"brain": b}, headers=H,
                       json={"attachments": [{"path": str(MAINTREE / "README.md"), "name": "README.md"}]})
    check("main-tree file added successfully to team files", r_mt.status_code == 200, r_mt.text)

    # 4. Add worktree folder
    r_dir = client.post(f"/teams/{tid}/files", params={"brain": b}, headers=H,
                        json={"attachments": [{"path": str(WORKTREE / "docs"), "name": "docs"}]})
    check("worktree folder added successfully to team files", r_dir.status_code == 200, r_dir.text)

    # 5. Check GET /sessions/{sid}/assets
    assets = client.get(f"/sessions/{sid}/assets", params={"brain": b}).json()
    asset_files = {f["name"]: f for f in assets.get("files", [])}
    check("worktree file exists in assets", "artifact_nghiem_thu_chang_1.md" in asset_files and asset_files["artifact_nghiem_thu_chang_1.md"]["exists"], asset_files)
    check("maintree file exists in assets", "README.md" in asset_files and asset_files["README.md"]["exists"], asset_files)
    check("worktree folder exists in assets", "docs" in asset_files and asset_files["docs"]["exists"] and asset_files["docs"]["dir"], asset_files)

    # 6. Check GET /files/raw
    r_raw_wt = client.get("/files/raw", params={"brain": b, "path": str(report_file), "team_id": tid})
    check("/files/raw serves worktree file inline", r_raw_wt.status_code == 200 and "Báo cáo nghiệm thu chặng 1" in r_raw_wt.text, r_raw_wt.status_code)
    r_raw_mt = client.get("/files/raw", params={"brain": b, "path": str(MAINTREE / "README.md"), "team_id": tid})
    check("/files/raw serves main-tree file inline", r_raw_mt.status_code == 200 and "origin/main" in r_raw_mt.text, r_raw_mt.status_code)

    # 7. Check _team_docs_block includes pinned worktree & maintree files
    team_data = main._TEAM_API.teams().get(tid)
    docs_block = main._team_docs_block(team_data)
    check("_team_docs_block contains worktree file content", "Báo cáo nghiệm thu chặng 1" in docs_block and "f7acf208636d" in docs_block, docs_block[:400])
    check("_team_docs_block contains maintree file content", "origin/main" in docs_block, docs_block[:400])
    check("_team_docs_block contains folder tree", "reports/" in docs_block or "artifact_nghiem_thu_chang_1.md" in docs_block, docs_block)

    # 8. Check auto-reading of mentioned file path in chat message (even if another file not in team docs)
    extra_report = WORKTREE / "docs" / "extra_check.md"
    extra_report.write_text("SHA: abc123456\nSTATUS: VERIFIED\n", encoding="utf-8")
    
    # Message mentioning the path
    msg_text = f"Kiểm tra cho anh file báo cáo {str(extra_report)}"
    block_with_mention = main._team_docs_block(team_data, extra_text=msg_text)
    check("_team_docs_block auto-loads mentioned file in message", "extra_check.md" in block_with_mention and "abc123456" in block_with_mention and "VERIFIED" in block_with_mention, block_with_mention[-500:])

    # 9. Test relative path in worktree mention: e.g. "docs/extra_check.md"
    msg_rel = "Xem giúp em file docs/extra_check.md nhé"
    block_rel = main._team_docs_block(team_data, extra_text=msg_rel)
    check("_team_docs_block auto-loads relative path from worktree", "extra_check.md" in block_rel and "VERIFIED" in block_rel, block_rel[-500:])

    # 10. Test sending a message with worktree file attachment via /teams/{tid}/messages
    r_msg = client.post(f"/teams/{tid}/messages", params={"brain": b}, headers=H, json={
        "text": "Gửi báo cáo nghiệm thu đính kèm",
        "mentions": [],
        "as_job": False,
        "client_msg_id": "c_wt_att",
        "attachments": [{"path": str(report_file), "name": "artifact_nghiem_thu_chang_1.md"}]
    })
    check("sending team message with worktree file attachment succeeds", r_msg.status_code == 200 and r_msg.json().get("ok"), r_msg.text)
    msg_obj = store.team_message(sid, r_msg.json()["message_id"])
    check("message content lists the attached worktree file", "artifact_nghiem_thu_chang_1.md" in msg_obj["content"], msg_obj["content"])

finally:
    main._team_repo_trees = orig_team_repo_trees

if fails:
    print(f"\nFAILED {len(fails)} test(s): {fails}")
    exit(1)
else:
    print("\nALL WORKTREE & MAIN-TREE TEAM FILE TESTS PASSED!")
