// Quoting a message with part of it selected quotes just that part, in the main chat and the Team chat (one helper,
// JavisActs.selectedIn in chat-acts.js).
//
//     node tests/js/test_quote_selection.js
"use strict";
const fs = require("fs");
const path = require("path");
const D = path.join(__dirname, "..", "..", "dashboard");
const A = require(path.join(D, "chat-acts.js"));
const app = fs.readFileSync(path.join(D, "app.js"), "utf8");
const teamsJs = fs.readFileSync(path.join(D, "teams.js"), "utf8");
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

// A message element and a text node inside it, and one outside.
const inside = { id: "in" };
const outside = { id: "out" };
const msg = { contains: (n) => n === inside || n === msg };
function win(sel) { return { getSelection: () => sel }; }
function sel(text, node, collapsed) {
  return { isCollapsed: !!collapsed, rangeCount: 1, toString: () => text, getRangeAt: () => ({ commonAncestorContainer: node }) };
}
check("selection inside the message: that text, trimmed", A.selectedIn(msg, win(sel("  ý hai \n", inside))) === "ý hai");
check("selection in another message: nothing", A.selectedIn(msg, win(sel("other", outside))) === "");
check("caret only (collapsed): nothing", A.selectedIn(msg, win(sel("x", inside, true))) === "");
check("no ranges: nothing", A.selectedIn(msg, win({ isCollapsed: false, rangeCount: 0, toString: () => "x" })) === "");
check("no selection API: nothing, no throw", A.selectedIn(msg, {}) === "" && A.selectedIn(msg, win(null)) === "");
check("no element: nothing", A.selectedIn(null, win(sel("x", inside))) === "");

check("main chat quote uses the shared helper", /const chon = window\.JavisActs\.selectedIn\(msgEl\);/.test(app)
  && !/sel\.getRangeAt\(0\)\.commonAncestorContainer/.test(app));
check("Team reply reads the selection in the message before redrawing",
  /S\.replyTo = replyTarget\(m, A && A\.selectedIn && el \? A\.selectedIn\(el\) : ""\);/.test(teamsJs)
  && teamsJs.indexOf("A.selectedIn(el)") < teamsJs.indexOf("drawMsgs(false);", teamsJs.indexOf("function startReply")));
check("Team quote and reply bar use the selected part when there is one",
  /preview: part \? partShort\(160\) : plainPreview\(m\.content, 160\)/.test(teamsJs)
  && /quote: who \+ ": " \+ \(part \|\| plainPreview\(m\.content, 280\)\)/.test(teamsJs));

if (fails) { console.log("\nFAIL - test_quote_selection: " + fails); process.exit(1); }
console.log("\nOK - test_quote_selection");
