# Evidence-based agent and skill improvements: M0 foundation

M0 adds an owner-only inventory of immutable source revisions. It does not execute a
solver, propose a patch with a model, change a live skill or activate a revision.
The complete feature roadmap is in
[the design](../superpowers/specs/2026-10-22-agent-skill-evidence-improvement-design.md).

## What is available

- Default improvement policy is `suggest`; only `off` and `suggest` are supported in M0.
  Existing learning, Auto, Team mode and goal permissions do not grant improvement rights.
- An explicit owner capture reads `skills/<slug>/SKILL.md` or `agents/<slug>.md` and stores
  the exact bytes encrypted outside the ordinary short-lived evidence store.
- Source hashes, protected hashes, source incarnation and ownership are pinned. Legacy
  mirrors are not canonical sources. Disabled, system, pack-managed and learning-locked
  assets are refused. Initial content remains protected, not automatically editable.
- An existing agent's stable host identity is included in its source manifest; capture does
  not enable its Auto switch. Replacement source files conservatively get a new improvement
  identity even when an editor replaced a file atomically. History remains available.
- Refreshing changed sources and retiring targets require their expected generation.
  Deleting/recreating or replacing a target never transfers its previous identity.
- A source reverted to earlier bytes points to the matching earlier immutable revision;
  the latest inserted history row is not assumed to be current.

## Owner API

The normal server authentication and CSRF rules apply. These endpoints are not public or
local-auth exemptions. `brain` is a query parameter resolved strictly to an existing brain;
a typo does not fall back to the default brain. Actor and ownership come from the host,
not JSON supplied by a caller.

| Method and path | Behavior |
|---|---|
| `GET /improvement-policy` | Policy/version and supported capabilities; no trial or activation capability |
| `PUT /improvement-policy` | Set `off` or `suggest` with `expected_version` and `event_key` |
| `GET /improvement-targets` | Live target metadata for the selected brain |
| `POST /improvement-targets/capture` | Explicitly capture a canonical source |
| `GET /improvement-targets/{id}/revisions` | Immutable revision metadata, without source content or blob paths |
| `POST /improvement-targets/{id}/retire` | Retire a tracked target, preserving history and canonical files |

Example capture body:

```json
{"kind":"skill","slug":"report","event_key":"owner-capture-report-001"}
```

The response supplies `target_id`, `revision_id`, `generation` and raw-byte/manifest hashes.
On a subsequent changed capture, include the `expected_generation` from the current target.
The same event key and same payload return the prior result. Reusing the key with another
payload conflicts. Stale generations return 409; cross-brain target IDs are unavailable.
Requests cannot declare a path, protected hash, actor, ownership or desired permission.

Reads of missing improvement state do not create a new database, encrypt a source or start
an engine. `off` rejects new capture; the owner can still inspect history and retire tracking.
Existing metadata reads open SQLite in read-only mode, without initializing or migrating
the shared runtime store. Retiring a target explicitly switches to the write dependency.
No UI activation button or trial endpoint is advertised before later milestones pass.

## Persistence and recovery

Metadata is in the existing `resonance.sqlite3`, under `improvement_*` tables. Initialization
uses the existing `BEGIN IMMEDIATE` transaction helper and records a schema version. A first
migration of an existing store uses SQLite's backup API and the
`.pre-improvement-m0.bak` suffix. Unsupported schema versions fail closed.

Revision bytes are in `<STATE_DIR>/improvement/<brain-hash>/<content-hash>.enc`. Encoding
before encryption preserves raw CRLF and other UTF-8 bytes. No plaintext fallback is
accepted. Readers decrypt and verify SHA-256; missing/corrupt data is unavailable rather
than reconstructed from a possibly changed live source. These files are not subject to
the normal evidence TTL or cleanup. Temporary atomic-write files may remain after a process
crash; M0 never treats them as committed revisions.

Back up the database, encryption key and revision directory consistently. Do not rotate or
restore the encryption key independently of existing ciphertext. Canonical files are not
changed, so retiring tracking does not require restoring user files. Do not modify immutable
revision/event rows to repair a pointer; preserve evidence and use a fresh capture.

## Verification

Run the new script tests with the project interpreter:

```text
python tests/run.py --py improvement -v
```

They cover real SQLite transactions, encryption integrity, history/idempotency, source
replacement, two-connection CAS, cross-brain authorization, migration rollback, Windows
junction rejection, disabled mirror shadowing and actual-main authentication/CSRF. No model
calls or startup/background jobs are part of these tests. Each test isolates state and brain
roots before importing server modules.

The source-capture stamp deliberately compares `ctime` within the path-stat source and
within the open-handle source separately. Windows Python can expose different meanings
through those APIs; cross-comparing them rejected valid captures. Raw-byte hashes remain
the source of content identity.

The [trial boundary contract v1](agent-skill-trial-contract-v1.md) freezes the M0 contract
for the next milestone; it does not enable execution. M1 and later implement isolated
trial execution, contracts/case sets, patch comparison, activation,
canary and the UI. M0's metadata tests do not establish those later capabilities or business
quality gains.
