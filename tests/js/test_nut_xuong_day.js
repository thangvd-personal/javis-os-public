/* Nút xuống cuối khung chat phải hiện NGAY khi cuộn lên, không chờ tin mới.

       node tests/js/test_nut_xuong_day.js

   Chủ repo yêu cầu (2026-07-31): "thêm nút xuống dưới cùng khi cuộn lên trên vì trong khung
   chat quá dài việc đọc lại xong xuống dưới mất quá nhiều thời gian".

   Bệnh cũ: đã có #newMsgBtn nhưng nó chỉ được bật trong scrollBottom(), mà scrollBottom() chỉ
   chạy khi CÓ TIN MỚI. Cuộn lên đọc lại một hội thoại dài rồi muốn quay xuống thì không có nút
   nào, phải tự kéo tay hết cả khung.

   Hành vi đã đo trong app THẬT bằng Chromium (15 phép thử: cuộn lên hiện nút, bấm nhảy xuống
   đáy, có tin mới thì nút nở ra có chữ, về đáy thì ẩn và nhả dạng tin mới, và nút đi theo khi
   phóng to khung chat). CI chỉ có node nên test này khoá phần hợp đồng đọc được từ mã nguồn. */
const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(path.join(__dirname, "../../dashboard/app.js"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "../../dashboard/style.css"), "utf8");
// 0.55.14: chữ tiếng Việt của nút đã dời vào từ điển i18n, nên khẳng định về LỜI LẼ soi
// hai vế: app.js gọi đúng khoá, và khoá đó trong vi.json mang đúng câu.
const VI = JSON.parse(fs.readFileSync(path.join(__dirname, "../../dashboard/i18n/vi.json"), "utf8"));

let fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}
// Lay than mot rule CSS theo dung selector (khong dinh rule ".x.y" hay ".x:hover")
// The main chat's #newMsgBtn shares its rules with the Team chat's .jv-new-msg ("#newMsgBtn, .jv-new-msg { ... }").
function than(sel) {
  const re = new RegExp(sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?:, [.#][\\w.-]+)?\\s*\\{[^}]*\\}");
  const m = CSS.match(re);
  return m ? m[0] : "";
}

// The logic moved to chat-scroll.js (shared with the Team chat). It is checked by BEHAVIOUR here, on a fake chat area,
// plus the main chat's wiring in app.js.
const CS = require(path.join(__dirname, "../../dashboard/chat-scroll.js"));
const SCROLL = fs.readFileSync(path.join(__dirname, "../../dashboard/chat-scroll.js"), "utf8");
function fakeBtn() {
  const set = new Set();
  const b = { parentNode: null, innerHTML: "", title: "", attrs: {}, listeners: {},
    classList: { add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c),
      toggle: (c, on) => { if (on === undefined) on = !set.has(c); if (on) set.add(c); else set.delete(c); return on; } },
    setAttribute(k, v) { b.attrs[k] = v; }, addEventListener(n, f) { b.listeners[n] = f; }, removeEventListener() {} };
  return b;
}
function fakeArea() {
  const ls = {};
  const area = { scrollHeight: 3000, clientHeight: 500, scrollTop: 2500, hover: {},
    addEventListener(n, fn) { (ls[n] = ls[n] || []).push(fn); }, removeEventListener() {},
    querySelector: (s) => area.hover[s] || null, fire(n, e) { (ls[n] || []).forEach((f) => f(e || {})); } };
  return area;
}
const win = { t: (k) => VI[k] || k, ic: (n) => "<svg data-ic=\"" + n + "\"></svg>", addEventListener() {},
  matchMedia: () => ({ matches: false }) };
const area = fakeArea();
const btn = fakeBtn();
const ctl = CS.attach(area, { button: btn, msg: ".msg", hold: ".msg-acts:hover, .jr-chat:hover", near: 90, win });

// ---- 1. Scrolling up shows the button AT ONCE, without waiting for a new message ----
area.scrollTop = 2400; area.fire("scroll");
check("no message yet (button not placed): the button is left alone", !btn.classList.contains("show"));
btn.parentNode = area;                       // app.js places it with the first message (chatAppend)
area.scrollTop = 1500; area.fire("scroll");
check("CANARY: scrolling up shows the button right in the scroll handler", btn.classList.contains("show") && ctl.stuck() === false);
area.scrollTop = 2450; area.fire("scroll");
check("back within 90px of the end: hidden, following again", !btn.classList.contains("show") && ctl.stuck() === true);

// ---- 2. Two looks ----
check("compact look: only the icon, no words", btn.innerHTML === "<svg data-ic=\"chevron-down\"></svg>" && VI["app.new_msg"] === "Tin mới");
check("accessible label for the compact look", btn.attrs["aria-label"] === VI["app.scroll_bottom"]
  && (VI["app.scroll_bottom"] || "").indexOf("Xuống cuối hội thoại") !== -1);
area.scrollTop = 1000; area.fire("scroll");
area.scrollHeight = 3200;                    // a new message arrives while reading higher up
ctl.toBottom();
check("a new message while reading: the view does not move, the button grows into New messages",
  area.scrollTop === 1000 && btn.classList.contains("has-new") && btn.innerHTML.indexOf(VI["app.new_msg"]) > 0
  && btn.attrs["aria-label"] === VI["app.new_msg_title"]);

// ---- 3. Back at the end drops the New messages look ----
btn.listeners.click();
check("pressing the button jumps to the end", area.scrollTop === area.scrollHeight && ctl.stuck() === true);
check("CANARY: at the end the New messages look is dropped", !btn.classList.contains("has-new") && !btn.classList.contains("show"));
check("icon through ic() with a fallback when icons.js is missing", /typeof win\.ic === "function"\) \? win\.ic\("chevron-down"\)/.test(SCROLL));
check("main chat uses the shared module on #chatArea with its button, selectors and the 90px threshold",
  /const chatScroll = window\.JavisChatScroll\.attach\(chatArea, \{\s*button: newMsgBtn, msg: "\.msg", hold: "\.msg-acts:hover, \.jr-chat:hover", near: 90,/.test(APP)
  && /function scrollBottom\(force\) \{ chatScroll\.toBottom\(force\); \}/.test(APP));

// ---- 4. CSS ----
const B = than("#newMsgBtn");
check("tìm được rule #newMsgBtn", B.length > 0);
// .transcript la flex DOC -> nut la flex item se BI CO theo chieu cao. Thieu flex:none thi
// nut tron 32px bi bop con 15px (da dinh that luc do trong trinh duyet).
check("CANARY: có flex:none, nếu không nút tròn bị bóp méo", /flex:\s*none/.test(B));
check("dạng gọn là hình tròn", /border-radius:\s*50%/.test(B));
check("dạng gọn vuông 32x32", /width:\s*32px/.test(B) && /height:\s*32px/.test(B));
check("dính đáy khung chat", /position:\s*sticky/.test(B) && /bottom:\s*6px/.test(B));
check("mặc định ẩn", /display:\s*none/.test(B));
const HN = than("#newMsgBtn.has-new");
check("dạng Tin mới nở ra thành viên thuốc",
      /width:\s*auto/.test(HN) && /border-radius:\s*16px/.test(HN));
check("dạng Tin mới ăn màu nhấn", /var\(--accent\)/.test(HN));
check("lớp show mới thực sự hiện", /#newMsgBtn\.show(?:, \.jv-new-msg\.show)?\s*\{[^}]*display:\s*inline-flex/.test(CSS));
// Nut noi DE len chu tin nhan -> bong phai du dam, khong thi chim vao nen chu.
check("bóng đủ đậm để tách khỏi chữ bên dưới", /box-shadow:\s*var\(--shadow-2\)/.test(B));

// ---- 5. Không được bỏ sót: nút vẫn nằm TRONG #chatArea để đi theo lúc phóng to ----
// chat-zoom.js dời NGUYÊN #chatArea vào lớp phóng to; nút là con của nó nên đi theo.
check("nút được chèn vào chính #chatArea",
      /newMsgBtn\.parentNode !== chatArea/.test(APP) && /chatArea\.appendChild\(newMsgBtn\)/.test(APP));
// `_neoChenCu` là cái neo của đường TẢI DẦN (cuộn lên lấy tin cũ): lúc đó bong bóng phải nằm
// trước tin già nhất đang hiện. Neo rỗng - tức mọi lượt bình thường - thì vẫn là chèn trước
// nút, nên nút giữ nguyên chỗ cuối cùng và sticky vẫn đúng.
check("tin nhắn luôn chèn TRƯỚC nút, để nút nằm cuối mà sticky",
      /chatArea\.insertBefore\(el, _neoChenCu \|\| newMsgBtn\)/.test(APP));

if (fails.length) {
  console.log("\nFAIL - test_nut_xuong_day: " + fails.length + " lỗi: " + fails.join(", "));
  process.exit(1);
}
console.log("\nOK - test_nut_xuong_day: tất cả pass");
