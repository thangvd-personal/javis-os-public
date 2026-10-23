/* Mic and speaker test in Settings > Voice (dashboard/mic-test.js, 0.222.0). Pure part, under node.

       node tests/js/test_mic_test.js */
const M = require("../../dashboard/mic-test.js");
const fs = require("fs");
const path = require("path");

let fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

check("level: silence is 0", M.level(new Float32Array(512)) === 0);
check("level: empty block is 0", M.level(new Float32Array(0)) === 0 && M.level(null) === 0);
const speech = new Float32Array(512).map((_, i) => 0.1 * Math.sin(i / 5));
check("level: ordinary speech lands mid-meter", M.level(speech) > 0.2 && M.level(speech) < 0.5);
check("level: clipped input caps at 1", M.level(new Float32Array(512).fill(1)) === 1);
check("verdict: nothing heard", M.verdict(0) === "silent" && M.verdict(0.03) === "silent" && M.verdict(NaN) === "silent");
check("verdict: faint", M.verdict(0.08) === "low");
check("verdict: clear", M.verdict(0.3) === "ok");
check("the test lasts 5 seconds", M.TEST_MS === 5000);

const root = path.join(__dirname, "..", "..");
const html = fs.readFileSync(path.join(root, "dashboard/index.html"), "utf8");
const vi = JSON.parse(fs.readFileSync(path.join(root, "dashboard/i18n/vi.json"), "utf8"));
const en = JSON.parse(fs.readFileSync(path.join(root, "dashboard/i18n/en.json"), "utf8"));
const card = html.slice(html.indexOf('id="voiceCard"'), html.indexOf("<!-- THƯƠNG HIỆU."));
for (const id of ["vcMicTest", "vcSpkTest", "vcMicBar", "vcMicResult"]) check(`voice card has #${id}`, card.includes(`id="${id}"`));
check("mic-test.js is loaded", html.includes("/static/mic-test.js"));
for (const k of ["settings.vc_mic_lbl", "settings.vc_mic_btn", "settings.vc_spk_btn", "settings.vc_mic_speak", "settings.vc_mic_ok",
  "settings.vc_mic_low", "settings.vc_mic_silent", "settings.vc_mic_denied", "settings.vc_spk_played", "settings.vc_spk_fail"])
  check(`i18n vi+en has ${k}`, typeof vi[k] === "string" && typeof en[k] === "string");
check("verdict texts carry the peak", vi["settings.vc_mic_ok"].includes("{pct}") && en["settings.vc_mic_low"].includes("{pct}"));
const css = fs.readFileSync(path.join(root, "dashboard/style.css"), "utf8");
check("result text is at least 16px", /\.mic-result \{[^}]*font-size: 16px/.test(css));

if (fails.length) { console.log("\nFAIL:", fails.length, fails); process.exit(1); }
console.log("\nOK - mic-test");
