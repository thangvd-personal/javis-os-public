# Agent and skill improvement M2A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an owner stage, inspect and authorize an exact body-only skill proposal while preserving canonical files and all existing trial gates.

**Architecture:** Add immutable ownership/candidate records to the existing improvement database, with encrypted bytes in RevisionBlobs. A pure byte-preserving validator constructs candidate content; an owner service observes actual canonical authority before committing. Private review supplies exact artifacts and diffs through browser-owner-only routes. M1 admission continues to reject changed candidates until later M2 experiment and semantic-review paths are implemented.

**Tech Stack:** Existing Python3.12/FastAPI/SQLite/Fernet storage; plain JavaScript dashboard and vi/en localization. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-10-22-agent-skill-evidence-improvement-design.md`, especially7–14,18,20,22,23/M2 and24. Authority/execution method: `docs/superpowers/AGENT-SKILL-IMPROVEMENT-GOAL.md` sections3/5. The Goal authorizes self-review and continuous inline implementation with independent review; no renewed routine plan approval is needed.

## Global Constraints

- Quality and correctness precede cost; do not change provider/model/reasoning or add a money cap.
- A legacy skill without an ownership map is protected. Never infer that its body was AI-written or editable.
- Candidate bytes stay outside canonical and global mirrors. Staging must not update target.current_revision_id, source_hash, protected_hash or generation.
- V1 changes one primary component and has at most two candidates per experiment; KEEP_BASE is valid.
- Preserve name/slug, description, frontmatter, disabled state, identity, ownership, scope, permissions, dependencies and owner rules. No helpers, scripts, routing changes or agent edits.
- Preserved lines do not prove semantic consistency. Edit authorization and semantic review are different records; unresolved semantic review grants no trial/apply authority.
- Reuse the existing database, encrypted blobs, event/CAS conventions and exact-output decisions. Do not create a Goal, Team job, ledger or engine call for staging/review.
- Storage/source observation runs off the event loop. Caller cancellation cannot abandon a committed owner action. GET status stays cold and probe-free.
- Actual user source grants, ACLs, source versions and revocation apply to private review. No answers/checker internals, native paths or secrets in browser projections.
- All UI copy is vi/en; long explanations use JavisInfo. Artifacts/diffs render as inert text.
- M2A does not enable trial/activation capability flags. Full M2 remains open after this subproject.

## Review Focus

1. UTF-8 multibyte boundaries, BOM, CRLF and ambiguous YAML delimiters: preserve exact untouched bytes; reject ambiguous layout or split code points (Task2).
2. Owner edits/replaces/deletes/disables a skill during slow blob or permission I/O: reject the stale decision without refreshing authority (Tasks2/3).
3. Concurrent third candidate, duplicate request and revoked edit grant: exactly two immutable slots, stable replay and no revived authority (Tasks1/2).
4. HTML/script/prompt injection, missing/corrupt encrypted artifact and stale tab: inert complete content or explicit unavailable; no invented exact hash/owner decision (Tasks3/4).
5. Cancelled HTTP response or shutdown during an owner write: retain the offloaded operation through its actual commit/rollback, with no native/auth/model call (Tasks3/4).

## Delivery sequence for all M2

This plan covers only subproject A. Write and self-review each later plan against the integrated source; do not scaffold their code here.

| Subproject | Independently reviewable result | Required before moving on |
|---|---|---|
| A, this plan | Ownership declarations, immutable candidate provenance, exact private artifact/diff review | Source/permission, migration, byte-preservation and inert UI evidence |
| B | Durable experiment lifecycle, maximum-two candidate arms, complete comparisons and one final qualification pair | Extend logical slot identity deliberately; preserve all old trial pins/history |
| C | Explicitly authorized proposal/semantic-review orchestration with tuning-only access | Real improve_propose/evaluate/compose purposes and roles; no disguised solver calls or holdout access |
| D | Shared scoped effective-view resolver and one verified production runtime/load path | Index/lazy loading/native materialization/telemetry agree; immutable task pin and resume boundary |
| E | Atomic manual apply/revert and designated-context canary | Current source/grant/owner/dependency guards, materialization/outbox recovery and compatible rollback |
| F | Complete operator workflow and6/6/6 feasibility | Baseline-win/tie/unknown paths, complete report and honest business/rollout gates |

M1 code checkpoint4c75c379, documentation46a42fb3, PR795. Independent M1 review passed. Integrate this subproject only after M1 final CI/merge/publication; before code, fetch main, inspect open PR reservations and reserve the next free release in its own draft PR. Do not include M2 source changes in PR795.

Integration update: M1 final CI39852883192 passed exact e0381902; PR795 merged
main9dd7a9f9 as0.291.0 and publication39856024117 succeeded. A's six own commits
were replayed onto that actual main without source-tree changes.0.292.0 remains
the sole open reservation. A's whole review and narrow stale-skill correction
review have no Important findings; final-head CI and A integration remain open.

## Source reuse and deliberate changes

- `improvement_assets.capture_source` is the actual canonical/pack/system/disabled/identity authority observer. Supply the actual pack-state root; do not invoke capture to stage a candidate.
- `ImprovementStore.capture` changes the canonical current revision and is unsuitable for proposals. Candidate tables reference their original baseline revision and encrypted result bytes separately.
- `HostContextRecords` provides cold scoped binding/grant records; `BrainFileFixtures` revalidates current-version fixture membership and ACLs without model/settings/auth probes.
- `TrialProducts.artifact` supplies hash/size-verified encrypted historical bytes. Existing trial decisions already bind the exact artifact; retain them.
- `TrialAdmission._bundle` and its frozen single-pair plan remain unchanged in A. Later B must model candidate arms before allowing changed candidates; unrelated experiment IDs must not simulate arms or reset holdout lineage.
- `agent_rules.missing_owner_lines` is supplemental. Do not broaden its managed sections or treat a regex/model vote as proof that new instructions respect owner rules.
- Current actual reasoning and native asset use can remain unknown. No A action changes historical receipts/results, aggregate rules or eligibility.

### Task1: schema4 ownership/candidate records and migration

**Files:** Create `server/improvement_candidate_schema.py`, `server/improvement_candidate_records.py`, `tests/python/test_improvement_candidate_records.py`; modify `server/improvement_store.py` migration dispatch only.

**Interfaces:** `CandidateRecords(assets)` uses the same store/blobs. `get(principal,candidate_id)->dict`, `list(principal,experiment_id)->list[dict]`, `contexts(principal,canonical_ref,*,cursor=None,limit=50)->{items,next_cursor}`, `revoke_authority(principal,authority_id,*,expected_generation,event_key)->dict`. Metadata omits raw bytes. Context listing reuses cold M1 context/grant metadata and the associated public objective, limited to the selected skill/brain. New records reference one existing host context experiment ID, one skill target, original base revision/hash/generation, immutable candidate ordinal1|2, resulting raw hash, patch/provenance hashes and authority generation. Authority records distinguish `editable_region` from `exact_diff`. Region records pin one UTF-8 byte interval; exact-diff authority pins both complete before/after hashes. Candidate content/provenance are append-only; revocation increments authority generation without rewriting them.

- [x] Write `test_schema3_cold_reads_do_not_migrate`, `test_schema4_upgrade_preserves_real_m1_trial_history`, `test_concurrent_upgrade_has_one_serialized_backup`, `test_failed_upgrade_rolls_back`, and `test_foreign_brain_and_revoked_authority_do_not_read_private_records` against scratch real SQLite/encrypted blobs. Preserve the existing schema3 fixture/history and old metadata behavior.
- [x] Run `python tests/run.py --py improvement_candidate_records -v`; capture RED for the absent schema/service.
- [x] Implement schema4 in one existing store transaction, backup suffix `.pre-improvement-m2-candidates.bak`. Accept known older schemas1/2/3; run each older schema block only when absent. Never replay schema3 CREATE TABLE/immutable triggers on an installed schema3 database. Unknown/newer schema remains unsupported. Add foreign keys, immutable-history triggers and unique `(brain_id,experiment_id,ordinal)` constraints.
- [x] Run new tests plus `improvement_store`, `improvement_cases`, `improvement_trials` and `improvement_trial_admission_transactions`. Check an isolated copy using the previous released M1 reader fails before an improvement write on schema4; do not alter the primary checkout or dispatch old work.
- [x] Commit only this tested unit with its migration/runbook notes. No candidate admission, model call or capability enablement.

### Task2: body layout, pure patch validation and owner staging service

**Files:** Create `server/improvement_skill_patch.py`, `server/improvement_skill_candidates.py`, `tests/python/test_improvement_skill_patch.py`, `tests/python/test_improvement_skill_candidates.py`; use Task1 records without changing canonical capture/admission.

**Interfaces:** `body_layout(base:bytes)->dict` returns exact body-start and frontmatter/prefix hashes without reconstructing the file. `validate_body_patch(base:bytes,patch:dict,region:dict|None)->dict` produces exact resulting bytes/raw hash plus structural checks, or a bounded rejection code. Closed patch fields: `version=1,target_id,base_revision_id,before_hash,operation='replace_body_region',start,end,replacement,hypothesis,failure_signature,expected_behavior,possible_regressions,evidence_refs,angle`; angle is `execution|domain_process`. Offset integers exclude booleans. UTF-8 replacement is bounded by MAX_ASSET_BYTES; total candidate is bounded by the existing asset limit. Evidence references are host-resolved owner-request/tuning references in the same context, never qualification answers or paths supplied as authority.

Reuse the closed IC identifier/digest validators and event-key length8..160. Offsets satisfy `0 <= start <= end <= len(base)` and both byte boundaries decode strictly. Hypothesis/failure_signature/expected_behavior are nonempty strings of at most4000 characters; possible_regressions is1..16 nonempty strings of at most2000 characters. Evidence refs are0..32 closed `{kind:'owner_request'|'tuning_trial',id}` objects; owner_request must resolve to the same-context owner event, tuning_trial to an authorized completed tuning record. A new explicit browser-owner staging request supplies its own owner-request event atomically when no earlier tuning evidence exists; model input cannot assert that provenance. Version is an actual integer1.

`SkillCandidates(assets,*,pack_state_dir).stage(principal,experiment_id,patch,*,event_key)->dict` binds the actual existing host context and skill baseline. `declare_region(principal,experiment_id,base_revision_id,start,end,*,expected_target_generation,event_key)->dict` creates explicit owner edit authority. `authorize_diff(principal,candidate_id,*,expected_generation,before_hash,after_hash,event_key)->dict` grants only the exact reviewed edit. The browser owner supplies intent; the host derives brain/actor, target/source identities and all hashes/provenance. These methods never return an engine-admissible changed bundle in A.

- [x] Write tests for no ownership map (diff-only), exact owner region, unchanged KEEP_BASE result, invalid byte boundaries/BOM/CRLF/YAML, untouched prefix/suffix/frontmatter/description, unsupported target/helper/script/routing change, sensitive text, revoked permission and concurrent third candidate. Assert canonical/global mirrors/current revision/generation remain byte-for-byte unchanged and agent_calls remains empty.
- [x] Run the two new selectors and capture behavioral RED. A typed caller hash must never substitute for hashing the actual stored/observed bytes.
- [x] Implement exact prefix+replacement+suffix construction. Plain YAML-layout ambiguity rejects; never normalize Unicode/newlines or parse-and-redump frontmatter. A region is entirely inside the body. Without an explicit region, stage only a proposed diff awaiting exact owner authorization. Preserve the frozen dependency inventory and reject new helper/script references; unrecognized dependency semantics remain unresolved rather than certified safe.
- [x] Observe actual canonical identity/ownership/disabled state before slow work and recheck before the writer commits, including after blob publication. Commit immutable provenance, ordinal and successful owner event together. Replay is historical and cannot restore a revoked grant. Semantic status stays `unreviewed`; an edit-authority approval cannot set it to pass or waive protected context/owner-rule checks. Later C supplies independently reviewable semantic evidence.
- [x] Run new tests and relevant assets/Host/admission regressions. Verify simultaneous different third-slot attempts have one valid remaining winner, no orphan candidate/event and no canonical mutation. Commit the reviewed unit.

### Task3: private exact review with current source authority

**Files:** Create `server/improvement_private_review.py`, `tests/python/test_improvement_private_review.py`; extend Task2 service only for scoped private reads.

**Interfaces:** `PrivateReview(assets,*,fixture_observer=BrainFileFixtures(),pack_state_dir).candidate(principal,candidate_id)->dict` returns full before/after UTF-8 text, raw hashes/byte sizes, exact proposed region, authority generation and separate unresolved-semantic status. `artifact(principal,trial_id,attempt)->dict` returns the exact immutable artifact, task/public contract, authorized fixture facts and source versions. Both are private review projections, separate from cold public status. No raw native paths, answers, checker implementation/reasons or current usage-index refresh.

- [x] Write tests for full non-ASCII/CRLF/BOM content and recomputed exact bytes/hash; corrupted/missing blob; foreign brain; revoked host grant; current ACL/source-version change during decrypt; disabled/replaced canonical skill; artifact HTML/JAVIS_RULE/task instructions as plain data. Assert encrypted evidence never changes and no model/native/auth/settings/source-capture builder runs.
- [x] Run new selector RED. Reuse existing hash/size-verified TrialProducts reads and actual host context/case associations; do not create a fake Host/engine callback just to satisfy construction.
- [x] Implement source-specific read checks: candidate review observes actual skill authority; artifact review verifies the active scoped read grant, case snapshot membership, exact frozen fixture versions and current BrainFileFixtures authority. Recheck grant/authority after slow reads before returning. Historical status remains available when private content is unavailable. Policy off does not itself create a new read grant; revoked/unavailable sources remain unavailable.
- [x] Return complete exact UTF-8 text with explicit raw SHA/byte size; do not truncate/redact while claiming the original hash. Current inputs are UTF-8-only. A bounded rejection remains explicit. Private answers never enter this projection. Viewing an artifact does not itself record owner acceptance or modify a trial verdict.
- [x] Run private-review, candidate, trial decision/result/view and source regressions. Commit the reviewed unit.

### Task4: browser-owner APIs and inert Studio review panel

**Files:** Create `server/improvement_candidate_api.py`, `dashboard/improvement-candidates.{js,css}`, `tests/python/test_improvement_candidate_api.py`, `tests/python/test_improvement_candidate_main.py`, `tests/js/test_improvement_candidates.mjs`; modify `server/main.py` dependency wiring, `dashboard/studio.js` skill-detail entry, `dashboard/index.html` script registration, both `dashboard/i18n/{vi,en}.json`, intentional `tests/python/route_table.json`.

**Interfaces:** Register browser-owner-gated routes: GET `/improvement-candidate-contexts` (cold selected-skill context metadata), GET `/improvement-candidates` (cold metadata), POST `/improvement-edit-regions`, POST `/improvement-candidates`, POST `/improvement-candidates/{candidate_id}/review` (explicit private read), POST `/improvement-candidates/{candidate_id}/authorize`, POST `/improvement-edit-authorities/{authority_id}/revoke`, POST `/improvement-trials/{trial_id}/artifact-review`, POST `/improvement-candidate-baselines/{experiment_id}/review`. The last route accepts only `{target_id}` and returns the complete verified baseline, current revision/generation and editable-start byte under the existing active context read grant. Brain is strictly resolved; mutation bodies carry event_key and relevant expected generations/hashes. Reuse M1's2MiB/depth32/node100000 bounded JSON parsing strategy and closed schemas. No client actor/grant/capability hash, arbitrary source path or executable checker is accepted.

**Source-based adjustment (Task4):** The actual `/skills/get` strips YAML metadata and normalizes text through `_read_md`; cold revision metadata intentionally supplies no private bytes. Neither can support byte-exact selection. Add the ninth explicit private baseline-read route above, reusing actual SkillCandidates source/ACL/grant checks before and after decryption. It records no edit authority/event and starts no trial. This preserves the spec's exact-byte invariant rather than reconstructing a different SKILL.md in the browser. Artifact review has a secondary stored-trial/attempt entry; task-history navigation remains part of F.

`window.JavisImprovementCandidates.open({brain,slug})` opens a panel from an enabled user skill's Studio detail. The panel can capture/choose the existing baseline, list/stage candidate diffs, choose an explicit body region, authorize one exact diff and inspect an exact artifact. Context/experiment selection uses actual authorized context metadata; it does not silently create sources/contracts or start a trial. Requests use current brain plus stable per-action event keys. Stale responses from a different brain/skill cannot redraw the active panel.

Show contexts by public objective/date rather than raw internal IDs. With none prepared, show a truthful empty state and keep staging disabled; complete context preparation belongs to F, not a dead-end placeholder button. After a replayed decision, refresh cold current authority metadata before enabling another action, so a historical approval cannot appear to revive a revoked edit grant.

- [x] Write ASGI RED tests for browser owner gate before body/dependencies, API-token-only rejection, CSRF, strict/cross-brain inputs, off-policy new staging versus historical replay, cold GET and repeated HTTP cancellation draining a committed write. Write JS RED tests for inert `<script>`/event-handler/Markdown/directive content, full-text/hash display, stale generations, brain-switch races, vi/en and explicit unavailable results.
- [x] Implement dependency wiring using current actual state/pack roots and source providers. Retain complete offloaded owner operations through transport cancellation and shutdown; reuse the existing settled-operation pattern. Source reads/SQLite/blob work stay off-loop. Error projections use fixed localized codes and omit private bytes/paths.
- [x] Render candidate/artifact text with textContent/text nodes, never innerHTML/Markdown execution. Show separate `proposed`, `edit authorized`, `semantic review pending`, `stale`, `rejected` states. Buttons describe edit authorization; no trial/apply/eligible/success claim appears in A. Long explanations use the JavisInfo delegated disclosure classes; primary actions remain visible. Use exact original bytes for download and clearly identify complete content/hash; no preview masquerading as the whole artifact.
- [x] Run new API/JS suites, route snapshot, authentication/CSRF/security, i18n and prompt-budget checks. Exercise mobile-width and vi/en panel rendering with synthetic data, keeping all runtime roots isolated and no production server startup/model calls. Focused14 Python scripts passed; the additional unrelated webhook POSIX-mode600 script failed on Windows and remains unchanged. Linux final-head CI is still required.
- [x] Fresh independent review, fix reproduced defects, then final-head CI/straight merge/publication verification under repo rules. Required CI39862650038 passed on d4703f34; PR799 straight squash merged cbef2f46 as0.292.0 with an identical tree, and publication39865749261 succeeded. Continue B from that published source. Business/rollout remain open; no actual brain activation or production restart.

## Plan self-review and acceptance boundary

- [x] Spec coverage: A implements ownership/provenance/private review portions of9/11/16; B–F explicitly carry the remaining M2 acceptance. No A completion claims full M2.
- [x] Interface consistency: exact names and record fields above are shared across tasks; schema4 migration retains old M1 request/result hashes. Later candidate arms are deliberately absent until B defines their durable identity.
- [x] All five Review Focus conditions have named behavioral tests. Tests must observe the intended failure, not fail only on an unrelated import/environment error.
- [x] Run actual source/migration/permission checks and record commands/results. Preserve final M1 profile/real-smoke provenance; if any of its four profile source files change, reassess and qualify the new exact profile instead of reusing its hash. No runtime-profile source changed in A.
- [x] A introduces no engine call, comparison verdict, apply authority or model fallback. Unknown actual reasoning/asset use stays unknown; manual permission does not waive source/config invariants.

The Goal's existing execution authorization overrides the generic skill request to stop for plan approval. Link the saved plan in progress, self-review it, then implement its verified prerequisites continuously. Ask the owner only for a concrete missing business/source/live permission after the independently authorized preparation is complete.
