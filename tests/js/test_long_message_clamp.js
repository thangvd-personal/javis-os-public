// Long messages the owner wrote collapse to ten lines with "Show more" in the Team chat, like the main chat. One rule and
// one toggle (chat-acts.js) for both; the Team page remembers an opened message across redraws.
//
//     node tests/js/test_long_message_clamp.js
"use strict";
const fs = require("fs");
const path = require("path");
const D = path.join(__dirname, "..", "..", "dashboard");
const A = require(path.join(D, "chat-acts.js"));
const T = require(path.join(D, "teams.js"));
const app = fs.readFileSync(path.join(D, "app.js"), "utf8");
const teamsJs = fs.readFileSync(path.join(D, "teams.js"), "utf8");
const css = fs.readFileSync(path.join(D, "console.css"), "utf8");
const vi = JSON.parse(fs.readFileSync(path.join(D, "i18n", "vi.json"), "utf8"));
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

const lines = (n) => Array.from({ length: n }, (_, i) => "line " + i).join("\n");
check("ten lines: not long", A.isLongText(lines(10)) === false);
check("eleven lines: long", A.isLongText(lines(11)) === true);
check("900 characters: not long; 901: long", A.isLongText("x".repeat(900)) === false && A.isLongText("x".repeat(901)) === true);
check("empty: not long", A.isLongText("") === false && A.isLongText(null) === false);
check("button: Show more when closed, Show less when open",
  A.clampButtonHtml(false) === '<button class="clamp-more" type="button">' + vi["app.show_more"] + "</button>"
  && A.clampButtonHtml(true).includes(vi["app.show_less"]));

// The toggle, on a fake bubble.
function cls(init) {
  const set = new Set(init);
  return { contains: (c) => set.has(c), toggle: (c) => (set.has(c) ? set.delete(c) : set.add(c)) };
}
const utext = { classList: cls(["utext", "clamped"]) };
const bubble = { querySelector: (s) => (s === ".utext" ? utext : null) };
const btn = { textContent: "", closest: (s) => (/\.tm-bubble/.test(s) ? bubble : null), parentNode: bubble };
check("toggle opens a Team bubble's text", A.toggleClamp(btn) === true && !utext.classList.contains("clamped")
  && btn.textContent === vi["app.show_less"]);
check("toggle closes it again", A.toggleClamp(btn) === false && utext.classList.contains("clamped")
  && btn.textContent === vi["app.show_more"]);

// Team rendering.
let h = T.bodyHtml({ author_kind: "owner", content: lines(16) }, true, false);
check("Team: a long owner message is collapsed with Show more", /class="tm-text utext clamped"/.test(h)
  && h.includes('<button class="clamp-more" type="button">' + vi["app.show_more"]), h.slice(0, 200));
h = T.bodyHtml({ author_kind: "owner", content: lines(16) }, true, true);
check("Team: an opened one stays open after a redraw", /class="tm-text utext"/.test(h) && h.includes(vi["app.show_less"]));
h = T.bodyHtml({ author_kind: "owner", content: "short" }, true, false);
check("Team: a short message has no button", !/clamp-more/.test(h) && /class="tm-text utext"/.test(h));
h = T.bodyHtml({ author_kind: "owner", content: "> Lan: " + "q".repeat(2000) + "\n\nshort answer" }, true, false);
check("Team: only the owner's own words count, not the quoted message", !/clamp-more/.test(h), h.slice(-200));
h = T.messageHtml({ id: 5, author_kind: "owner", content: lines(12), meta: {} }, { members: [], unclamped: { 5: true } });
check("Team: messageHtml passes the remembered open state", /class="tm-text utext"/.test(h) && !/utext clamped/.test(h));

check("main chat uses the shared rule and button", /const isLong = window\.JavisActs\.isLongText\(text\);/.test(app)
  && /window\.JavisActs\.clampButtonHtml\(false\)/.test(app) && !/u\.classList\.toggle\("clamped"\)/.test(app));
check("Team remembers the state when Show more is pressed, and forgets it on a team switch",
  /S\.unclamped\[cm\.getAttribute\("data-mid"\)\] = cu\.classList\.contains\("clamped"\);/.test(teamsJs)
  && /S\.unclamped = \{\};/.test(teamsJs));
check("CSS: the Team owner text clamps at ten lines",
  /\.tm-msg-owner \.tm-text\.utext\.clamped \{ display: -webkit-box; -webkit-line-clamp: 10;/.test(css));

if (fails) { console.log("\nFAIL - test_long_message_clamp: " + fails); process.exit(1); }
console.log("\nOK - test_long_message_clamp");
