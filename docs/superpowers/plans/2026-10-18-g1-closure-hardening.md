# G1 Closure Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the agreed G0 conditions and produce defensible, reproducible evidence for the G1 read-only pilot.

**Architecture:** Keep IAM/PDP authoritative in `sme_btos`, keep JavisOS dependent on the portable adapter protocol, and fail closed whenever secrets, device posture, revocation, audit integrity, or live PDP state cannot be verified. Live tests remain explicit integration tests; local tests cover deterministic policy behavior without pretending to be live evidence.

**Tech Stack:** Python 3.12, Django/DRF, FastAPI, pytest, SQLite, Docker-backed live integration.

**Spec:** `javisos/brains/SME-BTOS/documents/hop-dong-tich-hop-javisos-iam-mcp-v0.3.md`

## Global Constraints

- Corporate requests must fail closed when IAM/PDP or required metadata cannot be verified.
- No production credential or password bypass may be embedded in application code.
- `X-Test-Clock-Offset` is test-only and disabled unless explicitly enabled in an isolated debug environment.
- Device posture must be supplied by a trusted boundary; missing posture is not `COMPLIANT`.
- G1 remains read-only, single-workspace, bounded to the approved tenant/resource scope.
- Do not weaken live tests by replacing service calls with mocks.

## Review Focus

- Missing service secrets must fail at startup or request authentication rather than silently selecting a development secret.
- Missing device posture must deny token exchange/revocation checks rather than defaulting to compliant.
- Audit mutation and sequence gaps must be detected, not only initial hash-chain creation.
- Network-down tests must assert the contract reason code and audit reason code exactly.
- Acceptance documents must distinguish technical test evidence from formal approval.

### Task 1: Secret and Test-Only Boundary Hardening

**Files:**
- Modify: `sme_btos/backend/apps/platform/iam/views/sso_broker.py`
- Modify: `sme_btos/backend/apps/platform/iam/contract_adapter/service_auth.py`
- Modify: `sme_btos/backend/config/settings/base.py`
- Modify: `javisos/server/iam_contract/http_adapter.py`
- Modify: `javisos/server/main.py`
- Test: `sme_btos/backend/apps/platform/iam/tests/test_sso_authorization_code.py`
- Test: `javisos/tests/contract/test_g1_sso_security_matrix.py`

- [ ] Write failing tests proving missing production secrets are rejected, the password bypass is rejected, and clock override is disabled by default.
- [ ] Run focused tests and confirm RED for the intended reasons.
- [ ] Remove hardcoded credential fallbacks, require explicit configured values outside test mode, and set clock override default to `False`.
- [ ] Run focused tests and confirm GREEN.

### Task 2: Device Posture Fail-Closed

**Files:**
- Modify: `sme_btos/backend/apps/platform/iam/contract_adapter/services.py`
- Modify: `javisos/server/iam_contract/http_adapter.py`
- Modify: `javisos/server/mcp_gateway/pep_service.py`
- Test: `sme_btos/backend/apps/platform/iam/tests/test_contract_adapter.py`
- Test: `javisos/tests/contract/test_mcp_gateway_pep.py`

- [ ] Add failing coverage for omitted and untrusted device posture.
- [ ] Run the focused tests and confirm RED.
- [ ] Require a trusted posture value and deny missing/unknown/non-compliant posture.
- [ ] Run focused tests and confirm GREEN.

### Task 3: Tamper-Evident Audit Verification

**Files:**
- Modify: `javisos/server/mcp_gateway/audit_outbox.py`
- Create: `javisos/tests/contract/test_g1_audit_tamper_detection.py`
- Test: `javisos/tests/contract/test_g1_live_scenarios.py`

- [ ] Write failing tests for payload mutation, event deletion, and sequence gaps.
- [ ] Run the tests and confirm RED.
- [ ] Implement deterministic integrity verification with precise broken sequence/reason reporting.
- [ ] Run the focused tests and confirm GREEN.

### Task 4: Live G1 Evidence and Reason-Code Precision

**Files:**
- Modify: `javisos/tests/contract/test_g1_emergency_halt_live.py`
- Modify: `javisos/server/mcp_gateway/pep_service.py`
- Modify: `javisos/server/iam_contract/http_adapter.py`

- [ ] Add exact assertions for `PERMISSION_REVOKED`, `TENANT_SUSPENDED`, and `PDP_UNAVAILABLE_FAIL_CLOSED` plus audit reason codes.
- [ ] Run live tests with explicit credentials and the SME_BTOS container; preserve failure evidence when prerequisites are unavailable.
- [ ] Keep live tests unmocked and document the required environment variables and container state.

### Task 5: G1 Acceptance Evidence and Status

**Files:**
- Modify: `javisos/brains/SME-BTOS/documents/g1-pilot/g1-acceptance-matrix.md`
- Modify: `javisos/brains/SME-BTOS/documents/g1-pilot/g1-pilot-report.md`
- Modify: `javisos/brains/SME-BTOS/documents/bang-truy-vet-dong-g0-security-closure-javisos-iam-mcp-dexuat.md`

- [ ] Replace unsupported PASS claims with evidence-linked technical status.
- [ ] Record the hardening commits, focused test outputs, live prerequisites, open gaps, reviewer and approval fields.
- [ ] Mark G1 as technically ready only when all required live evidence is present; otherwise leave it blocked/unverified.

### Task 6: Full Verification and Index Refresh

- [ ] Run focused JavisOS and SME_BTOS tests.
- [ ] Run the full available contract suite with explicit environment prerequisites.
- [ ] Run `compileall`, `git diff --check`, and GitNexus status/analyze for both repos.
- [ ] Review the final diff and report any remaining blockers before claiming G1 completion.
