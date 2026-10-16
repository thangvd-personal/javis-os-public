/* T3 phần J (0.156.0): giao diện bộ phán xử (dashboard/agent-judge-ui.js) và chỗ nối trong teams.js.
 *
 *     python tests/run.py agent_judge_ui
 *
 * Hàm thuần dưới node: lịch sử quyết định theo ngữ cảnh (cả ca im không có tin), nút sửa "Đáng lẽ nên nói" / "Nên im
 * (chen ngang)" gọi đúng route, thành công CHỈ sau 200, lỗi giữ nguyên và nói lý do; lời gọi trực tiếp không có nút; i18n
 * vi/en; chữ >= 16px; nạp trong index.html có ?v=; teams.js gắn ô vào hàng thành viên và bảng giới hạn có judge_daily, K.
 */
const fs = require("fs");
const path = require("path");
const ROOT = path.resolve(__dirname, "../..");
const UI = require(path.join(ROOT, "dashboard", "agent-judge-ui.js"));
const TM = require(path.join(ROOT, "dashboard", "teams.js"));
const vi = require(path.join(ROOT, "dashboard", "i18n", "vi.json"));
const en = require(path.join(ROOT, "dashboard", "i18n", "en.json"));

let fails = 0;
function check(name, ok, extra) {
  console.log((ok ? "ok   " : "FAIL ") + name + (ok || !extra ? "" : "  " + String(extra).slice(0, 400)));
  if (!ok) fails++;
}

const silent = { decision_id: 7, agent_key: "ag_1", context_ref: "team:t1", kind: "message", verdict: "silent",
  address: "none", score: 0.4, threshold: 0.6, reason: "", silence_code: "below_threshold", message_ref: "", excerpt: "ads?",
  label: "", label_effect: "", created_at: 1792000000, volunteer: false };
const spoke = Object.assign({}, silent, { decision_id: 8, verdict: "speak", volunteer: true, message_ref: "msg:12",
  silence_code: "", reason: "có số liệu mới", score: 0.9, label: "should_silent", label_effect: "applied" });
const direct = Object.assign({}, silent, { decision_id: 9, kind: "direct", verdict: "speak", address: "certain",
  score: null, threshold: null, silence_code: "" });
const view = { name: "judge", agent_key: "ag_1", resonance_on: true, eagerness: "medium", contexts: [
  { context_ref: "team:t1", label: "Vận hành", offset: 0.12, threshold: 0.72, decisions: [spoke, silent, direct] },
  { context_ref: "dm:ag_1", label: "", offset: 0, threshold: 0.6, decisions: [] }] };

const html = UI.viewHtml(view);
check("vẽ cả hai ngữ cảnh: nhóm và chat riêng", html.includes(vi["judge.ctx_team"].replace("{name}", "Vận hành"))
  && html.includes(vi["judge.ctx_dm"]), html);
check("ca im không có tin vẫn có dòng và nút sửa", html.includes('data-agj-row="7"') && html.includes(vi["judge.no_message"])
  && html.includes('data-agj-id="7"'), html);
check("lý do im theo mã được dịch", html.includes(vi["judge.code.below_threshold"]));
check("hai nút sửa có đúng nhãn của chủ", html.includes(vi["judge.label.should_speak"]) && html.includes(vi["judge.label.should_silent"]));
check("nhãn đang có: nút được đánh dấu và có nút bỏ nhãn", /data-agj-label="should_silent" data-agj-id="8" aria-pressed="true"/.test(html)
  && html.includes(vi["judge.label.clear"]));
check("hiệu lực nhãn hiện rõ (đã dịch ngưỡng)", html.includes(vi["judge.effect.applied"]));
check("lời gọi trực tiếp: không nút sửa, có câu giải thích", !html.includes('data-agj-id="9"') && html.includes(vi["judge.direct_note"]));
check("điểm và ngưỡng hiện ra", html.includes("0.90") && html.includes("0.72"));
check("thoát HTML", !UI.viewHtml({ contexts: [{ context_ref: "team:x", label: "<b>x</b>", decisions: [] }] }).includes("<b>x</b>"));
check("bản gọn (hàng thành viên) không có tiêu đề lớn", !UI.viewHtml(view, { compact: true }).includes(vi["judge.title"]));
check("đang lưu: nút của dòng đó bị khoá", /data-agj-id="7" aria-pressed="false" disabled/.test(UI.viewHtml(view, { busy: "7" })));

