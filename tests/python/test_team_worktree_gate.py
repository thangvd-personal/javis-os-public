"""Test expanding team agent permissions to worktree (read & write, full access)
and main-tree (read-only) when connected to a github repo in gated mode.
"""
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-team-wt-")
TEMP = Path(tempfile.mkdtemp(prefix="javis-team-wt-temp-"))

import mcp_hub
import team_gate
import turn_context
import main
import coding_store
import github_repos

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  {str(extra)[:400]}" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def run(coro):
    return asyncio.run(coro)


def test_team_gate_roots():
    vault = TEMP / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    worktree = TEMP / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    maintree = TEMP / "maintree"
    maintree.mkdir(parents=True, exist_ok=True)
    outside = TEMP / "outside"
    outside.mkdir(parents=True, exist_ok=True)

    g = team_gate.open_gate(None, "team-1", "task-1", "step-1")
    team_gate.set_roots(g.gate_id, [str(worktree), str(vault)], readonly_roots=[str(maintree)])

    check("team_gate has worktree in roots", str(worktree.resolve()) in g.roots)
    check("team_gate has vault in roots", str(vault.resolve()) in g.roots)
    check("team_gate has maintree in readonly_roots", str(maintree.resolve()) in g.readonly_roots)
    check("maintree is not in writable roots", str(maintree.resolve()) not in g.roots)

    # Native tools classification
    # Write to worktree -> free
    cls_wt_write = team_gate.classify_native("Write", {"file_path": str(worktree / "test.py")}, g.roots, g.readonly_roots)
    check("native Write in worktree is free", cls_wt_write == "free", f"got {cls_wt_write}")

    # Edit in worktree -> free
    cls_wt_edit = team_gate.classify_native("Edit", {"file_path": str(worktree / "test.py")}, g.roots, g.readonly_roots)
    check("native Edit in worktree is free", cls_wt_edit == "free", f"got {cls_wt_edit}")

    # Write to vault -> free
    cls_vault_write = team_gate.classify_native("Write", {"file_path": str(vault / "note.md")}, g.roots, g.readonly_roots)
    check("native Write in vault is free", cls_vault_write == "free", f"got {cls_vault_write}")

    # Write to maintree -> outside (must be denied / needs approval)
    cls_mt_write = team_gate.classify_native("Write", {"file_path": str(maintree / "index.js")}, g.roots, g.readonly_roots)
    check("native Write in maintree is outside (read-only)", cls_mt_write == "outside", f"got {cls_mt_write}")

    # Edit in maintree -> outside (must be denied)
    cls_mt_edit = team_gate.classify_native("Edit", {"file_path": str(maintree / "index.js")}, g.roots, g.readonly_roots)
    check("native Edit in maintree is outside (read-only)", cls_mt_edit == "outside", f"got {cls_mt_edit}")

    # Write outside -> outside
    cls_out_write = team_gate.classify_native("Write", {"file_path": str(outside / "evil.sh")}, g.roots, g.readonly_roots)
    check("native Write outside is outside", cls_out_write == "outside", f"got {cls_out_write}")

    # Native Read in maintree -> free
    cls_mt_read = team_gate.classify_native("Read", {"file_path": str(maintree / "index.js")}, g.roots, g.readonly_roots)
    check("native Read in maintree is free", cls_mt_read == "free", f"got {cls_mt_read}")

    # Native Read in worktree -> free
    cls_wt_read = team_gate.classify_native("Read", {"file_path": str(worktree / "test.py")}, g.roots, g.readonly_roots)
    check("native Read in worktree is free", cls_wt_read == "free", f"got {cls_wt_read}")

    team_gate.close_gate(g.gate_id)


