"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "../..");
const TM = require(path.join(root, "dashboard/teams.js"));
const vi = JSON.parse(fs.readFileSync(path.join(root, "dashboard/i18n/vi.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(root, "dashboard/i18n/en.json"), "utf8"));

assert.strictEqual(typeof TM.qualityPolicyHtml, "function", "quality controls are available");
let html = TM.qualityPolicyHtml({ quality_policy: { enabled: false, limit_migration: 'retain', limit_migration_selected: false }, acceptance_template: null });
assert(html.includes("tm-quality-form") && html.includes("tm-quality-save"));
assert(html.includes("tm-quality-migration") && !html.includes('value="stop"'));
assert(html.includes("tm-template-form"), "the owner can set a default template");
assert(html.includes("info-body"), "long explanations are behind info tips");
assert(!html.includes('value="retain" selected'), "an unchosen legacy limit is not preselected");
assert.strictEqual(TM.qualityPolicyPayload({ enabled: true, limit_migration: "", reference_usd: "" }).ok, false);
for (const value of ["NaN", "Infinity", "-1", true]) {
  assert.strictEqual(TM.qualityPolicyPayload({ enabled: true, limit_migration: "observe", reference_usd: value }).ok, false);
}
const good = TM.qualityPolicyPayload({ enabled: true, limit_migration: "observe", reference_usd: "0.01" });
assert.deepStrictEqual(good.body, { enabled: true, limit_migration: "observe", reference_usd: 0.01 });
const template = { version: 4, criteria: [{ kind: 'owner_confirm', target: 'message' }],
  expected_outputs: [{ path: 'answer.txt' }], source_refs: [{ path: 'source.csv', sha256: 'abc' }], fix_rounds: 1 };
assert.deepStrictEqual(TM.qualityTemplatePayload(template, 4, [{ kind: 'file_exists', path: 'answer.txt' }]), {
  expected_version: 4, criteria: [{ kind: 'file_exists', path: 'answer.txt' }],
  expected_outputs: template.expected_outputs, source_refs: template.source_refs, fix_rounds: 1
}, 'editing template criteria preserves the other owner obligations');

html = TM.qualityUsageHtml({ attempts: 3, requests: null, tokens_in: 12, tokens_out: 7,
  money: { reported_usd: null, estimated_usd: null, subscription_equivalent_usd: 1.2, unknown_requests: 1 },
  coverage: { status: "partial", unobserved_attempts: 1, unresolved_events: 0 } }, 0.01);
assert(html.includes(vi["teams.quality.partial"]) && html.includes(vi["teams.quality.subscription"]));
assert(html.includes(vi["teams.quality.unknown"]) && !html.includes("$0.00"));
assert(!html.includes("data-ap-decide"), "a usage reference never becomes an approval gate");
html = TM.qualityUsageHtml({ attempts: 1, requests: 1, tokens_in: 1, tokens_out: 1,
  money: { reported_usd: 2, estimated_usd: null }, coverage: { status: "observed" } }, 0.01);
assert(html.includes(vi["teams.quality.above_reference"]));
html = TM.taskQualityHtml({ acceptance: { status: 'accepted', completion_valid: false },
  usage: { attempts: 1, requests: null, money: {}, coverage: { status: 'unknown' } } });
assert(html.includes(vi["teams.quality.stale"]) && html.includes(vi["teams.quality.task_usage"]));
html = TM.taskQualityHtml({ task: { status: 'done' }, acceptance: null });
assert(html.includes(vi['teams.quality.unverified']), 'historical done does not imply verified acceptance');

const dataset = { acc: "job", accRev: "3", accRound: "2", accContract: "5", accManifest: "abc" };
const first = TM.acceptanceDecisionPayload(dataset, "accept", "");
assert.strictEqual(first.contract_version, 5);
assert.strictEqual(first.output_manifest_hash, "abc");
assert(first.decision_key);
assert.deepStrictEqual(TM.acceptanceDecisionPayload(dataset, "accept", ""), first, "network retry reuses click identity");
assert.notStrictEqual(TM.acceptanceDecisionPayload(dataset, "reject", "fix").decision_key, first.decision_key);
html = TM.acceptanceCardHtml({ id: 2, meta: { acceptance: { task_id: "job", status: "review", revision: 3,
  rounds: 2, contract_version: 5, output_manifest_hash: "abc", criteria: [], last: [] } } });
assert(html.includes('data-acc-contract="5"') && html.includes('data-acc-manifest="abc"'));
assert.deepStrictEqual(TM.parseCriteria("confirm-message:").criteria, [{ kind: "owner_confirm", target: "message" }]);
for (const key of Object.keys(vi).filter(k => k.startsWith("teams.quality."))) assert(en[key], key);
console.log("PASS quality controls, usage provenance, and version-bound acceptance clicks");
