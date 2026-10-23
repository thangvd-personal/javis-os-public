# M1 implementation plan: isolated reproducible trials

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Use the
> existing Goal's explicit continuous self-reviewed execution method, then fresh review.

**Goal:** Run reproducible, isolated, owner-requested baseline trials on the selected engine.
**Architecture:** Immutable host records in the existing SQLite store, a hermetic child
worker with a strict Claude SDK profile, host-only evaluators and shared ledger/usage.
**Tech stack:** Python 3.12, SQLite, FastAPI, Claude Agent SDK 0.2.116, existing encryption.
**Spec:** docs/superpowers/specs/2026-10-22-agent-skill-evidence-improvement-design.md,
sections7/10/12/22/M1; docs/dev/agent-skill-trial-contract-v1.md.

Engineering-only implementation began while M0 PR #794 awaited final CI. M0 subsequently
passed final-head CI39793439501, merged as09903315 and published0.287.0 through39797072172.
M1 is now rebased onto that published baseline. This is the sole draft, #795, originally
reserved as0.288.0 at884bfbca before feature code. Retarget it to main and retain draft status
until all M1 tasks/review/CI pass. Trial support and business/rollout evidence remain separate.

## Outcome and constraints

Implement M1 from sections 7/10/12/23 and frozen trial contract v1: one genuinely isolated
selected-engine adapter, immutable snapshots and private cases, host evaluation and actual
usage attribution, durable trial/recovery lifecycle and installation receipts. No patch
generation, activation or automatic trigger is exposed at this milestone.

Verified non-secret local selection: anthropic-cli / claude-opus-5-5 / medium. SDK 0.2.116
is installed in D:/Project/Javis-OS/.venv/Scripts/python.exe. Native CLI 2.1.304 exists.
No provider/model/reasoning replacement. Snapshot sources for the eventual business pilot
are not yet selected; synthetic tests cannot satisfy business or live rollout evidence.

## Audit decisions

- Reuse ClaudeSDK.query event/usage pipeline through a dedicated trial profile. Do not use
  team_engine.choose_spec because its normal-chat fallback is incompatible with trials.
- SDK setting_sources=None loads all sources; use explicit []. SDK env is merged with
  os.environ, so a dictionary override is insufficient: a child worker gets a fresh
  allowlisted environment at process creation. No inherited server tokens or turn keys.
- Native tools must be absent (tools=[]), skills/plugins/subagents absent, no inherited
  hooks/settings, strict fixture-only MCP and deny unknown calls. Model instructions are
  not the isolation boundary. No resume/continue/fallback/session persistence.
- Credentials stay in the existing host authentication path; never serialize them in a
  trial request, workspace, snapshot, receipt or test fixture. If clean authentication
  cannot be obtained for this configuration, mark the adapter unsupported.
- Reuse team_acceptance.check_one/run_checks directly. Do not create Team jobs or clear
  requires_isolated_checker. Register trusted host checkers with pinned versions, scoped
  immutable inputs and no solver access; unsupported checker isolation yields unknown.
- Existing resonance METHODS holdout is not a general case store: it truncates input,
  lacks independent family splits and durable qualification consumption. Reuse transaction
  and ledger primitives, implement dedicated improvement metadata in the same SQLite DB.
- Extend turn_context and existing usage observations to improvement IDs; no fake team_id,
  new billing ledger, guessed cost or missing-usage-to-zero conversion.
- Native control probes on SDK0.2.116/CLI2.1.304 succeeded with scratch HOME, dummy auth,
  localhost endpoint/proxies and no user/query packets. Init/MCP controls sent no provider
  requests. get_context_usage sent four count_tokens requests; do not call it network-free
  or infer zero cost. All four native count envelopes had tools=[]. Zero loaded memory,
  MCP tools and agents were observed, but init listed four available built-in agent
  definitions despite agents={}. Metadata availability is not loaded context or execution.
  Raw sanitized probe/evidence lives beside this draft. This is synthetic control evidence,
  not real inference/OAuth/fixture execution. No adapter is enabled by this finding.
- A subsequent localhost fake-provider/native adversarial probe verified Bash/Read/WebFetch/
  unregistered MCP denial, exact fixture hit/miss and no outside or ambient side effects.
  `ENABLE_TOOL_SEARCH=false` removes the inert deferred placeholder, leaving exactly the
  fixture tool in native request inventories. Native fixture handlers return `is_error`,
  not `isError`. MCP pre-query [] does not prove absence: post-query shows it connected.
  Both final probes used two fake responses, under three seconds, no real inference or
  billing. Preserve native integration tests before enabling any released adapter.
