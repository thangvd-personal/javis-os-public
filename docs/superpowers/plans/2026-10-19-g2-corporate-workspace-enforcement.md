# G2 Corporate Workspace Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved G2 Corporate Workspace controls, application integration and reproducible acceptance evidence without opening business writes or operation automatically.

**Architecture:** Extend the existing IAM adapter port, MCP Gateway PEP, audit outbox and Corporate runtime. Resolve trusted attributes on the server, bind every operation and output to its authorized workspace, and enforce policy/revocation/halt/audit at the final dispatch and delivery boundaries. Keep deployment controls, operational parameter approval and acceptance separate from code completion.

**Tech Stack:** Existing Python/FastAPI, pytest, SQLite, `cryptography>=42.0`, JSON Schema Draft 2020-12, Django/DRF + PostgreSQL/Redis in SME_BTOS, dashboard JavaScript and existing Node test conventions. No new framework or policy engine.

**Spec:** [G2-DDR-v1](../../../brains/SME-BTOS/documents/g2-pilot/g2-design-decision-register.md), [contract v0.3 §14.5](../../../brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md), [19 acceptance criteria](../../../brains/SME-BTOS/documents/g2-pilot/g2-cwe-acceptance-matrix.md), [28 planned cases](../../../brains/SME-BTOS/documents/g2-pilot/g2-test-case-register.md), [traceability](../../../brains/SME-BTOS/documents/g2-pilot/g2-traceability-register.md), [inventory](../../../brains/SME-BTOS/documents/g2-pilot/g2-enforcement-inventory.md), [execution/evidence plan](../../../brains/SME-BTOS/documents/g2-pilot/g2-test-execution-evidence-plan.md). Read these together; the earlier readiness-preparation plan covers test preparation only.

## Global Constraints

- G0: `APPROVED — TECHNICAL SECURITY CLOSURE`; G1: `APPROVED — CORPORATE READ-ONLY PILOT`; G2: `PREPARATION APPROVED — NOT OPEN FOR OPERATION`.
- IAM/PDP remains the authorization authority. Consumers depend on `IamPdpAdapterPort`, never Django models or SME_BTOS internals.
- DENY by default. Only explicitly approved read operations and independently authorized `prompt_use`; read does not imply download, share or prompt use.
- Do not enable `write`, `delete`, `publish`, transaction, `export`, Corporate-to-Personal share or persistent Corporate conversations/results. Security control/audit bookkeeping is not a business action grant.
- Evaluate both resource permission and workspace boundary; shared source permission never grants another employee's private artifacts.
- All prompt inputs, retrieval, tools, worker retries and output delivery remain bound to verified context. No Corporate fallback to Personal.
- `900s` is the contract decryption-lease ceiling. `300s` decryption lease and `30s` heartbeat are an **evaluation baseline**, not a revoke SLA or signed production profile. Never invent other operational numbers.
- Prompt/history/output default to request/session-only. Cleanup and audit metadata retention are different policies. Unknown classification or stale authority cannot become ALLOW.
- Do not edit frozen schemas/API silently. Internal interfaces below are planning choices, subject to plan review; a wire-contract change requires an explicit change record and reapproval.
- No real provider credentials, employee data or sensitive raw payloads in tests, evidence or source control. No guarantee of retrieving bytes already delivered to a provider/person.
- Code completion, readiness to execute, G2 acceptance and operational opening are separate decisions. Preserve all `CHƯA XÁC MINH` statuses until actual reviewed G2 evidence exists.
- Read applicable AGENTS and `docs/quy-uoc-dev.md`; implementation uses an isolated worktree, preserves untracked preparation artifacts, follows the repository version reservation/PR conventions and English code/commit messages. This planning turn makes no version bump, commit, deployment or runtime change.

## Review Focus

1. Revoke/halt between authorization and dispatch or chunk delivery: close the race, measure propagation, never equate TTL with SLA (Tasks 7, 8, 10).
2. Two browser tabs/workspaces and background completion: no cross-subscriber fan-out, snapshot or notification leakage, including reconnect (Tasks 12, 14).
3. Registry expiry or policy rollback while a previously allowed action is cached: re-resolve authority and policy version before reuse (Tasks 3, 8, 9).
4. Audit disk-full/crash after dispatch but before outcome: preserve an unresolved attempt, reconcile, never invent success or automatically replay (Task 6).
5. Shared source authorized for A and B but private derived content authorized only for B: resource reuse must not widen artifact/search/key scope (Tasks 4, 11, 12).

---

## Delivery boundaries and current findings

This is one integrated security feature with four dependent work packages, not four independently shippable products. Keep one dependency map and interface vocabulary; individual task commits are reviewable without activating G2. The chosen execution method remains the previously authorized subagent workflow, with parallel work only where dependencies and file ownership allow.

Source inspection on 2026-10-19 found:

- `server/iam_contract/schemas.py` loads schemas from ignored `brains/`; a clean checkout needs an immutable, checksummed distribution.
- `McpGatewayPepService.execute_enterprise_action` exists, but provider metadata currently includes caller values/static registry and heuristic classification. Do not treat those as trusted evidence.
- Both adapters currently compare a resource workspace directly with the current workspace. Shared-resource semantics need an explicit registry-backed policy, not removing workspace checks globally.
- `CorporateTaskWorker`, `ArtifactLineageTracker` and cache interfaces are existing building blocks; their in-memory tests do not establish full application integration or durable cleanup.
- `CorporateLocalGuard` checks a route boolean; deployment egress proof is separate. Cache currently uses a custom XOR transform and `read_cached_artifact(cache_key)` lacks caller context.
- `ChatRuntime.publish` fans events to all registered writers and `snapshot` is not workspace-scoped. Corporate delivery must not reuse that behavior unchanged.
- Corporate preparation tests exist; planned `G2-TC-020..028` are descriptions, not executable tests yet.
- GitNexus lookup for `javis-os-public` returned repository-not-found in this session. Source reads were the fallback; this is not an impact-analysis result. Before editing symbols at implementation time, restore indexing and run the required impact checks; for cross-repo contract work, run group impact/sync.

Commands below run from `D:/btos_javis/javisos` unless a different directory is stated. A RED is the intended assertion/import failure, not missing dependencies, schema files, database access or a broken fixture. Use fake clocks and barriers, not wall-clock sleeps. Every numbered step is a 2–10 minute action; repeat the task's RED/GREEN cycle per named behavior when it would exceed that size. Stop and apply systematic debugging for an unexpected failure.

## File structure and interface ownership

New paths below are **planned**, not existing implementation. Existing files named here were inspected or located in this session.

| Owner task | Planned unit | Responsibility |
| --- | --- | --- |
| 1 | `docs/superpowers/specs/2026-10-19-g2-implementation-inputs.md`, `docs/dev/g2-route-inventory.json` | Approved input/dependency ledger; complete route/producer/store inventory for the selected build |
| 2 | `tools/build_iam_contract_bundle.py`, `server/iam_contract/bundle.py`, `server/iam_contract/resources/v1/` | Reproducible, generated immutable contract/schema snapshot + source manifest |
| 3 | `server/corporate_enforcement/types.py`, `profile.py`, `authority.py` | Internal records, validated profile and authoritative context/registry resolution |
| 4 | Existing Mock/SME contract services; new SME shared-scope service/model | Resource/workspace sharing policy behind the unchanged adapter port |
| 5 | `server/corporate_enforcement/operations.py`; existing connector catalog | Canonical operation/alias mapping and strict G2 allowlist |
| 6 | `server/corporate_enforcement/audit.py`; existing audit outbox | Durable attempt/outcome, health/backlog gates and reconciliation |
| 7–8 | `server/corporate_enforcement/halt.py`, `dispatch.py`; existing PEP | Scoped halt and final dispatch fence |
| 9–10 | `server/corporate_enforcement/provider.py`, `revocation.py`; existing worker | Prompt egress, streaming checkpoints, revoke propagation and cancellation |
| 11–12 | `server/corporate_enforcement/cache.py`, `artifacts.py`, `cleanup.py`; existing guard/lineage | Authenticated encryption, scoped ephemeral stores and cleanup |
| 13–15 | `server/corporate_enforcement/application.py`, `server/routes/corporate.py`, `operator.py`; existing main/MCP/chat/dashboard | Composition, ingress, scoped UI events and administration |
| 16–18 | Deployment profile/runbook, dual-adapter tests, `tools/run_g2_evidence.py`, CI | Network proof, reproducible tests and evidence/gate package |

