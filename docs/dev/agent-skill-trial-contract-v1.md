# Improvement trial boundary contract v1

This document freezes the M0 boundary for M1 implementation. It is a contract, not an
available trial runner. M0 advertises `trial=false` and has no engine execution endpoint.
Implementation must validate and pin these records before starting an engine; adding
optional metadata cannot weaken a required field or change a previously pinned hash.

## Canonical records

Host records are JSON objects serialized as UTF-8 with sorted keys, no insignificant
whitespace, no NaN/Infinity and no implicit datetime normalization. SHA-256 covers the
complete record excluding its own hash field. Byte assets use their raw SHA-256. Hashes
are host computed; a caller's asserted digest is checked against the referenced record.
All references carry the authorized brain identity. Missing references fail closed.

| Record | Required pinned content |
|---|---|
| Acceptance contract | `contract_version=1`, mandatory and business criteria with stable IDs and criticality, primary metric, better direction, aggregation, evaluator ID/version/hash, unknown conditions, output hashing policy, owner-confirmation requirements |
| Engine configuration | Requested provider/model/reasoning, selected engine ID, capability profile, host grant hash, operational deadline; actual configuration is observed separately, never inferred from the request |
| Source snapshot | Source identity/version, raw content hash, timezone, capture time, data period/cutoff, schema, read grant/freshness, redaction status and sufficient-source decision |
| Case set | Case IDs, source/family group keys, deduplication identity, immutable split assignments (`tuning`, `qualification`, `regression`), split seed and snapshot references; answers and checker references are evaluator-private |
| Trial request | Experiment/variant/case/repeat/attempt IDs, base and effective bundle hashes, contract/case-set/config/grant hashes, expected source/target generation, deadline, host learning origin |
| Installation receipt | Trial/session IDs, effective bundle/prompt/tool inventory/fixture/grant hashes, asset revisions available, selected and observed-used events, actual engine configuration and coverage |
| Trial result | Terminal execution state, artifact manifest/hash, receipt reference, per-criterion `pass`, `fail` or `unknown` with evidence/reason, observed usage and existing ledger references |

The acceptance adapter reuses `team_acceptance` criteria where applicable without creating
a Team job. A structural pass never implies a business or owner-confirmation pass. Owner
decisions bind the exact case/output hash. Checkers are host registered, pinned, and outside
the solver's filesystem, environment and tool inventory.

M1's acceptance record requires a public objective, a description for every criterion and
one declared UTF-8 text artifact `{kind:"text",path,media_type}`. The solver receives a
hash-pinned public projection of these requirements, without evaluator implementation
identities/hashes or answers. Structural requirements remain visible. Full and public
contract hashes are distinct pins; the host verifies their relationship. Missing public
intent is invalid, rather than permission to infer the owner's business requirements.

## Isolation and lifecycle

Every `(experiment, variant, case, repeat, attempt)` has a fresh workspace and context.
The solver receives the authorized task input, read-only snapshot fixtures and effective
assets. It receives no answers, checker internals, other arm outputs, live state, inherited
login files or production write capabilities. A missing fixture returns `fixture_miss`;
it never dispatches to a live connector. Native tools require host enforcement. An adapter
with unverified enforcement is unsupported and cannot instantiate a trial engine.

Qualification transitions `unseen -> reserved -> consumed` durably. Resumption preserves
the candidate/case/config pin; a new candidate cannot reuse consumed holdout evidence.
Leaked answers/checkers make the experiment contaminated. Redaction that destroys task
meaning yields `insufficient_source`, not a successful replay.

Trial states are `reserved -> prepared -> running -> evaluating -> completed`, with
alternative terminal states `failed`, `unknown`, `interrupted`, `cancelled`, `not_run`.
An execution can complete while the product fails a criterion. Host `engine_started`
is persisted at the existing ledger boundary; missing or late usage is unknown, never
silently zero. Cancellation and crash recovery retain attempt identity and artifacts.

