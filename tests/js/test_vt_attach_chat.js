/* Unit test cho tính năng icon action đính kèm nhanh file vào chat từ cây thư mục Vault
 *
 *     node tests/js/test_vt_attach_chat.js
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "../..");
const conSrc = fs.readFileSync(path.join(ROOT, "dashboard/console.js"), "utf8");
const viJson = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/vi.json"), "utf8"));
const enJson = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/en.json"), "utf8"));
const cssSrc = fs.readFileSync(path.join(ROOT, "dashboard/style.css"), "utf8");

let fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

// 1. Kiểm tra từ điển đa ngôn ngữ
check("vi.json có khóa cs.vt_attach_chat", !!viJson["cs.vt_attach_chat"]);
check("en.json có khóa cs.vt_attach_chat", !!enJson["cs.vt_attach_chat"]);

// 2. Kiểm tra _vtRowEl render nút attach cho file
check("console.js có nút attach data-a=\"attach\" trong vt-act", conSrc.indexOf('data-a="attach"') !== -1);
check("nút attach dùng icon paperclip", /data-a="attach"[^>]*>\$\{ic\("paperclip"\)\}/.test(conSrc));
check("nút attach dùng i18n cs.vt_attach_chat", conSrc.indexOf('cs.vt_attach_chat') !== -1);
check("chỉ hiện nút attach cho file (!isDir)", /!isDir\s*\?\s*`<button data-a="attach"/.test(conSrc));

// 3. Kiểm tra xử lý click attach và animation feedback
check("xử lý click data-a === \"attach\"", conSrc.indexOf('else if (a === "attach")') !== -1);
check("gọi _vtAttachFile khi click", /_vtAttachFile\(rel,\s*it\.name\)/.test(conSrc));
check("chuyển icon sang check khi click thành công", /b\.innerHTML = ic\("check"\)/.test(conSrc));

// 4. Kiểm tra hàm _vtAttachFile
check("có hàm _vtAttachFile", /async function _vtAttachFile\(/.test(conSrc));
check("_vtAttachFile gọi /files/resolve", conSrc.indexOf("/files/resolve?brain=") !== -1);
check("_vtAttachFile gọi window.JavisAttach.addBrainFile", conSrc.indexOf("window.JavisAttach.addBrainFile(att)") !== -1);
check("_vtAttachFile xử lý ảnh raw url", conSrc.indexOf("/files/raw?brain=") !== -1);
check("_vtAttachFile focus vào chatInput", conSrc.indexOf('document.getElementById("chatInput")') !== -1);
check("_vtAttachFile không chuyển trang nếu đang ở coding hoặc trang giữ chat",
  conSrc.indexOf('TRANG_GIU_CHAT.includes(act)') !== -1 && conSrc.indexOf('!chatHien && window.JavisNav') !== -1);

// 5. Kiểm tra kết quả tìm kiếm vaultResults cũng có vr-attach
check("kết quả tìm kiếm có nút vr-attach cho file", /!isDir\s*\?\s*`<button class="vr-attach"/.test(conSrc));
check("style.css có class .vr-attach", cssSrc.indexOf(".vr-attach {") !== -1);

if (fails.length) {
  console.log("\nFAIL - test_vt_attach_chat: " + fails.length + " lỗi");
  process.exit(1);
}
console.log("\nOK - test_vt_attach_chat: tất cả pass");
