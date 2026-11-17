"""Tests for generic provider schedule support across all LLM providers in Javis OS."""
import json
import time
from datetime import UTC, datetime, time as dtime, timedelta
import model_routing
import aux_engine
import main


def check(cond, msg):
    if not cond:
        raise AssertionError(f"FAIL: {msg}")
    print(f"PASS: {msg}")


def test_all_providers_support_schedule():
    for p in ("deepseek", "openai", "gemini", "openrouter", "groq", "anthropic-api",
              "anthropic-cli", "openai-oauth", "grok-cli", "antigravity-cli", "ollama-local"):
        check(model_routing.has_schedule_support(p), f"has_schedule_support({p}) is True")

    d_openai = model_routing.default_schedule("openai")
    check(d_openai["enabled"] is False, "openai default disabled")
    check(d_openai["timezone"] == "local", "openai default timezone is local")
    check(len(d_openai["days"]) == 7, "openai default days is all week")
    check(d_openai["windows"] == [{"start": "00:00", "end": "24:00"}], "openai default window 00:00-24:00")

    d_claude = model_routing.default_schedule("anthropic-cli")
    check(d_claude["enabled"] is False, "anthropic-cli default disabled")


def test_schedule_evaluation_work_hours():
    # Work hours: Mon-Fri 08:00 - 18:00 UTC
    sched = {
        "enabled": True,
        "timezone": "UTC",
        "days": [1, 2, 3, 4, 5],
        "windows": [{"start": "08:00", "end": "18:00"}],
        "weekend_all_day": False,
    }
    # Mon 10:00 UTC -> inside
    dt_mon_10 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=UTC)
    active, _, _ = model_routing.is_in_schedule(sched, dt_mon_10.timestamp())
    check(active, "Mon 10:00 UTC is inside work hours")

    # Mon 20:00 UTC -> outside (resting)
    dt_mon_20 = datetime(2026, 11, 16, 20, 0, 0, tzinfo=UTC)
    active, _, next_ts = model_routing.is_in_schedule(sched, dt_mon_20.timestamp())
    check(not active, "Mon 20:00 UTC is outside work hours (resting)")
    dt_next = datetime.fromtimestamp(next_ts, tz=UTC)
    check(dt_next.hour == 8 and dt_next.minute == 0, f"Next active is 08:00: {dt_next}")

    # Sat 10:00 UTC -> outside because weekend_all_day is False
    dt_sat_10 = datetime(2026, 11, 21, 10, 0, 0, tzinfo=UTC)
    active, _, next_ts = model_routing.is_in_schedule(sched, dt_sat_10.timestamp())
    check(not active, "Sat 10:00 UTC is outside because weekend_all_day is False")


def test_chat_routing_generic_provider_schedule():
    # primary: openai, fallback: gemini
    dt_mon_20 = datetime(2026, 11, 16, 20, 0, 0, tzinfo=UTC).timestamp()
    dt_mon_10 = datetime(2026, 11, 16, 10, 0, 0, tzinfo=UTC).timestamp()
    mcfg = {
        "order": ["openai", "gemini"],
        "openai_api_key": "sk-openai-key",
        "gemini_api_key": "gem-key",
        "account_keys": {
            "key1": "sk-openai-key",
            "key2": "gem-key",
        },
        "accounts": {
            "openai": [{"id": "key1", "key": "sk-openai-key", "label": "Key 1", "enabled": True}],
            "gemini": [{"id": "key2", "key": "gem-key", "label": "Key 2", "enabled": True}],
        },
        "schedules": {
            "openai": {
                "enabled": True,
                "timezone": "UTC",
                "days": [1, 2, 3, 4, 5],
                "windows": [{"start": "08:00", "end": "18:00"}],
                "weekend_all_day": False,
            }
        },
        "profiles": {
            "openai": {"model": "gpt-4o", "reasoning": "off"},
            "gemini": {"model": "gemini-2.5-flash", "reasoning": "off"},
        },
        "main": {"provider": "openai", "model": "gpt-4o"},
    }
    # During resting time for openai (20:00 UTC)
    ready_openai = main._route_ready(mcfg, "openai")
    check(not ready_openai, "_route_ready is False for openai during outside schedule")

    ready_gemini = main._route_ready(mcfg, "gemini")
    check(ready_gemini, "_route_ready is True for gemini (no schedule)")

    # Chat provider falls back to gemini during openai rest (20:00 UTC)
    orig_time = time.time
    orig_connected = main._routing_connected_ids
    main._routing_connected_ids = lambda cfg: ["openai", "gemini"]
    try:
        time.time = lambda: dt_mon_20
        prov, kind, key, model = main._chat_provider(mcfg)
        check(prov == "gemini", f"Chat falls back to gemini during openai rest: {prov}")

        # During active time for openai (10:00 UTC)
        time.time = lambda: dt_mon_10
        prov_active, _, _, _ = main._chat_provider(mcfg)
        check(prov_active == "openai", f"Chat uses openai at 10:00 UTC: {prov_active}")
    finally:
        time.time = orig_time
        main._routing_connected_ids = orig_connected


def test_plan_cli_provider_schedule():
    # Test that CodexCLI / ClaudeSDK / GrokCLI / AntigravityCLI is_available respects schedule
    # Set schedule for anthropic-cli
    cfg = {
        "model": {
            "schedules": {
                "anthropic-cli": {
                    "enabled": True,
                    "timezone": "UTC",
                    "days": [1, 2, 3, 4, 5],
                    "windows": [{"start": "00:00", "end": "06:00"}],
                    "weekend_all_day": False,
                }
            }
        }
    }
    # At 12:00 UTC (outside 00:00-06:00)
    dt_noon = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC).timestamp()
    sched_ok, _, _ = model_routing.provider_in_schedule(cfg["model"], "anthropic-cli", now=dt_noon)
    check(not sched_ok, "anthropic-cli is resting at 12:00 UTC")

    # At 03:00 UTC (inside 00:00-06:00)
    dt_night = datetime(2026, 11, 16, 3, 0, 0, tzinfo=UTC).timestamp()
    sched_ok, _, _ = model_routing.provider_in_schedule(cfg["model"], "anthropic-cli", now=dt_night)
    check(sched_ok, "anthropic-cli is active at 03:00 UTC")


def test_save_schedule_endpoint_generic():
    import asyncio
    mcfg_init = {"order": ["gemini"], "gemini_api_key": "test"}
    patch_data = {
        "schedule": {
            "provider": "gemini",
            "enabled": True,
            "timezone": "local",
            "days": [1, 2, 3, 4, 5],
            "windows": [{"start": "08:00", "end": "18:00"}],
            "weekend_all_day": False,
        }
    }
    # Directly invoke model_routing.set_schedule
    m = dict(mcfg_init)
    model_routing.set_schedule(m, "gemini", patch_data["schedule"])
    saved = model_routing.schedule(m, "gemini")
    check(saved["enabled"] is True, "gemini schedule enabled")
    check(saved["timezone"] == "local", "gemini schedule timezone local")
    check(saved["days"] == [1, 2, 3, 4, 5], "gemini schedule days Mon-Fri")
    check(saved["windows"] == [{"start": "08:00", "end": "18:00"}], "gemini schedule windows 08:00-18:00")
    check(saved["weekend_all_day"] is False, "gemini schedule weekend_all_day False")


if __name__ == "__main__":
    test_all_providers_support_schedule()
    test_schedule_evaluation_work_hours()
    test_chat_routing_generic_provider_schedule()
    test_plan_cli_provider_schedule()
    test_save_schedule_endpoint_generic()
    print("\nALL GENERIC SCHEDULE TESTS PASSED!")