Before each transition, host checks the pinned generation, permissions, source, contract
and selected engine configuration. Stale or late results cannot apply a new baseline.
Trial outputs carry host `learning_origin=trial|compose|qualification`; they cannot become
production lessons, tasks, messages or owner approvals. No state in this contract grants
activation. Baseline is always a valid selection; incomplete comparisons are inconclusive.

## M1 validation obligations

M1 implements the records and recovery behavior above with behavioral tests for C01–C05,
E01–E06, U01 and S02–S04 from the design. A fake runner is labeled synthetic. Real smoke
requires the owner's selected, verified model configuration and reports actual coverage
and usage. Supporting an API adapter never authorizes switching a CLI-selected user to API.

## M1 internal durable request and evaluator boundary

The unreleased request has the closed fields `version=1`, `pins`, `case_set_id`, `case_id`,
`variant=baseline|candidate`, positive `repeat`, `engine_config`, `baseline_context_hash`,
`target_generations`, `deadline_at`, and optional `qualification_id` for qualification only.
`pins` reuses CaseStore's exact experiment/base/candidate/contract/case-set/config/grant/
policy/scope pin shape. `engine_config` is the full adapter pin, never an alternative
provider chosen by the runner. `baseline_context_hash` binds the complete host context
record (content, raw content hash and coverage), not just its text. API clients must not
assert that host context collection was complete.

Schema3 adds trial/attempt/observation tables to the existing resonance database. Metadata
reads still support schemas1 and2 without migrating. A write upgrades in one transaction
after a serialized backup. Trial reservation and its real CallLedger reservation share
that transaction. The logical experiment/variant/case/repeat identity cannot produce two
calls through different idempotency keys.

Leases are internal host capabilities; ordinary trial reads omit their tokens. Input
materialization commits holdout consumption or tuning/regression exposure plus the exact
materialization hash before returning solver-visible input. Preparation validates the real
workspace journal and public projection against host records. Engine start changes the
trial and existing call marker atomically, before query acknowledgement. Cancellation
before that marker refunds; after it, the call remains counted and uncertain recovery is
`unknown`. Neither recovery path creates another attempt implicitly.

`evaluate_trial(contract, artifact_root, case_private, checker_registry)` consumes the exact
CaseStore private projection. Its optional keyword-only `owner_decisions` accepts typed
host records from authorized durable history, never answer/request JSON claiming owner
authority. Evaluator and registered checker ID/version/hash pins must match. Checkers get
immutable artifact data and private answers; they receive no executable or solver path.
Command/candidate-code execution remains unsupported without a separately verified isolated
checker. Structural, business and owner verdicts remain distinct, with exact raw output
hashes. The runner must additionally enforce execution/installation invariants; a standalone
artifact evaluator cannot attest native isolation, permission or actual configuration.

This boundary does not advertise a complete TrialRunner or an owner execution endpoint.

## M1 evidence and host finalization

Schema3 also stores immutable encrypted packet chunks, artifacts, installation receipts,
owner decisions and result manifests beside the same trial/call references. Raw packet
chunks are bounded to 1 MiB, with a complete packet limit of 8 MiB and verified reconstruction.
Normal packets require a live matching host lease, contiguous sequence and exact workspace
identity. Exact replay is read-only. The normal trace freezes at worker completion or
evaluation; a later error cannot silently change the facts being graded. Explicit late
evidence requires the retained writer capability and a terminal/older attempt; it cannot
reopen the trial, change its generation or refund a counted call.

`TrialStore.heartbeat` renews the current owned lease without changing its generation.
`abort(...,expected_generation,lease_token,reason)` closes the owned attempt, including
an expired lease: pre-marker is `not_run`/refunded, post-marker is `unknown`/counted. Reasons
are bounded host codes in the attempt and existing immutable event history. An external
cancellation remains authoritative. Closing authority grants no ability to start a query.

