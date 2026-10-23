/* Hồi quy: Enter khi bộ gõ tiếng Việt/IME còn composition không được gửi tin. */
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

// The rules live in chat-acts.js (shared with the Team chat): checked by behaviour.
const Acts = require(path.join(root, "dashboard", "chat-acts.js"));
const mouse = { matchMedia: () => ({ matches: false }) };
const enter = { key: "Enter", shiftKey: false, keyCode: 13 };
const seen = {};
const fakeInput = { addEventListener: (n, fn) => { seen[n] = fn; } };
const composing = Acts.trackComposition(fakeInput);
check("tracks compositionstart and compositionend", typeof seen.compositionstart === "function" && typeof seen.compositionend === "function");
seen.compositionstart();
check("keydown blocked by our own composition flag", composing() === true && Acts.enterSends(enter, composing(), mouse) === false);
seen.compositionend();
check("flag clears on compositionend", composing() === false);
check("keydown blocked by the standard isComposing", Acts.enterSends(Object.assign({}, enter, { isComposing: true }), false, mouse) === false);
check("keydown blocked by the IME keyCode 229 fallback", Acts.enterSends(Object.assign({}, enter, { keyCode: 229 }), false, mouse) === false);
check("plain Enter still sends", Acts.enterSends(enter, false, mouse) === true);
check("main chat wires the composition tracker on its input", /const chatInputComposing = window\.JavisActs\.trackComposition\(chatInput\);/.test(app));
const appVersion = Number((html.match(/app\.js\?v=(\d+)/) || [])[1] || 0);
check("app.js đã bump cache", appVersion >= 80);

if (fails.length) {
  console.log("\nFAIL - test_chat_ime_enter: " + fails.length + " lỗi: " + fails.join(", "));
  process.exit(1);
}
console.log("\nOK - test_chat_ime_enter: tất cả pass");
