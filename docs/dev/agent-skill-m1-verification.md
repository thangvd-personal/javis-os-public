# M1 verification map

This maps the design's M1 acceptance IDs to executable checks. It is not a claim that
every engine, production scope or business pilot is qualified. Focused final regression
and independent whole-M1 review have passed. Release integration remains pending green
final-head CI, merge and verified publication.

Run scripts with the repository interpreter via `tests/run.py --py <selector> -v`.
Runtime state roots must be isolated before server imports. The native qualification
suite is separately opt-in; it uses dummy credentials and a localhost fake provider.

| Requirement | Behavioral evidence in `tests/python/` | Boundary |
|---|---|---|
| C01: exact source period/version/timezone | `test_improvement_cases.py`, `test_improvement_trial_sources.py`, `test_improvement_trial_file_sources.py`, `test_improvement_trial_associations.py` | Immutable snapshot identities and all-split membership; changed current sources invalidate authority. |
| C02: independent families and split contamination | `test_improvement_cases.py` | Duplicate bytes/families, relabelled exposed tuning and cloned consumed qualification are rejected. |
| C03: private answers/checkers/holdout | `test_improvement_sandbox.py`, `test_improvement_host_snapshots.py`, `test_improvement_trial_host.py`, `test_improvement_runtime_qualification.py` | Public projection excludes private evaluation data; fixture tool has exact pinned membership and no live fallback. |
| C04: qualification consumption and resume | `test_improvement_cases.py`, `test_improvement_trials.py`, `test_improvement_trial_admission.py` | One transaction consumes before input visibility; resume keeps exact pins; another candidate cannot reset a family. |
| C05: insufficient or sensitive source | `test_improvement_cases.py`, `test_improvement_sandbox.py`, `test_improvement_trial_file_sources.py` | Loss of required input and credential-bearing input reject before materialization. |
| E01: side effects denied | `test_improvement_runtime_qualification.py`, `test_improvement_trial_checks.py` | Actual qualified CLI receives fixture-only tools, no ambient hooks/plugins/skills or native shell/web/live connector; ambiguous native outcomes remain unknown. |
| E02: fixture miss | `test_improvement_sandbox.py`, `test_improvement_runtime_qualification.py` | Wrong identity/version/grant produces a miss; no production connector fallback. |
| E03: actual view and duplicate context | `test_improvement_trial_catalog.py`, `test_improvement_trial_dashboard.py`, `test_improvement_trial_checks.py` | Canonical inventory, protected context exclusion and receipt pins; native observed use remains separately unknown. |
| E04: requested versus actual configuration | `test_improvement_claude.py`, `test_improvement_trial_selection.py`, `test_improvement_trial_checks.py` | Exact current SDK selector and runtime pin; observed mismatch fails, absent model/reasoning stays unknown. |
| E05: separate arm/repeat state | `test_improvement_sandbox.py`, `test_improvement_worker.py` | Distinct workspace/context and isolated Python process; no session resume. |
| E06: unsupported adapter | `test_improvement_claude.py`, `test_improvement_trial_dashboard.py`, `test_improvement_trial_api.py` | Drift, unmanaged contributors or unsupported profiles reject before dispatch; capabilities remain false. |
| U01: usage identity and partial attribution | `test_improvement_usage.py`, `test_improvement_trial_runner.py`, `test_improvement_trial_views.py` | Existing ledger/index linkage, immutable stored usage, late data and null versus known zero; equivalent subscription price is not a cash charge. |
| S02: materialization/commit/crash recovery | `test_improvement_trials.py`, `test_improvement_trial_admission_transactions.py`, `test_improvement_trial_workspace_lifetime.py` | Atomic reservations/markers, journal recovery and scoped plaintext cleanup; live foreign leases/workspaces survive recovery. |
| S03: cancellation before/after engine marker | `test_improvement_worker.py`, `test_improvement_trial_runner.py`, `test_improvement_trial_tasks.py`, `test_improvement_trial_owner_service.py` | Repeated cancellation retains spawn, worker cleanup and durable transitions; premarker refund and postmarker counted unknown; no implicit query retry. |
| S04: stale source/owner/grant/contract | `test_improvement_trial_host.py`, `test_improvement_trial_service.py`, `test_improvement_trial_results.py` | Revalidation before marker/finalization, source owner lifetime and immutable historical results; late evidence cannot reopen authority. |
| Default concurrency | `test_improvement_trials.py`, `test_improvement_trial_workspace_lifetime.py` | One model trial per brain, including the interval after durable cancellation until the old worker closes; other brains retain separate capacity. |

The actual-main protocol test is `test_improvement_trial_end_to_end.py`: all thirteen
handlers, real host/storage/admission/runner/usage/results and orderly shutdown, with
synthetic Claude verification/execution. It does not call a real model or establish
business effectiveness. Existing learning, owner rules, disabled/canonical skills,
grant/ledger/usage, backup/migration, authentication/CSRF, i18n and prompt-budget
regressions remain required alongside these component checks.

Historical real-engine feasibility is recorded in
`agent-skill-selected-engine-feasibility-2026-10-23.json`: one synthetic baseline,
profile `06e75c3046a666790035fd4ef8910abe3a1113baba5bc67f6f96a9378a98abed`,
requested/observed Opus5.5, configured medium effort with actual reasoning unknown.
The product check passed; aggregate execution evidence remains unknown. This history
must not be relabelled as execution on a later profile. A later profile's isolated
native qualification is a separate technical result. No business pilot, candidate
victory, qualification-set experiment, activation or live canary is established by it.

The final POSIX directory-stat race has a deterministic regression in
`test_improvement_trial_dashboard.py`: a sibling journal appears/disappears after
authority capture but before follow-up stat. Only fallback directory ctime may
change. Intermediate directory replacement, chmod, native ACL, actual birthtime
and protected-file content changes remain rejected. An actual-main E2E with true
filesystem ChangeTime projected as POSIX ctime reproduced the previous pre-engine
abort and passes after the narrow Dashboard correction. This is synthetic protocol
evidence; the final-head Linux workflow is still the integration gate.
