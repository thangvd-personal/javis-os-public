# Evidence-based improvement M0 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans for direct execution with a fresh whole-branch review. The user authorized plan self-review and continuous execution in the Goal; do not stop for routine approval.

**Goal:** Expose owner-controlled immutable baseline capture and policy/history for canonical user agents/skills without running trials or changing live assets.

**Architecture:** An ImprovementStore uses the existing GoalStore database/transaction seam, dedicated encrypted byte-preserving blobs, and host-created principals. A source-capture service validates canonical paths, ownership and raw-byte snapshots. Lazy dependency-injected owner routes expose metadata and history; no engine or activation runtime is connected in M0.

**Tech stack:** Python 3.12, SQLite, existing Fernet primitives, FastAPI, unittest-compatible script tests.

**Spec:** `../specs/2026-10-22-agent-skill-evidence-improvement-design.md`, especially 4, 7, 8, 9, 17, 18, 22 and M0 in 23.

## Global constraints

- Same `resonance.sqlite3`, existing `_Tx` and existing principal; no fake goals, no duplicate ledger.
- Default `suggest`, supported M0 modes `off|suggest`; unsupported trial/activation modes fail clearly.
- No model calls, canonical writes, activation, inherited Auto permission, or production settings in tests.
- User rules and source bytes remain intact. No symlink/junction/drive-relative or mirror-derived ownership.
- English code/comments/docstrings/tests; user-facing errors use existing localization.
- Immutable history, cross-brain authorization, event idempotency and generation CAS are required.
- Reserve release before code (done in PR #794); keep Vietnamese and English changelogs aligned.

## Review focus

1. A canonical-disabled skill with a stale enabled mirror must not be captured as enabled (Task 2).
2. Same-size changes with retained timestamps must produce distinct raw-byte revisions (Tasks 1–2).
3. An event key reused with another payload must conflict without altering history (Task 1).
4. A typo/deleted brain must not read or update default-brain policy (Task 3).
5. Concurrent capture/retire and lost/corrupt blobs must preserve immutable history and fail closed (Tasks 1–2).

## Task 1: Atomic scoped metadata, policies and durable blobs

**Files:** create `server/improvement_store.py`, `server/improvement_blobs.py`, `tests/python/test_improvement_store.py`.

**Interfaces produced:** `ImprovementStore(goal_store, blobs)` with `get_policy(p)`, `set_policy(p,mode,expected_version,event_key)`, `capture(p,source,content,event_key,expected_generation=None)`, `targets(p)`, `revisions(p,target_id)`, `read_revision(p,revision_id)`, `retire(p,target_id,expected_generation,event_key)`, `create_bundle(p,revision_ids,event_key)`, `events(p)`. `SourceSnapshot` descriptor is produced by Task 2; capture accepts a validated host descriptor, never raw request JSON. `RevisionBlobs(root,encrypt,decrypt)` stores exact bytes encoded before encryption, verifies SHA-256 at read, and has no normal-evidence TTL.

Schema: improvement schema marker, policies, targets, revisions, bundles plus scoped asset references, and append-only events with request hash/result for idempotency. Unique live canonical target per brain; new identity after retirement; immutable revisions/bundles enforced at SQLite level. Source updates require expected generation. Missing records cannot cross brain.

- [x] Add tests for default/read-only behavior, exact bytes and repeated capture, history, CAS race across two connections, atomic event replay/conflict, owner/cross-brain isolation, parent/bundle ownership and retirement/recreation.
- [x] Run tests, confirm RED for missing foundation behavior.
- [x] Implement atomic migrations (individual statements inside existing `_Tx`), policy guard and immutable capture, encrypted blobs and integrity checks.
- [x] Run tests GREEN, including corrupt/missing blob and migration future-version/rollback cases.
- [x] Commit tested task with ledger evidence.

## Task 2: Canonical source capture with ownership guard

**Files:** create `server/improvement_assets.py`, `tests/python/test_improvement_assets.py`.

**Interface produced:** frozen `SourceSnapshot(kind,canonical_ref,source_hash,protected_hash,enabled,ownership,identity)` plus `capture_source(root,kind,slug)` returning `(snapshot,raw_bytes)`. Validate filesystem ancestry and non-link components; sources are `skills/<slug>/SKILL.md` or `agents/<slug>.md`, never legacy mirror. Bound capture to 1 MiB as an explicit resource limit, fail rather than truncate. User skill `learn_lock` and bundled/pack-owned resources are blocked; metadata alone cannot declare them editable. Initial ownership is protected unless host has explicit managed-region authority. Agent identity is supplied by the host registry at the service boundary, not by model text.

- [x] Add failing tests for disabled shadowing, linked directories/files, traversal/Windows path forms, system/locked skills, raw CRLF, content edits and capture size/race rejection.
- [x] Implement source snapshot with read-before/after file identity checks and no filesystem writes; use canonical inventory semantics and existing source ownership helpers.
- [x] Run source/store tests plus existing owner-rule/skill tests; record both fresh and baseline results.
- [x] Commit tested task.

## Task 3: Lazy authenticated owner metadata routes

**Files:** create `server/improvement_api.py`, `tests/python/test_improvement_api.py`; modify only wiring seam in `server/main.py`; intentionally update `tests/python/route_table.json`.

**Interfaces:** `ImprovementApiDeps(read_store,write_store,resolve_brain,...)`, `register(app,deps)`. Read-store callback returns None when existing schema/store is absent. Strict brain resolver requires an existing configured brain/root without fallback. API creates Principal at host and rejects actor/brain spoof fields.

Expose `GET/PUT /improvement-policy`, `GET /improvement-targets`, `POST /improvement-targets/capture`, `GET /improvement-targets/{id}/revisions`, `POST /improvement-targets/{id}/retire`. Use expected versions and event keys; return metadata/capabilities (`trial=false`, `activation=false`), never ciphertext/blob paths/source body. Owner explicit capture is blocked in off mode. Every DB/file operation runs off event loop. Existing auth/CSRF middleware remains authoritative.

- [x] Add route tests before implementation for no-state GET, valid owner capture/history, off behavior, unknown brain, malformed request, stale version and cross-brain id denial.
- [x] Add actual-main auth/CSRF tests with scratch roots before import, no startup context manager, and no engine construction.
- [x] Implement/wire routes, update intentional snapshot, run route/security/improvement regression checks.
- [x] Commit tested task.

## Task 4: Evidence, fresh review and integration

**Files:** update progress, M0 operator docs and bilingual release notes, PR description.

- [x] Run focused foundation, ownership/skill, auth/CSRF, route snapshot, schema/restart and localization checks. Compile/import relevant modules under isolated state.
- [x] Request fresh whole-branch review against spec M0, address critical/important findings with reproducing RED→GREEN tests; record rulings/deferred minors.
- [x] Verify diff, source-doc hashes and complete M0 acceptance matrix; keep M1–M8 pending.
- [ ] Push, wait for current-head CI, mark draft ready and merge only green/review-clean; verify release workflow. If external CI waits, use the live job handle and continue independent planning, never assume completion.
- [ ] Continue M1 from latest integrated main, reserving its own version and plan.

## Self-review and interface check

- Task 1 owns DB/blob persistence and principal authorization; Task 2 produces typed validated source descriptors and never writes production files.
- Task 3 must call Task 2 from host fields, then Task 1; no client-provided ownership/path/hash is accepted.
- M0 does not claim install receipts, semantic patch approval or solver isolation that belong to later milestones.
- Existing 0.285.0 Team acceptance/usage work is reused and verified later, not implemented again here.
- Five review-focus cases have explicit owning tests. No implementation task requires a production engine.
