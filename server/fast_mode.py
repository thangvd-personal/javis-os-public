"""Fast mode: the composer bolt (settings.model.fast) wired into each brain (0.164.0).

One place decides, per brain, which parameter turns the fast tier on, so no engine sends a
parameter its model does not declare (spec: docs/superpowers/specs/2026-10-15-composer-plus-fast-mode-design.md).

- The two chat entry points (dashboard turn, Telegram turn) call `use(...)` next to where they
  read the thinking level. Engines read `current()` where they add the thinking level.
- Background runs (loops, reminders, kanban, teams, voice) never call `use`, so they keep the
  normal tier, exactly like the thinking level.
- A refusal (400 naming the parameter; for Anthropic's `speed` also 429/529) is retried once
  without the parameter by the engine: Fast mode must never fail a turn.
- `remember` / `states` keep the last state each brain reported, for the bolt tooltip.
"""
from __future__ import annotations

import json
import re
import threading
from contextvars import ContextVar
from typing import Dict, List, Optional, Tuple

CURRENT: ContextVar[bool] = ContextVar("javis_fast_mode", default=False)

# Claude Fast mode: Opus 5.5, Opus 5 and Opus 4.8 only. An empty model means Claude Code's
# default and the "opus" alias points at Opus 5.5, so both count.
# The version must end the id or be followed by a date (-YYYYMMDD): "opus-5-50" is not Opus 5.
_CLAUDE_FAST = re.compile(r"opus-(?:5-5|4-8|5)(?=$|-\d{8}(?!\d)|[^-\d.])")

ANTHROPIC_BETA = "fast-mode-2026-02-01"

# OpenAI-compatible APIs: the value each provider documents for its fast tier.
_COMPAT = {"openai": "fast", "openrouter": "priority", "gemini": "priority"}

_lock = threading.Lock()
_states: Dict[str, Dict[str, str]] = {}


def use(on: bool):
    """Set the current turn's choice. Returns the token for `CURRENT.reset(token)`."""
    return CURRENT.set(bool(on))


def current() -> bool:
    return bool(CURRENT.get())


def claude_supports(model: str) -> bool:
    m = str(model or "").strip().lower()
    return not m or m == "opus" or bool(_CLAUDE_FAST.search(m))


def claude_sdk_settings(model: str, on: bool) -> str:
    """`ClaudeAgentOptions(settings=...)` value (a JSON string).

    Always explicit: Claude Code persists /fast in ~/.claude/settings.json, and Javis loads user
    settings, so "nothing" would let a /fast typed once in a terminal bill every Javis turn 2x.
    Flag settings win over user settings, so `false` here means normal speed (review 0.164.0)."""
    return json.dumps({"fastMode": bool(on and claude_supports(model))})


def normal(coro):
    """Run background work spawned from a turn (compaction, summaries) at the normal tier:
    `create_task` copies the turn's context, which would otherwise carry the bolt along."""
    async def _run():
        CURRENT.set(False)
        return await coro
    return _run()


def anthropic_params(model: str, on: bool) -> Tuple[dict, dict]:
    """(extra headers, extra body) for the Anthropic Messages API."""
    if on and claude_supports(model):
        return {"anthropic-beta": ANTHROPIC_BETA}, {"speed": "fast"}
    return {}, {}


def compat_params(provider: str, on: bool) -> dict:
    """Extra body for OpenAI-compatible chat completions (OpenAI, OpenRouter, Gemini)."""
    tier = _COMPAT.get(str(provider or ""))
    return {"service_tier": tier} if on and tier else {}


def responses_params(on: bool) -> dict:
    """Extra body for the ChatGPT OAuth Responses API."""
    return {"service_tier": "priority"} if on else {}


def codex_override(model: str, on: bool, tiers: List[str]) -> Optional[str]:
    """`-c` override for Codex CLI.

    On: `service_tier="priority"` when the model declares that tier, otherwise nothing.
    Off: always `service_tier="default"`, because some Codex models default to `priority` and a
    user's config.toml may set it; the bolt being off has to mean the normal tier (owner approved).
    """
    if on:
        return 'service_tier="priority"' if "priority" in (tiers or []) else None
    return 'service_tier="default"'


def refused(status: int, text: str, key: str) -> bool:
    """Did the provider refuse the fast parameter `key`? Then the engine retries without it."""
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    if key == "speed" and status in (429, 529):
        return True
    return status in (400, 422) and key.lower() in str(text or "").lower()


def remember(provider: str, state: str, reason: str = "") -> None:
    """Keep the last Fast mode state a brain reported: on, off, cooldown or fallback."""
    if not provider or not state:
        return
    with _lock:
        _states[str(provider)] = {"state": str(state), "reason": str(reason or "")[:200]}


def states() -> Dict[str, Dict[str, str]]:
    with _lock:
        return {k: dict(v) for k, v in _states.items()}
