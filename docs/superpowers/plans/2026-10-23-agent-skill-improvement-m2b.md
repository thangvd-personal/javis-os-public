# M2B implementation plan: durable experiments and comparisons

Date: 2026-10-23 (Asia/Saigon).

Execution authority: docs/superpowers/AGENT-SKILL-IMPROVEMENT-GOAL.md and the full
29-section spec. Inline implementation, self-reviewed plans and independent final
review; no routine approval stop under the Goal's explicit authorization.

Actual baseline: published main cbef2f464932e533825512e8e11c2299e6b3bf4f,
version0.292.0, PR799. Required CI39862650038 passed on d4703f34, merged tree
identical; publication39865749261 succeeded on the actual merge. Original checkout
remains untouched. Reuse the existing managed M2 worktree with new branch
codex/agent-skill-improvement-m2b; retain ignored private evidence and worksheets.

Before code: reserve next free VERSION and both changelogs, push reservation and
open/attach draft PR. Engineering only until all required checks/review/publication;
no real-brain activation, production deploy/restart or live canary authorization.

## Outcome and explicit support

An owner can create one durable experiment for an existing exact Host context,
bind at most two actual A proposals, freeze complete counterbalanced comparisons,
execute actual tuning baseline slots, obtain immutable complete-pair reports and
seal KEEP_BASE or one final challenger. V1 requests/hashes/history stay unchanged.
B adds closed v2 lineage, not an alternate runtime/ledger/goal or generic model role.
Changed-arm execution remains unavailable before C semantic/dependency evidence
and D verified effective view. Qualification preflights BOTH variants before ANY
trial/call/global holdout claim/exposure. Until then it is blocked with zero evidence.
No compose, activation, eligibility from manual edit grant or alternate model.

## Review focus

1. Real schema4 migration/history/backup/old-reader refusal, no replayed DDL.
2. Exact context/contract/case-set/arm authority and immutable whole matrix.
3. V1 unchanged; v2 baseline has real trial/call/workspace/receipt/result lineage.
4. No early qualification call/claim/exposure; no renamed-family reset.
5. Stop retains worker/checker/process ownership through terminal cleanup.
6. Complete expected-slot accounting: unknown never disappears or becomes win.

## Task 1 — schema5, records and state CAS

- [x] Reproduce the named behavioral RED cases.
- [x] Implement this unit and inspect its exact source/authority boundaries.
- [x] Run named GREEN/regression checks, record actual evidence and commit the unit.

Evidence: experiment suite19 tests GREEN29s; combined CandidateRecords, Cases,
Experiments, PrivateReview, SkillCandidates, Store and Trials7/7 GREEN84s.
Interim independent Task1 review found no Important issues, including a distinct
valid context competing for proposed/blocked/stopping capacity. This is a tested
unit checkpoint, not whole-B acceptance or runtime/activation qualification.


New server/improvement_experiment_schema.py, improvement_experiments.py and
Python test_improvement_experiments.py; modify improvement_store.py migration.
Reuse existing ImprovementStore._replay/_event and actual GoalStore._Tx.

Schema5 explicitly accepts known1/2/3/4/5, candidate DDL only<4, experiment only<5.
Unknown/newer rejects before backup/write. Fresh store preserves M0 backup policy;
installed store gets one serialized pre-experiment backup with documented contents.
Preserve exact old rows/events/request JSON/hashes/FKs/immutable triggers.

Tables: experiments immutable definition+mutable phase/generation/typed blocker/
resume_from/stop intent; immutable arms (actual A ordinal1|2/candidate/envelope);
comparisons (exact matrix blob/hash); immutable cell->real trial/request links;
one irreversible final selection; immutable report refs. Composite brain FKs and
context->experiment identity triggers. No delete or candidate replacement. Partial
unique active-brain index includes blocked and stopping.

Records require actual owner principal. Cold get/list do not ensure schema,
decrypt sources, warm native/auth/settings/usage or infer current authority.
All writer helpers require existing serialized transaction. Host-built verified
definitions only; caller IDs/policy are requests, not authorization assertions.
Terminal transitions releasing active capacity require stopping protocol in Task6.

