// T3 phần L (0.156.0): mục "Bài học và hạn mức" trong bảng Nhịp (agent-lessons-ui.js).
//
//     python tests/run.py --js agent_lessons_ui -v
//
// Hàm thuần dưới node: bài học theo phạm vi kèm nguồn và nút Thu hồi / Dùng ở mọi nơi, nhãn "bài học cũ chưa có phạm vi",
// thay đổi của tự rà soát với trước/sau và nút Quay lại (chỉ khi còn quay lại được), đề xuất, hạn mức hôm nay và ô chọn,
// escape, URL hành động đúng route. Cộng chỗ nối: sổ JavisPulseParts, script trong index.html, CSS 16px/44px, i18n hai thứ
// tiếng, dòng bảng giới hạn của teams.js.
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..", "..");
const LUI = require(path.join(ROOT, "dashboard", "agent-lessons-ui.js"));
const P = require(path.join(ROOT, "dashboard", "agent-pulse.js"));
const vi = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "vi.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "en.json"), "utf8"));
const read = (f) => fs.readFileSync(path.join(ROOT, "dashboard", f), "utf8");

let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + String(extra).slice(0, 500)));
  if (!cond) fails++;
}

const lessons = {
  name: "lessons", agent_key: "ag_1", slug: "viet",
  scopes: [
    { scope: "dm:ag_1", lessons: [{ id: 3, scope: "dm:ag_1", kind: "presentation", text: "Giữ dưới <năm> dòng",
      source: "review", source_refs: ["rx:dm:9", "wr:4", "call:2"] }] },
    { scope: "team:tm_a", lessons: [{ id: 5, scope: "team:tm_a", kind: "method", text: "Nêu rõ ai làm gì", source: "self",
      source_refs: ["turn:t1"] }] },
    { scope: "*", lessons: [{ id: 7, scope: "*", kind: "speak", text: "Hỏi lại khi mơ hồ", source: "owner",
      source_refs: [] }] }
  ],
  legacy: ["Bài học cũ trộn nhóm"],
  changes: [
    { id: 1, kind: "offset_set", context_ref: "team:tm_a", before: { offset: 0 }, after: { offset: 0.12 },
      evidence: ["decision:1", "decision:2"], why: "chen ngang", status: "applied", policy_change_id: 11, can_revert: true },
    { id: 2, kind: "lesson_add", context_ref: "dm:ag_1", before: {}, after: { text: "Ngắn gọn", lesson_id: 9 },
      evidence: ["rx:dm:1"], status: "reverted", revert_reason: "estimate_down", policy_change_id: 12, can_revert: false },
    { id: 3, kind: "eagerness_set", context_ref: "*", before: { eagerness: "medium" }, after: { eagerness: "low" },
      evidence: ["decision:3"], status: "rejected", reject_reason: "owner_held", can_revert: false }
  ],
  proposals: [{ id: 1, area: "permissions", text: "Cho quyền mở việc" }],
  budget: { cap: 6, used: 2, reserved: 2, left: 4 },
  reviews: []
};
const view = { ok: true, agent_key: "ag_1", parts: { lessons: lessons } };
const h = LUI.partHtml(view, {});
check("tiêu đề và hạn mức hôm nay", h.includes(vi["lessons.title"]) && h.includes(
  vi["lessons.budget_line"].replace("{used}", "2").replace("{cap}", "6").replace("{reserved}", "2")), h);
check("bài học theo phạm vi: chat riêng, nhóm, mọi nơi", h.includes(vi["lessons.scope.dm"]) &&
  h.includes(vi["lessons.scope.all"]) && h.includes("tm_a"));
check("escape nội dung bài học", h.includes("Giữ dưới &lt;năm&gt; dòng") && !h.includes("<năm>"));
check("nguồn và mã nguồn của bài học", h.includes(vi["lessons.source.review"]) && h.includes("rx:dm:9") &&
  !h.includes("call:2"));
check("mỗi bài học có nút Thu hồi", (h.match(/data-lessons-act="revoke"/g) || []).length === 3);
check("nút Dùng ở mọi nơi chỉ cho bài học chưa ở mọi nơi", (h.match(/data-lessons-act="promote"/g) || []).length === 2 &&
  !h.includes('data-lessons-act="promote" data-id="7"'));
