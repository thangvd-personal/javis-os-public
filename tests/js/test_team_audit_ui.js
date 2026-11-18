"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..", "..");
const TM = require(path.join(ROOT, "dashboard", "teams.js"));
const vi = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "vi.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "en.json"), "utf8"));
const css = fs.readFileSync(path.join(ROOT, "dashboard", "console.css"), "utf8");

let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

// 1. Kiem tra i18n day du va khong co em dash
const auditKeys = [
  "teams.audit_log_enable", "teams.audit_log_explain", "teams.audit_log_view", "teams.audit_log_close",
  "teams.audit_title", "teams.audit_enabled_badge", "teams.audit_disabled_badge",
  "teams.audit_search_ph", "teams.audit_filter_time", "teams.audit_time_all", "teams.audit_time_1h",
  "teams.audit_time_24h", "teams.audit_time_7d", "teams.audit_time_30d", "teams.audit_filter_agent",
  "teams.audit_agent_all", "teams.audit_filter_action", "teams.audit_action_all", "teams.audit_action_work",
  "teams.audit_action_plan", "teams.audit_action_synthesize", "teams.audit_action_help",
  "teams.audit_action_reply", "teams.audit_action_volunteer", "teams.audit_filter_tool",
  "teams.audit_tool_all", "teams.audit_tool_has", "teams.audit_tool_none", "teams.audit_filter_status",
  "teams.audit_status_all", "teams.audit_status_done", "teams.audit_status_error",
  "teams.audit_status_approval", "teams.audit_reset_filters", "teams.audit_empty",
  "teams.audit_no_match", "teams.audit_refresh", "teams.audit_export", "teams.audit_request",
  "teams.audit_response", "teams.audit_tool_calls_title"
];

auditKeys.forEach(function (k) {
  check("i18n vi: " + k, !!vi[k]);
  check("i18n en: " + k, !!en[k]);
  check("khong co em dash trong vi: " + k, !/[\u2014]/.test(vi[k]));
  check("khong co em dash trong en: " + k, !/[\u2014]/.test(en[k]));
});

// 2. Kiem tra CSS
check("CSS co luot cho .tm-audit-view", css.includes(".tm-audit-view"));
check("CSS co luot [hidden] cho .tm-audit-view", css.includes(".tm-audit-view[hidden]"));
check("CSS co luot cho .tm-chat.audit-on", css.includes(".tm-chat.audit-on"));
check("CSS co luot cho .tm-audit-head", css.includes(".tm-audit-head"));
check("CSS co luot cho .tm-audit-filters", css.includes(".tm-audit-filters"));
check("CSS co luot cho .tm-audit-card", css.includes(".tm-audit-card"));
check("CSS co luot cho .tm-audit-tools", css.includes(".tm-audit-tools"));
check("CSS co luot cho .tm-audit-details", css.includes(".tm-audit-details"));
check("CSS co luot cho .tm-audit-empty", css.includes(".tm-audit-empty"));

// 3. Du lieu mau
const members = [
  { agent_key: "ag_lookup", slug: "ecc-docs-lookup", name: "Chuyen gia tra cuu", role: "member", status: "active" },
  { agent_key: "ag_leader", slug: "team-lead", name: "Truong nhom", role: "leader", status: "active" }
];

const now = Date.now() / 1000;
const sampleEntries = [
  {
    id: "aud_1",
    ts: now - 300, // 5 phut truoc
    agent_key: "ag_lookup",
    action: "work",
    task_id: "task_101",
    task_title: "Tra cuu tai lieu ERP",
    status: "completed",
    tool_calls: [
      { tool: "read_file", args: { path: "docs/erp.md" }, output: "ERP documentation content" }
    ],
    prompt: "Hay doc noi dung file docs/erp.md va tom tat.",
    response: "File docs/erp.md chua cac thong tin ve ERP...",
    duration_s: 2.5
  },
  {
    id: "aud_2",
    ts: now - 7200, // 2 gio truoc
    agent_key: "ag_lookup",
    action: "work",
    task_id: "task_102",
    task_title: "Chay kiem thu he thong",
    status: "error",
    tool_calls: [
      { tool: "bash", cmd: "pytest tests/", output: "Process timeout" }
    ],
    prompt: "Chay toan bo test trong thu muc tests/",
    response: "",
    error: "engine bao loi: timeout qua 120s",
    duration_s: 120.0
  },
  {
    id: "aud_3",
    ts: now - 86400 * 2, // 2 ngay truoc
    agent_key: "ag_leader",
    action: "plan",
    task_id: "task_103",
    task_title: "Lap ke hoach quy 4",
    status: "approval",
    tool_calls: [],
    prompt: "Len ke hoach trien khai phan he hoa don",
    response: "Can chu phe duyet de tiep tuc trien khai...",
    ask_approval: { question: "Chu co dong y tiep tuc khong?" },
    duration_s: 4.1
  }
];