RED/GREEN: real4->5 migration preserving actual A/M1 encrypted history; rollback,
concurrent ensure, serialized backup, cold old reads, unknown5+ refusal, same-brain
active conflict/foreign brain, immutable rows/CAS/event replay. Install actual4
using released four DDL blocks, never fake downgrade by editing version only.
Test actual released A reader refuses5 before writes; use repo's existing pinned
historical git-fetch pattern in CI if needed, missing history is failure not skip.
Older fixture installers must not leave B tables behind. Latest-version assertions
may use current constant; historical fixture/backup assertions retain literal4.

## Task 2 — host definition, arms, matrix and irreversible selection

- [x] Reproduce the named behavioral RED cases.
- [x] Implement this unit and inspect its exact source/authority boundaries.
- [x] Run named GREEN/regression checks, record actual evidence and commit the unit.

Unit evidence: comparison-plan suite24 tests GREEN48s; related8/8 regression
GREEN157s. Independent review reproduced final protected-context observation
races in create/bind; root2 RED then GREEN after final full observations, and
reviewer's original diagnostics independently pass. No open Important findings
in this scoped unit review. Whole-B review/release remains pending.

Sequencing detail: KEEP_BASE seals and enters stopping atomically now. Candidate
selection fails closed until Task5 can verify exact complete immutable tuning
reports; final-matrix freeze requires the matching stored selection. Task5 must
open that branch with report evidence, not caller references or trial counts.
Host definitions freeze multiple_tests=2 before results (the actual A arm limit).


New improvement_comparison_plans.py and tests; narrowly extend A new-write guards.
ExperimentPlans(host,skill_candidates,checker_registry=None) requires actual
HostContextStore. prepare/create takes existing experiment/contract/case_set/
base_bundle IDs, expected context generation, feasibility|pilot repeat policy and
existing bounded timeout, event key. Host derives all pins/definition/hash.

Reuse TrialCaseAssociations._authority for context/grant/contract, then explicitly
repeat TrialAdmission._prepare's exact _association/context/experiment/contract/
grant/case_set-manifest checks. _authority alone does NOT authorize a case set.
Actual Host methods are _decode/_snapshot_binding/_grant. Verify full case membership,
actual current target/revision/bundle bytes and checker registry. Recheck after
slow reads and before final transaction commit. Definition freezes endpoint all_pass,
evaluator/decision policy/multiple-test count/source/config/grant/runtime fingerprints.
No task/answer/private paths in public metadata.

Bind actual A proposal via SkillCandidates._state/observe/read_proposal/_guard,
including original source authority, owner event, patch/provenance and ordinal.
New B context means explicit new owner staging/authorization: no transplant of
immutable old A proposals or rebind of case sets to a different contract. Changing
store/cases changes EVALUATOR_PIN; old contract/results remain readable history,
not permission to execute. Prepare fresh explicit contracts/context/cases.

One tuning comparison per challenger. Fresh baseline for each challenger, never
reuse diagnostic baseline. Final comparison spans qualification+regression for
one irreversible selected arm. Freeze full schedule before dispatch: repeats1
(feasibility) or2 (pilot), deterministic counterbalance from frozen host seed.
Compact ordered pairs {pair_id64,case_id<=128,repeat,first_variant}; position and
complement define both cell orders. <=1000 cases =>2000 pairs/4000 cells, ~518KiB
manifest; exact canonical UTF-8 bound1MiB, no task duplication/truncation/chunking.

KEEP_BASE is valid final outcome without holdout consumption. Selection seals
staging/region/diff grants before publication; revocation remains available.
Historical replay returns history, cannot restore grants or reopen comparisons.
A semantic_status stays immutable unreviewed; C adds separate evidence later.

RED/GREEN: unrelated same-brain case set; wrong context/contract/grant/hash;
manifest/current source/ACL drift during slow read/commit; old proposal under new
context; second-arm collision/third slot; duplicate freeze; max-size exact matrix;
source/seed/order substitution; stage/authorize after seal; KEEP_BASE/replay.

## Task 3 — closed v2 and actual tuning-baseline execution

- [x] Reproduce the named behavioral RED cases.
- [x] Implement this unit and inspect its exact source/authority boundaries.
- [x] Run named GREEN/regression checks, record actual evidence and commit the unit.