- Host CLI auth status, sanitized to booleans/method/plan, reports logged in through
  firstParty claude.ai with Max. No tokens/account identifiers were read into artifacts.
  Managed local settings/drop-in/MCP paths and Windows HKLM/HKCU Policies/ClaudeCode keys
  are absent on this development host. Recheck before each verified profile/engine start.
  The official managed-settings docs confirm SDK setting_sources does not suppress managed
  policy; any present or unverifiable managed customization makes this profile unsupported.
  Server-managed policy is documented for Team/Enterprise, so Max is not evidence of such
  eligibility; do not generalize the scratch probe to another account or server runtime.

Primary references for the managed-policy boundary (checked 2026-10-22):
[managed settings](https://code.claude.com/docs/en/managed-settings),
[server-managed settings](https://code.claude.com/docs/en/server-managed-settings),
[SDK permission order](https://code.claude.com/docs/en/agent-sdk/permissions).

## Review focus

1. Clone a consumed qualification set under a new ID: deny reuse by underlying family /
   content identity (Task1).
2. Host environment leaks server credentials to an otherwise tool-denied worker: child
   process receives only a tested allowlist; secrets never appear in workspace (Task2).
3. Candidate completes correctly but provider cannot prove actual configuration: record
   coverage unknown and block comparable-success claims (Tasks2/4).
4. CLI summary and late raw requests overlap after a crash: preserve null/zero and dedupe
   without synthetic Team identity (Task3).
5. Trial output contains a valid production lesson/task directive: store only as evidence,
   with host origin preventing hooks from learning or dispatching (Task4).

## Task 1: immutable contract/snapshot/case records

Files: improvement_cases.py, improvement_store.py migration, test_improvement_cases.py.
Records are host validated, canonically hashed, scoped and immutable. Acceptance contracts
pin objective/business criteria, evaluator/metric/direction/aggregation/unknown behavior.
Snapshots retain exact authorized bytes and source/timezone/period/grant/freshness metadata,
encrypted with durable revision blob primitives. Redaction insufficiency is explicit.
Case sets pin tuning/qualification/regression assignments, source family keys and content
deduplication. Same-source paraphrases cannot cross splits or count as independent cases.
Answers and checker metadata are evaluator-private, never part of solver materialization.
Qualification unseen/reserved/consumed is transactional and bound to experiment/candidate/
contract/config. Resume matches all pins; a new candidate cannot consume the old set again.

**Interfaces produced:** `CaseStore(improvement_store)`, `put_contract(p,contract,event_key)->dict`,
`put_snapshot(p,manifest,raw:bytes,fixture:bytes,event_key)->dict`,
`get_contract(p,contract_id)->dict`, `get_snapshot(p,snapshot_id)->dict` (metadata only),
`put_case_set(p,cases:list[dict],seed:int,event_key)->dict`,
`reserve_qualification(p,case_set_id,pins:dict,event_key)->dict`,
`consume_qualification(p,reservation_id,pins:dict,event_key)->dict`,
`solver_case(p,case_set_id,case_id)->dict` (only task/fixture refs),
`evaluator_case(p,case_set_id,case_id)->dict` (private host-only boundary).
Host dictionaries require strict required/allowed keys; canonical hashes and exact schema
versions are returned with IDs. Calls consume M0 ImprovementStore/RevisionBlobs/_Tx/_event.

**Closed record keys:** contract `{version,objective,output,criteria,primary_metric,evaluator,unknown_conditions,
output_hash_policy}`; criterion `{id,category,critical,description,kind,config}`, category mandatory/business,
kind team-acceptance kind or host_checker; evaluator `{id,version,hash}`. primary_metric
`{criterion_id,direction,aggregation}` names an existing business criterion. At least one
mandatory and one business criterion; owner_confirm is a valid business criterion, whose
verdict remains unknown until the owner decides on the exact output hash. Structural
checks alone never make a business metric pass. Unsupported evaluator pins block execution.
Snapshot manifest `{version,source_ref,source_version,captured_at,timezone,data_period,cutoff,
schema,grant_hash,freshness,redaction,sufficient_source}` plus host raw/fixture hashes. No
implicit truncation; default 1MiB blob bound rejects a larger source before engine creation.
Case `{id,family_key,split,task,snapshot_ids,answer,evaluator_refs}` stores answer/evaluator
refs privately; solver_case returns only id/task/snapshot fixture content and declared public
contract. Qualification pins `{experiment_id,base_bundle_hash,candidate_bundle_hash,
contract_hash,case_set_hash,engine_config_hash,grant_hash,policy_version,scope_hash}` are
exact, not open-ended caller metadata. Clone detection checks both underlying snapshot/task
content identities and stable source/family membership across case-set IDs.
Objective and descriptions preserve the public business intent; evaluator implementations
and answers stay private. M1 declares one final UTF-8 artifact as output `{kind:"text",path,
media_type}`. `public_contract(contract)` returns objective/output/public criteria, retaining
structural requirements while replacing checker pins with `independent_check`. The worker
must receive that pinned projection before query. `output_path()` rejects traversal, device
names and cross-platform ambiguity, following [Win32 naming rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file).
Track exposure at the underlying family/content level too: a tuning clone cannot expose
an unseen/reserved qualification family through another set. Future improver materialization
has a separate `improver_cases(p,case_set_id)->list[dict]` interface that returns tuning only
and durably records exposure. Previously consumed qualification may become later tuning,
but can never regain qualification eligibility. Test cross-set exposure, not just cloning
after consumption. Case-set existence alone is not evidence of answer exposure.

RED-to-GREEN tests: C01–C05, cross-brain access, raw bytes, timezone/period differences,
duplicates/paraphrases, secret removal with insufficient source, concurrent qualification
reservation, replay/resume versus new candidate, interrupted migration/restart.

- [x] Add `test_qualification_clone_cannot_reset_consumed_family` and assert the clone
  reservation raises ConflictError; unchanged pinned reservation replays exactly.
- [x] Run `python tests/run.py --py improvement_cases -v`; confirm behavioral RED.
- [x] Implement the interfaces and atomic schema extension, preserving M0 tables/triggers.
  Read old M0 schema1 compatibly; atomically migrate explicit writes to schema2 and reject
  future schemas. Never recreate or mutate immutable M0 rows while extending the schema.
- [x] Run cases plus improvement_store scripts; confirm GREEN and migration rollback.
- [x] Commit the tested records as an independently usable host-only service.

## Task 2: hermetic trial worker and selected Claude adapter

Files: improvement_sandbox.py, improvement_claude.py, improvement_worker.py,
narrow claude_sdk_engine.py seam, test_improvement_sandbox.py,
test_improvement_claude.py, test_improvement_native.py.
Each attempt gets a fresh workspace/context and output directory. Input is a host-created
manifest of only authorized pinned snapshots/effective revisions. No checker, answer, live
canonical path or prior arm/session is passed. Read fixtures route only through a broker
keyed by capability/args/snapshot/grant. fixture_miss never calls production. Output writes
are host bounded and contained. No arbitrary solver scripts or helper execution in M1.
Fresh worker environment, explicit SDK fields and native tool denial enforce isolation;
actual options/runtime capability are verified before constructing the model engine.
Strict requested configuration has no fallback; unobserved actual config is unknown.
Receipt separates available, selected, observed_use and config/install coverage.
The materialized input includes `public_contract:{content,hash}`, generated from the full
host contract with `public_contract()`. The host runner verifies that relationship; the
worker checks the public schema/hash without receiving evaluator-private data. A shared
`trial_system_prompt()` assembles the complete baseline/assets/public requirements and
rejects oversize input without truncation. Receipt binding checks the journal, prompt,
working directory and exact immutable fixture inventory/grant before native options.
The Python child starts with `-I -X utf8` and only restores the trusted server source root;
workspace modules/PYTHONPATH/user startup hooks cannot become worker code. Native receipt
records exact prompt/inventory hashes and revision availability/selection. Claimed model
use does not upgrade observed_use; missing inventory/model/reasoning remains unknown.
Use `ResultMessage.result` as the declared final text artifact (raw UTF-8, no fence stripping)
and preserve intermediate messages only as trace. A missing result envelope is unknown;
do not concatenate interim tool narration into a supposedly valid JSON product. Raw native
observation must bypass the generic mapper's missing-usage-to-zero conversion for trials.

**Interfaces produced:** `FixtureBroker(fixtures:list[dict],grant_hash:str)`,
`read(capability_id,args:dict,snapshot_version,grant_hash)->dict`;
`TrialWorkspace(root:Path).prepare(trial_id,attempt,input_manifest)->dict`,
`cleanup(trial_id,attempt)->None` (journaled);
`ClaudeTrialAdapter(config_pin:dict).verify()->dict`,
`async execute(request:dict,workspace:dict)->AsyncIterator[dict]` with text/artifact,
receipt, usage, tool-denied and terminal host events. Execution never accepts an arbitrary
engine instance from untrusted input. Adapter capability hash binds the verified profile.
The adapter may receive host-only `workspace_root` and `before_engine_start` dependencies;
the callback must persist the shared ledger marker before the parent sends its IPC ACK.
Neither is accepted from request JSON. Unbound callbacks cannot launch a worker. The
execution request is closed `{version,trial_id,attempt,deadline_at,engine_config_hash,
capability_hash}`; worker packets pin trial/attempt/input hash and a monotonic sequence.

Tests: malicious source/output asking for shell, network, sending, live reads, traversal,
symlink/junction escape, stale mirror, answer access, other arm output, inherited config,
server env and tool keys. Fake local provider/native process test is synthetic and must
demonstrate actual host tool denial. Run real selected-model smoke only after those pass.

- [x] Add inherited-env and adversarial native tool tests; assert no outside marker file,
  fixture_miss with zero live-dispatch calls, no answer/config in worker request/env.
- [x] Run sandbox/Claude scripts; observe RED before adding the dedicated profile.
- [x] Implement hermetic worker, explicit native options, fixture broker and receipt.
  Set ENABLE_TOOL_SEARCH=false in the explicit worker environment: the verified profile
  has exactly the fixture MCP tool and no inert deferred placeholder. Persist broker errors
  independently of protocol formatting. Native research confirmed the SDK handler key is
  is_error; pre-query empty MCP status is not installation proof.
- [x] Run synthetic local provider/native integration and M0 source tests; require GREEN.
- [x] Commit profile and isolation tests; real engine support remains gated by verify().

## Task 3: shared ledger and usage provenance

Files: agent_ledger.py (closed improvement purpose), turn_context.py, usage_store.py,
usage_index.py, improvement_usage.py, improvement_telemetry.py, usage_execution.py,
quota_scheduler.py, test_improvement_usage.py.
Reserve before worker/engine; mark durable engine_started immediately before transport;
settle/refund through the existing CallLedger. Bound experiment/trial/case/variant/repeat/
attempt and learning origin in host execution metadata. Preserve existing Team consumers.
Use existing usage events and CLI request attribution to dedupe request/summary/late events.
Telemetry loss or unverified config remains unknown; distinguish observed zero from null.

**Interfaces produced:** extend `turn_context.execution_metadata(value)->dict|None` to
closed improvement refs without team_id; `usage_index.improvement_events(experiment_id,
trial_id=None)->tuple[list,list]`; `improvement_usage.trial_usage(experiment_id,
trial_id)->dict`. Existing CallLedger APIs/signatures remain unchanged, adding only
`improve_probe` to the purpose enum now. No new billing source is introduced.
Use genuine `mirror_ref=improvement_trial:<id>` to exempt a live leased trial from generic
ten-minute nonmirrored reconciliation; TrialStore recovery owns these references. Reserve
the trial/CallLedger row in one existing transaction. Persist ledger engine_at before the
worker receives permission to send a model query; conservative post-mark uncertainty is
retained as unknown, never refunded or replayed as a fresh attempt.

Tests: U01, existing Team usage regression, pre-start refund, post-start crash/unknown,
late CLI usage, repeat/attempt attribution, no duplicate raw/mirror totals, quota stop
without provider/reasoning change or extra USD caps.

- [x] Add summary+late-request dedupe/unknown test without team_id, assert null cost stays
  null and total tokens count each request once; capture metadata survives host context.
- [x] Run improvement_usage plus existing Team usage, observe missing-attribution RED.
- [x] Extend real telemetry/index/projection and bind existing CallLedger at host boundary.
- [x] Require improvement/Team usage and ledger regressions GREEN.
- [x] Commit shared telemetry changes.

Component boundary: `NativeUsageRecorder` requires a matching real call/brain/attempt/
provider/model after durable engine_at; it cannot start a query or settle/refund the call.
Task4 must construct it after its atomic lease/ledger start transition and before ACK,
persist validated raw trace first and call finish in recovery/cancellation paths. This
remaining runner integration is not an enabled M1 trial or a real-model smoke.

## Task 4: durable orchestrator and independent evaluator

Files: improvement_trials.py, improvement_evaluation.py, store migration additions,
improvement_trial_schema.py, test_improvement_trials.py, test_improvement_evaluation.py.
Trial lifecycle reserved/prepared/running/evaluating/completed with alternate terminal
states, lease owner/generation and idempotency. Persist workspace journal, installation
receipt, artifact hashes, ledger linkage and evaluation separately. Check pinned source/
grant/contract/config before every transition. Preserve all attempts; no cherry-picking.
Evaluate artifact copies using pinned team_acceptance criteria and trusted host checkers;
product failure can coexist with completed execution. Owner confirmations bind output hash.
Never dispatch trial outputs to live lessons/task/chat hooks. No activation API.

**Interfaces produced:** `TrialStore(improvement_store)` with
`reserve(p,request,event_key)->dict`, `transition(p,id,expected_generation,state,data)->dict`,
`cancel(p,id,expected_generation,event_key)->dict`, `recover(p)->dict`, `get(p,id)->dict`;
`TrialRunner(store,cases,adapter_factory,evaluator,ledger)` with `async run(p,id)->dict`;
`evaluate_trial(contract,artifact_root,case_private,checker_registry)->dict`.
Checker registry maps pinned host IDs/hashes to trusted evaluators; no model-supplied
command string or filesystem helper is executed. Unsupported command checker is unknown.

Tests: S02–S04 (crash at reserve/materialize/ready/engine/evaluate, restart recovery, lease
steal, late result, cancel), deterministic fake reruns, baseline/candidate isolation,
contract mutation/owner stale decision, injection treated as data, unknown evaluator.

- [x] Add crash-before/after-engine, stale-generation, cancellation and directive-output
  tests; assert refund only before engine_started, no new lesson/task/live file afterward.
- [x] Run trial/evaluation scripts; observe behavioral RED.
- [x] Implement durable transitions/journal, scoped host evaluation and clean output sink.
- [x] Require lifecycle/owner/learning/ledger/checker regressions GREEN.
- [x] Commit the complete fake-runner path and recovery behavior.

Task4 engineering component complete: schema3 reservation/materialization/lease/engine
marker/cancel/recover, immutable evidence, host evaluator and complete synthetic Runner
integration pass. Runner21 and storage32 checks include repeated cancellation and revoked
authority before claim. Independent review reproduced both defects, then independently
verified their fixes. Expanded adapter/worker/storage/evaluator/usage/ledger regression
passed13/13 files in90s; metadata API/assets/Windows-console passed3/3 in5s. Full M1 still
needs Task5's trusted context/grant service and owner API, then Task6's real selected-engine
feasibility, whole-branch review and integration. No actual model query was run here.

Task4 interface refinement: named host methods replace the draft unrestricted transition
dictionary. `TrialRunner(trials, *, context_loader,live_guard,adapter_factory,recorder_factory,
checker_registry)` binds trusted dependencies; JSON cannot choose callables or assert full
context. Additional files: improvement_trial_evidence.py, improvement_trial_products.py,
improvement_trial_checks.py, improvement_trial_decisions.py, improvement_trial_results.py,
improvement_trial_runner.py and corresponding focused tests. Result finalization and owner
reevaluation have different authority: only the former closes a live execution; the latter
appends historical product evidence without any engine/ledger mutation. No exposed endpoint
or real query is implied by these component seams.

## Task 5: explicit owner service/API and operating report

Files: improvement_api.py/main wiring, route snapshot, operator docs and M1 evidence.
Expose explicit authorized snapshot/case/trial operations only after the complete backend
passes. Default remains suggest and requires a specific owner trial request. Status/list
does not run a model. Unsupported engine has a clear unavailable reason. Sensitive case
answers never appear in list/error/output responses or improver input. No live activation.
Routes retain auth/CSRF and off-event-loop storage, source and worker operations.

**Interfaces consumed:** Tasks1–4 host services. Exact route request schemas are frozen
alongside service implementations before route work, not guessed from unrestricted
client dictionaries. Routes never expose evaluator_case or private qualification results
to an improver. Owner metadata is independently authorized from solver materialization.

Host dependency audit refinement (before exposing a trial run):

- Persist an encrypted experiment-context binding in the same improvement database. Bind
  authenticated brain/experiment, explicit production surface, frozen complete context,
  strict source manifest, exact selected engine pin and narrow revocable source grant.
  Existing request hashes cannot identify the agent/session/project surface by themselves.
- Context loading is a host operation. HTTP clients choose authorized references, not
  context bytes/coverage, provider fallback or a naked grant/scope hash. Capture all required
  protected owner/shared context and dependencies; missing/ambiguous inputs stay incomplete.
  Mutable target bytes remain in the effective bundle and must not be duplicated as baseline.
- Do not reuse live build_system_prompt or agent-turn assembly as a periodic guard: these
  paths seed/heal/sync memory, read volatile lessons/session state and embed clock/usage.
  Capture through strict read-only sources, freeze volatile rendering once and re-observe
  semantic source identity/content. Never run those side effects just to poll a trial.
- Strictly project persisted nonsecret selection fields without settings decryption,
  corrupt-file backup writes or provider/quota fallback. Revalidate the verified adapter
  pin; unsupported model/reasoning/provider remains unsupported. No credential enters a
  context record or workspace. capture_source/check_ancestry and read-only memory-path
  resolution are reusable, while permissive read/parse/truncation helpers need wrappers.
- A host grant explicitly covers exact source identities/snapshot versions, fixture reads,
  selected surface and owner scope. Existing goal/deliverable grants are not interchangeable;
  never create a fake goal to obtain improvement authority. Revocation must block the next
  marker/heartbeat/finalization while preserving historical evidence and counted usage.
- Keep strong task ownership keyed by brain/trial after durable reservation. Cancellation
  persists first, then cancels its worker task; status never starts a model or migrates a
  cold database. Restart only recovers leases, never silently resumes an uncertain query.

This adds improvement_trial_host.py, improvement_trial_context.py,
improvement_trial_selection.py, improvement_trial_sources.py and their focused tests to
Task5's planned files. Final closed route fields must follow the real host service.

Implemented component refinement: the host binding now includes a separate private fixture
inventory, not only context/targets. Inventory excludes snapshot/grant IDs to avoid circular
hashes; snapshots created afterward must match exact metadata/content membership. Qualified
source authorization does not expose content or consume a holdout. Permission freshness is
host-observed with explicit snapshot_version/require_current_version policy; a provider and
retention/tombstone projector remain required. Generic observers/file plans are components,
not sufficient evidence of a production permission or complete baseline collector.

Production completeness audit found ordinary dashboard also depends on SQL project/session
assets, latest workflow state, skill priority/layout/disabled-state inventories, plugin
visibility, locale/channel and coding/Core selections. Extract concrete pure templates into
main_prompt_rendering.py shared by production and trials, preserving current production
behavior/caps and explicitly preserving full protected content in trials. Then collect
strict semantic inputs and regenerate inventories/SQL projections on guards; freeze volatile
clock/usage rendering only. Reject unsupported dynamic surfaces explicitly, without naming
an easier kernel+memory subset a complete main surface. No live prompt builder, migration,
memory healing, plugin loading/visibility callback or mirror sync may run merely to poll.

Implemented dependency components add improvement_trial_catalog.py,
improvement_trial_file_sources.py and improvement_trial_semantics.py with focused tests.
Typed private capture v2 separates file/SQL/inventory/runtime/renderer projections from
the rendered baseline. Optional trusted collector.revalidate(...,frozen_capture=...) keeps
initial clock/usage while freshly observing semantic dependencies; exact host comparison
still applies. Current mutable-file provider supports require_current_version only, with
actual native authority projections and a final all-member check. Its lack of immutable
snapshot_version support remains explicit. Shared pure rendering is committedc6417044;
production composition and owner API remain pending.

SQL refinement: mode=ro alone can create native WAL/SHM sidecars, including after a static
precheck. Reuse already initialized production store connections under their actual close/
mutation locks for WAL sources, issuing only the collector's narrow read queries and an
owned read transaction. Do not construct a store or change its authorizer/query-only/
row-factory/progress state just to collect. Native existing SHM reader coordination is
allowed; logical DB/WAL writes and new source artifacts are excluded. Unsupported/cold
sources remain unavailable until a concrete safe lifetime path exists.

Tests: full owner API/auth/CSRF/cross-brain/disabled policy/unsupported/stale/cancel/retry;
isolate all runtime roots and avoid main startup background tasks.

- [x] Add explicit-trial auth/off/unsupported/cancel/stale route tests before wiring.
- [x] Run new route tests RED; implement validated owner schemas and dependency injection.
- [x] Update intentional route snapshot; run new API/auth/CSRF/security/i18n tests GREEN.
- [x] Commit operator/API surface with capability=false for any unverified adapter.

Task5 service/route request fields are now frozen in
docs/dev/agent-skill-owner-trial-api-v1.md from the actual service/source audit. Browser
owner policy, private bound snapshot capture, immutable case-set/contract/experiment
association, current ordinary SDK CLI resolution and lifespan wiring are required before
enablement. The process-owned TrialTasks component is committed07891922 after independent
foreign-lease/cleanup-error reproduction verification and15 task/24 Runner checks.
Concrete Dashboard composition is implemented but still under independent review; its
closed unsupported profiles cannot be advertised as universally complete.

## Task 6: complete M1 verification and integration

Run M0 regression plus M1 C/E/U/S matrix, learning/owner/ledger/usage/cancellation/i18n/
prompt-budget/migration tests. Real selected-model smoke runs in disposable brain with
synthetic inputs and labels technical feasibility; record real usage/config coverage and
blockers. No business pilot claim without authorized independent data/owner evaluation.
Fresh independent whole-branch review then one fixes pass with reproducing tests; green
final-head CI, PR integration, published build verification and progress update. Continue
M2 engineering where prerequisites pass; keep business/rollout gates separate.

- [x] Self-review full M1 matrix and exact interface signatures; fix gaps before execution.
- [x] Run selected-model technical smoke only when verified, recording actual usage and
  sources; otherwise preserve unsupported status and continue independent engineering.
- [x] Fresh whole-branch review; one fixes pass with behavioral RED-to-GREEN evidence.
- [x] Final-head CI GREEN, merge/new-version publication verified, progress recorded.

M1 engineering integration: exact e0381902 passed Linux CI39852883192; PR795
straight squash merged9dd7a9f9de3e8fadb338298e0f9743d48e5c1d4b as0.291.0. The
merged tree matches the reviewed/tested head. Publication39856024117 succeeded.
This completes engineering Task6, without changing the historical unknown smoke
verdict or claiming business acceptance, live canary, activation or deployment.

Selected-engine feasibility: exact qualified Windows CLI2.1.308/SDK0.2.116, persisted
anthropic-cli/claude-opus-5-5/medium/subscription/fastfalse. First deep-output attempt verified
runtime/auth but failed before reservation on Windows path length; it made no query. A
fresh explicit shallow output then completed one synthetic tuning baseline. Actual model
and fixture-read/checker pass are observed; aggregate remains unknown (reasoning/asset use
and native event/tool outcome coverage). Known token totals4362/158 have partial request
attribution; requests is null. No provider/model/reasoning fallback or second model query.
Evidence export is docs/dev/agent-skill-selected-engine-feasibility-2026-10-23.json. This
checks the feasibility execution item, not Task6/M1/Goal completion or business acceptance.

The source/test acceptance map is docs/dev/agent-skill-m1-verification.md. Fresh whole-M1
review reproduced orphaned worker creation, retained decrypted workspaces and missing
per-brain model capacity. Behavioral RED-to-GREEN fixes retain native spawn/cleanup,
protect worker/checker workspace lifetimes with kernel locks, clean terminal copies and
guard engine markers atomically. Cleanup-read and abort failures were also reproduced;
evaluation is now joined unconditionally before deleting temporary input. Encrypted
history and historical real-smoke provenance remain unchanged. Final qualification uses
the separate c3b4f27ee81899fa1ae650513ae5c4b051cdcfd7b4106635a2e6acd2209f4df6 profile
with a localhost fake provider. Fresh independent review reports no open Important findings;
its final Runner/lifetime17 checks passed2/2 in53s and all four original reproductions pass.
The exact-head CI and publication evidence above complete this engineering gate.