def test_mcp_hub_tools():
    vault = TEMP / "hub_vault"
    vault.mkdir(parents=True, exist_ok=True)
    worktree = TEMP / "hub_worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    maintree = TEMP / "hub_maintree"
    maintree.mkdir(parents=True, exist_ok=True)

    # Create test files
    (vault / "note.txt").write_text("Hello Vault", encoding="utf-8")
    (worktree / "code.py").write_text("print('Hello Worktree')", encoding="utf-8")
    (maintree / "main.py").write_text("print('Hello Main')", encoding="utf-8")

    # Test _safe_read_path
    p_wt = mcp_hub._safe_read_path(str(vault), "code.py", workspace_root=[str(worktree)], readonly_workspace_root=[str(maintree)])
    check("_safe_read_path finds file in worktree", p_wt.resolve() == (worktree / "code.py").resolve())

    p_mt = mcp_hub._safe_read_path(str(vault), "main.py", workspace_root=[str(worktree)], readonly_workspace_root=[str(maintree)])
    check("_safe_read_path finds file in maintree", p_mt.resolve() == (maintree / "main.py").resolve())

    p_abs_mt = mcp_hub._safe_read_path(str(vault), str(maintree / "main.py"), workspace_root=[str(worktree)], readonly_workspace_root=[str(maintree)])
    check("_safe_read_path finds absolute path in maintree", p_abs_mt.resolve() == (maintree / "main.py").resolve())

    # Test _builtin_tools with gate in HUB_MODE
    g = team_gate.open_gate(None, "team-hub", "task-hub", "step-hub")
    team_gate.set_roots(g.gate_id, [str(worktree), str(vault)], readonly_roots=[str(maintree)])

    async def _test_tools():
        tok = turn_context.bind(turn_context.make("team", chat_id="team-hub", team_gate=g.gate_id))
        try:
            tools_spec, route = mcp_hub._builtin_tools(
                team_gate.HUB_MODE, str(vault),
                workspace_root=[str(worktree)],
                readonly_workspace_root=[str(maintree)]
            )
            read_fn = route["javis_read_file"]["call"]
            write_fn = route["javis_write_file"]["call"]
            edit_fn = route["javis_edit_file"]["call"]
            ls_fn = route["javis_list_dir"]["call"]
            search_fn = route["javis_search_files"]["call"]

            # 1. Read file in worktree
            res_wt = await read_fn({"path": "code.py"})
            check("javis_read_file reads from worktree", "Hello Worktree" in res_wt, f"got {res_wt}")

            # 2. Read file in maintree
            res_mt = await read_fn({"path": "main.py"})
            check("javis_read_file reads from maintree", "Hello Main" in res_mt, f"got {res_mt}")

            # 3. List dir in maintree
            res_ls_mt = await ls_fn({"path": str(maintree)})
            check("javis_list_dir lists maintree", "main.py" in res_ls_mt, f"got {res_ls_mt}")

            # 4. Search in maintree
            res_search_mt = await search_fn({"path": str(maintree), "pattern": "Hello Main"})
            check("javis_search_files searches maintree", "main.py" in str(res_search_mt), f"got {res_search_mt}")

            # 5. Write to worktree -> succeeds freely
            res_wt_write = await write_fn({"path": str(worktree / "new_file.py"), "content": "print('new')"})
            check("javis_write_file writes to worktree freely", "Đã ghi new_file.py" in res_wt_write, f"got {res_wt_write}")
            check("worktree file was actually written", (worktree / "new_file.py").exists())

            # 6. Edit file in worktree -> succeeds freely
            res_wt_edit = await edit_fn({"path": str(worktree / "code.py"), "old_string": "Hello Worktree", "new_string": "Updated Worktree"})
            check("javis_edit_file edits worktree freely", "ERROR" not in res_wt_edit, f"got {res_wt_edit}")
            check("worktree file was actually edited", "Updated Worktree" in (worktree / "code.py").read_text())

            # 7. Write to maintree -> blocked with read-only error
            res_mt_write = await write_fn({"path": str(maintree / "main.py"), "content": "overwrite"})
            check("javis_write_file blocks writing to maintree as read-only", "chỉ đọc" in res_mt_write and "ERROR" in res_mt_write, f"got {res_mt_write}")
            check("maintree file was NOT overwritten", (maintree / "main.py").read_text() == "print('Hello Main')")

            # 8. Edit file in maintree -> blocked with read-only error
            res_mt_edit = await edit_fn({"path": str(maintree / "main.py"), "old_string": "Hello Main", "new_string": "Hacked Main"})
            check("javis_edit_file blocks editing maintree as read-only", "chỉ đọc" in res_mt_edit and "ERROR" in res_mt_edit, f"got {res_mt_edit}")
            check("maintree file was NOT modified", (maintree / "main.py").read_text() == "print('Hello Main')")

            # 9. Test automatic resolution from team_gate.current() in discover_all
            tools2, route2 = await mcp_hub.discover_all(team_gate.HUB_MODE, vault_root=str(vault))
            read_fn2 = route2["javis_read_file"]["call"]
            res_auto_mt = await read_fn2({"path": str(maintree / "main.py")})
            check("discover_all auto-resolves maintree from team_gate", "Hello Main" in res_auto_mt, f"got {res_auto_mt}")

            write_fn2 = route2["javis_write_file"]["call"]
            res_auto_mt_write = await write_fn2({"path": str(maintree / "main.py"), "content": "hacked"})
            check("discover_all auto-resolves readonly maintree and blocks write", "chỉ đọc" in res_auto_mt_write, f"got {res_auto_mt_write}")

        finally:
            turn_context.reset(tok)
            team_gate.close_gate(g.gate_id)

    run(_test_tools())


def test_main_team_repo_trees():
    d = TEMP / "team_factory_test"
    d.mkdir(parents=True, exist_ok=True)
    wt = d / "wt"
    wt.mkdir(parents=True, exist_ok=True)
    clone = d / "clone"
    clone.mkdir(parents=True, exist_ok=True)

    fake_team = {"team_id": "team-test-1", "brain_id": str(d), "session_id": "sid-test-1"}

    # Mock session_repos
    fake_repos = [{"full_name": "owner/repo1", "worktree": str(wt), "clone": str(clone), "folder_id": "f1"}]
    with patch.object(github_repos, "session_repos", return_value=fake_repos):
        with patch.object(coding_store, "thu_muc_cua_phien", return_value=[]):
            worktrees, main_trees = main._team_repo_trees(fake_team, str(d))
            check("_team_repo_trees finds worktree", str(wt.resolve()) in worktrees)
            check("_team_repo_trees finds clone as main_tree", str(clone.resolve()) in main_trees)


def main_all():
    test_team_gate_roots()
    test_mcp_hub_tools()
    test_main_team_repo_trees()

    if fails:
        print(f"\n{len(fails)} test(s) failed!")
        exit(1)
    else:
        print("\nALL WORKTREE & MAIN-TREE GATE TESTS PASSED!")


if __name__ == "__main__":
    main_all()