Evidence: final five new-unit scripts34 tests GREEN79s. Initial related14-script
run13/14 GREEN260s; only old frozen-capture call count needed3->5 after the new
post-I/O revalidation (one initial capture preserved), full Host24 GREEN15s.
Old M1 E2E/Runner/light-startup3/3 GREEN332s. Independent source review reproduced
v1 downgrade and late matrix/fixture authority/phase races; root RED->GREEN and
original independent repros now reject. No open Important scoped findings.

Measured follow-up: repeated public-source compilation consumed84% of capture
CPU. Bounded exact-content/filename code cache retains all fresh reads and live
function checks; cache tests3 RED->6 GREEN, independent6 GREEN. Independent
revalidation5.352->0.699s with byte-identical cached/uncached complete captures.
Root cache/Dashboard/old E2E3/3 GREEN75s; the same old E2E288->40s on this machine.
No timeout, freshness, native or quality gate was relaxed. Native four-file source
profile is unchanged. Candidate/final execution stays closed; the internal slot
admission is not an owner HTTP selector. Task6 must enforce next-cell scheduling.


New improvement_experiment_protocol.py (pure), improvement_experiment_lineage.py
(scoped SQL/blob reads), improvement_experiment_admission.py and tests. Deliberate
version branches in trials/host/checks/service/results/views/private review.
Protocol imports no Host/TrialStore/CaseStore. Lineage imports no Host/TrialStore/
SkillCandidates/CandidateRecords backedge. Higher Plans verifies actual A bytes.

validate_request_v2 and validate_lineage verify full closed shape/hash/equality.
Keep v1 parser/hash/logical expression verbatim. V2 carries same nine pins plus
exact definition/comparison/challenger/pair hashes and identity. Both variants
share challenger identity; variant selects actual assets. Namespaced logical key
includes comparison/pair/case/repeat/variant. candidate_bundle_hash in v2 is typed
verified A envelope hash, never a claim it is an installed canonical bundle.

_current resolves actual baseline and trusted arm envelope; baseline materializes
actual revisions. Changed variant rejects before reservation/plaintext/worker
until C+D. Host._bound validates full v2 lineage before/after source observation.
Never strip fields/recast v1. Native baseline worker/receipt protocol stays exact:
comparison ->request hash ->real trial ID ->materialization ->worker input ->result.
Commit slot link/trial/call/event in same writer transaction. Mint actual deadline
after preflight, not matrix queue creation. Existing CallLedger usage IDs remain;
arm attribution joins immutable links. No new telemetry money ledger.

RED/GREEN: exact v1 regression/replay; v2 substitution/cross-brain/source drift;
actual baseline-v2 E2E with synthetic availability/query, real workspace/Fernet/SQL/
ledger; second challenger no identity collision; changed arm fails lower reserve
before call/plaintext/trial evidence; historical v2 reads without native probes.

## Task 4 — one final qualification and both-variant readiness

- [x] Reproduce the bare-pin/renamed-owner behavioral RED cases.
- [ ] Implement this unit and inspect its exact source/authority boundaries.
- [ ] Run named GREEN/regression checks, record actual evidence and commit the unit.

Partial checkpoint: SQL-only ownership now follows experiment ID, frozen case-set
ID/hash and any incoming split sharing a frozen qualification fingerprint. Fresh
legacy reserve/consume and execution cannot erase that ownership; historical
event replay precedes the guard. The existing global claims/exposures are retained.
Original five tests reproduced3 failures/1 error; the guard made5/5 GREEN.
An additional renamed-owner admission test reproduced preparation before the
early ownership check; passing the actual case-set ID fixed it RED->GREEN.

Ruling: do not add an unused positive v2 reservation/consumption path or synthetic
readiness flags while C semantic evidence and D effective-view execution do not
exist. Full-v2 final qualification AND regression remain hard closed for both
variants in the actual TrialStore/Host/Admission boundaries. The technical test
fixture first verifies real frozen final lineage, then checks zero new claims,
calls or exposures at those boundaries. Positive selected-proposal allocation,
linked consumption and drift/resume tests remain C+D integration requirements;
Task4 stays PARTIAL. Task5 reporting can proceed independently of that closed
execution gate. Independent scoped review found no Important issue and passed
four focused final/alias/replay checks; this is not whole-B acceptance.