### Shared internal vocabulary (defined in Task 3)

Use `Record = Mapping[str, Any]`, `JsonObject = dict[str, Any]`, `Clock = Callable[[], float]`; do not leak these internal records into frozen wire schemas without explicit mapping/validation.

- `Binding`: frozen dataclass with `principal_id`, `tenant_id`, `workspace_id`, `session_id`, `device_id` (all `str`). The service constructs it from verified authority, never from a request body assertion.
- `AuthoritySnapshot`: frozen dataclass with `binding: Binding`, `execution_context: Record`, `resources: tuple[Record, ...]`, `policy_version: str`, `registry_version: str`, `expires_at: float`, `authority_epoch: str`.
- `G2Profile`: frozen dataclass with `profile_id: str`, `version: str`, `environment: str`, `approved_parameters: Record`, `evaluation_only: bool`. Parameter names and owner approvals are recorded in Task 1; no magic fallback values.
- `G2Denied(PermissionError)` has `reason_code: str`; use existing public reason codes when contract-defined. Any new public code mapping is reviewed in Task 1; internal codes do not modify effect enums.
- Dispatch callback: `Callable[[], Any]`; the final controlled boundary invokes it only after all checks. Never accept a callback from untrusted HTTP/MCP payloads.

## Dependencies, parallel execution and stop conditions

| Wave | Tasks | Parallel ownership |
| --- | --- | --- |
| A | 1 then 2 | Task 1 first closes implementation inputs; Task 2 packages approved sources |
| B | 3, then 4 / 5 / 6; then 7 | 4 owns adapters, 5 owns catalog/operations, 6 owns outbox/audit; 7 consumes completed audit and owns halt |
| C | 8 then 9 / 11 | Task 8 alone edits PEP; 9 owns provider module, 11 owns cache/guard |
| D | 10 then 12 | Task 10 alone edits worker/runtime; Task 12 follows it for lineage/store integration |
| E | 13 then 14 / 15 / 16 | Task 13 owns main/MCP composition; 14 owns chat/dashboard, 15 operator module, 16 deployment proof |
| F | 17 then 18 | Integrate dual-adapter suite, then evidence/whole-branch verification |

No subagent concurrently edits PEP, `main.py`, `corporate_runtime.py`, the same schema/profile or the same registry. Review each task before dependent tasks start. Merge task branches into the feature worktree only after their focused checks pass. Do not start dependent P0 behavior until its input ledger entries are CLOSED; unaffected generic tests/packaging can proceed. A missing value remains a named blocker, not a guessed default.

## Tasks

Each representative test body below belongs to that task's Step 1. Its named
fixture is a new test-local fixture to write in the same test file, using the
setup/scenario specified in Step 1 and attributes shown in the test. Construct
the real owning service with synthetic authorities, clocks and recording sinks;
do not mock the enforcement method under test. The full named test set remains
required, not only this representative assertion. No test code is created now.


### Task 1: Close build-specific implementation inputs and scope

**Files:** Create `docs/superpowers/specs/2026-10-19-g2-implementation-inputs.md`, `docs/dev/g2-route-inventory.json`; reference G2-DDR-v1 and all six G2 source registers. No product code changes.

**Interfaces:** Consumes DD-001..009/PAR-001..010. Produces ledger entries `{parameter_id, value_or_artifact, owner, approver, version, status, blocked_tasks}` and inventory rows `{surface, entrypoint, sink, classification_source, enforcement, audit_producer, store, disposition}`. Allowed dispositions: `G2_ROUTED`, `G2_DENIED`, `PERSONAL_ONLY` with server-enforced identity separation.

- [ ] **Step 1:** Record which PAR values are supplied; attach approvals and versions. Leave numeric revoke/authorization lease/cleanup/backlog values unresolved if absent. Map blockers: PAR-002 -> 3/4; PAR-003/004 -> 10/11/12; PAR-005 -> 9; PAR-006 -> 6; PAR-007 -> 8/11/13/16; PAR-009 -> 7/15; PAR-010 -> 2/17/18. IDs/data allowlists and device instances must close before their pilot run.
- [ ] **Step 2:** Inventory registered HTTP/WebSocket/MCP ingress, actual provider engines, tools/plugins/shell, task/schedule/retry, retrieval/files/history/backup/logs and external channels. Include `server/main.py`, `mcp_hub.py`, `chat_runtime.py`, `sessions.py`, `session_brain.py`, `routes/`, `dashboard/brains-ui.js`, `dashboard/app.js`. For each, identify exact handler and sink; unknown disposition blocks completeness, never defaults Personal.
- [ ] **Step 3:** Write the P0 topology/credential diagram and model-location decision with Platform/Security, plus internal resolver, profile and public reason-code mappings. Reuse IAM/MDM/registry sources already available; select their configured endpoints/authentication and freshness rules. Close the relevant ledger before implementing that interface. Do not add an unapproved wire field for sharing metadata; use existing extension points only if schemas explicitly permit them.
- [ ] **Step 4:** Validate inventory JSON with `python -m json.tool docs/dev/g2-route-inventory.json`; manually reconcile it against FastAPI route registrations, MCP registration and provider dispatch locations. Expected: valid JSON, no unmapped active Corporate route and explicit remaining owner blockers. Record readiness per task, not a blanket G2 approval.
- [ ] **Step 5:** Commit only these two documentation files after review: `git add docs/superpowers/specs/2026-10-19-g2-implementation-inputs.md docs/dev/g2-route-inventory.json` then `git commit -m "docs: close G2 implementation input boundaries"`.

### Task 2: Package frozen schemas and approved source provenance

**Files:** Create `tools/build_iam_contract_bundle.py`, `server/iam_contract/bundle.py`, generated `server/iam_contract/resources/v1/manifest.json` and schema files; modify `server/iam_contract/schemas.py`; test `tests/contract/test_g2_contract_bundle.py`. Include the approved design snapshot in generated `docs/superpowers/specs/g2-approved-baseline-v1.md` with source hashes.

**Interfaces:** Consumes canonical local contract/schema/docs and PAR-010 distribution decision. Produces `build_bundle(source_dir: Path, destination: Path) -> JsonObject`, `load_bundle(bundle_dir: Path) -> tuple[dict[str, JsonObject], Registry]`. Preserve `validate_contract_payload(schema_name: str, payload: Record) -> None` and schema `$id`/references.

