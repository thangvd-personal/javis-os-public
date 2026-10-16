// T2 (0.154.0): sáu reaction dùng chung cho chat nhóm và chat riêng (hợp đồng docs/dev/team-chat-t2-contract.md).
//
//     python tests/run.py --js reactions_ui -v
//
// Hàm thuần của reactions.js chạy dưới node (bảng sáu emoji, chọn/đổi/bỏ, bộ chọn hai nhóm chip, trạng thái, chip agent,
// nonce theo thao tác, nhấn giữ), chat-reactions.js với DOM giả tối thiểu (turn_saved gắn data-mid, chat_reaction vẽ lại,
// thử lại sau lỗi mạng dùng đúng nonce cũ), cộng các chỗ nối trong app.js, index.html, style.css và i18n.
"use strict";
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "..", "..");
const D = path.join(ROOT, "dashboard");
const JR = require(path.join(D, "reactions.js"));
const vi = JSON.parse(fs.readFileSync(path.join(D, "i18n", "vi.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(D, "i18n", "en.json"), "utf8"));
const reactJs = fs.readFileSync(path.join(D, "reactions.js"), "utf8");
const chatJs = fs.readFileSync(path.join(D, "chat-reactions.js"), "utf8");
const appJs = fs.readFileSync(path.join(D, "app.js"), "latin1");
const teamsJs = fs.readFileSync(path.join(D, "teams.js"), "utf8");
const mobileJs = fs.readFileSync(path.join(D, "mobile-chat.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(D, "index.html"), "utf8");
const css = fs.readFileSync(path.join(D, "style.css"), "utf8");
const EM = String.fromCharCode(0x2014), EN = String.fromCharCode(0x2013);   // em dash, en dash (không viết thẳng ký tự)

let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

// ───────────── bảng và chọn ─────────────
check("đúng sáu giá trị theo hợp đồng, giữ mã cũ", JR.EMOJI.map((r) => r.value).join(",") === "like,love,haha,wow,sad,angry");
check("sáu emoji gốc", JR.EMOJI.map((r) => r.emoji).join("") === "\u{1F44D}❤️\u{1F602}\u{1F62E}\u{1F622}\u{1F621}");
check("sáu lý do chất lượng", JR.REASONS.map((r) => r.value).join(",") === "on_point,wrong_facts,missing,too_long,unclear,off_topic");
check("trục nói/im hai giá trị", JR.AXES.map((r) => r.value).join(",") === "should_speak,should_silent");
check("chọn mới, đổi, chọn lại là bỏ", JR.nextReaction(undefined, "like") === "like" && JR.nextReaction("like", "wow") === "wow"
      && JR.nextReaction("wow", "wow") === "none" && JR.nextReaction(null, "sad") === "sad");

const pl = JR.payload("bogus", ["missing", "x", "missing", "too_long"], "maybe", "  " + "a".repeat(600) + "  ", "sha1");
check("thân gửi: giá trị lạ thành none, lý do lạ bỏ và không trùng, trục lạ rỗng, góp ý cắt 500, kèm seen_sha",
      pl.value === "none" && pl.reasons.join(",") === "missing,too_long" && pl.axis === "" && pl.comment.length === 500
      && pl.seen_sha === "sha1", JSON.stringify(pl).slice(0, 120));
check("thả emoji không cần gì khác", JSON.stringify(JR.payload("like")) === JSON.stringify({ value: "like", reasons: [], axis: "", comment: "" }));
check("trục hợp lệ giữ nguyên", JR.payload("none", [], "should_silent", "").axis === "should_silent");

// ───────────── nonce theo thao tác ─────────────
(function () {
  let n = 0;
  const K = JR.nonceKeeper(function () { n++; return "n" + n; });
  const b1 = JR.payload("like");
  const a = K.take("m1", b1);
  K.settle("m1", 0);
  check("lỗi mạng: thử lại cùng thao tác gửi đúng nonce cũ", K.take("m1", JR.payload("like")) === a);
  K.settle("m1", 503);
  check("lỗi 5xx: vẫn giữ nonce để thử lại", K.take("m1", JR.payload("like")) === a);
  K.settle("m1", 200);
  const b = K.take("m1", JR.payload("like"));
  check("thành công: thao tác kế tiếp nonce mới", b !== a);
  K.settle("m1", 400);
  check("lỗi 4xx: thao tác kế tiếp nonce mới", K.take("m1", JR.payload("like")) !== b);
  const c = K.take("m2", JR.payload("sad"));
  check("thân khác là thao tác khác, nonce khác", K.take("m2", JR.payload("wow")) !== c);
  check("khoá theo tin, không lẫn tin khác", K.take("m3", JR.payload("sad")) !== K.take("m4", JR.payload("sad")));
  const x = JR.newNonce(), y = JR.newNonce();
  check("newNonce khác nhau mỗi lần, dài không quá 100", x !== y && x.length > 0 && x.length <= 100 && y.length <= 100);
})();

// ───────────── chip, bộ chọn, trạng thái, chip agent ─────────────
check("không có phản hồi: không chip", JR.chipHtml(null, { mid: 1 }) === "" &&
      JR.chipHtml({ value: null, reasons: [], axis: "", comment: "" }, { mid: 1 }) === "");
let h = JR.chipHtml({ value: "sad", reasons: ["too_long", "missing"], axis: "should_speak", comment: "x" }, { mid: 'a"b' });
check("chip: emoji, nhãn, số lý do, trục, có góp ý; mã tin được escape", h.includes("\u{1F622}") && h.includes(vi["teams.react.sad"])
      && h.includes(vi["teams.react_reasons_n"].replace("{n}", "2")) && h.includes(vi["react.axis.should_speak"])
      && h.includes(vi["teams.react_has_comment"]) && h.includes('data-react-open="a&quot;b"') && h.includes('class="jr-react-chip"'), h);
check("chip theo họ class của nơi gọi", JR.chipHtml({ value: "like" }, { mid: 1, prefix: "tm" }).includes('class="tm-react-chip"'));

h = JR.pickerHtml(9, { value: "love", reasons: ["unclear"], axis: "should_silent", comment: "Gọn <b>hơn</b>" });
check("bộ chọn: sáu nút emoji, emoji đang chọn được đánh dấu", (h.match(/class="jr-react-pick"/g) || []).length === 6
      && /data-react="love"[^>]*aria-pressed="true"/.test(h) && (h.match(/aria-pressed="true"/g) || []).length === 1, h);
check("bộ chọn: sáu lý do một nhóm chọn nhiều", (h.match(/type="checkbox" name="jr-reason"/g) || []).length === 6
      && /value="unclear" checked/.test(h));
check("bộ chọn: trục nói/im là nhóm RIÊNG chọn một (radio cùng tên, có Không chọn)",
      (h.match(/type="radio" name="jr-axis-9"/g) || []).length === 3 && /role="radiogroup"/.test(h)
      && /value="should_silent" checked/.test(h) && !/value="" checked/.test(h)
      && h.indexOf('name="jr-reason"') < h.indexOf('role="radiogroup"') && h.includes(vi["react.axis.none"])
      && h.includes(vi["react.axis.should_speak"]) && h.includes(vi["react.axis.should_silent"]) && h.includes(vi["react.axis"]), h);
check("bộ chọn: góp ý tuỳ chọn tối đa 500 ký tự, đã escape; không ô nào bắt buộc", h.includes('maxlength="500"')
      && h.includes("Gọn &lt;b&gt;hơn&lt;/b&gt;") && !h.includes("<b>hơn") && !/\brequired\b/.test(h));
check("bộ chọn: có Lưu, Bỏ, Đóng và ghi chú cảm xúc", h.includes("data-react-save=\"9\"") && h.includes("data-react-clear=\"9\"")
      && h.includes("data-react-close=\"9\"") && h.includes(vi["teams.react_note"]));
check("bộ chọn chưa có reaction: trục mặc định Không chọn", /value="" checked/.test(JR.pickerHtml(3, null)));
h = JR.pickerHtml(9, { value: "love", reasons: [], axis: "", comment: "" }, { draft: { reasons: ["missing"], axis: "should_speak", comment: "đang gõ" } });
check("bản nháp giữ lý do, trục, góp ý qua lần vẽ lại", /value="missing" checked/.test(h) && /value="should_speak" checked/.test(h)
      && h.includes("đang gõ") && /data-react="love"[^>]*aria-pressed="true"/.test(h), h);
check("bộ chọn chèn được nút riêng của nơi gọi (Ghim ở trang Nhóm)", JR.pickerHtml(1, null, { extraHtml: "<i>X</i>" }).includes("<i>X</i>"));

const STATUSES = ["received", "pending_review", "reviewing", "adjusted", "no_change", "waiting_quota", "stored_only"];
check("bảy trạng thái của hợp đồng đều có khoá viết trọn trong reactions.js",
      STATUSES.every((s) => reactJs.includes('"react.status.' + s + '"')) && Object.keys(JR.STATUS_KEY).length === 7);
check("nhãn trạng thái có ở vi (có dấu) và en", STATUSES.every((s) => vi["react.status." + s] && en["react.status." + s])
      && vi["react.status.received"] === "Đã nhận" && vi["react.status.stored_only"] === "Đã nhận, chỉ lưu",
      STATUSES.filter((s) => !vi["react.status." + s] || !en["react.status." + s]).join(","));
check("smoke T2: lý do trùng ý với nhãn không bị lặp, lý do khác nhãn vẫn hiện",
      (JR.statusHtml({ status: "stored_only", status_reason: vi["react.status.stored_only"] }).match(/chỉ lưu/g) || []).length === 1
      && JR.statusHtml({ status: "stored_only", status_reason: "Cộng hưởng của trợ lý đang tắt" }).includes("Cộng hưởng của trợ lý đang tắt"));
check("mỗi trạng thái vẽ đúng nhãn", STATUSES.every((s) => JR.statusHtml({ status: s }).includes(vi["react.status." + s])
      && JR.statusHtml({ status: s }).includes('data-status="' + s + '"')));
h = JR.statusHtml({ status: "waiting_quota", status_reason: "Hết <hạn> mức hôm nay" });
check("trạng thái kèm lý do ngắn của host, đã escape", h.includes("Hết &lt;hạn&gt; mức hôm nay") && h.includes('role="status"'), h);
check("không có reaction hay trạng thái lạ: không vẽ", JR.statusHtml(null) === "" && JR.statusHtml({ status: "bogus" }) === ""
      && JR.statusHtml({ value: "like" }) === "");

h = JR.agentChipsHtml([{ agent_key: "ag_1", name: "Lan <b>", source_kind: "leader", value: "wow", reasons: [], comment: "" },
  { agent_key: "ag_2", name: "Minh", source_kind: "member", value: null, reasons: ["missing"], comment: "" },
  { agent_key: "ag_3", name: "Rỗng", value: null, reasons: [], comment: "" }]);
check("chip agent: tên agent đã escape, chỉ đọc (span, không nút), bỏ dòng rỗng",
      h.includes("Lan &lt;b&gt;") && !h.includes("<b>") && (h.match(/class="jr-react-agent"/g) || []).length === 2
      && !/<button/.test(h) && !h.includes("Rỗng") && h.includes("\u{1F62E}"), h);
check("chip agent: không có thì không vẽ", JR.agentChipsHtml([]) === "" && JR.agentChipsHtml(null) === "");
check("chip agent: nhãn đọc màn hình có ở vi và en", vi["react.agent_chip"] && en["react.agent_chip"]
      && h.includes('<span class="jr-react-sr">Lan &lt;b&gt;: ' + vi["teams.react.wow"] + "</span>")
      && /<span aria-hidden="true">/.test(h) && !/<span[^>]*aria-label/.test(h));

// ───────────── nhấn giữ 500 ms ─────────────
(function () {
  const timers = [];
  const realSet = global.setTimeout, realClear = global.clearTimeout;
  global.setTimeout = function (fn, ms) { timers.push({ fn: fn, ms: ms, live: true }); return timers.length; };
  global.clearTimeout = function (id) { if (id && timers[id - 1]) timers[id - 1].live = false; };
  const ls = {};
  const root = { addEventListener: function (n, f) { ls[n] = f; }, removeEventListener: function (n) { delete ls[n]; } };
  const bubble = { id: "b" };
  const tgt = (isBtn) => ({ closest: function (sel) { if (sel === ".msg-javis[data-mid]") return bubble; return isBtn ? {} : null; } });
  let opened = null;
  const off = JR.bindLongPress(root, ".msg-javis[data-mid]", function (el) { opened = el; });
  ls.pointerdown({ pointerType: "mouse", target: tgt(false), clientX: 0, clientY: 0 });
  check("nhấn giữ: chuột bỏ qua", timers.length === 0);
  ls.pointerdown({ pointerType: "touch", target: tgt(true), clientX: 0, clientY: 0 });
  check("nhấn giữ: chạm lên nút thì không mở", timers.length === 0);
  ls.pointerdown({ pointerType: "touch", target: tgt(false), clientX: 5, clientY: 5 });
  check("nhấn giữ: hẹn đúng 500 ms", timers.length === 1 && timers[0].ms === 500);
  ls.pointermove({ clientX: 30, clientY: 5 });
  check("nhấn giữ: rung tay quá 10px là huỷ", timers[0].live === false);
  ls.pointerdown({ pointerType: "touch", target: tgt(false), clientX: 5, clientY: 5 });
  timers[1].fn();
  check("nhấn giữ: đủ 500 ms mở bộ chọn của đúng bong bóng", opened === bubble);
  let prevented = false;
  ls.contextmenu({ preventDefault: function () { prevented = true; } });
  check("nhấn giữ đã mở: chặn menu ngữ cảnh của lần giữ đó", prevented);
  prevented = false;
  ls.contextmenu({ preventDefault: function () { prevented = true; } });
  check("menu ngữ cảnh lần sau không bị chặn", !prevented);
  off();
  check("hàm gỡ bỏ hết trình nghe", !ls.pointerdown && !ls.contextmenu);
  global.setTimeout = realSet; global.clearTimeout = realClear;
})();

// ───────────── chat-reactions.js với DOM giả ─────────────
function fakeBubble() {
  return {
    dataset: {}, children: [],
    querySelector: function (sel) {
      if (sel === ".jr-chat") return this.children.find((c) => c.isJr) || null;
      return null;
    },
    appendChild: function (n) { n.parentNode = this; this.children.push(n); },
    insertBefore: function (n) { this.appendChild(n); },
    replaceChild: function (n, o) { const i = this.children.indexOf(o); this.children[i] = n; n.parentNode = this; },
    row: function () { const c = this.children.find((x) => x.isJr); return c ? c.html : ""; },
  };
}
global.document = {
  activeElement: null,
  createElement: function () {
    return { set innerHTML(v) { this.firstChild = { isJr: true, html: v, parentNode: null, contains: () => false, querySelector: () => null }; } };
  },
};
const CR = require(path.join(D, "chat-reactions.js"));
const bubblesInDom = [];
const listeners = {};
const fakeRoot = {
  addEventListener: function (n, f) { listeners[n] = f; },
  querySelectorAll: function (sel) {
    const m = /data-mid="([^"]*)"/.exec(sel);
    return bubblesInDom.filter((b) => b.dataset.mid === (m && m[1]));
  },
  querySelector: function () { return null; },
};
let changes = 0;
CR.init({ root: fakeRoot, brain: function () { return "vault/a b"; }, onChange: function () { changes++; } });

check("URL route reaction đúng hợp đồng, brain mã hoá", CR.reactionUrl("agent:lan", 12, "vault/a b") ===
      "/sessions/agent%3Alan/messages/12/reaction?brain=vault%2Fa%20b");
const noMid = fakeBubble();
check("chưa có mã tin: không gắn, không có nút", CR.attach(noMid, { sid: "s1", mid: null }) === false && !noMid.dataset.mid
      && noMid.children.length === 0);
const live = fakeBubble();
bubblesInDom.push(live);
const entry = { role: "javis", text: "x" };
CR.expect("s1", live, entry);
check("turn_saved của phiên khác không đụng bong bóng", CR.onFrame({ type: "turn_saved", session_id: "s2", message_id: 5 }) === false
      && !live.dataset.mid);
CR.expect("s1", live, entry);
const before = changes;
check("turn_saved: bong bóng vừa chốt nhận data-mid, data-sid, content_sha và hiện nút",
      CR.onFrame({ type: "turn_saved", session_id: "s1", message_id: 123, content_sha: "abc" }) === true
      && live.dataset.mid === "123" && live.dataset.sid === "s1" && live.dataset.sha === "abc"
      && live.row().includes('class="jr-react-btn"') && live.row().includes('data-react-open="123"'), live.row());
check("turn_saved ghi mã tin vào convo để F5 còn nút", entry.mid === 123 && entry.sha === "abc" && changes > before);
const streamed = fakeBubble();
bubblesInDom.push(streamed);
check("bong bóng stream chưa đăng ký: lấy bong bóng của lượt từ gợi ý",
      CR.onFrame({ type: "turn_saved", session_id: "s3", message_id: 7 }, { bubble: streamed }) === true && streamed.dataset.mid === "7");
const hist = fakeBubble();
bubblesInDom.push(hist);
const hEntry = { role: "javis", mid: 123 };
CR.attach(hist, { sid: "s1", mid: 123, reaction: null, entry: hEntry });
const other = fakeBubble();
bubblesInDom.push(other);
CR.attach(other, { sid: "s9", mid: 123, reaction: null });
CR.onFrame({ type: "chat_reaction", session_id: "s1", message_id: 123,
  reaction: { value: "haha", reasons: [], axis: "", comment: "", status: "received", status_reason: "" } });
check("chat_reaction vẽ lại MỌI bong bóng của tin đó trong phiên (tab khác đổi cũng thấy)",
      live.row().includes("\u{1F602}") && hist.row().includes("\u{1F602}") && hist.row().includes(vi["react.status.received"]));
check("chat_reaction không đụng tin cùng mã ở phiên khác", !other.row().includes("\u{1F602}"));
check("chat_reaction cập nhật convo để F5 đúng", hEntry.reaction && hEntry.reaction.value === "haha");
CR.onFrame({ type: "chat_reaction", session_id: "s1", message_id: 123, reaction: null });
check("bỏ hết reaction: chip biến mất, nút còn", !hist.row().includes("jr-react-chip") && hist.row().includes("jr-react-btn"));

// Gửi: lỗi mạng hiện Thử lại; Thử lại dùng đúng nonce cũ; thao tác sau thành công dùng nonce mới.
(async function () {
  const calls = [];
  let mode = "network";
  global.fetch = function (url, opt) {
    calls.push({ url: url, opt: opt, body: JSON.parse(opt.body) });
    if (mode === "network") return Promise.reject(new Error("offline"));
    return Promise.resolve({ status: 200, json: function () {
      const b = JSON.parse(opt.body);
      return Promise.resolve({ ok: true, reaction: { value: b.value === "none" ? null : b.value, reasons: b.reasons, axis: b.axis,
        comment: b.comment, status: "received", status_reason: "" } });
    } });
  };
  const target = function (data, el) {
    const btn = { dataset: data, closest: function (sel) {
      if (sel.indexOf("[data-react") === 0) return btn;
      if (sel === ".jr-chat") return { closest: function () { return el; } };
      return null;
    } };
    return { target: btn };
  };
  live.dataset.sha = "abc";
  await listeners.click(target({ react: "love" }, live));
  const first = calls[0];
  check("bấm emoji: POST đúng route, thân có value/reasons/axis/comment/nonce/seen_sha",
        first && first.opt.method === "POST" && first.url === "/sessions/s1/messages/123/reaction?brain=vault%2Fa%20b"
        && first.body.value === "love" && Array.isArray(first.body.reasons) && first.body.axis === "" && first.body.comment === ""
        && !!first.body.nonce && first.body.seen_sha === "abc" && first.opt.headers["Content-Type"] === "application/json",
        JSON.stringify(first && first.body));
  check("lỗi mạng: dòng lỗi kèm nút Thử lại", live.row().includes("jr-react-err") && live.row().includes("data-react-retry=\"123\"")
        && live.row().includes(vi["react.err_network"]));
  mode = "ok";
  await listeners.click(target({ reactRetry: "123" }, live));
  check("Thử lại gửi đúng nonce cũ (server trả trạng thái hiện tại, không ghi trùng)", calls[1].body.nonce === first.body.nonce
        && calls[1].body.value === "love");
  check("thành công: chip mới, hết dòng lỗi", live.row().includes("\u{2764}") && !live.row().includes("jr-react-err"));
  await listeners.click(target({ react: "love" }, live));
  check("chọn lại emoji đang chọn là bỏ, nonce mới", calls[2].body.value === "none" && calls[2].body.nonce !== first.body.nonce);
  await listeners.click(target({ reactClear: "123" }, live));
  check("Bỏ phản hồi gửi none, lý do rỗng, trục rỗng, góp ý rỗng", calls[3].body.value === "none" && calls[3].body.reasons.length === 0
        && calls[3].body.axis === "" && calls[3].body.comment === "");

  // ───────────── review T2 UI: thứ tự gói, refresh đủ tin, Esc, lỗi cũ ─────────────
  const realNow = Date.now;
  let clock = realNow();
  Date.now = function () { return clock; };
  const vb = fakeBubble(); bubblesInDom.push(vb);
  const vEntry = { role: "javis", text: "giọng" };
  check("P2-1 turn_saved tới TRƯỚC response (làn giọng): chưa có bong bóng thì giữ lại",
        CR.onFrame({ type: "turn_saved", session_id: "v1", message_id: 300, content_sha: "s300" }) === false);
  CR.expect("v1", vb, vEntry);
  check("P2-1 response tới sau: expect ghép ngay mã đã giữ, convo có mã (F5 còn nút)", vb.dataset.mid === "300"
        && vb.dataset.sha === "s300" && vEntry.mid === 300 && vb.row().includes("jr-react-btn"), vb.row());
  const vb2 = fakeBubble(); bubblesInDom.push(vb2);
  CR.expect("v1", vb2, null);
  check("P2-1 mã đã dùng không bị ghép lần hai cho bong bóng sau", !vb2.dataset.mid);
  CR.turnDone("v1"); clock += 4000;
  check("P2-1 bong bóng chờ quá hạn sau turn_done không nhận mã của lượt sau",
        CR.onFrame({ type: "turn_saved", session_id: "v1", message_id: 301 }) === false && !vb2.dataset.mid);
  const vb3 = fakeBubble(); bubblesInDom.push(vb3);
  CR.expect("v1", vb3, null);
  check("P2-1 lượt sau: mã giữ lại ghép đúng bong bóng của lượt đó", vb3.dataset.mid === "301");
  CR.onFrame({ type: "turn_saved", session_id: "v2", message_id: 310 });
  CR.turnDone("v2"); clock += 4000;
  const vb4 = fakeBubble(); bubblesInDom.push(vb4);
  CR.expect("v2", vb4, null);
  check("P2-1 mã giữ lại của lượt đã khép (phiên nền) không gán cho bong bóng mới", !vb4.dataset.mid);
  const dEntry = { role: "javis", text: "bản sửa lại" };
  CR.expect("v1", vb3, dEntry);
  check("P2-1 hai khung response (JAVIS_LESSON): bong bóng đã có mã thì chỉ chép mã vào convo, không chờ thêm",
        dEntry.mid === 301 && CR.onFrame({ type: "turn_saved", session_id: "v1", message_id: 302 }) === false && vb3.dataset.mid === "301");
  const pc = fakeBubble(), pd = fakeBubble(); bubblesInDom.push(pc, pd);
  CR.expect("v3", pc, null);
  CR.attach(pc, { sid: "v3", mid: 320 });
  check("P2-1 bong bóng chờ đã có mã (gắn đường khác): bỏ qua, dùng bong bóng của lượt",
        CR.onFrame({ type: "turn_saved", session_id: "v3", message_id: 321 }, { bubble: pd }) === true && pc.dataset.mid === "320"
        && pd.dataset.mid === "321");
  Date.now = realNow;

  // P2-2: refresh hỏi đủ số tin vừa dựng, gắn mã còn thiếu bằng cách khớp từ cuối lên, sửa reaction theo server.
  const r1 = fakeBubble(), r2 = fakeBubble(), r3 = fakeBubble(), r4 = fakeBubble();
  bubblesInDom.push(r1, r2, r3, r4);
  const e2 = { role: "javis", mid: 81, text: "Hai", reaction: { value: "sad", status: "received" } };
  CR.attach(r2, { sid: "rf", mid: 81, reaction: e2.reaction, entry: e2 });
  const items = [{ el: r4, entry: { role: "javis", text: "Không khớp" } }, { el: r1, entry: { role: "javis", text: "Một\n\n<!-- JAVIS_VIEC: {} -->" } },
    { el: r2, entry: e2 }, { el: r3, entry: { role: "javis", text: "  Ba " } }];
  const srv = [{ id: 79, role: "user", content: "hỏi" }, { id: 80, role: "assistant", content: "Một", content_sha: "h80", reaction: null },
    { id: 81, role: "assistant", content: "Hai", reaction: { value: "sad", status: "adjusted", status_reason: "" } },
    { id: 82, role: "assistant", content: "Ba", reaction: { value: "like", status: "received" } }];
  let gotUrl = "";
  global.fetch = function (u) { gotUrl = u; return Promise.resolve({ json: function () { return Promise.resolve({ messages: srv }); } }); };
  await CR.refresh("rf", items);
  check("P2-2 hỏi đủ số tin vừa dựng (không chỉ 30)", gotUrl === "/sessions/rf/messages?limit=18", gotUrl);
  check("P2-2 gắn mã cho bong bóng khôi phục còn thiếu, khớp từ cuối lên theo nội dung", r1.dataset.mid === "80" && r3.dataset.mid === "82"
        && items[1].entry.mid === 80 && items[3].entry.mid === 82 && r3.row().includes("\u{1F44D}"));
  check("P2-2 không khớp nội dung thì không gán bừa", !r4.dataset.mid);
  check("P2-2 tin đã có mã nhận trạng thái mới của server", r2.row().includes(vi["react.status.adjusted"]) && e2.reaction.status === "adjusted");
  const many = Array.from({ length: 150 }, () => ({ el: fakeBubble(), entry: { role: "javis", text: "x" } }));
  await CR.refresh("rf", many);
  check("P2-2 trần 200 tin mỗi lần hỏi", /limit=200$/.test(gotUrl), gotUrl);
  let release;
  global.fetch = function () { return new Promise(function (ok) { release = ok; }); };
  const pend = CR.refresh("rf", items);
  CR.onFrame({ type: "chat_reaction", session_id: "rf", message_id: 82, reaction: { value: "angry", status: "received" } });
  release({ json: function () { return Promise.resolve({ messages: srv }); } });
  await pend;
  check("P3 kết quả refresh cũ hơn lần ghi mới (WS/POST) bị bỏ, không đè", r3.row().includes("\u{1F621}") && !r3.row().includes("\u{1F44D}"));
  check("P2-2 socket nối lại thì hỏi lại reaction của phiên đang mở",
        /if \(_daChao\) \{ try \{ if \(window\.JavisChatReactions && savedSessionId\) window\.JavisChatReactions\.refresh\(savedSessionId\); \} catch \(e\) \{\} \}/.test(appJs));
  check("P2-2 F5 đưa danh sách câu trả lời vừa dựng cho refresh", appJs.includes("_traLoi.push({ el, entry: t });")
        && appJs.includes("window.JavisChatReactions.refresh(savedSessionId, _traLoi)"));
  check("P2-1 turn_done khép mục chờ của lượt", /window\.JavisChatReactions\.turnDone\(sid\)/.test(appJs));

  // P3: Esc không lọt ra phím Esc chung; lỗi cũ xoá khi host xác nhận; i18n; focus; nhấn giữ; chữ ẩn cho trình đọc màn hình.
  live._jr.open = true;
  let stopped = false, prevented2 = false;
  listeners.keydown({ key: "Escape", target: target({}, live).target, preventDefault: function () { prevented2 = true; },
    stopPropagation: function () { stopped = true; } });
  check("P3 Esc đóng bộ chọn và dừng lan (không tắt loa, rảnh tay)", stopped && prevented2 && live._jr.open === false);
  live._jr.error = { text: "cũ", retry: true };
  CR.onFrame({ type: "chat_reaction", session_id: "s1", message_id: 123, reaction: { value: "wow", status: "received" } });
  check("P3 gói chat_reaction xác nhận thì xoá dòng lỗi cũ", !live.row().includes("jr-react-err") && live._jr.error === null);
  check("P3 đổi ngôn ngữ vẽ lại hàng reaction", chatJs.includes('window.addEventListener("javis:i18n", rerenderAll)'));
  check("P3 lưu xong trả focus về nút mặt cười", /focusBtn\(el\);\s+\/\/ bảng vừa đóng/.test(chatJs));
  check("P3 bỏ đường rbrain không ai đặt", !chatJs.includes("rbrain"));
  check("P3 máy chạm: tắt menu chạm giữ của iOS trên bong bóng có mã", /@media \(hover: none\) \{[\s\S]{0,300}\.msg-javis\[data-mid\] \{ -webkit-touch-callout: none; \}/.test(css));
  check("P3 nhấn giữ hạ cờ ở lần chạm sau (iOS không bắn contextmenu)", /var down = function \(e\) \{[\s\S]{0,200}held = false;\s*if \(e\.pointerType === "mouse"\) return;/.test(reactJs));
  check("P3 chữ ẩn cho trình đọc màn hình có CSS ở cả hai trang", css.includes(".jr-react-sr {")
        && fs.readFileSync(path.join(D, "console.css"), "utf8").includes(".tm-react-sr {"));

  // ───────────── chỗ nối ─────────────
  check("app.js: lịch sử gắn mã tin (m.id), content_sha và reaction vào bong bóng trả lời",
        /function veTinDaLuu\(m, brainCua, sidCua\)/.test(appJs)
        && /JavisChatReactions\.attach\(el, \{ sid: sidCua, mid: m\.id, sha: m\.content_sha \|\| ""/.test(appJs)
        && /mid: m\.id, sha: m\.content_sha \|\| "", reaction: m\.reaction \|\| null \};/.test(appJs));
  check("app.js: hai đường tải lịch sử truyền mã phiên", appJs.includes("veTinDaLuu(m, st.brain, st.sid)")
        && appJs.includes("veTinDaLuu(m, sess.brain, id)"));
  check("app.js: bong bóng vừa chốt chờ turn_saved, chỉ bong bóng trả lời",
        /JavisChatReactions\.expect\(sid, msgEl, finalText\.trim\(\) \? convo\[convo\.length - 1\] : null\)/.test(appJs));
  check("app.js: gói turn_saved và chat_reaction đi vào chat-reactions, gợi ý là bong bóng của lượt",
        /data\.type === "turn_saved" \|\| data\.type === "chat_reaction"\) \{[\s\S]{0,700}JavisChatReactions\.onFrame\(data, \{ bubble: t && t\.bubble \}\)/.test(appJs));
  check("app.js: F5 dựng lại hàng reaction từ convo và hỏi lại server",
        /t\.mid != null && window\.JavisChatReactions\) window\.JavisChatReactions\.attach\(el, \{ sid: savedSessionId, mid: t\.mid/.test(appJs)
        && /JavisChatReactions\.refresh\(savedSessionId\)/.test(appJs));
  check("app.js: init trên #chatArea, đổi reaction thì lưu convo", /JavisChatReactions\.init\(\{ root: chatArea, brain: currentBrainPath, onChange: persistSession \}\)/.test(appJs)
        && appJs.indexOf("JavisChatReactions.init(") < appJs.lastIndexOf("restoreSession();"));
  check("app.js: chạm hàng reaction trên điện thoại không bật/tắt hàng nút của tin", appJs.includes('if (e.target.closest(".jr-chat")) return;'));
  check("app.js: bong bóng lỗi và tin push không gắn reaction (không có mã tin)",
        !/function appendJavisError[\s\S]{0,400}JavisChatReactions/.test(appJs) && !/data\.type === "push"\) \{[\s\S]{0,600}JavisChatReactions/.test(appJs));
  check("app.js, teams.js, mobile-chat.js, chat-reactions.js không tự chứa emoji của bảng",
        [appJs, teamsJs, mobileJs, chatJs].every((s) => !/\u{1F44D}|\u{1F602}|\u{1F62E}|\u{1F622}|\u{1F621}/u.test(s)) &&
        [teamsJs, mobileJs, chatJs].every((s) => s.indexOf("❤") < 0));
  check("chat-reactions.js và teams.js dùng module chung", chatJs.includes("window.JavisReactions") && teamsJs.includes("window.JavisReactions"));
  check("chat-reactions.js nhấn giữ qua bindLongPress dùng chung", /JR\.bindLongPress\(root, "\.msg-javis\[data-mid\]"/.test(chatJs));
  const pos = (f) => indexHtml.indexOf("/static/" + f);
  check("index.html nạp reactions.js trước chat-reactions.js, app.js, teams.js, mobile-chat.js (có ?v=)",
        pos("reactions.js?v=") > 0 && pos("reactions.js") < pos("chat-reactions.js") && pos("chat-reactions.js") < pos("app.js")
        && pos("reactions.js") < pos("teams.js") && pos("reactions.js") < pos("mobile-chat.js"));
  check("CSS: nút luôn hiện ở máy chạm, vùng chạm 44px", /@media \(hover: none\) \{\s*\.jr-react-btn \{ opacity: 1; min-width: 44px; min-height: 44px; \}/.test(css)
        && /\.jr-react-chip, \.jr-react-reason, \.jr-actions \.ws-btn, \.jr-react-err \.ws-btn \{ min-height: 44px; \}/.test(css));
  check("CSS: máy tính hiện nút khi rê chuột hay đang mở", /\.msg-javis:hover \.jr-react-btn, \.jr-react-btn:focus-visible, \.jr-react-btn\[aria-expanded="true"\] \{ opacity: 1; \}/.test(css));
  check("CSS: chữ 16px", ["jr-react-chip", "jr-react-sub", "jr-react-reasons", "jr-react-comment", "jr-react-note"].every((c) => {
    const i = css.indexOf("." + c + " {"); return i >= 0 && /font-size: 16px/.test(css.slice(i, css.indexOf("}", i)));
  }));
  check("CSS: lớp flex có luật [hidden] riêng", [".jr-chat", ".jr-row", ".jr-react-panel", ".jr-react-reasons", ".jr-actions", ".jr-react-err"]
        .every((s) => css.includes(s + "[hidden] { display: none; }")));
  check("CSS 375px: sáu nút lọt bề ngang", /@media \(max-width: 480px\) \{[\s\S]{0,200}\.jr-react-row \{ flex-wrap: nowrap; justify-content: space-between; gap: 4px; \}/.test(css));
  const used = Array.from(new Set((reactJs + chatJs).match(/"react\.[a-z0-9_.]+"/g).map((s) => s.slice(1, -1))));
  check("mọi khoá react.* dùng trong hai file có ở vi và en", used.length >= 12 && used.every((k) => vi[k] && en[k]),
        used.filter((k) => !vi[k] || !en[k]).join(","));
  const added = Object.keys(vi).filter((k) => k.indexOf("react.") === 0);
  check("khoá mới đứng ngay sau teams.react_saved", (function () {
    const ks = Object.keys(vi); const i = ks.indexOf("teams.react_saved");
    return added.every((k) => { const j = ks.indexOf(k); return j > i && j <= i + added.length; });
  })());
  check("không em dash, en dash trong file mới và chuỗi mới", [reactJs, chatJs].every((s) => s.indexOf(EM) < 0 && s.indexOf(EN) < 0)
        && added.every((k) => (vi[k] + en[k]).indexOf(EM) < 0 && (vi[k] + en[k]).indexOf(EN) < 0));
  process.exit(fails ? 1 : 0);

// Quyết định của chủ (0.168.0): bong bóng tin đẩy về có mã tin thì gắn ngay hàng sáu cảm xúc (tin báo goal không còn hàng A3).
{
  const appSrc = require("fs").readFileSync(require("path").join(__dirname, "..", "..", "dashboard", "app.js"), "latin1");
  const pushAt = appSrc.indexOf('if (data.type === "push") {');
  const seg = pushAt >= 0 ? appSrc.slice(pushAt, pushAt + 4000) : "";
  check("app.js: tin đẩy về có message_id thì gắn hàng cảm xúc qua JavisChatReactions.attach",
    seg.includes("data.message_id != null") && seg.includes("window.JavisChatReactions.attach(el, { sid: data.session_id, mid: data.message_id"));
}

})().catch(function (e) { console.log("FAIL ném lỗi: " + (e && e.stack)); process.exit(1); });
