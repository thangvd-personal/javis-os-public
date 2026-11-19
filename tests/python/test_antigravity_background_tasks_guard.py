"""Tests for Antigravity CLI background tasks guard & idle hang prevention.

Verifies:
1. _la_thong_bao_nen_vo_hai identifies root agent idle / background task waiting messages.
2. _doi_su_kien ignores exit 0 even with stderr output, and filters out harmless background task waiting notices.
3. aux_engine._build_antigravity injects background task cleanup instructions.
4. team_runner.WORK_TOOLS_NOTE contains background task cleanup rules.
"""
import sys
from unittest.mock import MagicMock
from _paths import ROOT, SERVER  # noqa: F401

import antigravity_cli
import aux_engine
import team_runner

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(extra)[:300] + "]") if extra and not cond else ""))
    if not cond:
        fails.append(name)


# 1. _la_thong_bao_nen_vo_hai
check("_la_thong_bao_nen_vo_hai matches standard agy idle background waiting message",
      antigravity_cli._la_thong_bao_nen_vo_hai("root agent idle; waiting up to 720h0m0s for 2 background task(s)") is True)

check("_la_thong_bao_nen_vo_hai matches root agent idle substring",
      antigravity_cli._la_thong_bao_nen_vo_hai("root agent idle") is True)

check("_la_thong_bao_nen_vo_hai matches background task(s) substring",
      antigravity_cli._la_thong_bao_nen_vo_hai("waiting up to 10s for 1 background task(s)") is True)

check("_la_thong_bao_nen_vo_hai rejects real errors",
      antigravity_cli._la_thong_bao_nen_vo_hai("fatal error: server connection refused") is False)

check("_la_thong_bao_nen_vo_hai rejects empty string",
      antigravity_cli._la_thong_bao_nen_vo_hai("") is False)


# 2. _doi_su_kien behavior on exit and stderr
cli = antigravity_cli.AntigravityCLI()

# Exit code 0 with diagnostic stderr should not produce an error event
res_exit_0_stderr = cli._doi_su_kien({"_exit": 0, "_err": "root agent idle; waiting up to 720h0m0s for 2 background task(s)"}, [])
check("_doi_su_kien ignores exit code 0 with agy idle stderr",
      res_exit_0_stderr == [], res_exit_0_stderr)

res_exit_0_warning = cli._doi_su_kien({"_exit": 0, "_err": "DeprecationWarning: Node.js 20 is deprecated"}, [])
check("_doi_su_kien ignores exit code 0 with generic stderr warning",
      res_exit_0_warning == [], res_exit_0_warning)

# Exit code != 0 but error message is purely background task idle waiting should not produce an error event
res_exit_err_idle = cli._doi_su_kien({"_exit": -15, "_err": "root agent idle; waiting up to 720h0m0s for 2 background task(s)"}, [])
check("_doi_su_kien suppresses error event when killed during agy idle background wait",
      res_exit_err_idle == [], res_exit_err_idle)

# Real error on exit != 0 should still produce an error event
res_real_error = cli._doi_su_kien({"_exit": 1, "_err": "command not found: foobar"}, [])
check("_doi_su_kien emits error event for genuine command failure",
      len(res_real_error) == 1 and res_real_error[0].get("type") == "error", res_real_error)


# 3. aux_engine._build_antigravity prompt instruction injection
base = MagicMock()
base.cwd = "/tmp"
base.system_prompt = "You are an E2E testing agent."
base.javis_mode = "full"

agy_engine = aux_engine._build_antigravity({"provider": "antigravity-cli", "model": "gemini-3.8-flash-medium"}, base, "full", "tag-test")
check("aux_engine._build_antigravity injects task cleanup instructions",
      "QUY TẮC QUẢN LÝ TÁC VỤ NỀN CHO ANTIGRAVITY" in (agy_engine.instructions or ""),
      agy_engine.instructions)
check("aux_engine._build_antigravity preserves original system prompt",
      "You are an E2E testing agent." in (agy_engine.instructions or ""),
      agy_engine.instructions)


# 4. team_runner.WORK_TOOLS_NOTE contains task cleanup guidance
check("team_runner.WORK_TOOLS_NOTE mentions background task cleanup (manage_task kill)",
      "schedule, run_command async" in team_runner.WORK_TOOLS_NOTE and "huỷ (kill)" in team_runner.WORK_TOOLS_NOTE,
      team_runner.WORK_TOOLS_NOTE)


if fails:
    raise SystemExit(f"{len(fails)} checks failed: {fails}")