- [ ] **Step 1: RED test:** `test_clean_checkout_bundle_validates_and_detects_tamper` builds into `tmp_path` from approved canonical inputs, hides the source path, validates a known context, mutates one bundled byte and asserts `ContractSchemaError`. `test_missing_bundle_is_actionable` asserts missing manifest fails explicitly, never silently skips validation. Both test files/source snapshots use synthetic data.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_contract_bundle.py -q`. Expected RED for missing bundle loader/build behavior, not unavailable source input.
- [ ] **Step 3:** Implement the two signatures; manifest records source/version/hash for every file, including approvals. Check in an immutable **generated** snapshot, not a second editable authority. Loader uses packaged schemas in clean builds, rejects hash/version/reference mismatches and invalidates cached validators when the explicit bundle version changes. Changes to source require a rebuild/change record.
- [ ] **Step 4:** Run `python -m pytest tests/contract/test_g2_contract_bundle.py tests/contract/test_g2_readiness_preparation.py -q`. Expected GREEN with source-hidden and tamper cases; inspect generated diff is byte-equivalent to frozen source. Add CI reproduction to Task 18.
- [ ] **Step 5:** Commit only the files listed in this task and generated schema/snapshot files: `git commit -m "feat: package verified IAM contract bundle"` after an explicit `git add` list. Never force-add all ignored `brains/`.

### Task 3: Validated profile and authoritative context resolution

**Files:** Create `server/corporate_enforcement/__init__.py`, `types.py`, `profile.py`, `authority.py`; modify `server/iam_contract/http_adapter.py` token-verification boundary; tests `tests/contract/test_g2_authority.py`; extend `tests/contract/g2_readiness_support.py` for synthetic registry/posture providers only.

**Interfaces:** Define the shared vocabulary above. Produce `load_g2_profile(document: Record, *, operational: bool) -> G2Profile`; `AuthorityResolver.resolve(execution_token: str, *, resource_ids: tuple[str, ...], purpose: str) -> AuthoritySnapshot`; `AuthorityResolver.refresh(snapshot: AuthoritySnapshot) -> AuthoritySnapshot`. Constructor consumes `adapter: IamPdpAdapterPort`, `profile: G2Profile`, `registry_lookup: Callable[[str, str], Record]`, `clock: Clock`. Lookup keys/types/response requirements come from the CLOSED Task 1 mapping, not client input.

- [ ] **Step 1: RED tests:** `test_client_posture_and_classification_cannot_override_registry`, `test_expired_or_unavailable_authority_denies`, `test_profile_rejects_unsigned_operational_values`, `test_tampered_execution_token_is_not_verified_by_decoding_claims`. Assert verified binding and server classification stay unchanged after request tampering; stale evidence raises `G2Denied`; missing operational approvals reject startup. Test evaluation-only 300s/30s profile separately from operational profile.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_authority.py -q`; expect RED for missing resolver/profile behavior.
- [ ] **Step 3:** Implement signatures and frozen records. Verify token authenticity and session through the adapter using the CLOSED Task 1 authenticated verification mechanism. Base64-decoded claims plus a successful revocation query are not signature verification; reject payload/signature/issuer/audience/expiry tampering and token-session-principal mismatch without importing Django into the consumer. If the frozen contract cannot support authoritative verification, open an explicit change record and block integration rather than trusting decoded claims. Resolve resource/workspace and attestation from configured authorities. Validate policy/registry version, effective time and expiry using injected clock; never infer classification from resource names or trust `device_trusted`, ZDR or egress booleans. Client hints only identify what to resolve. Resolve every prompt source.
- [ ] **Step 4:** Run the same file plus `tests/contract/test_iam_contract_core.py`; expected GREEN for allowed baseline and stale/forged/rollback variants. Assert no network/connector dispatch occurs in authority-only tests.
- [ ] **Step 5:** Explicitly stage task files and commit `feat: resolve authoritative G2 execution attributes`.

### Task 4: Shared resources without weakening workspace isolation (cross-repo)

**Files:** Modify `server/iam_contract/mock_adapter.py`; create `tests/contract/test_g2_resource_sharing.py`. In `D:/btos_javis/sme_btos/backend`, modify `apps/platform/iam/contract_adapter/services.py`; create `apps/platform/iam/contract_adapter/workspace_scope.py`; modify `apps/platform/iam/models.py` (add a shared-resource grant beside `RegisteredResource`), a new migration and `apps/platform/iam/tests/test_g2_workspace_sharing.py`. The existing `RegisteredResource` is in `models.py`, not a models package; use the next migration number at execution time, never invent it in parallel branches.

**Interfaces:** Preserve `resolve_resource(req: Record) -> JsonObject` and `evaluate_pdp(req: Record) -> JsonObject`. Add SME internal `can_use_resource_in_workspace(*, resource: RegisteredResource, principal_id: str, workspace_id: str, operation: str) -> bool`; Mock implements equivalent semantics from its test registry. The registry-backed grant records resource, permitted workspace/scope, validity and policy version; PDP still evaluates principal permission separately.

- [ ] **Step 1: RED tests:** `test_shared_source_requires_each_principal_permission` and `test_private_derivative_is_not_shared_with_source`: A/B in distinct same-tenant workspaces both read one shared ID only with independent grants; revoking B denies B; private source/derivative remains DENY, cross-tenant always DENY. Assert deny resource resolution does not disclose existence. Run same semantic assertions in both repo suites.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_resource_sharing.py -q`; from SME backend run `python -m pytest apps/platform/iam/tests/test_g2_workspace_sharing.py -q`. Expected RED at the existing strict workspace equality behavior, not database/schema setup.
- [ ] **Step 3:** Implement registry-backed scope check and migration with tenant isolation/indexes and explicit effective/expiry/version fields from Task 1. Keep private equality check unless an authoritative shared grant applies, then require principal permission evaluation. Do not trust sharing scope in a request or duplicate resource IDs/content per employee. Run GitNexus group impact before changes and group sync after integration.
- [ ] **Step 4:** Re-run both files and SME `apps/platform/iam/tests/test_contract_adapter.py`; expected GREEN including grants revoked/expired, tenant RLS and private derived artifact negatives. Validate existing wire response shapes with Task 2 bundle.
- [ ] **Step 5:** Stage/commit task-owned paths separately in each repo: `feat: authorize shared resources within workspace boundaries`. Do not stage other SME changes or change G0/G1 approval records.

### Task 5: Canonical operation mapping and strict read-only gate

**Files:** Create `server/corporate_enforcement/operations.py`, `tests/contract/test_g2_operations.py`; modify `server/mcp_gateway/connector_catalog.py`. Adapter gate parity belongs to Task 4 or a sequential follow-up commit owned by this task; do not edit adapters concurrently.

**Interfaces:** Produce `resolve_operation(connector_id: str, tool_name: str, *, profile: G2Profile) -> str`, `assert_g2_operation(operation: str, *, profile: G2Profile) -> None`. Catalog maps tool aliases to operation and resource resolver; preserve `validate_request`/`invoke` signatures.

- [ ] **Step 1: RED test:** `test_every_registered_alias_obeys_g2_scope` parameterizes canonical/alias/case/unknown forms for read, prompt_use, write/delete/publish/transaction/export/share/download. Assert only explicitly allowlisted read/prompt_use map through; unknown and all prohibited forms raise `G2Denied`; denied handler count is zero.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_operations.py -q`; expected RED for missing alias/policy behavior.
- [ ] **Step 3:** Implement catalog-owned alias resolution and allowlist intersection. Reject destination overrides recursively where connector input permits nested network destinations; don't assume checking top-level URL fields is complete. No model-created operation gets authority.
- [ ] **Step 4:** Run the task file plus `tests/contract/test_mcp_gateway_pep.py`; expected GREEN for allowed reads, SSRF/alias denials and no credential disclosure. Feed resolved operation into Task 8 rather than writing another PEP branch now.
- [ ] **Step 5:** Commit `feat: enforce canonical G2 read operation allowlist` with only listed paths.

### Task 6: Durable audit admission, outcome and outage behavior

**Files:** Create `server/corporate_enforcement/audit.py`, `tests/contract/test_g2_audit_admission.py`; modify `server/mcp_gateway/audit_outbox.py`.

**Interfaces:** Preserve outbox `append_event(...) -> JsonObject`, `verify_integrity() -> JsonObject`, `flush_to_adapter(adapter) -> JsonObject`. Produce `AuditAdmission.begin(snapshot: AuthoritySnapshot, *, operation: str, resource_ids: tuple[str, ...], correlation_id: str) -> str` (durable attempt ID), `finish(attempt_id: str, *, outcome: str, reason_code: str) -> None`, `assert_healthy() -> None`, `reconcile() -> list[JsonObject]`. Constructor consumes outbox/profile/clock; outcome is internal until mapped to existing audit schema fields.

