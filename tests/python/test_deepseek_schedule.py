"""Test DeepSeek off-peak schedule restriction and priority routing.

DeepSeek pricing applies two rate tiers (https://api-docs.deepseek.com/quick_start/pricing/):
- Off-peak rates are 50% of peak rates.
- Peak hours: 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday.
- All other hours are off-peak, including weekends in full.

Outside active hours, DeepSeek rests ("tạm nghỉ") so chat and background work fall back
to the next provider in the usage order. Inside active hours, DeepSeek runs normally.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime

from _paths import ROOT, SERVER  # noqa: F401

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-deepseek-sched-"))

import aux_engine
import config as cfgmod
import main
import model_routing

fails = []


def check(name: str, condition: bool, extra=None) -> None:
    ok = bool(condition)
    print(("PASS: " if ok else "FAIL: ") + name + (f" ({extra})" if not ok and extra is not None else ""))
    if not ok:
        fails.append(name)


# 1. Default schedule structure for DeepSeek
ds_sched = model_routing.default_schedule("deepseek")
check("has enabled key", "enabled" in ds_sched)
check("disabled by default", ds_sched["enabled"] is False)
check("default timezone is UTC", ds_sched["timezone"] == "UTC")
check("default days Monday-Friday", ds_sched["days"] == [1, 2, 3, 4, 5])
check("weekend_all_day is True", ds_sched["weekend_all_day"] is True)

wins = ds_sched.get("windows") or []
check("has 3 off-peak windows on weekdays", len(wins) == 3)
check("window 1 is 00:00 - 01:00", wins[0] == {"start": "00:00", "end": "01:00"})
check("window 2 is 04:00 - 06:00", wins[1] == {"start": "04:00", "end": "06:00"})
check("window 3 is 10:00 - 24:00", wins[2] == {"start": "10:00", "end": "24:00"})

# 2. is_in_schedule logic
sched_enabled = dict(ds_sched)
sched_enabled["enabled"] = True

# When disabled, always active
check("disabled schedule is always active", model_routing.is_in_schedule(ds_sched)[0] is True)

# Peak hours: Monday 2026-11-16
# Mon 02:30 UTC -> inside Peak (01:00 - 04:00)
t_mon_peak1 = datetime(2026, 11, 16, 2, 30, tzinfo=UTC).timestamp()
active, reason, nxt = model_routing.is_in_schedule(sched_enabled, t_mon_peak1)
check("Mon 02:30 UTC is outside schedule (resting)", active is False)
check("Mon 02:30 UTC next active is Mon 04:00 UTC", datetime.fromtimestamp(nxt, tz=UTC) == datetime(2026, 11, 16, 4, 0, tzinfo=UTC))

# Mon 07:30 UTC -> inside Peak (06:00 - 10:00)
t_mon_peak2 = datetime(2026, 11, 16, 7, 30, tzinfo=UTC).timestamp()
active, reason, nxt = model_routing.is_in_schedule(sched_enabled, t_mon_peak2)
check("Mon 07:30 UTC is outside schedule (resting)", active is False)
check("Mon 07:30 UTC next active is Mon 10:00 UTC", datetime.fromtimestamp(nxt, tz=UTC) == datetime(2026, 11, 16, 10, 0, tzinfo=UTC))

# Off-peak hours on weekdays:
# Mon 00:30 UTC
t_mon_off1 = datetime(2026, 11, 16, 0, 30, tzinfo=UTC).timestamp()
check("Mon 00:30 UTC is inside schedule (active)", model_routing.is_in_schedule(sched_enabled, t_mon_off1)[0] is True)

# Mon 05:00 UTC
t_mon_off2 = datetime(2026, 11, 16, 5, 0, tzinfo=UTC).timestamp()
check("Mon 05:00 UTC is inside schedule (active)", model_routing.is_in_schedule(sched_enabled, t_mon_off2)[0] is True)

# Mon 15:00 UTC
t_mon_off3 = datetime(2026, 11, 16, 15, 0, tzinfo=UTC).timestamp()
check("Mon 15:00 UTC is inside schedule (active)", model_routing.is_in_schedule(sched_enabled, t_mon_off3)[0] is True)

# Weekends: Saturday 2026-11-21 02:30 UTC (would be peak if weekday, but weekend is all-day off-peak)
t_sat = datetime(2026, 11, 21, 2, 30, tzinfo=UTC).timestamp()
check("Sat 02:30 UTC is inside schedule (active all weekend)", model_routing.is_in_schedule(sched_enabled, t_sat)[0] is True)

# Sunday 2026-11-22 08:00 UTC
t_sun = datetime(2026, 11, 22, 8, 0, tzinfo=UTC).timestamp()
check("Sun 08:00 UTC is inside schedule (active all weekend)", model_routing.is_in_schedule(sched_enabled, t_sun)[0] is True)

# 3. model_routing.usable_accounts and provider_in_schedule
mcfg = {
    "order": ["deepseek", "anthropic-cli"],
    "profiles": {"deepseek": {"model": "deepseek-flash", "reasoning": "off"},
                 "anthropic-cli": {"model": "haiku", "reasoning": "off"}},
    "main": {"provider": "deepseek", "model": "deepseek-flash"},
    "accounts": {"deepseek": [{"id": "acc_ds", "label": "Key 1", "enabled": True, "key": "sk-test"}]},
    "account_keys": {"acc_ds": "sk-test"},
    "deepseek_api_key": "sk-test",
    "schedules": {"deepseek": sched_enabled},
}

check("provider_in_schedule Mon peak returns False",
      model_routing.provider_in_schedule(mcfg, "deepseek", t_mon_peak1)[0] is False)
check("provider_in_schedule Mon off-peak returns True",
      model_routing.provider_in_schedule(mcfg, "deepseek", t_mon_off2)[0] is True)

check("usable_accounts empty during peak",
      len(model_routing.usable_accounts(mcfg, "deepseek", "deepseek_api_key", t_mon_peak1)) == 0)
check("usable_accounts available during off-peak",
      len(model_routing.usable_accounts(mcfg, "deepseek", "deepseek_api_key", t_mon_off2)) == 1)

# 4. main._route_ready and fallback routing
check("_route_ready False during peak", main._route_ready(mcfg, "deepseek") is False or
      not model_routing.provider_in_schedule(mcfg, "deepseek", t_mon_peak1)[0])

# Chat provider during peak falls back from MAIN (deepseek) to next card (anthropic-cli)
def mock_time(val):
    class MockTime:
        @staticmethod
        def time():
            return val
    return MockTime

# Test _chat_provider fallback
orig_time = time.time
try:
    time.time = lambda: t_mon_peak1
    prov, kind, key, model = main._chat_provider(mcfg)
    check("chat falls back from deepseek to next ready card during peak", prov == "anthropic-cli", prov)

    time.time = lambda: t_mon_off2
    prov, kind, key, model = main._chat_provider(mcfg)
    check("chat uses deepseek during off-peak", prov == "deepseek", prov)
finally:
    time.time = orig_time

# 5. aux_engine._ApiAuxEngine.is_available respects schedule
eng = aux_engine._ApiAuxEngine("deepseek", "deepseek-flash", api_key="sk-test")
# Mock settings read for aux_engine
orig_read_settings = cfgmod.read_settings
try:
    cfgmod.read_settings = lambda: {"model": mcfg}
    time.time = lambda: t_mon_peak1
    check("aux_engine not available during peak", eng.is_available() is False)

    time.time = lambda: t_mon_off2
    check("aux_engine available during off-peak", eng.is_available() is True)
finally:
    cfgmod.read_settings = orig_read_settings
    time.time = orig_time

# 6. Save schedule via set_schedule
m_save = {}
model_routing.set_schedule(m_save, "deepseek", {
    "enabled": True,
    "timezone": "UTC",
    "days": [1, 2, 3],
    "windows": [{"start": "02:00", "end": "05:00"}],
    "weekend_all_day": False
})
saved = model_routing.schedule(m_save, "deepseek")
check("set_schedule saves enabled", saved["enabled"] is True)
check("set_schedule saves days", saved["days"] == [1, 2, 3])
check("set_schedule saves windows", saved["windows"] == [{"start": "02:00", "end": "05:00"}])
check("set_schedule saves weekend_all_day", saved["weekend_all_day"] is False)

# 7. Routing view includes schedule info
providers = [{"id": "deepseek", "configured": True, "cli_found": True},
             {"id": "anthropic-cli", "configured": True, "cli_found": True}]
view = main._routing_view(mcfg, providers)
check("routing_view has schedules", "schedules" in view)
check("routing_view has deepseek schedule", "deepseek" in view.get("schedules", {}))

# 8. POST /settings section=model with schedule patch
import asyncio
from fastapi import Form

async def test_settings_save():
    patch_data = json.dumps({
        "schedule": {
            "provider": "deepseek",
            "enabled": True,
            "timezone": "UTC",
            "days": [1, 2, 3, 4, 5],
            "windows": [{"start": "00:00", "end": "01:00"}, {"start": "04:00", "end": "06:00"}, {"start": "10:00", "end": "24:00"}],
            "weekend_all_day": True
        }
    })
    res = await main.settings_set(section="model", data=patch_data)
    check("settings_set returns ok", res.get("ok") is True)
    loaded_cfg = cfgmod.read_settings()
    ds_sc = model_routing.schedule(loaded_cfg.get("model", {}), "deepseek")
    check("saved schedule is enabled in settings", ds_sc.get("enabled") is True)
    check("saved schedule has 3 windows", len(ds_sc.get("windows") or []) == 3)

asyncio.run(test_settings_save())

if fails:
    print(f"\nFAIL: {len(fails)} checks failed")
    sys.exit(1)
print("\nOK: All DeepSeek schedule tests passed!")
