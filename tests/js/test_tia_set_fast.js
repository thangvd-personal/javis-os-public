/* Tia sét Fast mode (0.153.0): tooltip theo bộ não, nút mờ khi bộ não không có, lưu settings.model.fast.

       node tests/js/test_tia_set_fast.js
*/
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "../..");
const src = fs.readFileSync(path.join(ROOT, "dashboard/model-picker.js"), "utf8");
const vi = require("../../dashboard/i18n/vi.json"), en = require("../../dashboard/i18n/en.json");
let fails = [];
function check(name, cond) { console.log((cond ? "ok   " : "FAIL ") + name); if (!cond) fails.push(name); }

const m = src.match(/\/\/ FAST_NOTE_BEGIN[\s\S]*?\/\/ FAST_NOTE_END/);
check("có khối FAST_NOTE tách được để test", !!m);
const fastNote = new Function(m[0] + "\nreturn fastNote;")();
const cases = [
  ["anthropic-cli", "", true, "fast.claude"], ["anthropic-cli", "claude-opus-5-5", true, "fast.claude"],
  ["anthropic-cli", "claude-sonnet-5-5", false, "fast.model_none"], ["anthropic-api", "claude-opus-4-8", true, "fast.api"],
  ["openai-oauth", "gpt-6.1-sol", true, "fast.codex"], ["openai", "gpt-6-sol", true, "fast.api"],
  ["openrouter", "openai/gpt-6", true, "fast.api"], ["gemini", "gemini-4-argon", true, "fast.api"],
  ["grok-cli", "", false, "fast.none"], ["antigravity-cli", "gemini-3.8-flash-high", false, "fast.none"],
  ["groq", "x", false, "fast.none"], ["deepseek", "x", false, "fast.none"], ["ollama", "x", false, "fast.none"],
  // Bí danh "opus" của Claude Code trỏ tới Opus 5.5 (đứng đầu danh mục, là mặc định dự phòng).
  ["anthropic-cli", "opus", true, "fast.claude"], ["anthropic-cli", "sonnet", false, "fast.model_none"],
];
cases.forEach(([p, mo, sup, note]) => {
  const r = fastNote(p, mo);
  check(p + " " + (mo || "(mặc định)") + " -> " + (sup ? "có" : "không") + ", " + note, r.supported === sup && r.note === note);
});
["fast.claude", "fast.codex", "fast.api", "fast.none", "fast.model_none"].forEach(k =>
  check("khoá " + k + " có ở vi và en", !!vi[k] && !!en[k]));
check("tooltip mở đầu bằng 'Fast mode', không có chữ bật/tắt hiện trên trang", src.indexOf('"Fast mode\\n" + window.t(') !== -1);
// Spec: giữa hai bản, tia sét lưu được nhưng chưa đổi tốc độ, tooltip phải nói rõ điều đó.
check("tooltip nói rõ mới lưu, bản sau mới có hiệu lực", src.indexOf('window.t("fast.pending")') !== -1 && !!vi["fast.pending"] && !!en["fast.pending"]);
check("bấm tia sét lưu settings.model.fast", src.indexOf("saveModel({ fast: state.fast })") !== -1);
check("nút mờ dùng aria-disabled, không disabled", src.indexOf('setAttribute("aria-disabled"') !== -1 && src.indexOf("fb.disabled") === -1);

if (fails.length) { console.log("\nFAIL - test_tia_set_fast: " + fails.length + " lỗi"); process.exit(1); }
console.log("\nOK - test_tia_set_fast: tất cả pass");