- [ ] **Step 1: RED tests:** `test_sink_outage_requires_durable_bounded_outbox`, `test_full_or_unverifiable_outbox_prevents_read`, `test_crash_between_dispatch_and_outcome_stays_unresolved`. Assert dispatcher count zero when begin cannot commit, healthy configured backlog permits a positive read, and an attempt without outcome after restart is unresolved rather than success/replay. Add concurrent append and partial sink acknowledgement assertions.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_audit_admission.py -q`; expect RED for missing health/admission/reconciliation semantics.
- [ ] **Step 3:** Implement signatures with SQLite transaction/sequence allocation safe under concurrency, configured durability and bounded backlog. Record pre-attempt then terminal outcome, reconcile unknowns; handle disk/write/full/integrity/max-age separately. No configured approved outage limits means no continue-on-outage. Flush partial/duplicate acknowledgements without dropping events; redact values before append, not after persistence.
- [ ] **Step 4:** Run the task file and `tests/contract/test_g1_audit_tamper_detection.py`; expected GREEN for crash/restart, sequence/hash integrity, fault injection and event confidentiality. Record exactly what local commit durability does and does not guarantee for the selected storage topology.
- [ ] **Step 5:** Commit `feat: gate G2 dispatch on durable audit admission` with task-owned paths.

### Task 7: Scoped emergency halt and controlled recovery

**Files:** Create `server/corporate_enforcement/halt.py`, `tests/contract/test_g2_halt.py`.

**Interfaces:** Produce `HaltController.halt(*, scope: str, target: str, actor: AuthoritySnapshot, reason_code: str) -> JsonObject`; `assert_active(snapshot: AuthoritySnapshot, *, destination_id: str) -> str` returns current halt epoch; `recover(halt_id: str, *, actor: AuthoritySnapshot, checks: Record) -> JsonObject`. Constructor consumes durable state path, role-authorizer callback, profile, audit and clock. Scope enum: USER/WORKSPACE/TENANT/DESTINATION. States: ACTIVE/HALTING/HALTED/RECOVERY_CHECK. Role authorization remains IAM/PDP-backed.

- [ ] **Step 1: RED tests:** `test_halting_blocks_before_completion`, `test_recover_one_scope_does_not_clear_overlapping_halt`, `test_unknown_halt_state_and_failed_recovery_stay_denied`. Assert same token/session ALLOW baseline then DENY for all four scopes; wrong-role recovery denied; restart retains state; no timer auto-recovery.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_halt.py -q`; expected RED for scoped state machine behavior.
- [ ] **Step 3:** Implement fail-closed durable state and monotonically changing epoch. Block from HALTING before cancellation completes; bind authorized initiator/target/reason/config/time/correlation. Failure in recovery returns HALTED. Keep overlap checks; infrastructure failure is not ACTIVE. Job/key consumers subscribe through Task 10, operator HTTP through Task 15.
- [ ] **Step 4:** Re-run task file; expected GREEN for authenticated halt, overlap, independent recovery authority and durable audit. Clock-based numeric SLA assertions remain blocked until PAR-003/009 is signed.
- [ ] **Step 5:** Commit `feat: add scoped G2 emergency halt state machine`.

### Task 8: Wire authoritative controls into the final Gateway dispatch fence

**Files:** Create `server/corporate_enforcement/dispatch.py`, `tests/contract/test_g2_dispatch_fence.py`; modify `server/mcp_gateway/pep_service.py` only after Tasks 3–7 land.

**Interfaces:** Produce `DispatchFence.run(snapshot: AuthoritySnapshot, *, resource_ids: tuple[str, ...], operation: str, destination_id: str, correlation_id: str, callback: Callable[[], Any]) -> Any`. Consumes resolver.refresh, operation policy, IAM/PDP decision, halt epoch and audit admission. Add optional constructor dependency `dispatch_fence: DispatchFence | None` to PEP for legacy compatibility; Corporate application composition **must require** it, never silently use the legacy path. Preserve `execute_enterprise_action(...) -> GatewayExecutionResult` and wire payload contract.

- [ ] **Step 1: RED tests:** `test_revoke_between_authorize_and_invoke_blocks_dispatch`, `test_halt_between_authorize_and_invoke_blocks_dispatch`, `test_old_idempotency_result_cannot_bypass_revalidation`. Use a barrier to invalidate authority/halt epoch after preliminary ALLOW; assert callback count 0 and no cached sensitive result returned. Include positive ALLOW, mandatory unsupported obligation and audit failure cases.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_dispatch_fence.py -q`; expected RED at the final dispatch boundary, not merely the route return code.
- [ ] **Step 3:** Implement shared final boundary using current authoritative version/epoch and fresh PDP authorization with audited attempt. Make final epoch validation and local dispatch admission ordered against locally processed revoke/halt; multi-process coordination follows CLOSED Task 1 topology (no claim that a process-local lock solves distributed races). Scope idempotency/result reuse to verified principal/tenant/workspace/session/resource/operation/policy and revalidate before result delivery. Never cache an ALLOW as future authority.
- [ ] **Step 4:** Run task file plus `tests/contract/test_mcp_gateway_pep.py tests/contract/test_g2_readiness_preparation.py`; expected GREEN, no downstream on denied paths, frozen-schema validation and legacy G0/G1 regression intact.
- [ ] **Step 5:** Commit `feat: enforce G2 controls at final gateway dispatch`.

### Task 9: Whole-prompt provider authorization and actual egress

**Files:** Create `server/corporate_enforcement/provider.py`, `tests/contract/test_g2_provider_egress.py`; modify `server/mcp_gateway/retention_evaluator.py` and integrate PEP in a task-owned sequential commit. Actual provider engine wiring is completed in Task 13, not inferred from recording connector mocks.

**Interfaces:** Produce `ProviderGate.evaluate(snapshot: AuthoritySnapshot, *, sources: tuple[Record, ...], provider_id: str, model_id: str, purpose: str) -> JsonObject`; `dispatch(snapshot: AuthoritySnapshot, *, sources: tuple[Record, ...], provider_id: str, model_id: str, purpose: str, correlation_id: str, send: Callable[[], Any]) -> Any`. Sources include verified source reference/type/classification/policy, not arbitrary client labels; `send` runs behind Task 8 fence. Approved provider evidence comes from Task 3 resolver.

- [ ] **Step 1: RED tests:** `test_all_prompt_sources_obey_most_restrictive_policy`, `test_unknown_or_expired_classification_never_becomes_public`, `test_provider_failure_cannot_choose_unapproved_fallback`. Parameterize user input/attachment/history/retrieval/tool output; highest label alone must not drop a lower-label source's purpose/region restriction. Assert send count/bytes 0 for DENY and correct positive destination.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_provider_egress.py -q`; expect RED for missing whole-prompt matrix enforcement.
- [ ] **Step 3:** Implement authoritative taxonomy ordering and intersection of every source's allowed purpose/provider/region/retention/training/fallback. Eliminate heuristic classification and request-supplied attestation from the G2 path. Distinguish internal inference service/workstation profiles; unknown model does not select Personal engine/subscription. Record only references and delivery metadata in audit.
- [ ] **Step 4:** Run `python -m pytest tests/contract/test_g2_provider_egress.py tests/contract/test_mcp_gateway_pep.py -q` and `python -m pytest tests/contract/test_g1_live_scenarios.py -k retention -q`; expected GREEN across the approved synthetic policy matrix. Test a socket-capturing fake provider in addition to the unit callable; no real external provider or data required.
- [ ] **Step 5:** Commit `feat: authorize complete Corporate prompts before provider egress`.