Extend existing CaseStore qualification branch; no parallel registry. Preserve
shared fingerprint claims, case-set uniqueness and consumption-at-materialization.
New experiment-aware lower-store guard cannot be bypassed with legacy nine-pin
shape/version omission. Current M1 checks actual bundle existence; v2 substitutes
only the explicitly verified selected proposal branch, never global relaxation.

Both selected variants must be ready before any final comparison dispatch, claim
or exposure, and after slow preflight/inside first claim+lineage+reservation commit.
Use same final nine-pin set for both; full extra lineage binds frozen comparison.
Regression cells also require final readiness. Until C+D actual profile ready,
no baseline qualification call/claim/exposure. Label synthetic readiness tests only.
Consumption/drift keeps original claims/final arm; fixed-candidate resume only.
No hidden ordinary wrong-answer retry or renamed experiment/case-family reuse.

RED/GREEN: direct lower-store unavailable candidate blocks baseline too; version
omission bypass; drift at claim/materialization; renamed families/case sets; wrong
final arm; duplicate consumption; KEEP_BASE zero claims; consumed source drift and
explicit recovery. Preserve owner-private artifact reads versus model projections.

## Task 5 — pure comparator and immutable exact-result reports

- [x] Reproduce the named behavioral RED cases.
- [x] Implement this unit and inspect its exact source/authority boundaries.
- [x] Run named GREEN/regression checks, record actual evidence and commit the unit.

Partial report component now has pure expected-cell arithmetic and a concrete
ComparisonResults store. Reports enumerate missing cells, pin exact immutable
result IDs/hashes, derive native integrity separately from product criteria and
retain unknown/unstable coverage. Repeats count within cases; corrected sign-test
decisions use integer arithmetic. Technical numerical success never certifies
business acceptance or real independence. Regression primary business losses
also block the pilot, even when that criterion is not marked critical.

Scoped review found three arithmetic defects, reproduced RED then fixed: cross-
split qualification contamination, an unknown repeat masking a known critical
failure, and primary regression loss being offset by qualification wins. Initial
pure13 tests became16 after those reproductions; result-projection tests bring
the pure suite to19 GREEN. Actual Runner/Fernet/ledger synthetic baseline tests
verify immutable report refs across genuine reevaluation and reject a pointer
change during report creation. Missing actual artifacts and missing real call
markers also reproduced RED->GREEN. A final-policy file-I/O source race and
uncaught corrupt Fernet result both reproduced RED->GREEN; the scoped reviewer
independently reran original diagnostics and found no remaining Important issue.
Final component regression passed5/5 scripts178s (Plans, new Reports/Results,
v2 Execution/Lineage); the report component has31 tests and was committedc860d8c0.
The selection component adds11 tests and a final seven-script GREEN264s run.
Only exact current complete tuning reports for EVERY declared arm can seal the
experiment. The chosen arm must show supported tuning improvement; other complete
arms may tie or lose. All report IDs/hashes are pinned in the immutable selection.
Publications use monotonic event IDs, preserving both wall-clock rollback safety
and explicit republication of identical immutable reports. The new source/authority
guards run after all slow policy/result work; replay remains cold after stopping.
The scoped reviewer found premature sealing with an incomplete second arm; the
root reproduced RED, fixed all-arm coverage and passed the full regression. Root
clock-order/republication reproductions also passed RED->GREEN. These synthetic
complete-storage fixtures never claim real native execution or business acceptance.
Task5's supported mechanics are complete; final execution remains C+D gated and
whole-B engineering/release acceptance is still Task6 work.


New improvement_comparison_results.py and tests. Enumerate expected cells first.
Select exact linked trial/attempt/result_id/result_hash, read via TrialResults.read
with explicit result ID. Verify brain/request/comparison/pair/variant/arm/contract/
case/evaluator pins before/after slow work. Latest pointer changes cannot alter
old reports. Missing/corrupt/stale result is incomplete, not silently dropped.

Separate execution integrity, product criteria, required regression and repeat
stability. Baseline infrastructure failure is unknown/incomplete, not challenger
win. Binary all_pass only until real numeric metrics exist. Per-criterion required
loss rejects regardless primary gain. Complete W/L/tie/unknown and conservative
one-sided exact sign test only after integrity/coverage. Pilot requires >=20 real
independent qualification families, >=12 regression cases, two repeats/variant,
>=5 discordants and p<=0.05/M with declared M, no required loss. Feasibility6/6/6
cannot satisfy pilot. Unknown/unstable blocks completeness. No endpoint switching.

