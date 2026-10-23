# Owner trial API contract, M1

Status: implemented on the M1 development branch. The owner routes are registered;
trial and activation capabilities remain false pending final integration verification.
The M0-M8 Goal authorizes implementation; this document freezes the service boundary
for Task5 and does not authorize production activation.

## Authority and dependencies

Every new route uses the application's real browser-owner session check, including
when full API tokens also pass global authentication. Retain global CSRF checks and
use POST/PUT for mutations. Resolve the selected brain with the existing strict resolver;
actor, principal, authority, callbacks, binary, model and reasoning never come from JSON.
When authentication is disabled, preserve the application's existing local-owner policy.

GET/status uses only the existing read-only improvement store. Cold status neither creates
or migrates a database nor constructs an adapter, captures context, verifies authentication,
loads a plugin, heals memory or starts a model. Error responses contain fixed host codes
and localized text; no exception text, source path, answer or native stderr is echoed.
Bound incoming bytes before parsing; reject duplicate/unknown keys, nonfinite JSON,
boolean integers and unsupported nested union members.

Production composition uses the actual imported main module. It retains the already
initialized SessionStore connection/lock/path and WorkflowRunStore connection/lock/path
through ExistingSQLiteSource. Missing/cold/closed sources are unavailable; never call a
store constructor to warm a capture. DashboardContextCapture and PluginCapabilityProjection
use those actual dependencies. The supported surface is exactly
`{kind: "dashboard", session_id, language}`; the stored SQL session supplies project
membership. Native user root is the default `Path.home()/".claude"`; custom roots remain
unsupported. Collector gates and unknown trial invariants remain separate.

Resolve the current ordinary SDK binary without mutating discovery. Its priority is
JAVIS_CLAUDE_CLI, then the actual tim_binary filesystem/cache selection, not the separate
find_claude_cli/JAVIS_CLAUDE_BIN path. Pin only the strictly projected persisted selection
and that exact current binary through create_pin. Unresolved/unsupported current binaries
do not trigger a search for an older qualified installation or the SDK bundled binary.
Metadata paths never run native version/auth checks. Explicit start performs actual adapter
verification off the event loop before reservation; Runner rechecks at the query boundary.

HostContextStore receives the concrete Dashboard collector, strict current pin callback,
fresh canonical user-owned skill target observer, and BrainFileFixtures. Provider grants
are host-observed, require_current_version only, and precede snapshot IDs. Source selections
describe owner-requested scope; clients cannot assert permission, identity or content hashes.

## Closed requests

All mutations include event_key, scoped by authenticated brain. Path IDs, revision IDs,
positive generations, repeat/seed/timeout and event keys reuse the actual store validators
and bounded service limits. Successful creation returns real stored IDs/hashes; callers
never supply internal trial pins or assert complete context.

| Route | Exact request fields besides path IDs/brain query |
| --- | --- |
| POST /improvement-contexts/{experiment_id} | event_key, surface, target_ids, source_selections |
| GET /improvement-contexts/{experiment_id} | No body; safe binding/grant metadata only |
| POST /improvement-contexts/{experiment_id}/revoke | event_key, expected_generation |
| POST /improvement-contexts/{experiment_id}/snapshots | event_key, source_ref |
| POST /improvement-contracts | event_key, objective, output, criteria, primary_metric, unknown_conditions |
| POST /improvement-contexts/{experiment_id}/case-sets | event_key, contract_id, seed, cases |
| POST /improvement-bundles | event_key, revision_ids |
| POST /improvement-trials | event_key, experiment_id, contract_id, case_set_id, case_id, base_bundle_id, candidate_bundle_id, variant, repeat, timeout_seconds, expected_context_generation |
| GET /improvement-trials/{trial_id} | No body; stored metadata and safe evaluation/usage summaries |
| POST /improvement-trials/{trial_id}/cancel | event_key, expected_generation |
| POST /improvement-trials/recover | event_key |
| POST /improvement-trials/{trial_id}/attempts/{attempt}/owner-decisions | event_key, criterion_id, verdict, output_hash, contract_hash, expected_generation |
| POST /improvement-trials/{trial_id}/attempts/{attempt}/reevaluate | event_key, expected_generation |

Register fixed recover routes before variable-ID routes where needed. No HTTP claim,
heartbeat, materialize, engine-start or finish operation exists.

Each source_selection is exactly
`{source_ref, timezone, data_period, cutoff, schema}`. The service fixes
freshness_policy=require_current_version and obtains all other inventory fields from the
concrete provider. Snapshot capture privately resolves the active bound inventory entry,
captures with expected_entry, and supplies the stored grant_hash and host captured_at.
Neither raw bytes nor caller-selected expected metadata/permission versions are accepted.
Expose no private binding decoder or unscoped source-reading endpoint.

Contract output is exactly `{kind: "text", path, media_type}`. A criterion is exactly
`{id, category, critical, description, kind, config}`. Reuse the actual closed structural
config unions for file_exists/file_contains/json_keys and owner_confirm. For host_checker
or command_ok the public config contains only checker_id; the host resolves a closed trusted
registry entry with version, source hash and callable. There is no arbitrary command,
callable upload or model-generated checker. The host supplies version, evaluator pin and
raw output hash policy. Mandatory/business categories and a business primary metric are
required by the existing contract validator. Unsupported checker execution remains unknown.

