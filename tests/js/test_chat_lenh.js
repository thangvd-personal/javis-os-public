/* Lệnh "/" hệ thống của khung chat web (dashboard/chat-lenh.js + chat-slash.js). Chạy:
 *
 *     node tests/js/test_chat_lenh.js
 *
 * Không cần trình duyệt: chỉ kiểm HÀM THUẦN (nhận diện lệnh, quyết định vòng /goal, định dạng
 * kết quả) và các ràng buộc giữa các file phải nói cùng một thứ (khoá i18n, dấu nhận diện khối
 * chỉ dẫn giữa JS và Python).
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../..");
const S = require("../../dashboard/chat-slash.js");
const L = require("../../dashboard/chat-lenh.js");

let fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

// ---- 1. Nhận diện: lệnh hệ thống chỉ chạy ở ĐẦU tin ----
check("classify: status = system", S.classify("status") === "system");
check("classify: skill lạ vẫn là skill", S.classify("viet-email") === "skill");
check("route: /status ở đầu tin", (() => { const r = S.route("/status"); return r.type === "system" && r.cmd === "status" && r.arg === ""; })());
check("route: /model kèm tên", (() => { const r = S.route("/model gpt-5.5"); return r.type === "system" && r.cmd === "model" && r.arg === "gpt-5.5"; })());
check("route: /plan giữ nguyên nội dung nhiều dòng", (() => { const r = S.route("/plan dọn kho\ncuối tháng"); return r.type === "system" && r.arg === "dọn kho\ncuối tháng"; })());
check("route: chữ HOA vẫn nhận", S.route("/Compact").type === "system");
check("route: giữa câu KHÔNG chạy lệnh hệ thống", S.route("hãy /compact lại giúp").type === "passthrough");
check("route: URL không bị bắt nhầm", S.route("xem https://vd.com/status nhé").type === "passthrough");

// Skill trùng tên lệnh hệ thống: ở ĐẦU tin lệnh thắng, giữa câu skill vẫn gọi được như cũ.
S.setKnownSkills(["plan", "viet-email"]);
check("skill trùng tên: đầu tin = lệnh hệ thống", S.route("/plan x").type === "system");
check("skill trùng tên: giữa câu vẫn là skill", (() => { const r = S.route("lam giup /plan cho toi"); return r.type === "skill" && r.cmd === "plan"; })());
S.setKnownSkills([]);

// ---- 2. Menu ----
const menu = S.buildMenu([
  { slug: "notes", name: "Notes", description: "x" },
  { slug: "status", name: "Skill trùng tên", description: "x" },
]);
const heThong = menu.filter(i => i.kind === "system").map(i => i.cmd);
check("menu: đủ 12 lệnh hệ thống", heThong.length === 12 && ["help", "status", "model", "brain", "retry", "usage", "tasks", "compact", "plan", "memory", "export", "goal"].every(c => heThong.includes(c)));
check("menu: lệnh phiên vẫn đứng đầu", menu[0].kind === "session" && menu[0].cmd === "new");
check("menu: skill trùng tên lệnh hệ thống bị ẩn", !menu.some(i => i.kind === "skill" && i.cmd === "status"));
check("menu: skill thường vẫn hiện", menu.some(i => i.kind === "skill" && i.cmd === "notes"));
check("menu: /plan và /goal cần nội dung kèm", menu.filter(i => i.needArg).map(i => i.cmd).sort().join() === "goal,plan");
check("menu: mỗi lệnh có tên và mô tả thật (không lộ khoá i18n)", menu.filter(i => i.kind === "system").every(i => i.name && i.desc && !i.name.startsWith("lenh.") && !i.desc.startsWith("lenh.")));

// ---- 3. Mọi khoá i18n lệnh dùng phải CÓ trong cả vi và en ----
const vi = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/vi.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/en.json"), "utf8"));
{
  // Khoá của menu nằm ở bảng dữ liệu trong chat-slash.js (không ghép chuỗi lúc chạy), nên
  // test_i18n không thấy chúng: canh ở đây.
  const nguon = fs.readFileSync(path.join(ROOT, "dashboard/chat-slash.js"), "utf8");
  const khoaMenu = [...nguon.matchAll(/(?:nhan|mota): "(lenh\.[a-z0-9_]+)"/g)].map(m => m[1]);
  check("menu: 24 khoá tên/mô tả", khoaMenu.length === 24);
  check("menu: khoá nào cũng có trong vi.json", khoaMenu.every(k => k in vi));
  check("menu: khoá nào cũng có trong en.json", khoaMenu.every(k => k in en));
  const lenhJs = fs.readFileSync(path.join(ROOT, "dashboard/chat-lenh.js"), "utf8");
  const khoaDung = [...lenhJs.matchAll(/tw\("(lenh\.[a-z0-9_]+)"/g)].map(m => m[1]);
  const thieu = [...new Set(khoaDung)].filter(k => !(k in vi) || !(k in en));
  check("chat-lenh.js: khoá nào dùng cũng có ở cả vi lẫn en (" + new Set(khoaDung).size + " khoá)", thieu.length === 0 && khoaDung.length > 60);
  if (thieu.length) console.log("     thiếu:", thieu.join(", "));
  // Chỗ điền {ten} của hai bản phải giống nhau, nếu không bản tiếng Anh in nguyên "{n}" ra màn hình.
  const chua = v => (String(v).match(/\{(\w+)\}/g) || []).sort().join(",");
  const lech = Object.keys(vi).filter(k => k.startsWith("lenh.") && chua(vi[k]) !== chua(en[k]));
  check("lenh.*: chỗ điền {..} khớp giữa vi và en", lech.length === 0);
}

// ---- 4. Dấu nhận diện khối chỉ dẫn: JS, Python và app.js phải nói cùng một thứ ----
{
  const py = fs.readFileSync(path.join(ROOT, "server/lenh_he_thong.py"), "utf8");
  const app = fs.readFileSync(path.join(ROOT, "dashboard/app.js"), "utf8");
  const ses = fs.readFileSync(path.join(ROOT, "server/sessions.py"), "utf8");
  check("Python: PLAN_MARK = [CHẾ ĐỘ KẾ HOẠCH:", py.includes('PLAN_MARK = "[CHẾ ĐỘ KẾ HOẠCH:"'));
  check("Python: GOAL_MARK = [MỤC TIÊU:", py.includes('GOAL_MARK = "[MỤC TIÊU:"'));
  check("app.js: bong bóng gỡ được khối /plan và /goal", app.includes('"[CHẾ ĐỘ KẾ HOẠCH:"') && app.includes('"[MỤC TIÊU:"'));
  check("sessions.py: tên hội thoại bóc được khối /plan và /goal", ses.includes("CHẾ ĐỘ KẾ HOẠCH|MỤC TIÊU"));
}

// ---- 5. /goal: bóc dòng ẩn ----
const R = L.tachMucTieu;
check("goal: không có marker -> giữ nguyên, goal null", (() => { const r = R("Xong rồi\n\n\nnhé"); return r.goal === null && r.clean === "Xong rồi\n\n\nnhé"; })());
check("goal: done true", (() => { const r = R('Đã đối soát.\n<!-- JAVIS_GOAL: {"done":true} -->'); return r.goal && r.goal.done === true && r.clean === "Đã đối soát."; })());
check("goal: done false kèm left", (() => { const r = R('Còn việc.\n<!-- JAVIS_GOAL: {"done":false,"left":"chưa gửi báo cáo"} -->'); return r.goal && r.goal.done === false && r.goal.left === "chưa gửi báo cáo"; })());
check("goal: marker hỏng (JSON sai) -> null, câu trả lời vẫn còn", (() => { const r = R("Ok\n<!-- JAVIS_GOAL: {done:true -->"); return r.goal === null && r.clean === "Ok"; })());
check("goal: done không phải boolean -> null", R('a <!-- JAVIS_GOAL: {"done":"yes"} -->').goal === null);
check("goal: nhiều marker -> marker CUỐI thắng", R('<!-- JAVIS_GOAL: {"done":false} --> b <!-- JAVIS_GOAL: {"done":true} -->').goal.done === true);
check("goal: marker cuối hỏng thì không lấy marker trước", R('<!-- JAVIS_GOAL: {"done":true} --> b <!-- JAVIS_GOAL: xxx -->').goal === null);
check("goal: left quá dài bị cắt", R('<!-- JAVIS_GOAL: {"done":false,"left":"' + "x".repeat(500) + '"} -->').goal.left.length === 300);

// ---- 6. /goal: quyết định vòng tiếp ----
const Q = L.quyetDinhVongTiep;
const st = (o) => Object.assign({ vong: 1, toiDa: 5, left: "" }, o || {});
check("vòng: đạt -> dừng (dat)", (() => { const r = Q(st(), { marker: { done: true, left: "" } }); return r.act === "dung" && r.ly === "dat"; })());
check("vòng: chưa đạt -> tiếp, mang left", (() => { const r = Q(st(), { marker: { done: false, left: "còn 2 đơn" } }); return r.act === "tiep" && r.left === "còn 2 đơn"; })());
check("vòng: hết trần -> dừng (het_vong)", (() => { const r = Q(st({ vong: 5 }), { marker: { done: false, left: "a" } }); return r.act === "dung" && r.ly === "het_vong"; })());
check("vòng: chưa tới trần thì còn chạy", Q(st({ vong: 4 }), { marker: { done: false, left: "a" } }).act === "tiep");
check("vòng: hai vòng liền báo y hệt -> dừng (khong_tien_trien)", (() => { const r = Q(st({ left: "Còn  2 đơn" }), { marker: { done: false, left: "còn 2 đơn" } }); return r.act === "dung" && r.ly === "khong_tien_trien"; })());
check("vòng: left khác thì vẫn coi là có tiến triển", Q(st({ left: "còn 3 đơn" }), { marker: { done: false, left: "còn 2 đơn" } }).act === "tiep");
check("vòng: không có marker -> DỪNG, không đoán 'chưa xong'", (() => { const r = Q(st(), { marker: null }); return r.act === "dung" && r.ly === "khong_bao"; })());
check("vòng: ket rỗng/undefined -> dừng", Q(st(), undefined).act === "dung");
check("vòng: lỗi thắng mọi thứ", Q(st(), { loi: true, marker: { done: false, left: "a" } }).ly === "loi");
check("vòng: hỏi ngược người dùng -> dừng (hoi_lai)", Q(st(), { hoiLai: true, marker: { done: false, left: "a" } }).ly === "hoi_lai");
check("vòng: trần lớn nhất luôn dừng dù model nói chưa xong (vô hạn là bug)", (() => { let s = st(), n = 0; while (n < 100) { const r = Q(s, { marker: { done: false, left: "l" + n } }); if (r.act !== "tiep") break; s = st({ vong: s.vong + 1, left: r.left }); n++; } return n === 4; })());

// ---- 6b. /goal 0.173.1: trần 5, dừng khi không làm được, dòng nhắc gập mở ----
check("goal: trần là 5 vòng", L.GOAL_TOI_DA === 5);
check("goal: đọc được blocked kèm lý do", (() => { const r = R('Không vào được POS.\n<!-- JAVIS_GOAL: {"done":false,"blocked":"thiếu quyền POS"} -->'); return r.goal && r.goal.blocked === "thiếu quyền POS" && r.clean === "Không vào được POS."; })());
check("goal: done true thì bỏ qua blocked", R('<!-- JAVIS_GOAL: {"done":true,"blocked":"x"} -->').goal.blocked === undefined);
check("vòng: blocked -> dừng ngay (khong_lam_duoc), mang lý do", (() => { const r = Q(st({ vong: 1 }), { marker: { done: false, left: "a", blocked: "thiếu quyền POS" } }); return r.act === "dung" && r.ly === "khong_lam_duoc" && r.why === "thiếu quyền POS"; })());
check("vòng: lỗi và hỏi ngược vẫn đứng trước blocked", Q(st(), { loi: true, marker: { done: false, blocked: "x" } }).ly === "loi");
const V = L.docVong;
const khoiVong = '[MỤC TIÊU: đối soát\nChế độ...\n<!-- JAVIS_GOAL_ROUND: {"v": 3, "n": 5, "left": "còn 8 đơn"} -->\nCuối...]\n\nTiếp tục làm cho tới khi đạt mục tiêu.';
check("docVong: đọc v, n, left từ tin vòng tự động", (() => { const r = V(khoiVong); return r && r.v === 3 && r.n === 5 && r.left === "còn 8 đơn"; })());
check("docVong: tin thường, vòng 1, JSON hỏng đều là null",
  V("dọn kho giúp tôi") === null && V('<!-- JAVIS_GOAL_ROUND: {"v": 1, "n": 5} -->') === null && V("<!-- JAVIS_GOAL_ROUND: {v:2} -->") === null && V(null) === null);
check("docVong: v lớn hơn n là null", V('<!-- JAVIS_GOAL_ROUND: {"v": 6, "n": 5} -->') === null);
const H = L.vongHtml;
check("vongHtml: dòng gập mở có vòng, tình trạng, nút Tắt mục tiêu đang bật",
  (() => { const h = H({ v: 2, n: 5, left: "còn 8 đơn" }, true); return h.includes("<details") && h.includes("2/5") && h.includes("còn 8 đơn") && h.includes("goal-round-stop") && !h.includes("disabled") && h.includes("Tắt mục tiêu"); })());
check("vongHtml: mục tiêu đã dừng thì nút mờ (disabled)", H({ v: 2, n: 5, left: "" }, false).includes("disabled"));
check("vongHtml: escape tình trạng do model viết", (() => { const h = H({ v: 2, n: 5, left: "<b>x</b>" }, true); return !h.includes("<b>") && h.includes("&lt;b&gt;"); })());
check("vongHtml: null thì rỗng", H(null, true) === "");
check("goal kết thúc: mọi nút Tắt mục tiêu trên các dòng nhắc đều mờ đi", (() => {
  const btns = [{ disabled: false }, { disabled: false }];
  global.document = { querySelectorAll: (q) => (q === ".goal-round-stop" ? btns : []) };
  try { L.ketThucMucTieu(); } finally { delete global.document; }
  return btns.every((b) => b.disabled === true) && L.dangCoMucTieu() === false;
})());

// ---- 7. Định dạng ----
check("gonSo", L.gonSo(950) === "950" && L.gonSo(84300) === "84k" && L.gonSo(6100) === "6.1k" && L.gonSo(1250000) === "1.3M");
check("trạng thái: có engine, model, brain, số tin", (() => {
  const t = L.dinhDangTrangThai({ provider_label: "Claude Code", model: "opus", pinned: true, brain: "Brain Default", msg_count: 12, compact_count: 4, last_input_tokens: 84300, running: false, version: "0.64.81" });
  return t.includes("Claude Code") && t.includes("opus") && t.includes("Brain Default") && t.includes("12") && t.includes("84k") && t.includes("0.64.81");
})());
check("trạng thái: hội thoại trống không lộ 'undefined'", !L.dinhDangTrangThai({ provider: "anthropic-cli", brain: "b" }).includes("undefined"));
check("mức dùng: có số hôm nay và tổng", (() => {
  const t = L.dinhDangMucDung({ today: { total: { turns: 3, in: 12000, out: 900, cost: 0 }, items: [{ provider: "anthropic-cli", model: "opus", turns: 3, in: 12000, out: 900 }] }, all_time: { total: { turns: 40, in: 1e6, out: 5e4, cost: 1.5 } } });
  return t.includes("3") && t.includes("12k") && t.includes("40") && t.includes("$1.50") && !t.includes("$0.00");
})());
check("mức dùng: hôm nay chưa có lượt nào", L.dinhDangMucDung({ today: { total: {} }, all_time: { total: {} } }).length > 20);
check("việc nền: liệt kê theo nhóm + cảnh báo tự vận hành tắt", (() => {
  const t = L.dinhDangViec({ orchestration: "off", columns: { running: [{ title: "Đối soát đơn" }], todo: [{ title: "Gửi báo cáo" }] }, completed_24h: 2 });
  return t.includes("Đối soát đơn") && t.includes("Gửi báo cáo") && t.includes("TẮT") && t.includes("2");
})());
check("việc nền: trống", L.dinhDangViec({ orchestration: "auto", columns: {} }).length > 10);
check("việc nền: cắt bớt khi quá nhiều", (() => { const ds = Array.from({ length: 9 }, (_, i) => ({ title: "v" + i })); return L.dinhDangViec({ columns: { running: ds } }, 5).includes("4"); })());
check("bộ nhớ: link tương đối được gắn tiền tố memory/", L.vaLinkBoNho("- [A](facts/a.md) [B](https://x.vn) [C](/abs) [D](#neo)") === "- [A](memory/facts/a.md) [B](https://x.vn) [C](/abs) [D](#neo)");
check("bộ nhớ: trống thì nói trống", L.dinhDangBoNho({ text: "  " }).includes("memory/MEMORY.md"));
check("xuất: bỏ khối điều khiển khỏi tin của Javis, làm sạch tin người dùng", (() => {
  const md = L.dinhDangXuat({ title: "Kế hoạch", messages: [
    { role: "user", content: "[SKILL: x\nHãy dùng]\n\nchào" },
    { role: "assistant", content: 'Xin chào\n<!-- JAVIS_GOAL: {"done":true} -->' },
    { role: "system", content: "bỏ" },
  ] }, "12:00", (s) => s.replace(/^\[[^\]]*\]\n\n/, ""));
  return md.startsWith("# Kế hoạch") && md.includes("chào") && !md.includes("[SKILL") && !md.includes("JAVIS_GOAL") && !md.includes("bỏ");
})());
check("tên file xuất: bỏ dấu, chỉ a-z0-9 và gạch ngang", L.tenFileXuat("Đối soát đơn hàng!", "2026-09-29") === "javis-doi-soat-don-hang-2026-09-29.md");
check("tên file xuất: tiêu đề rỗng có tên dự phòng", L.tenFileXuat("", "2026-09-29") === "javis-hoi-thoai-2026-09-29.md");

// ---- 8. Khớp tên (brain) ----
const bs = [{ name: "Brain Default" }, { name: "Cửa hàng" }, { name: "Cửa hàng 2" }];
const layTen = (b) => b.name;
check("khớp tên: đúng nguyên tên thắng dù có tên chứa nó", L.khopTen(bs, "cửa hàng", layTen).hit === bs[1]);
check("khớp tên: một phần, ra đúng một -> chọn", L.khopTen(bs, "default", layTen).hit === bs[0]);
check("khớp tên: ra nhiều -> KHÔNG đoán", (() => { const r = L.khopTen(bs, "cửa", layTen); return r.hit === null && r.nhieu.length === 2; })());
check("khớp tên: không có", (() => { const r = L.khopTen(bs, "xyz", layTen); return r.hit === null && r.nhieu.length === 0; })());
check("lệnh tắt goal: clear/off/tắt", L.laLenhTat("clear") && L.laLenhTat("OFF") && L.laLenhTat("tắt") && !L.laLenhTat("dọn kho"));

if (fails.length) { console.log("\nFAIL - test_chat_lenh: " + fails.length + " lỗi"); process.exit(1); }
console.log("\nOK - test_chat_lenh: tất cả pass");