check("bài học cũ ghi 'bài học cũ chưa có phạm vi'", h.includes(vi["lessons.legacy"]) && h.includes("Bài học cũ trộn nhóm"));
check("thay đổi tự rà soát: trước/sau, bằng chứng, trạng thái", h.includes("+0.00") && h.includes("+0.12") &&
  h.includes(vi["lessons.evidence"].replace("{n}", "2")) && h.includes(vi["lessons.status.applied"]));
check("lý do quay lại và lý do không áp hiện ra", h.includes(vi["lessons.revert.estimate_down"]) && h.includes(vi["lessons.reject.owner_held"]));
check("nút Quay lại chỉ khi còn quay lại được, trỏ đúng thay đổi chính sách",
  (h.match(/data-lessons-act="revert"/g) || []).length === 1 && h.includes('data-change="11"'));
check("đề xuất cần chủ quyết", h.includes(vi["lessons.proposals_title"]) && h.includes(vi["lessons.area.permissions"]));
check("ô chọn hạn mức có Không giới hạn, chọn đúng giá trị hiện tại",
  h.includes('value="unlimited"') && h.includes('<option value="6" selected>'));
const hu = LUI.partHtml({ parts: { lessons: Object.assign({}, lessons, { budget: { cap: null, used: 4 } }) } }, {});
check("Không giới hạn: dòng hạn mức và ô chọn", hu.includes(vi["lessons.budget_unlimited"].replace("{used}", "4")) &&
  hu.includes('value="unlimited" selected'));
const h7 = LUI.partHtml({ parts: { lessons: Object.assign({}, lessons, { budget: { cap: 7, used: 0 } }) } }, {});
check("giá trị hạn mức lạ (7) vẫn có trong ô chọn", h7.includes('<option value="7" selected>'));
// Review T3 L P3: ô chọn nhớ giá trị đang lưu để máy chủ từ chối thì về lại đúng giá trị đó.
const lsrc = fs.readFileSync(path.join(ROOT, "dashboard", "agent-lessons-ui.js"), "utf8");
check("ô chọn hạn mức mang giá trị đang lưu (data-prev) và đặt lại khi lưu lỗi",
  h.includes('class="lessons-budget-sel" data-prev="6"') && hu.includes('data-prev="unlimited"')
  && /el\.sel\.value = el\.prev/.test(lsrc));
check("thông báo chỉ sau khi máy chủ xác nhận (msg/err do người gọi truyền)",
  LUI.partHtml(view, { msg: vi["lessons.done"] }).includes('role="status"') &&
  LUI.partHtml(view, { err: "x" }).includes('role="alert"') && !h.includes('role="status"'));
check("không có mục lessons thì không vẽ gì", LUI.partHtml({ parts: {} }, {}) === "");
check("mục lỗi phía máy chủ thì báo lỗi tải", LUI.partHtml({ parts: { lessons: { error: "x" } } }, {})
  .includes(vi["lessons.error_load"]));
const empty = LUI.partHtml({ parts: { lessons: Object.assign({}, lessons, { scopes: [], legacy: [], changes: [],
  proposals: [] }) } }, {});
check("chưa có bài học: câu trống", empty.includes(vi["lessons.empty"]));

// URL hành động
const r1 = LUI.actionRequest("việt", "brain", "revoke", { id: "3" });
check("Thu hồi -> POST /agents/{slug}/lessons/{id}/revoke", r1.url === "/agents/vi%E1%BB%87t/lessons/3/revoke?brain=brain");
check("Dùng ở mọi nơi -> /lessons/{id}/promote", LUI.actionRequest("viet", "b", "promote", { id: "5" }).url ===
  "/agents/viet/lessons/5/promote?brain=b");
const r3 = LUI.actionRequest("viet", "b", "revert", { change: "11" });
check("Quay lại -> route của P /pulse/revert kèm change_id", r3.url === "/agents/viet/pulse/revert?brain=b" &&
  r3.body.change_id === 11);
check("Hạn mức -> /budget, Không giới hạn gửi 'unlimited'", LUI.actionRequest("viet", "b", "budget", { value: "unlimited" })
  .body.daily_calls === "unlimited" && LUI.actionRequest("viet", "b", "budget", { value: "4" }).body.daily_calls === 4);

// Sổ JavisPulseParts: agent-pulse.js gọi render(view) và bọc trong .pulse-part
const wrapped = P.partsHtml(view, [LUI.part]);
check("góp vào bảng Nhịp qua sổ đăng ký (render(view) -> HTML)", wrapped.includes('data-part="lessons"') &&
  wrapped.includes(vi["lessons.title"]));