`stage_evaluation` requires a complete non-late trace with one native terminal event. It
preserves exact UTF-8 result bytes and receipt separately from verdicts. The declared
contract output receives a complete host copy through exclusive publication; an owner
edit is never replaced. Multiple different final artifacts/receipts are ambiguous.

Internal `finish(...,expected_generation,lease_token,capability_hash,live_guard,
checker_registry)` accepts trusted host dependencies, never a client/model report or
verdict. The required guard returns the exact scope/grant/config/context/target pins.
The host evaluates outside SQLite's write lock, then rechecks authority, lease, output and
generation before atomically appending an encrypted result and settling the existing call.
Pure execution-record checks bind the complete/public contract and installation evidence;
their `record_checks_only` label does not prove live permission or business acceptance.
Missing actual reasoning/asset use remains unknown. Execution `completed` does not imply
product `pass`; malformed evidence can fail while unsupported evaluation remains unknown.

Owner confirmation is append-only, terminal-only and binds case/criterion/output/contract.
Only authenticated owner history creates typed evaluator decisions. Historical
`reevaluate(...,attempt,expected_generation,event_key,checker_registry)` grades an immutable
artifact copy and appends a new result pointing to its predecessor. It never queries a
model, changes original execution/mandatory facts, settles another call or activates a
bundle. A concurrent decision invalidates publication by generation. Prior result IDs
remain readable within the same owner/brain/trial/attempt scope.

The worker does not receive lease tokens, evaluator answers, checker callables, live-guard
callbacks or owner-history capabilities. These services remain internal until the full
runner and authorized owner API pass their own checks.

## M1 runner component

`TrialRunner(trials, *, context_loader, live_guard, adapter_factory, recorder_factory,
checker_registry)` accepts synchronous trusted host dependencies. The context loader and
guard receive `(principal, request)`; the runner binds a zero-argument guard for finalization.
`run(principal, trial_id)` owns one already-reserved attempt and returns current metadata
plus its result, when present. Calling it again cannot create a new query or steal a lease.

The start callback persists the real trial/call marker and constructs NativeUsageRecorder
before acknowledging the worker. Its canonical execution identity is
`improvement_trial:<trial_id>`; attempt is a separate field. Validated raw packets persist
before telemetry observation. A raw-trace write failure still records known usage, closes
the attempt as uncertain and never retries a query. Process/generator cleanup finishes
before product staging and host evaluation.

The runner renews its 60-second lease every 20 seconds and rechecks host authority. Cancellation
drains owned state/telemetry operations through repeated cancellation requests. Evaluation
commit authority is revoked before waiting for its host thread; no abandoned thread may
publish after the runner has returned. A preclaim authority failure refunds only a still
unclaimed reservation through exact generation/no-lease/no-engine checks. A concurrent
claim is preserved. Unsupported configuration never releases the query acknowledgement.

Trusted synchronous host checkers must terminate. Python threads cannot safely kill an
arbitrary callable; the runner invalidates its finalization capability and waits for owned
work. Candidate code and command checkers remain unsupported here and need a separately
verified process/container checker. This component does not enable production trial API
capabilities or establish selected-engine/business feasibility by synthetic tests.

## M1 host context and fixture authority components

`HostContextStore` accepts trusted synchronous context, selected-engine, target and fixture
observers. `create(owner, experiment_id, surface, target_ids, source_refs=..., event_key=...)`
records an encrypted immutable binding and an independently revocable grant in the same
database. HTTP clients may eventually select authorized references; they cannot supply
context bytes, completeness, runtime pins, source hashes or permission versions as authority.
Logical creation is idempotent across event keys; replay cannot reactivate a revoked grant.

The source-file primitive preserves full UTF-8/CRLF bytes, stable identity, explicit optional
absence and declared-plan coverage. It rejects credential-bearing sources, linked/reparse
paths, aliases and replacement/read races. It cannot prove that a fixed file plan covers
an actual production surface. The production collector must also regenerate directory
membership and typed SQL/config/registry projections; an added skill or attachment must
invalidate the semantic manifest even when the old files have not changed.