Report stores ordered [cell_position,result_id,result_hash] (~819KiB max plus
bounded aggregate); exact1MiB canonical byte limit. Full details stay immutable
TrialResults. Experiment summary references comparison reports, never flattens
all detailed criteria into one oversize blob. Reevaluation appends new report.
Encrypted report+event publishes under CAS after authority revalidation. No report
activates or certifies present runtime from historical source hashes.

RED/GREEN: baseline win/tie/unknown/rejection/inconclusive; missing critical/regression,
repeat disagreement; wrong result attempt/arm/pair; mutable pointer races; corrupt
refs/byte bounds; exact statistical boundary and multiple-test correction.

## Task 6 — owner API, stopping/recovery, qualification and release

- [x] Reproduce the named behavioral RED cases.
- [x] Implement this unit and inspect its exact source/authority boundaries.
- [x] Run named GREEN/regression checks, record actual evidence and commit the unit.

Published engineering evidence: Task6 checkpoints69a91832/2fe944ae; fixed-source
nine-script regression9/9 GREEN251s, native exact-profile qualification5/5
GREEN14.845s. Fresh whole-B review's one recovery-scope finding reproduced RED
and fixed; independent original diagnostic plus3 recovery checks pass, no open
Important/Critical. Exact-head LinuxCI39880551488 SUCCESS on2fe944ae. PR800 squash
merged47f50f70/0.293.0 with identical reviewed tree4e53048721ca658f620a73361b4281326852e3b6;
actual GHCR publication39882926274/Build & push SUCCESS. Task6 acceptance audit
and task-done recorded. Task4 positive allocation/consumption still requires C+D;
the complete six-unit/full-M2 acceptance checkbox remains open deliberately.

New improvement_experiment_api.py/tests; concrete service/main lifetime wiring;
intentional route snapshot, runbook/status/vi-en errors. Full prep/navigation is F.
Closed owner controls: metadata list/get, create, bind arm, freeze comparison,
next slot, report, final selection, cancel/resume/recover. Host chooses next slot,
not caller cherry-picking. B no compose/apply/activation action. Reuse bounded
JSON/owner-session-before-body/CSRF/cross-brain/CAS/event semantics and lazy startup.

begin_stop(...expected_generation,kind,reason,event_key)->StopTicket commits durable
stopping fence/desired terminal; retains active index. Every v2 reserve/claim/
heartbeat/start/finish guard rejects stopping. Service drains pending admissions,
cancels actual linked nonterminal trials through existing service and drains all
owned tasks including DB-terminal rows OUTSIDE writer transactions. Expose
TrialTasks.drain_owned(principal,trial_ids). Foreign worker is not locally owned.
finish_stop nonblocking-acquires existing brain execution lock, cleans linked
terminal workspaces outside SQL writer, then short final CAS while holding lock.
Busy foreign worker or cleanup failure remains stopping; explicit recover retries
closure without dispatch. No fabricated trial ID for brain-only lock helper.
Never call public TrialStore.cancel inside experiment writer (nested transaction/
postcommit FS cleanup). Shutdown cleans own tasks, not implicit foreign cancellation.
All capacity-releasing terminal outcomes, including KEEP_BASE/stale/rejected/
inconclusive, use stop/drain/finalize. Late checker result cannot reopen stopped
experiment. Persist observations and genuine usage settlement through abort/recovery.

Tests: owner/token/CSRF/bounds/cold no-probe GET; stale/concurrent next; repeated
HTTP cancellation; cancel during native launch/checker/write/cleanup; DB terminal
but process alive; foreign live lock; restart/recovery no dispatch; lazy import.

Exposing TrialWorkspace brain-only lock changes improvement_sandbox.py and thus
native four-file profile hash. Rerun exact selected runtime qualification using
existing dummy HOME/auth+localhost fake-provider test protocol and save current
sanitized evidence; oldc3b is historical, not new-source proof. No real account
needed for that technical qualification. Real baseline-v2 smoke only after exact
selected config/runtime verified, with usage and unknown coverage honest; never
rerun to obtain a win. No arbitrary model-generated Python execution: command_ok
needs independently verified isolated runtime, remains unsupported before that.

