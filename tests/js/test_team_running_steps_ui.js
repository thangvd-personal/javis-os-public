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

// 1. Kiểm tra i18n
const keys = [
  "teams.active_subtasks", "teams.steps_in_progress", "teams.steps_completed", "teams.all_steps", "teams.step_loading",
  "teams.step_status_running", "teams.step_status_waiting_owner",
  "teams.step_status_done", "teams.step_status_queued",
  "teams.step_status_held", "teams.step_status_failed",
  "teams.step_status_cancelled", "teams.step_status_continued",
  "teams.jump_to_msg", "teams.step_jump"
];
keys.forEach(function (k) {
  check("i18n vi: " + k, !!vi[k]);
  check("i18n en: " + k, !!en[k]);
  check("không có em dash trong: " + k, !/[\u2014]/.test(vi[k]) && !/[\u2014]/.test(en[k]));
});

// 2. Kiểm tra CSS
check("CSS có luật cho .tm-job-steps", css.includes(".tm-job-steps"));
check("CSS có luật [hidden] cho .tm-job-steps", css.includes(".tm-job-steps[hidden]"));
check("CSS có luật cho .tm-steps-toggle", css.includes(".tm-steps-toggle"));
check("CSS có luật cho .tm-step-card", css.includes(".tm-step-card"));
check("CSS có luật cho .is-jumpable", css.includes(".is-jumpable"));
check("CSS có luật cho .tm-step-jump-btn", css.includes(".tm-step-jump-btn"));

// 3. Kiểm tra hiển thị các bước con được tách làm 2 danh sách trong panel Việc
const members = [
  { agent_key: "ag_e2e", slug: "e2e", name: "Người chạy test E2E", role: "member", status: "active" },
  { agent_key: "ag_tdd", slug: "tdd", name: "Người dẫn TDD", role: "member", status: "active" },
  { agent_key: "ag_rev", slug: "rev", name: "Người review Python", role: "member", status: "active" }
];

const queue = {
  version: 1,
  running: [{ id: "t_main", title: "Viết bộ tài liệu kiến trúc ASCTax", source_ref: "msg:sess_1:140" }],
  waiting: [],
  blocked: []
};

const taskSteps = {
  t_main: [
    { seq: 1, kind: "work", agent_key: "ag_rev", status: "done", brief: "Xây dựng mô hình dữ liệu Django Core", result_msg_id: 151, updated_at: 1700000010 },
    { seq: 2, kind: "work", agent_key: "ag_e2e", status: "running", brief: "Thiết kế phương án triển khai và vận hành E2E test", input_refs: [148], updated_at: 1700000020 },
    { seq: 3, kind: "work", agent_key: "ag_tdd", status: "running", brief: "Sửa đề xuất luồng dữ liệu TDD", input_refs: ["step_1", 149], updated_at: 1700000030 }
  ]
};

const view = {
  members: members,
  taskSteps: taskSteps,
  stepsOpen: { "active:t_main": true, "done:t_main": true }
};

const html = TM.queueHtml(queue, view);

check("panel Việc có khung .tm-job-steps", html.includes("tm-job-steps"), html);
check("tách riêng danh sách Đang thực hiện", html.includes(vi["teams.steps_in_progress"]), html);
check("tách riêng danh sách Đã hoàn thành", html.includes(vi["teams.steps_completed"]), html);
check("danh sách Đang thực hiện giữ thuộc tính open", html.includes('data-steps-key="active:t_main" open'), html);
check("danh sách Đã hoàn thành giữ thuộc tính open khi được bật", html.includes('data-steps-key="done:t_main" open'), html);

// Kiểm tra thứ tự đảo ngược (reverse): step 3 (Người dẫn TDD) xuất hiện trước step 2 (Người chạy test E2E)
const idxStep3 = html.indexOf("Sửa đề xuất luồng dữ liệu TDD");
const idxStep2 = html.indexOf("Thiết kế phương án triển khai và vận hành E2E test");
check("task mới nhất (#3) hiển thị phía trên task trước (#2)", idxStep3 >= 0 && idxStep2 >= 0 && idxStep3 < idxStep2,
      "idxStep3: " + idxStep3 + ", idxStep2: " + idxStep2);

check("hiện badge Đang làm cho các bước running", html.includes(vi["teams.step_status_running"]), html);
check("bước đã hoàn thành được liệt kê trong danh sách Đã hoàn thành", html.includes("Xây dựng mô hình dữ liệu Django Core"), html);

// 4. Kiểm tra action jump cuộn tới tin nhắn
check("step 1 (done) có data-jump trỏ tới result_msg_id (151)", html.includes('data-jump="151"'), html);
check("step 2 (running) có data-jump trỏ tới input_refs (148)", html.includes('data-jump="148"'), html);
check("step 3 (running) có data-jump trỏ tới input_refs cuối cùng (149)", html.includes('data-jump="149"'), html);
check("thẻ bước có class is-jumpable", html.includes("is-jumpable"), html);
check("thẻ bước có nút tm-step-jump-btn", html.includes("tm-step-jump-btn"), html);
check("thẻ bước có role=button và tabindex=0", html.includes('role="button" tabindex="0"'), html);