Selection capture reads only persisted nonsecret main/explicit-legacy fields, without
decryption, backup writes or fallback. It builds the existing adapter's exact11-field pin
from an allowlisted binary hash, installed SDK and profile hash, without executing native
version/auth commands. Runtime/auth verification remains the adapter's separate boundary.
Unsupported provider/auth/fast/reasoning/configuration is never mapped to another selection.

Fixture inventory is host-private and separate from the common baseline. Each canonical
entry pins source reference/version/identity, raw and fixture hashes, timezone/period/cutoff,
schema/redaction/sufficiency, permission ID/version and a freshness policy. Snapshot IDs,
snapshot manifest hashes and grant hashes are excluded from the inventory identity. Create
the inventory/grant first, snapshots carrying that grant second, and case-set membership
afterward. This avoids a circular hash and does not expose or consume qualification data.

At the bound runner boundary, every selected case snapshot must match exactly one authorized
inventory entry, its case-set/snapshot hashes and grant, with both encrypted raw/fixture
blobs intact. A matching grant hash alone cannot authorize an unrelated source. Context and
fixture-source hash overlap is rejected as a basic shared-input leakage guard; production
semantic role classification and later experiment contamination checks remain required.

`snapshot_version` permits only the specifically authorized frozen version while permission
remains valid; `require_current_version` additionally requires current-origin compatibility.
The trusted source observer must enforce the selected policy and retention/tombstone rules,
return the frozen inventory or fail, and never fetch a newer version silently. The current
components validate observer evidence; production source-provider permission enforcement is
still required before any owner execution API is enabled. Grant revocation and fresh source/
config/target checks occur around I/O; metadata/old context remain historical evidence.

These components do not declare an ordinary dashboard collector complete. Complete typed
dependency composition, source-provider integration and owner API remain unfinished. No
source inventory or qualification content is inserted into the common owner baseline merely
to satisfy a completeness flag.

### Concrete current-file provider and catalog observations

`BrainFileFixtures` observes explicit `brain-file:<portable-relative>` references inside
the authenticated owner's canonical brain. Host-selected metadata declares timezone,
period/cutoff/schema and `require_current_version`; it cannot declare roots, hashes or
permission versions. Native Windows DACL/owner/group or Linux mode/identity/ACL projections,
root/ancestor incarnation, source bytes and credential/path policies determine authority.
Permission versions are positive hashes of observed authority, not monotonic ACL counters.
Hard links, reparse paths and denied credential/internal paths are unsupported. Observation
returns metadata only; capture returns exact UTF-8 raw/fixture bytes and manifest fields for
host-added grant/time. All selected members are re-observed together before returning.
Mutable files cannot prove retention of an old version, so this provider refuses
`snapshot_version`; an immutable version provider remains a separate requirement.

`capture_catalog` observes actual canonical/legacy agent/workflow paths, canonical/disabled/
mirror skill precedence, loops/pause state and skill priority. It records absent directories,
exact native filenames, full file hashes/identity and metadata projections, then repeats
membership/source observation. It does not load plugins, sync mirrors or return raw bodies.
Ordinary YAML dates are canonicalized to ISO strings; aliases, malformed data, credentials
and oversized inputs fail closed. This catalog is one context dependency, not a full main
surface. Source access is a host operation; it is not exposed to an isolated solver.

### Typed private context and frozen volatile rendering

Private context capture version2 records file, SQL, inventory, runtime and renderer sources
with roles, locators, presence/identity, source and projection hashes. File source hashes
pin original UTF-8 bytes; SQL/other semantic hashes pin canonical JSON projections. Protected
string projections must appear verbatim in the rendered baseline. File discovery requires
metadata mappings. Source payloads remain separate private evidence: validation never
automatically appends them to the model input or infers complete production coverage.
The host also compares raw UTF-8 hashes of protected strings against fixtures, and rejects
full immutable target source bytes in the common baseline even under a discovery label.
These are basic alias guards; collectors must still classify/exclude case/qualification
inputs and mutable target bodies correctly.

