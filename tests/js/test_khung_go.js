/* Khung gõ một khối (0.153.0): mọi thứ nằm trong #hudVoice, các id cũ còn nguyên.

       node tests/js/test_khung_go.js
*/
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "../..");
const html = fs.readFileSync(path.join(ROOT, "dashboard/index.html"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "dashboard/style.css"), "utf8");
const consoleJs = fs.readFileSync(path.join(ROOT, "dashboard/console.js"), "utf8");
const mobile = fs.readFileSync(path.join(ROOT, "dashboard/mobile-chat.js"), "utf8");
const app = fs.readFileSync(path.join(ROOT, "dashboard/app.js"), "utf8");
const ws = fs.readFileSync(path.join(ROOT, "dashboard/workspace.js"), "utf8");
let fails = [];
function check(name, cond) { console.log((cond ? "ok   " : "FAIL ") + name); if (!cond) fails.push(name); }

const card = (html.match(/<div class="hud-voice" id="hudVoice">[\s\S]*?<!-- \/hudVoice -->/) || [""])[0];
check("tìm thấy khung #hudVoice (kết thúc bằng dấu <!-- /hudVoice -->)", !!card);
["voiceFocusResume", "attachBar", "fileInput", "folderInput", "chatInput", "cpRow", "plusBtn", "cpChips",
 "mbOpen", "effBtn", "effTxt", "fastBtn", "voiceBtn", "stopBtn", "sendBtn", "mbPop", "effPop", "plusMenu"].forEach(id => {
  check("trong khung có #" + id, card.indexOf('id="' + id + '"') !== -1);
});
const order = ["attachBar", "chatInput", "cpRow"].map(id => card.indexOf('id="' + id + '"'));
check("ảnh đính kèm ở trên, ô gõ giữa, hàng nút dưới", order[0] < order[1] && order[1] < order[2]);
const row = card.slice(card.indexOf('id="cpRow"'));
check("hàng nút: + trước chip, mic và gửi ở cuối",
  row.indexOf('id="plusBtn"') < row.indexOf('id="cpChips"') && row.indexOf('id="cpChips"') < row.indexOf('id="voiceBtn"')
  && row.indexOf('id="voiceBtn"') < row.indexOf('id="sendBtn"'));
check("#attachBtn đã bỏ khỏi trang", html.indexOf('id="attachBtn"') === -1);
check("#modelBar còn, rỗng, đứng ngay trước khung", /<div class="model-bar" id="modelBar"><\/div>\s*(<!--[\s\S]*?-->\s*)*<div class="hud-voice" id="hudVoice">/.test(html));
check("#folderInput chọn thư mục", /id="folderInput"[^>]*webkitdirectory/.test(card));
check("textarea giữ id đầu tiên và rows=1", /<textarea id="chatInput"[^>]*rows="1"/.test(card));
check("không có disabled trên #voiceBtn", !/id="voiceBtn"[^>]*disabled/.test(card));

check("CSS: khung xếp dọc, bo 24px trước nền --bg2",
  /\.hud-voice \{[^}]*flex-direction: column[^}]*align-items: flex-end[^}]*border-radius: 24px;[^}]*background: var\(--bg2\)/s.test(css));
check("CSS: #modelBar rỗng thì ẩn", css.indexOf(".model-bar:empty { display: none; }") !== -1);
check("CSS: không còn luật order kiểu cũ trên điện thoại", css.indexOf(".hud-voice .attach-btn { order: 1;") === -1);

check("CHAT_NODE_IDS bỏ attachBar (đã nằm trong khung)",
  consoleJs.indexOf('const CHAT_NODE_IDS = ["callBar", "chatArea", "bgStrip", "modelBar", "hudVoice"];') !== -1);
check("cp-css trang Trò chuyện bo 24px", consoleJs.indexOf(".chatpage-slot .hud-voice{ background:var(--bg2); border:1px solid var(--border); border-radius:24px; }") !== -1);

check("điện thoại -> máy tính: chip model về lại #cpChips trước #effBtn",
  mobile.indexOf('chipHost.insertBefore(mbOpen, document.getElementById("effBtn"))') !== -1
  && mobile.indexOf("mbPopHome.appendChild(mbPop)") !== -1);
check("app.js không còn gọi thẳng #attachBtn", app.indexOf('document.getElementById("attachBtn").addEventListener') === -1);
check("Sources lấy từ file đầu tiên có sources (file brain không có)",
  app.indexOf("(atts.find(a => a.sources) || {}).sources") !== -1);
check("workspace khoá cả + và gửi khi phiên chưa sẵn", /\["plusBtn", "sendBtn"\]\.forEach/.test(ws));
const coding = fs.readFileSync(path.join(ROOT, "dashboard/coding.js"), "utf8");
check("trang Coding vẫn chèn hàng chip ngữ cảnh vào #modelBar (hiện vì không còn rỗng)",
  coding.indexOf('getElementById("modelBar")') !== -1 && css.indexOf(".model-bar:empty { display: none; }") !== -1);

const slash = fs.readFileSync(path.join(ROOT, "dashboard/chat-slash.js"), "utf8");
check("menu / neo vào khung gõ (#hudVoice), không che ảnh đính kèm",
  slash.indexOf('input.closest(".hud-voice") || input') !== -1);

if (fails.length) { console.log("\nFAIL - test_khung_go: " + fails.length + " lỗi"); process.exit(1); }
console.log("\nOK - test_khung_go: tất cả pass");