Run relevant migration/permission/prompt/security/ledger/learning/Team/lifetime/
UI/route checks. Fresh whole-B independent review, fix actual reproduced issues,
final exact-head Linux CI, fresh version/main checks, straight merge and publication
success. Preserve M1 encrypted smoke history/key. Continue C against actual published
source, old B contexts may legitimately become stale after C/D code changes.

## Arm edit authority detail

SkillCandidates.read_proposal verifies original immutable authoring bytes, not a
current execution grant (private reads deliberately survive edit revocation).
B arm binding must additionally choose and pin one actual active edit authority:
original editable-region grant or an exact-diff grant bound to this candidate's
before/after bytes/context/target/source authority. Pin authority ID/generation/hash
in the immutable envelope, revalidate status and complete identity at dispatch/
qualification/query/publication. Never trust CandidateRecords' display-priority
edit_authority alone, never auto-switch to another grant after pinned revocation.
An explicitly later exact-diff authorization can authorize actual bytes after an
older region was revoked; this does not revive that older region. After sealing,
no new authorize/stage/region action can refresh the pinned experiment. Tests must
separate historical readable proposal, unauthorized proposal, revoked chosen grant,
explicit fresh exact-diff grant, and revocation during slow arm publication.

## Report evidence-class boundary

Distinct family keys/source fingerprints enforce declared case separation, not a
proof of real-world statistical independence. B source inputs have no accepted
business-dataset attestation today. Reports must preserve that limitation and the
technical/synthetic evidence label; a hypothetical complete synthetic20/12 matrix
may pass the pure numerical gate in tests, never become business eligibility.
Freeze evidence class/provenance in definition; do not accept a caller/model string
as an accepted real dataset. Until actual M3 owner/data review exists, persisted
reports expose mechanical coverage/statistics separately and business eligibility
unverified. E cannot use a technically passing report to bypass real dataset/
semantic/runtime/scope prerequisites. No invented independence from distinct IDs,
multiple rows/repeats or multiple examples of one historical bug family.

## Transport projection detail

Principal.brain_id is the canonical absolute source root internally. New public
experiment/list/status projections must omit it and any native root/paths/raw
bindings; use authenticated requested brain label/opaque IDs/hash/status instead.
A already does this. Stored phase/metadata on GET is not a fresh source/native
availability claim: expose not_checked/unavailable where appropriate. Explicit
owner preflight/write may observe source/config; cold lists never warm source or
probe auth/model. Keep cryptography/native service imports inside actual operations;
run existing khoi_dong_nhe after API wiring, not only at final full CI.

Task6 concrete service file: create server/improvement_experiment_service.py and
its lifetime tests instead of growing main or admission with async ownership code.
TrialWorkspace.try_cleanup returns False for both already-absent and busy lifetime;
finish_stop must prove linked terminal workspace/journal absent or successfully
cleaned under the actual existing brain lock, not interpret every False as success.
Do not delete unrelated journals/workspaces. Terminal finish replay never resumes
or creates a new stop intent; retain original stop event/generation and historical
acknowledgment, refresh current metadata separately. Add failure-between-cleanup/
final-CAS and repeated cancellation of stop tests.


## Self-review and implementation gates

- [x] Actual published A source and final-head CI/publication verified; no assumed
  API names substitute for source. Reused M0/M1/A pieces are identified per task.
- [x] Independent draft-review findings corrected: exact case-set association,
  fresh context/proposal lineage, immutable result refs, chosen active edit grant,
  real stop ownership and explicit technical/business evidence distinction.
- [x] V1/source/owner/grant/model/runtime invariants retained. Unknown effort and
  semantic use are not promoted to pass. Later C/D changes may stale B contexts.
- [ ] Complete six units with meaningful RED/GREEN, current-source qualification
  and required scope/migration/lifetime regressions.
- [x] Fresh independent whole-branch review; fix reproduced findings and verify.
- [x] Final exact-head Linux CI; fresh main/version/head checks; straight merge
  and actual build/publication success. Continue C on integrated source.

No mock-only result, planned matrix, manual edit approval or technical statistic
fulfills the complete M2/business/rollout milestones. Preserve all three statuses.