An optional trusted collector `revalidate(..., frozen_capture=...)` receives the persisted
host capture on the second observation and live guards. It must freshly observe nonvolatile
dependencies while retaining the original clock/usage render. Returned captures still pass
closed validation and exact binding comparison. Deterministic legacy collectors continue
to be called normally. This protocol does not expose a client-selected frozen clock or make
a partial collector a supported production surface.

### Existing SQL source lifetime

`SQLiteContextProjection(session_db, workflow_db=None, existing_sources=..., principal_type=...,
redactor=...)` captures only a specifically authorized ordinary web session, its matching
project, ordered file/link memberships and latest workflow metadata. It excludes messages,
workflow inputs/outputs and private evaluation content. Explicit legacy brain aliases must
resolve to the same authenticated canonical root. It records semantic rows, not volatile
database or shared-memory hashes; unrelated message inserts do not invalidate the context.

Every declared source requires `ExistingSQLiteSource(path, connection, lock)` using the
production store's already initialized native SQLite connection and its actual mutation/
close lock. Capture does not open source connections, construct stores, migrate schemas,
change host query-only/authorizer/row-factory/progress settings, or copy other brains' data.
Closed main-qualified read statements run within an owned read transaction; preexisting
transactions, wrong/closed connections, linked paths, missing WAL sidecars and nonempty
rollback journals are unavailable. Locks are ordered/deduplicated and retained through
rollback and source checks. Native existing SHM reader coordination is permitted; logical
DB/WAL writes and new source artifacts are excluded.

Each source has one consistent read transaction; separate databases still require outer
double observation/CAS. An omitted workflow source is a host declaration, not proof that
the corresponding production dependency is absent. Coding/pinned-folder, adaptive routing,
plugins, source document contents and complete rendering remain composer responsibilities.

Project/session asset templates are shared pure functions in main_attachment_rendering.
Ordinary main retains its actual15-row,2000-character/file,6000-character total limits and
per-row read/error order. PreparedAttachmentText stores a bounded prefix plus the true
source length so ordinary rendering does not retain every large raw document at once.
Protected trial mode takes full texts/all rows and rejects an incomplete prepared prefix.
It preserves raw CRLF/whitespace and known empty sources; a strict collector must reject
required unreadable/binary sources before declaring complete coverage. These renderers
never authorize or load source content themselves.

The exact Windows2.1.308 CLI/SHA256c9796ba22a9221586b36371b36f5b66198d66eb4badf6a4cf4a5f7b07becea53
and SDK0.2.116 tuple is also verified by explicit developer-only synthetic native
qualification; see agent-skill-runtime-2.1.308-qualification.json. Unknown tuples still
cannot execute through production verification, even for version/auth probes. The isolated
single-query worker temporarily adds the shared no-window flags to SDK descendant launches
and restores its transport afterward. This does not patch the shared chat process or the
installed SDK. Qualification proves the recorded dummy-provider boundaries, not real model,
account, reasoning fidelity, business quality, or an OS filesystem/network sandbox.

### Prewarmed plugin capability inputs

`PluginCapabilityProjection` observes the actual imported plugin/pack modules and the
already loaded cache under its native lock. It checks source precedence, membership,
enabled/environment state, locale, source hashes and loaded callable identity, then
re-observes them. It never loads plugins or calls visibility/registration/settings hooks.
The supported profile is the ordinary owner web turn with no agent. The audited bundled
Goal visibility branch is projected for that exact future turn; an outside-turn menu can
show different tools when another agent is enabled. A real SQLite/actual-main regression
covers this distinction without constructing GoalStore during capture.