### Task 10: Revocation coordination, worker checkpoints and stream delivery

**Files:** Create `server/corporate_enforcement/revocation.py`, `tests/contract/test_g2_revocation_stream.py`; modify `server/corporate_runtime.py` worker methods. Do not edit lineage concurrently with Task 12.

**Interfaces:** Produce `RevocationCoordinator.observe(event: Record) -> None`, `assert_current(snapshot: AuthoritySnapshot) -> None`, `guard_output(snapshot: AuthoritySnapshot, *, destination: Binding, chunk: bytes) -> bytes`. Consume approved clocks/checkpoint/lease profile, resolver, halt and dispatch fence. Extend worker construction with coordinator and dispatch dependencies; retain existing task method signatures where compatible and add verified token handle/binding internally. Reauthorize from fresh state; a persisted context is not a credential.

- [ ] **Step 1: RED tests:** `test_revoke_between_stream_chunks_stops_next_delivery`, `test_disconnected_worker_does_not_resume_old_authority`, `test_task_binding_survives_ui_switch_but_not_session_end`. Assert each detection/block/output/key/cleanup timeline against the **injected approved** profile, no more chunks or dispatch after local processing, same old token fails; prior bytes cannot be claimed recalled.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_revocation_stream.py -q`; expected RED for checkpoint/output/reconnect enforcement. If PAR-003 is missing, do not manufacture an SLA to make this test pass: complete untimed semantic tests and mark the timed acceptance variant BLOCKED.
- [ ] **Step 3:** Implement push invalidation plus configured polling/authorize/dispatch checks, cancellation and output checks per chunk. Separate monotonic elapsed timing from authoritative wall-clock event timestamp; handle clock skew according to Task 1. Halt/revoke marks queue entries blocked; retry never auto-resumes from a pre-halt grant. UI switch alone need not kill A, but session end/lock must stop it.
- [ ] **Step 4:** Run task file and `tests/contract/test_corporate_guard_and_runtime.py`; expected GREEN for normal, IAM timeout, heartbeat loss, mid-flight race and reconnect. Record measured stage timing rather than equating 300s or 30s with revoke SLA.
- [ ] **Step 5:** Commit `feat: propagate G2 revocation through workers and streams`.

### Task 11: Authenticated scoped cache and independent key leases

**Files:** Create `server/corporate_enforcement/cache.py`, `tests/contract/test_g2_scoped_cache.py`; modify `server/corporate_guard.py`.

**Interfaces:** Produce `ScopedCache.put(snapshot: AuthoritySnapshot, *, resource_id: str, plaintext: bytes, retention_seconds: int) -> str`; `read(snapshot: AuthoritySnapshot, *, cache_key: str) -> bytes`; `lock(binding: Binding, *, reason_code: str) -> int`. Constructor consumes resolver/coordinator, profile/clock and storage path. Keep legacy lease facade for existing tests, but require caller-bound new interface on every G2 read; never use cache_key alone as authorization.

- [ ] **Step 1: RED tests:** `test_cache_ciphertext_and_binding_tamper_fail_closed`, `test_shared_source_does_not_share_cached_private_output`, `test_expired_lease_and_missing_keys_after_restart_cannot_reopen`. Assert cross-principal/workspace/session read denied, authenticated ciphertext mutation denied, TTL/authorization/decryption expiry independent, and no keys/plaintext persisted.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_scoped_cache.py -q`; expect RED for authenticated encryption/caller binding.
- [ ] **Step 3:** Use existing `cryptography` AES-GCM with fresh 256-bit data key and nonce per artifact; bind tenant/principal/workspace/session/resource/policy metadata as associated data. Remove custom XOR from the G2 path. Keep only transient keys, bounded by approved lease <=900s; evaluation 300s/30s only in evaluation profile. Expired or revoked authority never renews keys. No persistent chat cache exception without separate approval.
- [ ] **Step 4:** Run task file and existing guard/runtime tests; expected GREEN including corrupted metadata/ciphertext, cleanup/restart/key loss and IAM unavailable. Document limits of Python memory zeroization and physical disk wiping without claiming guaranteed erasure.
- [ ] **Step 5:** Commit `feat: protect Corporate cache with scoped authenticated encryption`.

### Task 12: Scoped ephemeral artifact/search/history stores and cleanup

**Files:** Create `server/corporate_enforcement/artifacts.py`, `cleanup.py`, `tests/contract/test_g2_artifact_lifecycle.py`; modify `server/corporate_runtime.py` lineage section after Task 10; integrate `server/sessions.py` and `server/session_brain.py` only through explicit Corporate branches.

**Interfaces:** Produce `ArtifactStore.put(snapshot: AuthoritySnapshot, *, kind: str, source_ids: tuple[str, ...], payload: bytes) -> str`; `read(snapshot: AuthoritySnapshot, artifact_id: str) -> bytes`; `search(snapshot: AuthoritySnapshot, query: str) -> list[JsonObject]`; `CleanupService.close(binding: Binding, *, reason_code: str) -> JsonObject`; `retry_pending() -> list[JsonObject]`. Allowed transient kinds include prompt/history/output/summary/extraction/index/embedding/memory/cache/queue/temp. Store-private payloads use Task 11 cache; cleanup journal contains identifiers/status only.

- [ ] **Step 1: RED tests:** `test_search_history_and_lineage_cannot_cross_binding`, `test_source_revoke_blocks_all_descendant_kinds`, `test_cleanup_retry_after_restart_cannot_restore_access`. Assert A/B/Personal canaries never cross responses, results/counts/caches, or notifications; shared source doesn't share B's summary. After failed cleanup, access remains locked, status is failed/pending and retry completes within approved deadline.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_artifact_lifecycle.py -q`; expect RED for scoped lifecycle, not missing fake data.
- [ ] **Step 3:** Implement filtered indexes and caller-aware access on every path; extend lineage to queue/temp and multi-source descendants. Corporate content bypasses default Personal persistent sessions, Second Brain memory, logging, backup/sync and engine resume caches. At session close destroy keys/block immediately and cleanup stores with durable identifier-only retry state/owner escalation; retain redacted audit metadata under separate retention.
- [ ] **Step 4:** Run task file and runtime tests; expected GREEN for every store kind, partial cleanup failures, restart and residual marker scans. No retained prompt/output after default session close; an empty index with a readable raw backing file is a failure.
- [ ] **Step 5:** Commit `feat: isolate Corporate artifacts and enforce session cleanup`.

### Task 13: Application composition and complete ingress/bypass coverage

**Files:** Create `server/corporate_enforcement/application.py`, `server/routes/corporate.py`, `tests/contract/test_g2_application_routes.py`; modify `server/main.py` auth/SSO/chat/stream/WebSocket/route-registration/provider construction seams, `server/mcp_hub.py` Corporate tool routing and `docs/dev/g2-route-inventory.json`.

**Interfaces:** Produce `build_corporate_services(profile: G2Profile, *, adapter: IamPdpAdapterPort) -> JsonObject`; `require_corporate_context(request: Request) -> AuthoritySnapshot`; `register_corporate_routes(app: FastAPI, services: Record) -> None`. Planned internal routes: `GET /corporate/workspaces`, `POST /corporate/workspace-context`, `POST /corporate/read`, `POST /corporate/prompt`, `GET /corporate/session-state`; exact payload mappings are in CLOSED Task 1 input artifact, not amendments to IAM wire API. HTTP/WS context derives from authenticated session and server binding, never URL/path/header alone.

- [ ] **Step 1: RED tests:** `test_corporate_request_never_reaches_personal_engine_or_store`, `test_every_registered_ingress_is_routed_or_denied`, `test_disabled_or_incomplete_corporate_composition_denies_instead_of_fallback`. Assert valid read/prompt goes through actual application composition to fake network sinks; tampered brain/vault/header/scope/alias and legacy chat/file/share/task endpoints get no Corporate data/dispatch.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_application_routes.py -q`; expect RED at application integration, not a direct PEP-only unit test.
- [ ] **Step 3:** Inject required controls and server credentials into the **actual** provider/MCP send path, not only a test connector. Guard all inventory ingress centrally and at sinks; unsupported Corporate feature/engine/channel/plugin/shell/schedule paths explicitly DENY. Protect authenticated users switching Personal/Corporate tabs; a global environment flag or arbitrary brain folder/header is insufficient identity. Prevent credential/config/file endpoints revealing server secrets. Keep Personal behavior isolated and regression-tested.
- [ ] **Step 4:** Run `python -m pytest tests/contract/test_g2_application_routes.py tests/contract/test_g1_sso_code_exchange.py tests/contract/test_mcp_gateway_pep.py -q`; expected GREEN for HTTP, WS and legacy aliases. Reconcile inventory with registered FastAPI/MCP routes and actual model engines; unmapped paths block completion. Verify route snapshots against build identity.
- [ ] **Step 5:** Commit `feat: route Corporate application traffic through G2 enforcement`.