// postLabel: thành công chỉ sau 200.
function fakeFetch(status, body, seen) {
  return function (url, opt) {
    seen.push({ url: url, opt: opt });
    return Promise.resolve({ status: status, json: function () { return Promise.resolve(body); } });
  };
}
(async function () {
  const seen = [];
  const ok = await UI.postLabel(fakeFetch(200, { ok: true, effect: "applied", decision: spoke }, seen), "minh", "/b", 7, "should_speak");
  check("POST đúng route /agents/{slug}/judge/label kèm brain", seen[0].url === "/agents/minh/judge/label?brain=%2Fb"
    && seen[0].opt.method === "POST", seen[0].url);
  check("thân yêu cầu có decision_id và label", JSON.parse(seen[0].opt.body).decision_id === 7
    && JSON.parse(seen[0].opt.body).label === "should_speak");
  check("200: báo đã lưu", ok.ok && ok.text === vi["judge.saved"] && ok.decision.decision_id === 8);
  const bad = await UI.postLabel(fakeFetch(404, { ok: false, error: "Không có quyết định này" }, []), "minh", "b", 7, "should_speak");
  check("404: không báo thành công, nói lý do của máy chủ", !bad.ok && bad.text === "Không có quyết định này");
  const half = await UI.postLabel(fakeFetch(500, {}, []), "minh", "b", 7, "should_speak");
  check("lỗi không có thân: không thành công, câu lỗi chung", !half.ok && half.text === vi["judge.save_failed"]);
  const net = await UI.postLabel(function () { return Promise.reject(new Error("offline")); }, "minh", "b", 7, "");
  check("rớt mạng: không thành công", !net.ok);
  const clr = await UI.postLabel(fakeFetch(200, { ok: true, decision: silent }, []), "minh", "b", 7, "");
  check("bỏ nhãn 200: báo đã bỏ", clr.ok && clr.text === vi["judge.cleared"]);
  check("GET route có context_ref", UI.viewUrl("minh", "b", "team:t1") === "/agents/minh/judge?brain=b&context_ref=team%3At1");

  // i18n, CSS, nạp trong index.html.
  const keys = Object.keys(vi).filter(function (k) { return k.indexOf("judge.") === 0; });
  check("khoá judge.* có đủ ở vi và en, en đã dịch", keys.length > 40 && keys.every(function (k) { return en[k] && en[k] !== vi[k] || /\{/.test(vi[k]) && en[k]; })
    && !keys.some(function (k) { return /[ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/i.test(en[k]); }));
  const src = fs.readFileSync(path.join(ROOT, "dashboard", "agent-judge-ui.js"), "utf8");
  check("không em dash hay en dash", !/[\u2013\u2014]/.test(src));
  check("đăng ký phần judge với khung nhịp của phần P", src.includes('window.JavisPulseParts.push({ name: "judge", render: render })'));
  const css = fs.readFileSync(path.join(ROOT, "dashboard", "console.css"), "utf8");
  check("CSS phán xử: chữ 16px, nút 44px", /\.agj \{[^}]*font-size: 16px/.test(css) && /\.agj-btn \{[^}]*min-height: 44px/.test(css));
  const idx = fs.readFileSync(path.join(ROOT, "dashboard", "index.html"), "utf8");
  check("index.html nạp agent-judge-ui.js có ?v= và TRƯỚC teams.js",
    /agent-judge-ui\.js\?v=\d+/.test(idx) && idx.indexOf("agent-judge-ui.js") < idx.indexOf("/static/teams.js"));

  // teams.js: ô trong hàng thành viên, gợi ý, bảng giới hạn.
  const team = { team_id: "t1", leader_key: "ag_1", members: [{ agent_key: "ag_2", slug: "minh", name: "Minh" }] };
  const mh = TM.membersHtml(team, [], "ag_2");
  check("hàng thành viên đang mở có ô phán xử theo đúng nhóm", mh.includes('class="agj-host" data-judge-slug="minh" data-judge-ctx="team:t1"'), mh);
  check("hàng thành viên đóng thì không tải", !TM.membersHtml(team, [], null).includes("agj-host"));
  const lim = TM.limitsHtml({ authority: { limits: [
    { key: "judge_daily", category: "owner", value: 40, unit: "count", unlimited_ok: true },
    { key: "volunteer_k", category: "owner", value: 1, unit: "count", unlimited_ok: false },
    { key: "judge_off_batch", category: "owner", value: 0, unit: "mode", unlimited_ok: false }] } });
  check("bảng giới hạn: judge_daily, K, chế độ khi Cộng hưởng tắt có nhãn", lim.includes(vi["teams.cap.judge_daily"])
    && lim.includes(vi["teams.cap.volunteer_k"]) && lim.includes(vi["teams.limits.mode_code"]), lim);
  const caps = { task_turns: 12, task_steps: 8, help_depth: 2, daily_turns: 100, mentions: 3, busy_wait_s: 1800, judge_daily: 40,
    volunteer_k: 1, judge_off_batch: 1 };
  const form = TM.capsFormHtml({ caps: caps });
  check("ô giới hạn của chủ có judge_daily (kèm Không giới hạn), K và ô chọn chế độ",
    form.includes('name="tm-cap-judge_daily"') && form.includes('name="tm-cap-judge_daily-unl"') && form.includes('name="tm-cap-volunteer_k"')
    && form.includes('<select name="tm-cap-judge_off_batch">'), form);
  const pl = TM.capsPayload(caps, function (k) {
    return { judge_off_batch: { value: "0" }, judge_daily: { checked: true, value: "40" } }[k] || { value: caps[k] == null ? "" : String(caps[k]),
      checked: ["task_turns", "task_steps", "help_depth", "daily_turns"].indexOf(k) >= 0 ? false : null };
  });
  check("Lưu: gửi chế độ chỉ lọc code (0) và judge_daily Không giới hạn", pl.caps.judge_off_batch === 0
    && pl.caps.judge_daily && pl.caps.judge_daily.unlimited === true, JSON.stringify(pl));
  const tjs = fs.readFileSync(path.join(ROOT, "dashboard", "teams.js"), "utf8");
  check("ngăn Nhóm gọi hydrate sau khi vẽ", tjs.includes("window.JavisAgentJudge.hydrate(side)"));
  check("gợi ý 'sao không trả lời' mở lịch sử quyết định của đúng người", tjs.includes("data-judge-hint") && tjs.includes("if (d.judgeHint)"));

  // Ghép T3: trang nhịp gọi render(view, null, {slug}) và cần chuỗi HTML (mục ở view.parts.judge).
  const PULSE = require(path.join(ROOT, "dashboard", "agent-pulse.js"));
  const pulseView = { slug: "lan", parts: { judge: view } };
  const viaPulse = PULSE.partsHtml(pulseView, [{ name: "judge", render: UI.render }], { slug: "lan" });
  check("trang nhịp vẽ được mục phán xử (render(view, null, {slug}) trả HTML có lịch sử và nút sửa)",
    viaPulse.includes('data-part="judge"') && viaPulse.includes('data-judge-slug="lan"') && viaPulse.includes('data-agj-id="7"'),
    viaPulse);
  check("mục phán xử lỗi hay rỗng thì trang nhịp không vẽ gì", UI.render({ parts: { judge: { error: "x" } } }, null,
    { slug: "lan" }) === "" && UI.render({ parts: {} }, null, { slug: "lan" }) === "");

  // Smoke T3: sau khi lưu nhãn, ô phán xử lấy lại khung từ máy chủ để dòng ngưỡng/độ lệch đúng số mới.
  {
    const calls = [];
    global.window = { fetch: function (url) { calls.push(url); return Promise.resolve({ status: 200, json: function () {
      return Promise.resolve({ judge: Object.assign({}, view, { contexts: [Object.assign({}, view.contexts[0],
        { offset: -0.075, threshold: 0.525 })] }) }); } }); } };
    const host = { innerHTML: "" };
    await UI.refreshView(host, "lan", "lan|team:t1", false);
    delete global.window;
    check("smoke T3: lưu nhãn xong thì tải lại khung và vẽ ngưỡng mới", calls.length === 1 && calls[0].includes("/agents/lan/judge")
      && (host.innerHTML.includes("0.53") || host.innerHTML.includes("0.525")), host.innerHTML.slice(0, 300));
  }

  console.log(fails ? `\n${fails} FAIL` : "\nOK");
  process.exit(fails ? 1 : 0);
})();
