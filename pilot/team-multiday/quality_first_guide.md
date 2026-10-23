# Quality-first Team pilot, 0.283.0 candidate

Status: technical preparation only. No real model execution or business effectiveness result has been recorded. The older 0.210.0 instructions in this folder remain a historical pilot; use this guide for the opt-in quality policy and paired comparison.

## Enable on a test copy

Use a separate state directory, brain and working folders. Freeze a consistent backup with the dispatcher and proactive jobs stopped; test restoring that copy before starting. Never downgrade an older build into upgraded live state or replay outside actions during rollback.

In the Team settings, enable default acceptance and explicitly select how existing limits should migrate. Select **observe** for quality-first owner obligations: migrated old saving presets no longer stop those obligations. **Retain** keeps existing limits binding. Explicit later owner/admin limits, provider quotas, permissions and autonomous-work mandates remain binding. Record this choice in the pilot configuration.

An optional USD reference only informs. Exceeding it, missing prices or telemetry errors must not stop work, reduce reasoning/context, change the model or skip verification. Do not add a cost approval checkpoint.

Set a default acceptance template before submission. Each new job snapshots that template. Without a template, the actual stored final message requires owner confirmation. Existing owner contracts remain binding when the policy is disabled. Amend contracts explicitly and reload the latest card before deciding; a retry of the same click retains its decision key.

**Command checker limitation:** the current host does not provide an immutable checker/dependency/fixture environment. For quality jobs, command criteria return `unknown` with `checker_isolation_unavailable` and require owner review; they cannot auto-certify success. File/JSON checks prove only their stated structural criteria, not business correctness. A trusted isolated checker is further work before automatic business certification is enabled.

## Rehearse without provider calls

From the repository root:

```text
python pilot/team-multiday/paired_evaluation.py --dry-run --out <new-output-directory>
```

The harness creates 20 synthetic pairs (10 content, 10 sales analysis), 40 execution records, independent workspaces/histories, a frozen manifest and a blank owner-review template. Its command line only accepts dry runs. Repeating the command against the same manifest does not replay executions. A changed manifest is refused; use a separate experiment directory. Interrupted or failed records remain part of the evidence.

The synthetic sales fixture contains an expected net solely for harness rehearsal. Before a real experiment, keep independently prepared answers outside both model contexts. Real adapters require `source_kind=real_frozen`, the same frozen model/reasoning/tools for both arms, isolated histories, and an explicit provider configuration. The current dry-run command does not construct a production Team adapter.

## Measure real business work separately

Prepare ten real content requests and ten real sales-analysis requests with frozen source hashes. Remove customer secrets and use copies. For each pair, preserve both drafts, failures, fixes, engine configuration and all observable usage. Do not publish, message customers, order or mutate original data as part of this pilot.

Observe for 7–10 days. The owner grades factual accuracy, completeness, usefulness and clarity, records critical errors and active review minutes, and decides acceptance independently of the model. Keep missing scores and unobserved usage null. Accepted-result cost must include failed/fix attempts and must remain undefined if no result was accepted. Subscription equivalents are not API cash bills. Compare workflows separately; twenty pairs are exploratory evidence.

The current harness report deliberately leaves business quality/cost conclusions pending. Completing grading ingestion, a production executor adapter and a comparative final report is a later pilot step; the dry run does not establish a saving or quality improvement.

## Evidence and coverage

`trace_export.py` exports contract/template versions, manifest hashes and raw usage observations with identities and money provenance. It opens databases read-only. It does not recheck live products: `completion_valid=null` and `validity=not_rechecked_by_export` distinguish historical evidence from current validity. Usage observations are not an additive reconciled bill; use the Team usage view for its projection and retain its partial/unknown coverage labels.

API instrumentation observes actual HTTP attempts including retries. Detailed CLI reconciliation is available only for exact newly linked Claude sessions whose token totals reconcile. Resumed or other CLI paths remain partial. Team judge executions without trustworthy host context remain unobserved by role, and cross-context reviews are unallocated. Runtime/wait durations are not part of the new usage summaries; the paired harness measures its own elapsed duration.

Stop for wrong acceptance, scope/permission breaches, original-data mutation, secret leakage or repeated actions without progress. Preserve evidence and record the actual reason. High reference spending alone is not a stop condition. Provider outages/quotas are technical interruptions; do not silently switch models to fill the measurement set.

## Release boundary

The candidate is opt-in and should remain a draft until recovery regressions and the branch checks pass. Default enablement for new teams, a real pilot conclusion and deployment to a production server are not completed by preparing this candidate. Rollback requires stopping dispatch, preserving post-upgrade evidence and restoring the complete consistent backup with its matching build in a test environment first.
