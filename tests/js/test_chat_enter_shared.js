// Enter in the Team chat follows the main chat's rules (one helper, JavisActs.enterSends in chat-acts.js): an IME
// composition never sends, a touch screen keeps Enter as a new line (Send button sends), a computer sends on Enter and
// Shift+Enter is a new line.
//
//     node tests/js/test_chat_enter_shared.js
"use strict";
const fs = require("fs");
const path = require("path");
const D = path.join(__dirname, "..", "..", "dashboard");
const A = require(path.join(D, "chat-acts.js"));
const teamsJs = fs.readFileSync(path.join(D, "teams.js"), "utf8");
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

const touch = { matchMedia: (q) => ({ matches: q === "(pointer: coarse)" }) };
const mouse = { matchMedia: () => ({ matches: false }) };
const broken = { matchMedia: () => { throw new Error("old browser"); } };
const key = (o) => Object.assign({ key: "Enter", shiftKey: false, keyCode: 13, isComposing: false }, o);

check("computer: Enter sends", A.enterSends(key(), false, mouse) === true);
check("computer: Shift+Enter is a new line", A.enterSends(key({ shiftKey: true }), false, mouse) === false);
check("phone: Enter is a new line", A.enterSends(key(), false, touch) === false);
check("IME: isComposing, keyCode 229 and our own flag never send",
  A.enterSends(key({ isComposing: true }), false, mouse) === false && A.enterSends(key({ keyCode: 229 }), false, mouse) === false
  && A.enterSends(key(), true, mouse) === false);
check("another key never sends", A.enterSends(key({ key: "a" }), false, mouse) === false);
check("matchMedia missing or throwing counts as a computer", A.enterSends(key(), false, broken) === true
  && A.enterSends(key(), false, {}) === true);

// Composition events bubble, so the Team page tracks them on its root: the input is redrawn often.
const ls = {};
const root = { addEventListener: (n, fn) => { ls[n] = fn; } };
const on = A.trackComposition(root);
ls.compositionstart();
const during = on();
ls.compositionend();
check("trackComposition follows start and end", during === true && on() === false);
check("trackComposition without a root never claims a composition", A.trackComposition(null)() === false);

check("Team onKey sends only when the shared helper says so",
  /A && A\.enterSends\(e, S\.composing && S\.composing\(\)\)\) \{\s*e\.preventDefault\(\);\s*send\(\);/.test(teamsJs));
check("Team tracks IME composition on its page root", /S\.composing = AC && AC\.trackComposition \? AC\.trackComposition\(el\) : null;/.test(teamsJs));
check("the old Team rule (Enter sends everywhere) is gone",
  !/contains\("tm-input"\) && e\.key === "Enter" && !e\.shiftKey && !e\.isComposing/.test(teamsJs));

if (fails) { console.log("\nFAIL - test_chat_enter_shared: " + fails); process.exit(1); }
console.log("\nOK - test_chat_enter_shared");