### Task 14: Scoped chat subscriptions, workspace switching and end-user UI

**Files:** Modify `server/chat_runtime.py`, `server/main.py` WebSocket registration/delivery in a sequential integration commit, `dashboard/brains-ui.js`, `dashboard/app.js`, `dashboard/i18n/vi.json`, `dashboard/i18n/en.json`; create `dashboard/corporate-workspace.js`, `tests/contract/test_g2_chat_delivery.py`, `tests/js/test_corporate_workspace.js`.

**Interfaces:** Add `ChatRuntime.add_corporate_client(client_id: str, writer: Writer, *, binding: Binding) -> None`, `corporate_snapshot(binding: Binding) -> list[JsonObject]`, `publish_corporate(event: Record, *, snapshot: AuthoritySnapshot) -> Awaitable[None]`. Existing Personal methods retain behavior but cannot receive Corporate events. UI consumes only Task 13 server-selected workspace/context and redacted denial/status messages; context tokens aren't stored in localStorage.

- [ ] **Step 1: RED tests:** `test_two_tabs_never_receive_other_workspace_stream_or_snapshot`, `test_switch_discards_old_stream_and_reconnect_does_not_restore_private_history`. Create A/B subscribers with identical session display labels and distinct binding; assert A completion never appears at B, including notifications, snapshot and retry. Node tests assert UI switches only on server response, cancels stale render, clears content on lock/logout and never accepts cached localStorage workspace as authority.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_chat_delivery.py -q` and `node tests/js/test_corporate_workspace.js`; expected RED for unscoped fan-out/UI state.
- [ ] **Step 3:** Implement explicit Corporate subscriber filtering with output checkpoint from Task 10, scoped snapshots/jobs and workspace UI module; clear old display on switch/lock and show localized denied/unavailable state. Server-reject unauthorized subscriptions before sending bytes. Include module using existing dashboard asset registration; no UI create/share/export/persistent history flow in G2.
- [ ] **Step 4:** Run the task commands plus `node tests/js/test_workspace.js` and `python tests/run.py i18n`; expected GREEN. Run real-browser checks for two authenticated tabs, workspace switching, stream revoke, reconnect/logout and browser storage inspection. Capture redacted evidence and reject UI-only security claims.
- [ ] **Step 5:** Commit `feat: isolate Corporate workspace chat delivery and switching`.

### Task 15: Scoped audit access and halt/recovery administration

**Files:** Create `server/corporate_enforcement/operator.py`, `tests/contract/test_g2_operator_access.py`; extend `server/routes/corporate.py` in a separate sequential commit.

**Interfaces:** Produce `OperatorService.list_audit(actor: AuthoritySnapshot, *, scope: Record) -> list[JsonObject]`; `halt(actor: AuthoritySnapshot, *, scope: str, target: str, reason_code: str) -> JsonObject`; `recover(actor: AuthoritySnapshot, *, halt_id: str) -> JsonObject`. Route mappings: `GET /corporate/audit`, `POST /corporate/halt`, `POST /corporate/recovery`; each requires IAM/PDP-authorized control scope, CSRF protection where cookie-authenticated, Task 6 durable audit and server-side recovery checks.

- [ ] **Step 1: RED test:** `test_auditor_operator_and_recovery_roles_are_separate` asserts employee can't read admin audit; auditor only sees authorized tenant/workspace redacted events; halter cannot self-grant recovery; client `checks_passed=true` cannot open HALTED; all denied controls audited where durable storage permits.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_operator_access.py -q`; expected RED for new control-service enforcement.
- [ ] **Step 3:** Implement role/scope checks through IAM, audit reads of audit and supply server-derived recovery checks for policy/credentials/backlog/tasks/evidence. Recovery failure keeps HALTED and no queued jobs auto-resume. Do not let test runner/console perform business G3 actions.
- [ ] **Step 4:** Re-run task file and `tests/contract/test_g1_assurance_console_contract.py`; expected GREEN for absent/expired roles, scope tampering, CSRF and recovery failure.
- [ ] **Step 5:** Commit `feat: enforce Corporate audit and recovery administration roles`.

### Task 16: Endpoint posture and real network/credential enforcement proof

**Files:** Create `docs/dev/g2-deployment-profile.md`, `docs/dev/g2-incident-runbook.md`, `tools/verify_g2_deployment.py`, `tests/contract/test_g2_deployment_checks.py`. Deployment-specific firewall/MDM configuration paths are selected and recorded in Task 1 before this task; do not guess firewall commands or modify employee devices from this plan.

**Interfaces:** Produce `verify_deployment(profile_path: Path, *, evidence_dir: Path) -> JsonObject`, CLI `python tools/verify_g2_deployment.py --profile PATH --evidence-dir PATH`. Consumes signed topology/device/profile/credential-custody artifacts, not a client passed_gateway boolean. Report includes separate configured/observed/blocked checks, build/config versions, no secrets.