This projection represents displayed plugin capabilities. It does not authorize tools or
claim to capture actual hook, HTTP or webhook registration maps. Packs, unknown dynamic
visibility, closures/external handlers, cached errors or inconsistent loaded code remain
explicitly unsupported. A complete Dashboard composer must include or reject its other
prompt contributors separately; this component does not establish full prompt coverage.

### Process-owned trial tasks

`TrialTasks(trials, runner)` owns explicit reservation/dispatch operations independently
of a cancelled HTTP response. `start(principal, request, event_key=...)` retains committing
storage threads and dispatches one runner per brain/trial. It does not verify a runtime or
authorize a context itself; the owner service must perform those checks before calling it.
Idempotency replays refresh current metadata and never silently retry an uncertain attempt.

`cancel(..., expected_generation=..., event_key=...)` commits owner cancellation before
cancelling its local worker task. `close()` prevents new starts, drains pending operations,
then closes only acquired leases using writer-bound aborts and awaits worker cleanup.
`TrialRunner.run(..., on_claimed=...)` is a private synchronous host notification of an
actual claim; its token never becomes public trial metadata. A failing notification refunds
the claim before any adapter. An opaque lease from another host is never owner-cancelled
by shutdown. Unclaimed cleanup uses the existing strict no-lease CAS.

Repeated cancellation cannot abandon an owned storage operation or shutdown. Post-commit
read failures attempt a refund; failures to settle a committed/owned operation remain a
visible `trial_shutdown_incomplete`, including after its task leaves the live registry.
The application lifespan must await close before closing source/store lifetimes. This
component is not yet HTTP/lifespan wiring, a recovery scheduler or an enabled trial API.

### Controlled selected-engine feasibility

`tools/run_agent_skill_trial_smoke.py` requires explicit absolute settings, native binary
and fresh private output paths. Default operation only captures selection/pin metadata;
it performs no native auth or query. Explicit --live verifies the selected native runtime
and account profile before reservation, then runs one tuning baseline through real
encrypted stores, TrialRunner, the existing ledger and NativeUsageRecorder. It creates
no Goal/Team identity, candidate, promotion or activation. Windows output paths are checked
in UTF-16 units before root/key creation or native preflight; choose an explicit shallow
output directory rather than relying on unqualified extended paths.

The retained output contains a sanitized report, encrypted evidence and its private
disposable Fernet key. Key loss prevents historical decryption. The final report survives
cancelled marked calls and returns exit130 for cancellation; optional usage recovery cannot
erase a durable start marker. Product evaluation, execution checks and aggregate verdict
are separate. A successful query/JSON checker is insufficient to promote a revision.

The 2026-10-23 selected-engine evidence export records one actual subscription query with
requested/observed claude-opus-5-5 and configured medium effort. Fixture read and the JSON
product checker passed. Aggregate verdict remains unknown: actual reasoning/internal skill
use and some native event/tool outcomes are unobserved. Observed token totals4362/158 have
partial request attribution; requests remains null. The USD0.0226818 subscription equivalent
is a reference value, not an additional cash bill. No repeat query was run to improve that
result. This is synthetic technical evidence, not ordinary Dashboard context or business
value. Full owner API wiring follows agent-skill-owner-trial-api-v1.md and remains disabled.

### Current ordinary SDK executable

`resolve_sdk_cli(actual_claude_cli, actual_claude_sdk_engine, state_root)` resolves the
ordinary current SDK selection without invoking its mutable finder. It verifies imported
module/callable identity against audited source, follows JAVIS_CLAUDE_CLI then current PATH/
extra directories then the actual loaded-or-cold remembered cache, and resolves twice with
native file identity checks. It reads a cold cache without warming it, never writes cache
state and never invokes the CLI, authentication or settings decryption. Unsupported source,
runtime, relative/bundled/linked/unresolved selections fail closed. Existing create_pin
validates the returned current binary independently; its allowlist never chooses a path.
The source guard is deliberately strict, so discovery source changes require parity review.
