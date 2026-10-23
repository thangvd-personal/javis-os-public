# Additional Python bug-fix pilot: preparation catalog

Owner delegated pilot selection on2026-10-22. This is an additional coding pilot, not a
replacement for the design's reconciliation Pilot A or document Pilot B. No model run,
business verdict or accepted case is recorded here. Survey baseline:f35c7fac.

The shipped `.claude/skills` packages are system-owned (`system_sync.py`), so they are
protected targets. Prepare a separate user-owned `python-bugfix` skill in an isolated
pilot brain, keeping normal repository instructions/context complete and unchanged.

## Provisional families

The checker path means its Git blob at the listed fix commit, not today's working copy.
Each candidate needs an independently pinned checker that fails on the selected parent
and passes on the historical fix. Release commits can contain unrelated changes; extract
only a bounded task and its authorized source/dependency tree. None is validated yet.

| Family | Proposed fix | Source surface | Checker in `tests/python/` |
|---|---|---|---|
| Job input-marker recognition |348d90fb|tasks.py|test_kanban_nghiem_thu_va_quy_trinh.py|
| Transient settings-read failure |5584cb9d|config.py|test_settings_ghi_an_toan.py|
| Learning preserves wiki pages |475a524c|learn.py,git_brain.py|test_learn_khong_xoa_wiki.py|
| Stable attachment ordering |a14e0530|sessions.py|test_project_panel.py|
| Local-login exemption and forwarded requests |edc35224|main.py|test_local_exempt_tunnel.py|
| Separate 2FA state on login disable |f0cbff7b|config.py,main.py|test_auth_disable_keeps_2fa.py|
| Equivalent brain identifiers |1edcf0fc|brain_ident.py,stores|test_brain_filter_spellings.py|
| Background recipient/source provenance |1e91d082|owner_channels.py,channel_context.py|test_javis_core_routing.py|
| Memory policy reaches native restrictions |355f2155|memory_control.py,claude_sdk_engine.py|test_javis_core_followups.py|
| Read-only shell-chain classification |70f77c3c|team_gate.py|test_team_approval_0207.py|
| Connector names and verb classification |76fdc6d0|mcp_catalog.py|test_composio_app_name_verbs.py|
| Speech language/model compatibility |32d5d0ce|tts_eleven.py|test_tts_eleven.py|
| Quoted image projection |063750ac|Zalo adapters,chatbot_runtime.py|test_zalo_anh_quote.py|
| Long message context |3c4c8837|chatbot_runtime.py,chatbot_reply_policy.py|test_bot_doc_du_tin_dai.py|
| Failed bot-turn outcomes |e15ef3ba|chatbot_runtime.py,bot adapters|test_chatbot_grounding.py|
| Mention markup delivery |0afe8add|Zalo adapters|test_bot_zalo_tag.py|
| Response stream/tool lifecycle |d9b5e408|engine.py|test_responses_tool_stream.py|
| Systemd updater/user boundary |cadd6d53|updater.py|test_updater_systemd.py|

Reserves:934c96a9 (`engine.py`,test_thu_lai_tam_thoi.py) stays grouped with stream behavior
until independence is justified;47999c67 (`system/plugins/desktop-apps/plugin.py`,
test_desktop_apps_plugin.py) requires mocked platform operations, never an actual app launch.

## Required gates

- Eighteen provisional families might supply6/6/6 feasibility only after validation.
  The12/20/12 pilot requires44 independent families: at least26 more are not established.
  Do not count paraphrases, repeated assertions or provider spellings as new families.
- Freeze assignments before any improvement. Solver/improver input must exclude this host
  catalog, Git history, fix diffs, later changelogs, checker source and private answers.
- Allowlist source/dependency files. Do not archive whole historical repositories: some
  commits contain runtime artifacts, including mcp-stdio-pids.json at063750ac. Never copy
  configuration, account credentials, state databases, transcripts or production data.
- Pin source/checker/dependency hashes, platform and acceptance before execution. Reject
  failures caused only by unrelated API/dependency drift. Review legacy fixture provenance.
- A solver may return a patch artifact using fixture-only tools. Applying that patch and
  executing candidate Python requires an isolated evaluator without host credentials,
  network or live state. An ordinary host subprocess does not satisfy that boundary.
- Keep selected model/reasoning fixed, every attempt, unknown result and rejected candidate.
  Synthetic enforcement tests and historical parent/fix validation do not prove model value.

The full Goal remains open while dataset, checker isolation, real trials, qualification or
owner/live gates are incomplete.
