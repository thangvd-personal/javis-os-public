/* Thanh trượt mức suy nghĩ (0.153.0): 6 nấc, nhãn tiếng Anh, model gắn sẵn mức thì khoá.

       node tests/js/test_thanh_truot_effort.js
*/
const fs = require("fs");
const path = require("path");
const S = require("../../dashboard/effort-slider.js");
const ROOT = path.join(__dirname, "../..");
const picker = fs.readFileSync(path.join(ROOT, "dashboard/model-picker.js"), "utf8");
const vi = require("../../dashboard/i18n/vi.json"), en = require("../../dashboard/i18n/en.json");
let fails = [];
function check(name, cond) { console.log((cond ? "ok   " : "FAIL ") + name); if (!cond) fails.push(name); }

check("6 nấc đúng thứ tự giá trị server", JSON.stringify(S.LEVELS) === JSON.stringify(["off", "low", "medium", "high", "xhigh", "ultra"]));
check("vị trí 0 -> off, 1 -> ultra", S.levelFromRatio(0) === "off" && S.levelFromRatio(1) === "ultra");
check("vị trí giữa làm tròn về nấc gần nhất", S.levelFromRatio(0.41) === "medium" && S.levelFromRatio(0.49) === "medium");
check("vị trí ngoài thanh bị kẹp", S.levelFromRatio(-3) === "off" && S.levelFromRatio(9) === "ultra");
check("ratioFromLevel ngược lại", S.ratioFromLevel("medium") === 0.4 && S.ratioFromLevel("lạ") === 0);
check("Antigravity tên đuôi -high là model khoá mức", S.isLocked("antigravity-cli", "gemini-3.8-flash-high") === true);
check("Antigravity không đuôi mức thì không khoá", S.isLocked("antigravity-cli", "claude-sonnet-4-6") === false);
check("nhà khác không bao giờ khoá", S.isLocked("openai-oauth", "gpt-6.1-sol-high") === false);
const labels = { off: "Off", low: "Light", medium: "Medium", high: "High", xhigh: "Extra", ultra: "Ultra" };
Object.keys(labels).forEach(k => check("nhãn " + k + " = " + labels[k] + " ở cả vi và en", vi["eff." + k] === labels[k] && en["eff." + k] === labels[k]));
check("model-picker.js giữ đủ 6 chữ giá trị có ngoặc kép", ["off", "low", "medium", "high", "xhigh", "ultra"].every(v => picker.indexOf('"' + v + '"') !== -1));
check("chip model không còn chữ 'Effort:'", picker.indexOf('"Effort: "') === -1 && picker.indexOf("mb-eff-row") === -1);
check("bấm ra ngoài: bảng model đóng cả khi bấm trong hàng nút (+, mic, gửi)", picker.indexOf('e.target.closest("#modelBar")') === -1);
const srcSlider = fs.readFileSync(path.join(ROOT, "dashboard/effort-slider.js"), "utf8");
check("thanh trượt có role=slider và phím mũi tên", srcSlider.indexOf('"slider"') !== -1 && srcSlider.indexOf("ArrowRight") !== -1);
check("tôn trọng giảm chuyển động", srcSlider.indexOf("prefers-reduced-motion") !== -1);
check("không chữ Việt trong effort-slider.js", !/[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/i.test(srcSlider.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "")));

if (fails.length) { console.log("\nFAIL - test_thanh_truot_effort: " + fails.length + " lỗi"); process.exit(1); }
console.log("\nOK - test_thanh_truot_effort: tất cả pass");