// 4. Kiem tra auditEntryCardHtml
const cardHtml1 = TM.auditEntryCardHtml(sampleEntries[0], members);
check("Card 1 co ten cong su", cardHtml1.includes("Chuyen gia tra cuu"));
check("Card 1 co tieu de viec", cardHtml1.includes("Tra cuu tai lieu ERP"));
check("Card 1 co nhan trang thai done", cardHtml1.includes("tm-status-completed"));
check("Card 1 co tool read_file", cardHtml1.includes("read_file"));
check("Card 1 co prompt yeu cau LLM", cardHtml1.includes("Hay doc noi dung file docs/erp.md"));
check("Card 1 co ket qua LLM", cardHtml1.includes("File docs/erp.md chua cac thong tin"));

const cardHtml2 = TM.auditEntryCardHtml(sampleEntries[1], members);
check("Card 2 co nhan trang thai error", cardHtml2.includes("tm-status-error"));
check("Card 2 hien thi loi", cardHtml2.includes("timeout qua 120s"));
check("Card 2 co tool bash", cardHtml2.includes("bash"));

const cardHtml3 = TM.auditEntryCardHtml(sampleEntries[2], members);
check("Card 3 co nhan trang thai approval", cardHtml3.includes("tm-status-approval"));
check("Card 3 co thao tac plan", cardHtml3.includes("Lap ke hoach") || cardHtml3.includes("plan"));

// 5. Kiem tra filterAuditEntries
// a) Loc theo agent
const fAgent = TM.filterAuditEntries(sampleEntries, { agent: "ag_leader" });
check("Loc theo agent leader tra ve 1 ban ghi", fAgent.length === 1 && fAgent[0].id === "aud_3");

// b) Loc theo action
const fAction = TM.filterAuditEntries(sampleEntries, { action: "work" });
check("Loc theo action work tra ve 2 ban ghi", fAction.length === 2);

// c) Loc theo tool
const fHasTool = TM.filterAuditEntries(sampleEntries, { tool: "_has_tools" });
check("Loc theo _has_tools tra ve 2 ban ghi", fHasTool.length === 2);

const fNoTool = TM.filterAuditEntries(sampleEntries, { tool: "_no_tools" });
check("Loc theo _no_tools tra ve 1 ban ghi", fNoTool.length === 1 && fNoTool[0].id === "aud_3");

const fBash = TM.filterAuditEntries(sampleEntries, { tool: "bash" });
check("Loc theo tool bash tra ve 1 ban ghi", fBash.length === 1 && fBash[0].id === "aud_2");

// d) Loc theo status
const fError = TM.filterAuditEntries(sampleEntries, { status: "error" });
check("Loc theo status error tra ve 1 ban ghi", fError.length === 1 && fError[0].id === "aud_2");

const fDone = TM.filterAuditEntries(sampleEntries, { status: "completed" });
check("Loc theo status completed tra ve 1 ban ghi", fDone.length === 1 && fDone[0].id === "aud_1");

// e) Loc theo thoi gian
const f1h = TM.filterAuditEntries(sampleEntries, { time: "1h" });
check("Loc trong 1h qua tra ve 1 ban ghi", f1h.length === 1 && f1h[0].id === "aud_1");

const f24h = TM.filterAuditEntries(sampleEntries, { time: "24h" });
check("Loc trong 24h qua tra ve 2 ban ghi", f24h.length === 2);

// f) Loc theo tu khoa
const fKwPrompt = TM.filterAuditEntries(sampleEntries, { kw: "pytest" });
check("Loc theo tu khoa pytest tra ve ban ghi aud_2", fKwPrompt.length === 1 && fKwPrompt[0].id === "aud_2");

const fKwAgent = TM.filterAuditEntries(sampleEntries, { kw: "Chuyen gia", members: members });
check("Loc theo tu khoa ten agent tra ve 2 ban ghi", fKwAgent.length === 2);

const fKwTask = TM.filterAuditEntries(sampleEntries, { kw: "ERP" });
check("Loc theo tu khoa tieu de viec ERP tra ve 1 ban ghi", fKwTask.length === 1 && fKwTask[0].id === "aud_1");

// 6. Kiem tra buildAuditListHtml
const emptyHtml = TM.buildAuditListHtml([], []);
check("buildAuditListHtml rong hien empty state", emptyHtml.includes("tm-audit-empty"));

const fullHtml = TM.buildAuditListHtml(sampleEntries, sampleEntries, members);
check("buildAuditListHtml co du 3 card", (fullHtml.match(/<article class="tm-audit-card/g) || []).length === 3);

if (fails > 0) {
  console.error("\nCo " + fails + " test that bai!");
  process.exit(1);
} else {
  console.log("\nTat ca test audit log UI da pass!");
}