- [ ] **Step 1: RED test:** `test_deployment_verifier_rejects_boolean_only_gateway_proof` supplies no route capture/device attestation or topology and asserts not-ready; `test_unmanaged_and_expired_posture_deny` asserts unsupported device gets no Corporate dispatch despite valid user identity.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_deployment_checks.py -q`; expect RED for missing verifier/proof requirements.
- [ ] **Step 3:** Implement evidence verification and runbook for chosen topology. Apply approved egress/MDM rules in isolated pilot environment through its deployment owner. Test endpoint direct connections to fake provider/connector, shell/plugin/URL alternatives and credential inventory; keep provider/enterprise credentials server-side. Include local-admin/screenshot/SSD/external-copy residual risk and cleanup/incident owners.
- [ ] **Step 4:** Run unit command then deployment CLI with the **actual approved profile**. Expected GREEN/successful proof only with network/attestation evidence, no direct endpoint route, correct supported-device behavior and halt/recovery drill; otherwise return blocked/fail, never infer network isolation from unit tests.
- [ ] **Step 5:** Commit verifier/test/docs with `test: verify G2 deployment and endpoint boundaries`; store captured evidence only in approved evidence repository, not public Git.

### Task 17: Reusable dual-adapter G2 semantic and application suite

**Files:** Create `tests/contract/g2_semantic_cases.py`, `tests/contract/test_g2_semantics_mock.py`, `tests/contract/test_g2_semantics_http.py`; in SME backend create `apps/platform/iam/tests/test_g2_semantic_contract.py`. Reuse existing `apps/platform/iam/contract_adapter/client.py`, existing dual-adapter fixture patterns and Task 2 bundle without Django imports in Gateway consumers.

**Interfaces:** Produce `run_g2_case(case_id: str, *, adapter: IamPdpAdapterPort, scenario: Record) -> JsonObject`; scenario is harness-only synthetic setup/mutation callbacks; common cases execute identical consumer logic for Mock, SmeBtosDjangoAdapterClient and HttpIamPdpAdapter. Results include expected/actual effect, reason/obligations, downstream counts, policy/build/schema identities; never raw tokens.

- [ ] **Step 1: RED tests:** `test_same_g2_contract_semantics_for_both_adapters` covers TC-001..028; use each owning test's assertions, positive baselines and mutations. Assert resource grant, binding, authority, fail-closed effects, obligations and no-dispatch equivalence, not structural `isinstance` alone. Missing backend environment is BLOCKED in evidence, not a counted PASS/skip.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_semantics_mock.py -q`; from SME backend `python -m pytest apps/platform/iam/tests/test_g2_semantic_contract.py -q`. Expect RED for coverage/parity gaps; resolve database/schema/service setup before interpreting behavior.
- [ ] **Step 3:** Implement shared case runner plus adapter-specific setup hooks; fix semantic drift in the owning modules with focused regression tests, not an adapter-specific authorization branch in Gateway. Add authenticated HTTP-backed same cases against isolated SME test service; verify transport timeout/malformed/partial response failure too.
- [ ] **Step 4:** Re-run both commands and `python -m pytest tests/contract/test_g2_semantics_http.py -q` with configured approved test service; expected GREEN with both adapter result sets and no sensitive logs. Run `python -m pytest tests/contract/test_substitutability_g0_025.py -q` in JavisOS and `python -m pytest apps/platform/iam/tests/test_substitutability_g0_025.py -q` in SME backend. Do not pretend APIClient alone proves deployed HTTP routing.
- [ ] **Step 5:** Commit task-owned tests separately in each repo: `test: prove G2 semantics across IAM adapters`.

### Task 18: Evidence runner, CI and whole-feature review

**Files:** Create `tools/run_g2_evidence.py`, `tests/contract/test_g2_evidence_runner.py`, `.github/workflows/g2-contract.yml`, `docs/dev/g2-implementation-report.md`; update original G2 inventory/trace/test/evidence registers by explicit run references only. Existing `G2-CWE-*` remains the sole acceptance matrix.

**Interfaces:** Produce `run_evidence(manifest: Record, *, output_dir: Path) -> JsonObject`, CLI `python tools/run_g2_evidence.py --manifest PATH --output-dir PATH`. Manifest maps all 28 case IDs to executable tests, prerequisite profile approvals, adapters/builds/schema versions, expected evidence and runner/reviewer identity. Exit success means execution/reporting succeeded, **not gate acceptance**; a report field separates PASS/FAIL/BLOCKED from operation approval. Never execute arbitrary shell strings from an untrusted manifest.

- [ ] **Step 1: RED tests:** `test_missing_case_or_adapter_evidence_cannot_be_complete`, `test_blocked_or_skipped_case_is_not_pass`, `test_secret_redaction_and_checksum_validation`, `test_runner_cannot_open_operation`. Assert manifest has TC-001..028/CWE-001..019 with no gaps, approval-free operational claims are rejected, altered artifacts fail verification and unresolved/failed run retains defect IDs.
- [ ] **Step 2:** Run `python -m pytest tests/contract/test_g2_evidence_runner.py -q`; expected RED for missing evidence runner semantics.
- [ ] **Step 3:** Implement runner using allowlisted test commands/paths, structured subprocess arguments, immutable run IDs/index/checksums and approved repository ACL/retention. CI reconstructs Task 2 bundle without ignored source folders and runs unit/contract suites, then separate isolated backend/HTTP jobs. Missing service/parameters yields BLOCKED report, not success coverage. Keep actual evidence out of public Git; check only redacted report references.
- [ ] **Step 4:** Run `$g2TestFiles = @(Get-ChildItem tests/contract/test_g2_*.py | ForEach-Object FullName)` then `python -m pytest @g2TestFiles -q` in PowerShell; `python -m pytest tests/contract/test_corporate_guard_and_runtime.py tests/contract/test_mcp_gateway_pep.py tests/contract/test_substitutability_g0_025.py -q`; `node tests/js/test_corporate_workspace.js`, `node tests/js/test_workspace.js`, `python tests/run.py i18n`; SME G2 + existing contract/substitutability suites; actual deployment verifier and evidence CLI. Expected GREEN or explicitly documented pre-existing failures/blocked environment, never a blanket acceptance claim. Obtain fresh independent security/whole-branch review, address findings with RED/GREEN tests and reconcile all 19 criteria/28 cases with actual run evidence.
- [ ] **Step 5:** Commit runner/CI/docs with `test: produce reproducible G2 acceptance evidence`. Update gate registers only after authorized reviewer decisions: implementation readiness -> execution readiness -> acceptance -> operational opening, separately. Follow release conventions when integration is subsequently authorized; this plan does not authorize operational opening or business G3 actions.

## Representative RED assertions (part of each owning task's Step 1)

Imports use the producing module named in the task. Each fixture is created in
that task's test file with the exact scenario from Step 1; its setup may fake
authorities/network sinks, but cannot fake the method under test. Fixture
helpers below are test-local setup/action helpers to implement, not additional
production interfaces. Repeat the RED/GREEN cycle for the remaining named cases.

### Task 2 assertions

```python
def test_missing_bundle_is_actionable(tmp_path):
    with pytest.raises(ContractSchemaError, match="manifest"):
        load_bundle(tmp_path / "absent")
```

### Task 3 assertions

```python
def test_expired_or_unavailable_authority_denies(expired_resolver):
    with pytest.raises(G2Denied):
        expired_resolver.resolve("synthetic-token", resource_ids=("r-a",), purpose="READ")
```

### Task 4 assertions

`shared_case.evaluate(principal_id: str, operation: str) -> JsonObject` builds
the real adapter request for the one shared resource. Its grant mutation updates
the authoritative fixture registry, not the returned decision.

```python
def test_shared_source_requires_each_principal_permission(shared_case):
    assert shared_case.evaluate("employee-a", "read")["effect"] == "ALLOW"
    assert shared_case.evaluate("employee-b", "read")["effect"] == "ALLOW"
    shared_case.remove_grant("employee-b", "read")
    assert shared_case.evaluate("employee-b", "read")["effect"] == "DENY"
```

### Task 5 assertions

```python
def test_every_registered_alias_obeys_g2_scope(prohibited_alias_case):
    with pytest.raises(G2Denied):
        resolve_operation(prohibited_alias_case.connector_id,
                          prohibited_alias_case.tool_name,
                          profile=prohibited_alias_case.profile)
    assert prohibited_alias_case.sink.calls == []
```

### Task 6 assertions

```python
def test_full_or_unverifiable_outbox_prevents_read(full_outbox_case):
    with pytest.raises(G2Denied):
        full_outbox_case.admission.begin(full_outbox_case.snapshot,
            operation="read", resource_ids=("r-a",), correlation_id="corr-test")
    assert full_outbox_case.sink.calls == []
```

### Task 7 assertions

`overlap_case` applies workspace and tenant halts and recovers only workspace
through the real authorized controller; it leaves tenant halt active.

```python
def test_recover_one_scope_does_not_clear_overlapping_halt(overlap_case):
    overlap_case.recover_workspace_only()
    with pytest.raises(G2Denied):
        overlap_case.controller.assert_active(overlap_case.snapshot,
                                               destination_id="provider-test")
```

### Task 8 assertions

`race_case.execute_read()` invokes the real PEP/fence. A test barrier processes
authoritative revoke between preliminary authorization and final admission.

```python
def test_revoke_between_authorize_and_invoke_blocks_dispatch(race_case):
    race_case.revoke_at_final_admission_barrier()
    with pytest.raises(G2Denied):
        race_case.execute_read()
    assert race_case.sink.calls == []
```

