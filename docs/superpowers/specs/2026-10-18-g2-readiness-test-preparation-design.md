# G2 Readiness Test Preparation Design

## Intent

Prepare reviewable technical evidence for G2 without opening G2 operation or
implementing policy decisions that remain unresolved. The work will map current
enforcement points and provide isolated test boundaries for workspace isolation,
MCP dispatch, provider calls, and conformance to the frozen IAM/PDP contract.

## Goals

- Inventory enforcement entry points and current test coverage against G2-CWE.
- Add test-only fixtures for two users in distinct workspaces within one tenant.
- Add recording MCP and model-provider doubles that expose dispatch count and
  payload markers to tests.
- Exercise the frozen JSON Schemas and `IamPdpAdapterPort` without changing
  runtime behavior or contract semantics.
- Record gaps and unresolved decisions separately from verified behavior.

## Non-goals and anti-goals

- No production authorization, session, lease, retention, audit, or cache
  behavior changes.
- No opening of G2 operation; the gate remains
  `PREPARATION APPROVED — NOT OPEN FOR OPERATION`.
- No edits to frozen contract schemas or selection of values for unresolved
  policy/SLA decisions.
- No claims that G2-CWE criteria pass based on preparation tests.

## Constraints

- Reuse the existing pytest and contract-test conventions in `javisos/tests`.
- Keep test doubles in test support code, not production modules.
- Use the existing contract schema loader and port as the contract boundary.
- Contract documents currently live under ignored `brains/`; tests must fail
  with an actionable message when those inputs are absent. This work will not
  copy the frozen schemas into a second source of truth.
- Preserve unrelated user changes and test artifacts.

## Proposed implementation

1. Add a G2 enforcement inventory mapping G2-CWE areas to code entry points,
   existing candidate tests, known coverage gaps, and unresolved dependencies.
2. Add test fixtures with same-tenant principals/resources in separate
   workspaces, each using distinct canary markers.
3. Add test-only recording doubles for the approved connector catalog and model
   provider boundary. Tests will assert calls and payload markers, including
   zero downstream dispatch after a deny.
4. Add contract conformance tests for representative PEP-built execution
   context/PDP payloads and mock-adapter responses using the frozen schema set.
   Any mismatch is reported as drift; production payload generation is not
   changed in this preparation step.

## Acceptance criteria

- The inventory names each reviewed enforcement boundary and labels evidence as
  candidate coverage rather than G2 verification.
- Fixtures prove workspace identities differ while tenant identity matches and
  provide deterministic, non-overlapping canaries.
- Recording doubles report zero dispatch on deny and preserve sufficient call
  metadata for assertions without logging secrets.
- Contract tests validate against the frozen schema directory and fail with a
  clear setup message if that directory is unavailable.
- No production module or frozen schema is modified; all G2 statuses and gate
  state remain unchanged.

## Decisions left to the implementer

- Exact test helper names and organization, following local pytest conventions.
- Inventory format and placement alongside the G2 preparation package.
- The smallest representative payload set needed to reveal contract drift.

## Open dependencies to record, not decide

- Lease/revoke SLA and the 300s versus 900s discrepancy.
- Full operation-deny coverage across every ingress/worker/connector path.
- Provider/classification/purpose/retention allowlists.
- Audit sink failure behavior and incident/halt ownership.
- Secure encryption requirements for the corporate local cache.
- A versioned, CI-available distribution path for frozen contract schemas.