// ───────────── 5. Kiểm tra thẻ công cụ đã và đang chạy (chỉ xem không sửa) ─────────────
const toolKeys = [
  "teams.audit_log_enable",
  "teams.audit_log_explain",
  "teams.tools_running_title",
  "teams.tools_running_n",
  "teams.tools_done_n",
  "teams.tools_empty",
  "teams.tools_cmd",
  "teams.tools_read",
  "teams.tools_write",
  "teams.tools_view_only"
];
for (const k of toolKeys) {
  check("i18n vi: " + k, !!vi[k], "vi thiếu khoá: " + k);
  check("i18n en: " + k, !!en[k], "en thiếu khoá: " + k);
  check("không có em dash trong: " + k, !vi[k].includes("\u2014") && !en[k].includes("\u2014"), k);
}

// CSS kiểm tra
const toolCssClasses = [
  ".tm-job-tools-card",
  ".tm-tools-summary",
  ".tm-view-only-badge",
  ".tm-tools-list",
  ".tm-tool-item",
  ".tm-tool-cmd",
  ".tm-tool-output",
  ".tm-audit-radio",
  ".tm-audit-explain"
];
for (const cls of toolCssClasses) {
  check("CSS có luật cho " + cls, css.includes(cls), "css thiếu " + cls);
}

// Kiểm tra formatToolLabel
check("formatToolLabel cho lệnh bash", TM.formatToolLabel({ tool: "javis_terminal", cmd: "pytest -v" }).includes("pytest -v"));
check("formatToolLabel cho đọc file", TM.formatToolLabel({ tool: "javis_read_file", input: { path: "docs/readme.md" } }).includes("readme.md"));
check("formatToolLabel cho ghi file", TM.formatToolLabel({ tool: "javis_write_file", input: { path: "src/app.py" } }).includes("app.py"));

// Kiểm tra card công cụ với dữ liệu mẫu
const sampleTools = [
  { tool: "javis_terminal", cmd: "python -m unittest", head: "OK (12 tests)", ts: "2026-11-17T18:00:00" },
  { tool: "javis_read_file", input: { path: "/app/README.md" }, output: "# Project Readme", ts: "2026-11-17T18:01:00" }
];
const toolsView = {
  taskTools: {
    t_main: sampleTools
  }
};
const toolsCardHtml = TM.runningToolsCardHtml(queue.running[0], toolsView);
check("card công cụ có class .tm-job-tools-card", toolsCardHtml.includes("tm-job-tools-card"), toolsCardHtml);
check("card công cụ có nhãn chỉ xem không sửa", toolsCardHtml.includes(vi["teams.tools_view_only"]), toolsCardHtml);
check("card công cụ hiển thị lệnh đã chạy", toolsCardHtml.includes("python -m unittest"), toolsCardHtml);
check("card công cụ hiển thị kết quả output", toolsCardHtml.includes("OK (12 tests)"), toolsCardHtml);

// Kiểm tra card rỗng
const emptyCardHtml = TM.runningToolsCardHtml(queue.running[0], { taskTools: {} });
check("card công cụ rỗng hiển thị thông báo rỗng", emptyCardHtml.includes(vi["teams.tools_empty"]), emptyCardHtml);
check("card công cụ rỗng vẫn có nhãn chỉ xem", emptyCardHtml.includes(vi["teams.tools_view_only"]), emptyCardHtml);

// ───────────── 6. Kiểm tra tùy chọn 'Ghi nhật ký việc làm' trong Team -> Nâng cao ─────────────
const sampleTeamAuditOff = {
  team_id: "tm_test",
  quality_policy: { enabled: true, limit_migration: "retain", audit_enabled: false }
};
const qpHtmlAuditOff = TM.qualityPolicyHtml(sampleTeamAuditOff);
check("form chất lượng có checkbox Ghi nhật ký việc làm", qpHtmlAuditOff.includes("tm-audit-enabled"), qpHtmlAuditOff);
check("form chất lượng có phần giải thích nhật ký", qpHtmlAuditOff.includes(vi["teams.audit_log_explain"]), qpHtmlAuditOff);
check("checkbox nhật ký không checked khi audit_enabled=false", !qpHtmlAuditOff.includes('class="tm-audit-enabled" checked'), qpHtmlAuditOff);

const sampleTeamAuditOn = {
  team_id: "tm_test",
  quality_policy: { enabled: true, limit_migration: "retain", audit_enabled: true }
};
const qpHtmlAuditOn = TM.qualityPolicyHtml(sampleTeamAuditOn);
check("checkbox nhật ký có checked khi audit_enabled=true", qpHtmlAuditOn.includes('class="tm-audit-enabled" checked'), qpHtmlAuditOn);

// Kiểm tra qualityPolicyPayload
const payloadAuditOn = TM.qualityPolicyPayload({ enabled: true, limit_migration: "retain", audit_enabled: true });
check("qualityPolicyPayload ghi nhận audit_enabled = true", payloadAuditOn.ok && payloadAuditOn.body.audit_enabled === true);

const payloadAuditOff = TM.qualityPolicyPayload({ enabled: true, limit_migration: "retain", audit_enabled: false });
check("qualityPolicyPayload ghi nhận audit_enabled = false", payloadAuditOff.ok && payloadAuditOff.body.audit_enabled === false);

if (fails > 0) {
  console.error("FAILED " + fails + " checks");
  process.exit(1);
} else {
  console.log("All checks passed successfully!");
}
