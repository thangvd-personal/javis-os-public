# G2 Readiness Test Preparation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a code-linked G2 enforcement inventory and test-only fixtures/mocks that exercise same-tenant workspace boundaries, downstream dispatch behavior, and frozen contract schemas without changing runtime policy or gate state.

**Architecture:** Keep production modules and frozen schemas unchanged. Put deterministic fixtures and recording doubles under `javisos/tests/contract`; validate using existing `IamPdpAdapterPort`, PEP and schema loader. Store the inventory beside the G2 preparation registers and clearly label code/tests as candidate coverage, not G2 evidence.

**Tech Stack:** Python, pytest, existing IAM/PDP contract schemas, MCP Gateway PEP and approved connector catalog.

## Review Focus

- Same-tenant users in different workspaces must not access each other's resources or canary markers.
- Every deny scenario must prove the connector/provider recording boundary received no call.
- Schema conformance must use the frozen local schema directory and give a clear test setup failure if the ignored `brains/` contract package is absent.
- Lease SLA, operation aliases, provider policy, audit failure behavior, and cache cryptography remain recorded dependencies, not implementation decisions.

### Task 1: Record Existing Enforcement Inventory

**Files:**
- Create: `brains/SME-BTOS/documents/g2-pilot/g2-enforcement-inventory.md`
- Read: G2-CWE matrix, contract v0.3, IAM mock adapter, PEP, connector catalog, corporate guard/runtime, audit outbox, and candidate contract tests.

- [ ] Map G2-CWE-001..019 to enforcement code, current candidate test IDs, known gaps, and unresolved decision dependencies.
- [ ] Label every code/test mapping as inventory or candidate coverage; leave verification statuses and gate untouched.
- [ ] Verify all named paths and test IDs exist with `rg` and file reads.

### Task 2: Add Deterministic G2 Test Data and Recording Doubles

**Files:**
- Create: `tests/contract/g2_readiness_support.py`
- Test: `tests/contract/test_g2_readiness_preparation.py`

- [ ] Add tests asserting the fixture principals share one tenant, have distinct workspaces, and receive distinct deterministic canary markers.
- [ ] Add a recording connector/provider double that stores call count and redacted metadata/payload markers, never credentials.
- [ ] Run the focused test before implementing support and confirm failure is due to missing fixture/double behavior.
- [ ] Implement only test support; rerun the focused tests and confirm pass.

### Task 3: Exercise Workspace Denial and No-Dispatch Boundary

**Files:**
- Modify: `tests/contract/test_g2_readiness_preparation.py`
- Read: `server/iam_contract/mock_adapter.py`, `server/mcp_gateway/pep_service.py`, `server/mcp_gateway/connector_catalog.py`.

- [ ] Add a same-tenant cross-workspace resource access test through the Gateway, asserting fail-closed denial and zero connector calls.
- [ ] Add a denied provider-boundary case using the recording test double and assert the canary is not received.
- [ ] Run only these new tests to confirm expected red before completing their test setup.
- [ ] Keep scenarios limited to behavior the current boundary can exercise; document uncovered ingress/search/worker paths instead of claiming coverage.

### Task 4: Add Frozen Contract Conformance Checks

**Files:**
- Modify: `tests/contract/test_g2_readiness_preparation.py`
- Read: `server/iam_contract/port.py`, `server/iam_contract/schemas.py`, `brains/SME-BTOS/documents/contract/schemas/*.schema.json`.

- [ ] Validate representative PEP-generated ExecutionContext/PDP request and mock adapter decision payloads against their frozen schemas.
- [ ] Assert missing ignored schema inputs fail setup with a message naming the required contract schema directory.
- [ ] Run focused contract tests; report schema drift as a finding without modifying producer or schema.

### Task 5: Verify Scope and Test Results

**Files:**
- Review: `git diff` and G2 readiness registers.

- [ ] Run focused new contract tests and the directly related existing IAM/PEP/corporate runtime tests.
- [ ] Confirm no production code or frozen schema changed and all G2 verification statuses remain `CHƯA XÁC MINH`.
- [ ] Record actual commands/results and any blockers; do not upgrade the gate or claim G2 acceptance.

## Final Verification Commands

Run from `javisos/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_g2_readiness_preparation.py -q
.\.venv\Scripts\python.exe -m pytest tests/contract/test_iam_contract_core.py tests/contract/test_mcp_gateway_pep.py tests/contract/test_corporate_guard_and_runtime.py -q
git diff --check
```

The G2 preparation documents and contract schema source currently reside in ignored `brains/`. Their local presence does not establish that CI or a clean checkout has them.