### Task 9 assertions

`unknown_source_case.dispatch_prompt()` invokes the real ProviderGate using one
source without authoritative classification and a socket-recording provider.

```python
def test_unknown_or_expired_classification_never_becomes_public(unknown_source_case):
    with pytest.raises(G2Denied):
        unknown_source_case.dispatch_prompt()
    assert unknown_source_case.provider.request_count == 0
    assert unknown_source_case.provider.bytes_received == 0
```

### Task 10 assertions

`stream_case.deliver(chunk: bytes) -> bytes` calls real guard_output then records
only bytes returned for delivery; its revoke event is from the synthetic IAM.

```python
def test_revoke_between_stream_chunks_stops_next_delivery(stream_case):
    assert stream_case.deliver(b"first") == b"first"
    stream_case.coordinator.observe(stream_case.authoritative_revoke)
    with pytest.raises(G2Denied):
        stream_case.deliver(b"second")
    assert stream_case.delivered == [b"first"]
```

### Task 11 assertions

`cache_case` has distinct A/B verified bindings and a positive valid profile.
`corrupt_ciphertext(cache_key: str)` flips a stored ciphertext byte, not a key.

```python
def test_cache_ciphertext_and_binding_tamper_fail_closed(cache_case):
    key = cache_case.cache.put(cache_case.snapshot_a, resource_id="r-a",
        plaintext=b"CANARY-A", retention_seconds=cache_case.approved_ttl)
    assert cache_case.cache.read(cache_case.snapshot_a, cache_key=key) == b"CANARY-A"
    with pytest.raises(G2Denied):
        cache_case.cache.read(cache_case.snapshot_b, cache_key=key)
    cache_case.corrupt_ciphertext(key)
    with pytest.raises(G2Denied):
        cache_case.cache.read(cache_case.snapshot_a, cache_key=key)
```

### Task 12 assertions

```python
def test_search_history_and_lineage_cannot_cross_binding(artifact_case):
    assert artifact_case.store.search(artifact_case.snapshot_a, "CANARY-B") == []
    with pytest.raises(G2Denied):
        artifact_case.store.read(artifact_case.snapshot_a, artifact_case.private_b_id)
```

### Task 13 assertions

`application_case.post_corporate_prompt()` uses TestClient against registered
application routes with verified synthetic login/device and a single approved
provider. It observes real composition, not a replacement fake PEP.

```python
def test_corporate_request_never_reaches_personal_engine_or_store(application_case):
    response = application_case.post_corporate_prompt()
    assert response.status_code == 200
    assert application_case.approved_provider.request_count == 1
    assert application_case.personal_engine.calls == []
    assert application_case.personal_store.writes == []
```

### Task 14 assertions

```python
def test_two_tabs_never_receive_other_workspace_stream_or_snapshot(two_tabs):
    asyncio.run(two_tabs.runtime.publish_corporate(
        two_tabs.event_a, snapshot=two_tabs.snapshot_a))
    assert len(two_tabs.writer_a.events) == 1
    assert two_tabs.writer_b.events == []
    assert two_tabs.runtime.corporate_snapshot(two_tabs.snapshot_b.binding) == []
```

### Task 15 assertions

```python
def test_auditor_operator_and_recovery_roles_are_separate(operator_case):
    with pytest.raises(G2Denied):
        operator_case.service.recover(operator_case.auditor, halt_id=operator_case.halt_id)
    assert operator_case.halt_state() == "HALTED"
```

### Task 16 assertions

The deployment report schema must expose `ready: bool` and `network_proof:
str` with PASS/FAIL/BLOCKED; the fixture profile only asserts a gateway boolean.

```python
def test_deployment_verifier_rejects_boolean_only_gateway_proof(boolean_profile, tmp_path):
    report = verify_deployment(boolean_profile, evidence_dir=tmp_path)
    assert report["ready"] is False
    assert report["network_proof"] == "BLOCKED"
```

### Task 17 assertions

`dual_adapter_case.run_same_case(case_id)` calls run_g2_case once per real
adapter with identical setup. `run_denied_variant` removes B's authoritative
grant before B's request; it cannot substitute a hard-coded DENY result.

```python
def test_same_g2_contract_semantics_for_both_adapters(dual_adapter_case):
    results = dual_adapter_case.run_same_case("G2-TC-020")
    assert results["mock"]["effect"] == results["sme_btos"]["effect"] == "ALLOW"
    results = dual_adapter_case.run_denied_variant("G2-TC-020")
    assert results["mock"]["downstream_count"] == 0
    assert results["sme_btos"]["downstream_count"] == 0
```

### Task 18 assertions

The evidence report includes `complete: bool`, `blocked_count: int` and
`operational_opening: bool`. A test run never sets operational_opening true;
the external authorized gate decision is recorded separately.

```python
def test_blocked_or_skipped_case_is_not_pass(blocked_manifest, tmp_path):
    report = run_evidence(blocked_manifest, output_dir=tmp_path)
    assert report["complete"] is False
    assert report["blocked_count"] > 0
    assert report["operational_opening"] is False
```

## Coverage cross-check

| G2-CWE | Owning tasks | Planned test cases |
| --- | --- | --- |
| 001 | 1/3/13/16 | TC-001/020/026 |
| 002 | 2/3/13/17 | TC-001/022/026/028 |
| 003 | 11/12/13/14 | TC-002/005/021/026 |
| 004 | 4/12/14 | TC-003/004/020/021 |
| 005 | 4/5/8/9 | TC-007/020/023/028 |
| 006 | 5/8/13 | TC-008 |
| 007 | 5/8/13/16 | TC-005/021/022/026 |
| 008 | 3/9/10 | TC-009/022/023/024 |
| 009 | 5/8/9/13/17 | TC-006/023 |
| 010 | 7/8/10/11/14 | TC-010/021/024/026/027 |
| 011 | 3/6/7/8/10 | TC-011/022/024 |
| 012 | 10/12 | TC-012/024 |
| 013 | 10/11/12/14 | TC-013/021/024 |
| 014 | 6/15/18 | TC-014/025 |
| 015 | 6/18 | TC-015/025 |
| 016 | 7/15 | TC-016/027 |
| 017 | 2/3/7/8/17 | TC-017/022/027/028 |
| 018 | 7/10/15/16 | TC-018/027 |
| 019 | 2/16/17/18 | TC-019/028 |

Task 17 must include explicit **TC-006 prompt-injection** corpus assertions (direct prompt, retrieved document and tool result cannot alter context/policy/tool permission) and **TC-017 policy change/rollback** assertions (effective-time/version/approval and old-cache invalidation). These are named case implementations, not a claim that the preparation recorders cover them. A missing executable assertion for any row blocks Task 18 completeness.

## Commit/review and final handoff rules

For Tasks 3–18, the commit step means explicitly stage only the listed task files, inspect staged diff, run required GitNexus impact/detect_changes checks and commit with the provided English message. Do not use `git add .` in either shared repo. Tasks that touch a shared file are sequenced in the dependency table. Reserve the release version through the established draft-PR workflow before product coding; do not select a version number in this document. Never force-add ignored approval documents wholesale.

Self-review of this plan: all DD-001..009 and PAR-001..010 have task owners; all 19 criteria and 28 cases have implementation/evidence mappings; shared names/signatures above are reused by dependents; five Review Focus risks have named tests; runtime/deployment evidence and approvals remain separate. Open values are deliberate owner-controlled blockers listed in Task 1, not implementation guesses.

Before starting implementation, ask the user to review this saved plan as required by writing-plans. Preserve the already authorized subagent method; no need to ask them to select it again. After approval, start Task 1/input closure and worktree/version setup; do not jump to PEP or UI code while its P0 dependencies remain open.