Each case is exactly `{id, family_key, split, task, snapshot_ids, answer}`. Answers are
authorized private owner imports, never returned by list/status or inserted into common
baseline context. Host evaluator_refs come from the selected approved contract. Validate
all referenced snapshots against the selected context grant, including qualification and
regression cases, without exposing or consuming holdouts during authorization.

CaseStore retains its component interface; TrialCaseAssociations persists an immutable
scoped service association in the existing database. Case creation and the association
share one writer transaction. Historical unassociated component records remain readable
but cannot be adopted or dispatched through the owner API, even with identical input.
Trial start resolves only approved associated context/contract/case set and actual stored
bundle/revision membership, constructs the existing nine pins and internal request, and
uses the host clock for deadline. Qualification reservation uses the real CaseStore, never
a fake Goal or a caller-generated qualification token. No auto retry after an uncertain run.

Owner decisions are terminal evidence bound to the actual output/contract hash and criterion
generation. Reevaluation uses the trusted registry and appends history; neither can reopen
an execution, call a model, mutate usage or alter earlier results.

## Task and shutdown ownership

Retain one process-owned TrialTasks per service/store lifetime. Storage, filesystem, pin,
source observation and native verification run off the event loop. Start returns 202 with
the actual persisted status, which can still be reserved. Cancelling a response cannot
abandon a committed request. Explicit cancellation commits before local task cancellation.
GET never launches/resumes work; explicit recovery only settles expired leases using the
real host clock, without querying or silently retrying.

Await TrialTasks.close at the beginning of main shutdown, before source/store/MCP/executor
teardown. Shutdown closes only acquired writer tokens; foreign opaque leases remain intact.
Unsettled storage cleanup remains a visible failure, including after a task finishes.

One model trial per brain is the default. A cross-process brain lifetime lock spans
materialization, worker closure, evaluation and plaintext cleanup. An independent atomic
database check rejects another running/evaluating trial before its ledger marker. Capacity
rejection does not silently queue or retry a model query; its unstarted attempt is not_run
and refunded. A different brain retains independent capacity. The active-experiment limit
belongs to M2's experiment lifecycle, beyond M1's immutable admission-plan records.

Execution workspaces are temporary decrypted copies. Normal completion, cancellation and
host abort remove them after the owning worker/evaluator has settled; encrypted artifacts,
receipts, results and the cleaned journal remain available. A per-attempt kernel lock keeps
recovery from deleting a live worker's workspace, including after durable cancellation or
lease expiry. Unlink failures remain explicit and are retried by scoped owner recovery.
After a host crash/restart, invoke the explicit recovery endpoint for the authorized brain;
it settles expired attempts and retries terminal-workspace cleanup. Replaying its event
keeps the original durable result and still retries pending cleanup. Startup and cold GET
do not dispatch or automatically recover work. Untracked files and other brains are outside
this recovery operation. No uncertain query resumes.

The process service retains admission from the first preflight await through handoff.
Concurrent identical requests join one operation; a changed payload under the same event
key conflicts before a second native probe. Qualification claims, trial, call ledger,
the immutable experiment plan and successful owner admission commit in one transaction.
The plan freezes all nine pins and a versioned explicit-owner timeout policy. The owner
chooses1..300 seconds for a single attempt; the host allocates the deadline once after
preflight, and replay preserves that original deadline. This is a technical wall-time
bound, not a money cap or a change to the selected model/reasoning.

Only an exact frozen current baseline and a candidate with identical captured bytes are
admitted by this M1 surface. Changed candidates require M2's separately validated authoring
path. Equal arms support technical plumbing checks and do not establish improvement.
Historical same-event replay uses stored records without probing sources/authentication
or reopening terminal work. A new event for an already admitted logical slot conflicts.

Status returns separate product, execution and aggregate verdicts. Its usage has an
explicit stored_result or unavailable origin; GET neither ingests the ambient usage index
nor invents zero tokens/requests/cash. It omits raw output, private checker explanations,
answers, baseline content and native paths. Artifact hashes identify the recorded product;
business owner confirmation still requires independent review of that exact product.
The M2 operator report must supply the authorized product-review experience; a synthetic
API confirmation is only a protocol test. No business acceptance is implied here.

Creating a new bundle event obeys mode=off inside the existing writer transaction.
Historical same-event bundle replay remains available. Revocation, cancellation, recovery
and historical decision evidence remain separate from permission to launch new work.

## Required verification before capability enablement

- Actual owner/auth/CSRF and cross-brain rejection; no startup background tasks in tests.
- Cold metadata/status does not migrate, capture, probe auth or query a model.
- Strict request bounds/duplicates/nested schemas; no answer/credential/path error leakage.
- Real scoped snapshot/grant/contract/case-set associations and immutable idempotency.
- Current source/model/policy/generation drift blocks reservation and query markers.
- One real runner, strong HTTP cancellation ownership, durable cancellation and shutdown.
- Unsupported current engine/context returns unavailable without model fallback or retry.
- Safe real usage/result summaries; actual output verdict and execution unknowns stay distinct.
- Recovery never resumes uncertain work; owner reevaluation never creates usage.
- Full focused regression, intentional route snapshot, fresh independent review and final CI.

Controlled native feasibility is recorded separately in
agent-skill-selected-engine-feasibility-2026-10-23.json. It proves one synthetic runtime/
artifact/checker slice, with an aggregate unknown verdict. It does not enable this API,
prove full production context, establish business gains or authorize activation.
