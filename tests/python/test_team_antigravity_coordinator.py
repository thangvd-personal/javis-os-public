"""Tests for Antigravity CLI coordinator / text-only exception in team_engine.

Verifies:
1. text_only_supported recognizes 'antigravity-cli'.
2. choose_spec allows 'antigravity-cli' for text-only turns without forcing main model fallback.
3. team_engine.build returns an AntigravityCLI instance configured with suggest mode, text_only=True, no_tools=True.
4. System prompt / instructions include the text-only reminder guard for coordination.
"""
from unittest.mock import MagicMock
from _paths import ROOT, SERVER  # noqa: F401

import aux_engine
import team_engine

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(extra)[:300] + "]") if extra and not cond else ""))
    if not cond:
        fails.append(name)


# 1. text_only_supported check
check("text_only_supported recognizes 'antigravity-cli'",
      team_engine.text_only_supported("antigravity-cli") is True)
check("text_only_supported recognizes aux_engine.ANTIGRAVITY",
      team_engine.text_only_supported(aux_engine.ANTIGRAVITY) is True)

# 2. choose_spec check
def resolve(model, prov):
    return "antigravity-cli"

def available(spec):
    return True, ""

main_spec = {"provider": "anthropic-cli", "model": "claude-3-5-sonnet"}
spec = team_engine.choose_spec(
    agent_model="gemini-2.0-flash",
    agent_provider="antigravity-cli",
    main=main_spec,
    resolve=resolve,
    available=available,
    tools=False,
)
check("choose_spec retains antigravity-cli for text-only turn",
      spec.get("provider") == "antigravity-cli" and spec.get("source") == "agent", spec)

# 3. build check
base = MagicMock()
base.cwd = "/tmp"
base.system_prompt = "You are the team coordinator."

eng = team_engine.build({"provider": "antigravity-cli", "model": "gemini-2.0-flash"}, base, "team-tag-123")
check("build returns AntigravityCLI in suggest mode", eng.mode == "suggest", eng.mode)
check("build marks text_only=True and no_tools=True", eng.text_only is True and eng.no_tools is True)
check("instructions contain original prompt", "You are the team coordinator." in eng.instructions)
check("instructions contain prompt guard reminder", "QUY TẮC ĐIỀU PHỐI / NHẬN VIỆC THUẦN VĂN BẢN" in eng.instructions)

if fails:
    raise SystemExit(f"{len(fails)} checks failed: {fails}")
