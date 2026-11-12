/* Unit & wiring tests for the Record ("Ghi am") page (dashboard/voice-record.js).

       node tests/js/test_voice_record.js
*/
const fs = require("fs");
const path = require("path");
const ROOT = path.join(__dirname, "../..");

const VR = require(path.join(ROOT, "dashboard/voice-record.js"));
const consoleJs = fs.readFileSync(path.join(ROOT, "dashboard/console.js"), "utf8");
const consoleCss = fs.readFileSync(path.join(ROOT, "dashboard/console.css"), "utf8");
const indexHtml = fs.readFileSync(path.join(ROOT, "dashboard/index.html"), "utf8");
const viJson = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/vi.json"), "utf8"));
const enJson = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard/i18n/en.json"), "utf8"));

const fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

// 1. Timer formatting
check("fmtTime 0 -> 00:00", VR.fmtTime(0) === "00:00");
check("fmtTime 65 -> 01:05", VR.fmtTime(65) === "01:05");

// 2. Join raw text across takes
check("joinRawText into empty returns spoken", VR.joinRawText("", "xin chào") === "xin chào");
check("joinRawText appends with space across takes", VR.joinRawText("đoạn một", "đoạn hai") === "đoạn một đoạn hai");
check("joinRawText respects trailing newline", VR.joinRawText("dòng một\n", "dòng hai") === "dòng một\ndòng hai");

// 3. CTA state machine rules
check("empty idle: canRecord=true, canNormalize=false, canCancelTake=false",
  VR.canRecord({ state: "idle", locked: false, rawText: "" }) === true
  && VR.canNormalize({ state: "idle", locked: false, rawText: "" }) === false
  && VR.canCancelTake({ state: "idle", locked: false, rawText: "" }) === false);

check("while recording: canNormalize=false, canCancelTake=true, canNewRecord=false",
  VR.canNormalize({ state: "recording", locked: false, rawText: "abc" }) === false
  && VR.canCancelTake({ state: "recording", locked: false, rawText: "abc" }) === true
  && VR.canNewRecord({ state: "recording", locked: false }) === false);

check("after stopping with rawText: canNormalize=true and canRecord=true (append before normalize)",
  VR.canNormalize({ state: "idle", locked: false, rawText: "có nội dung" }) === true
  && VR.canRecord({ state: "idle", locked: false, rawText: "có nội dung" }) === true);

check("once normalized (locked=true): canRecord=false, canNormalize=false, canNewRecord=true",
  VR.canRecord({ state: "idle", locked: true, rawText: "có nội dung" }) === false
  && VR.canNormalize({ state: "idle", locked: true, rawText: "có nội dung" }) === false
  && VR.canNewRecord({ state: "idle", locked: true }) === true);

// 4. Sidebar placement: RAIL_TOP = ["chat", "record"]
const topMatch = consoleJs.match(/const RAIL_TOP = \[([^\]]+)\]/);
const topItems = topMatch ? topMatch[1].match(/"([a-z]+)"/g).map(s => s.replace(/"/g, "")) : [];
check("RAIL_TOP places record immediately below chat",
  JSON.stringify(topItems) === JSON.stringify(["chat", "record"]));
check("VIEW_ICON assigns mic icon to record", /record:\s*"mic"/.test(consoleJs));
check("console.js exposes JavisNoteEditor.openInSlot & closeInSlot",
  /openInSlot:\s*function/.test(consoleJs) && /closeInSlot:\s*function/.test(consoleJs));
check("openNote borrows #noteEditor into #vrEdit on body.on-record",
  /body\.classList\.contains\("on-record"\)/.test(consoleJs) && /vrEdit/.test(consoleJs));

// 5. HTML script load order
const vrIdx = indexHtml.indexOf("/static/voice-record.js");
const csIdx = indexHtml.indexOf("/static/console.js");
check("index.html loads voice-record.js before console.js", vrIdx > 0 && csIdx > vrIdx);

// 6. i18n keys present in both vi.json and en.json
const requiredKeys = [
  "page.record.label", "page.record.sub",
  "vr.btn.record", "vr.btn.stop", "vr.btn.cancel_take", "vr.btn.normalize", "vr.btn.new",
  "vr.raw.title", "vr.raw.placeholder", "vr.raw.locked_badge", "vr.raw.locked_note",
  "vr.audit.title", "vr.audit.saved", "vr.history.title", "vr.history.empty"
];
check("vi.json and en.json have all record/vr keys",
  requiredKeys.every(k => typeof viJson[k] === "string" && typeof enJson[k] === "string"));

// 7. No em dash (U+2014) anywhere in new JS/CSS/i18n strings
const vrJsSrc = fs.readFileSync(path.join(ROOT, "dashboard/voice-record.js"), "utf8");
check("no U+2014 em dash in voice-record.js or console.css",
  !vrJsSrc.includes("\u2014") && !consoleCss.includes("\u2014"));

if (fails.length) {
  console.log("\nFAIL:", fails.length, fails);
  process.exit(1);
}
console.log("\nOK - voice_record");