// Chỗ nối
check("index.html nạp agent-lessons-ui.js sau agent-pulse.js", read("index.html").indexOf("agent-lessons-ui.js") >
  read("index.html").indexOf("agent-pulse.js") && read("index.html").includes("/static/agent-lessons-ui.js"));
const css = read("console.css");
check("CSS: chữ 16px, nút 44px, không tràn ngang", /\.lessons-part \{ font-size: 16px/.test(css) &&
  /\.lessons-part \.ws-btn \{ min-height: 44px; font-size: 16px/.test(css) && css.includes(".lessons-text { color: var(--text); overflow-wrap: anywhere"));
check("bảng giới hạn của teams.js có nhãn agent_daily_calls", read("teams.js").includes(
  'agent_daily_calls: "teams.limits.agent_daily_calls"'));
const src = read("agent-lessons-ui.js");
const keys = Array.from(new Set((src.match(/"lessons\.[a-z_.]+"/g) || []).map((s) => s.slice(1, -1))));
const miss = keys.filter((k) => !(k in vi) || !(k in en));
check("mọi khoá lessons.* có ở vi và en", keys.length > 40 && miss.length === 0, miss.join(", "));
check("bản tiếng Việt có dấu", /[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/.test(vi["lessons.legacy"]));
check("không em dash / en dash", !/[–—]/.test(src + JSON.stringify(Object.fromEntries(
  Object.entries(vi).filter(([k]) => k.startsWith("lessons."))))));

// Smoke T3 (ngăn Nhóm): hàng thành viên của một nhóm chỉ vẽ phần của nhóm đó, tên nhóm thay mã, không lặp ô phán xử.
{
  const JUI = require(path.join(ROOT, "dashboard", "agent-judge-ui.js"));
  const sv = { slug: "lan", resonance: true, contexts: [], parts: {
    lessons: { slug: "lan", agent_key: "ag_l", budget: { cap: 6, used: 0, reserved: 2 }, legacy: [], changes: [], scopes: [
      { scope: "dm:ag_l", lessons: [{ id: 2, scope: "dm:ag_l", kind: "method", text: "Bài học chat riêng", source: "self", source_refs: [] }] },
      { scope: "team:tm_1", lessons: [{ id: 1, scope: "team:tm_1", kind: "presentation", text: "Bài học nhóm", source: "owner", source_refs: [] }] }] },
    judge: { name: "judge", agent_key: "ag_l", contexts: [{ context_ref: "team:tm_1", label: "Nhóm A", decisions: [] }] } } };
  const reg = [{ name: "lessons", render: LUI.part.render }, { name: "judge", render: JUI.render }];
  const rowHtml = P.partsHtml(sv, reg, { slug: "lan", only: "team:tm_1", teamNames: { tm_1: "Nhóm A" } });
  check("smoke T3: hàng thành viên nhóm chỉ hiện bài học của nhóm đó, không hiện chat riêng",
    rowHtml.includes("Bài học nhóm") && !rowHtml.includes("Bài học chat riêng"), rowHtml);
  check("smoke T3: phạm vi nhóm hiện tên nhóm, không hiện mã thô", rowHtml.includes("Nhóm A") && !rowHtml.includes("tm_1<"), rowHtml);
  check("smoke T3: ô phán xử không vẽ lần hai trong khung nhịp của hàng nhóm", !rowHtml.includes('data-part="judge"'), rowHtml);
  const cardHtml = P.partsHtml(sv, reg, { slug: "lan" });
  check("smoke T3: thẻ trợ lý (không lọc) vẫn hiện mọi phạm vi và ô phán xử", cardHtml.includes("Bài học chat riêng")
    && cardHtml.includes('data-part="judge"'), cardHtml);
  const panel = P.panelHtml({ resonance: true, contexts: [], parts: {} }, { slug: "lan", registry: [] });
  check("smoke T3: Auto đang bật mà chưa có nhịp thì không khuyên bật Auto", panel.includes(vi["pulse.empty_waiting"])
    && !panel.includes(vi["pulse.empty"]) && !!en["pulse.empty_waiting"], panel);
}

console.log(fails ? `\n${fails} FAIL` : "\nALL OK");
process.exit(fails ? 1 : 0);
