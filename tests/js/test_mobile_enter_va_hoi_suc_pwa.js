/* Hai lỗi mobile do người dùng thật báo (17/08/2026), cùng một phiên chat trên điện thoại:

   1. Máy chạm không có Shift+Enter nên Enter-gửi kiểu desktop nghĩa là KHÔNG CÁCH NÀO
      viết tin nhiều dòng. Máy chạm phải để Enter xuống dòng, gửi bằng nút Gửi.
   2. App "Thêm vào màn hình chính" bị iOS đóng băng nền: socket chết, tin đến trong lúc
      ngủ mất khỏi luồng live, và standalone KHÔNG có nút reload để user tự cứu. Phải tự
      hồi sức khi app dậy: nối lại socket + kéo lại hội thoại từ server.

       node tests/js/test_mobile_enter_va_hoi_suc_pwa.js
*/
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..", "..");
const app = fs.readFileSync(path.join(root, "dashboard", "app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "dashboard", "index.html"), "utf8");

let fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

// ---- 1. Enter on a touch screen = new line ----
// The rules moved to chat-acts.js (JavisActs.enterSends) so the Team chat shares them; checked by behaviour here.
const Acts = require(path.join(root, "dashboard", "chat-acts.js"));
const touch = { matchMedia: (q) => ({ matches: q === "(pointer: coarse)" }) };
const mouse = { matchMedia: () => ({ matches: false }) };
const enter = { key: "Enter", shiftKey: false, keyCode: 13 };
check("touch screen (pointer: coarse): Enter does not send", Acts.enterSends(enter, false, touch) === false);
// Measured by pointer, not screen width: a touch laptop with a mouse still sends on Enter.
check("enterSends does not look at the screen width", !/innerWidth/.test(Acts.enterSends.toString()));
check("computer: Enter sends, Shift+Enter is a new line",
  Acts.enterSends(enter, false, mouse) === true && Acts.enterSends(Object.assign({}, enter, { shiftKey: true }), false, mouse) === false);
check("an IME composition never sends, on any device",
  Acts.enterSends(Object.assign({}, enter, { keyCode: 229 }), false, mouse) === false && Acts.enterSends(enter, true, mouse) === false);
check("main chat keydown uses the shared rules",
  /if \(window\.JavisActs\.enterSends\(e, chatInputComposing\(\)\)\) \{ e\.preventDefault\(\); sendMessage\(\); \}/.test(app));

// ---- 2. Hồi sức sau giấc ngủ nền ----
check("connect() có chốt chống nối trùng (hai đường gọi: retry 3s + hồi sức)",
  /readyState === WebSocket\.OPEN \|\| ws\.readyState === WebSocket\.CONNECTING\)\) return;/.test(app));
check("nghe visibilitychange để biết lúc app dậy", /addEventListener\("visibilitychange"/.test(app));
check("nghe pageshow bắt ca bfcache trả trang cũ", /addEventListener\("pageshow"/.test(app));
check("dậy là nối lại socket ngay, không đợi retry", /_resumeSauNgu/.test(app) && /try \{ connect\(\); \}/.test(app));
check("kéo lại hội thoại đang xem từ server để bù tin lỡ",
  /openStoredSession\(savedSessionId\)/.test(app));
// Ngủ ngắn (chuyển app qua lại vài giây) thì đừng ồn ào kéo lại - có ngưỡng thời gian.
check("có ngưỡng ngủ tối thiểu trước khi kéo lại", /_hiddenAt < 20000|20000 > /.test(app) || /_hiddenAt < 20 \* 1000/.test(app));

// ---- cache-bust ----
const v = Number((html.match(/app\.js\?v=(\d+)/) || [])[1] || 0);
check("app.js đã bump ?v= (>= 90)", v >= 90);

if (fails.length) {
  console.log("\nFAIL - test_mobile_enter_va_hoi_suc_pwa: " + fails.length + " lỗi: " + fails.join(", "));
  process.exit(1);
}
console.log("\nOK - test_mobile_enter_va_hoi_suc_pwa: tất cả pass");
