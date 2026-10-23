// Copying a message: one shared helper (JavisActs.copy in chat-acts.js) for the main chat, the Team chat and the code
// block button. It works on plain HTTP (textarea fallback) and tells the caller whether the copy worked, so the Team chat
// no longer fails silently and copies the same text the main chat would.
//
//     node tests/js/test_copy_shared.js
"use strict";
const fs = require("fs");
const path = require("path");
const D = path.join(__dirname, "..", "..", "dashboard");
const A = require(path.join(D, "chat-acts.js"));
const T = require(path.join(D, "teams.js"));
const app = fs.readFileSync(path.join(D, "app.js"), "utf8");
const teamsJs = fs.readFileSync(path.join(D, "teams.js"), "utf8");
const render = fs.readFileSync(path.join(D, "chat-render.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(D, "index.html"), "utf8");
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

function fakeDoc(execResult) {
  const log = [];
  return {
    log,
    body: { appendChild(n) { log.push("append:" + n.value); } },
    createElement() { return { style: {}, select() { log.push("select"); }, remove() { log.push("remove"); } }; },
    execCommand(c) { log.push("exec:" + c); if (execResult === "throw") throw new Error("no"); return execResult; },
  };
}

(async function () {
  // Secure page: the clipboard API.
  let wrote = [];
  let ok = await A.copy("hello", { doc: fakeDoc(true), win: { isSecureContext: true },
    nav: { clipboard: { writeText: (s) => { wrote.push(s); return Promise.resolve(); } } } });
  check("secure page: clipboard API, resolves true", ok === true && wrote[0] === "hello");

  // Plain HTTP: no secure context, the textarea fallback.
  let doc = fakeDoc(true);
  ok = await A.copy("plain", { doc, win: { isSecureContext: false }, nav: { clipboard: { writeText: () => Promise.reject() } } });
  check("plain HTTP: textarea + execCommand, resolves true", ok === true && doc.log.join(",") === "append:plain,select,exec:copy,remove",
    doc.log.join(","));

  // Clipboard refused: fallback.
  doc = fakeDoc(true);
  ok = await A.copy("x", { doc, win: { isSecureContext: true }, nav: { clipboard: { writeText: () => Promise.reject(new Error("denied")) } } });
  check("clipboard permission refused: falls back to the textarea", ok === true && doc.log.indexOf("exec:copy") >= 0);

  // Every way failed: resolves false, never rejects.
  ok = await A.copy("x", { doc: fakeDoc(false), win: { isSecureContext: false }, nav: {} });
  check("execCommand says no: resolves false", ok === false);
  ok = await A.copy("x", { doc: fakeDoc("throw"), win: { isSecureContext: false }, nav: {} });
  check("execCommand throws: resolves false, does not reject", ok === false);

  // What the Team chat copies.
  global.window = { JavisVisibleMarkdown: (s) => String(s).replace(/<!--[\s\S]*?-->/g, "") };
  const agent = { author_kind: "agent", content: "## Plan\n\n**Bold** [link](https://x.y)\n<!-- JAVIS_ASK: {} -->" };
  check("assistant message: the visible markdown, hidden blocks dropped",
    T.copyTextOf(agent) === "## Plan\n\n**Bold** [link](https://x.y)\n", JSON.stringify(T.copyTextOf(agent)));
  delete global.window;
  const owner = { author_kind: "owner", content: "> Lan: earlier answer\n\nmy reply" };
  check("owner message: own words, the quote of an earlier message dropped", T.copyTextOf(owner) === "my reply",
    JSON.stringify(T.copyTextOf(owner)));

  check("Team copyMsg uses the shared helper and reports success or failure",
    /var run = A && A\.copy \? A\.copy\(text\) : Promise\.resolve\(false\);/.test(teamsJs)
    && /setNote\(tw\(ok \? "teams\.copied" : "common\.copy_fail"\)\)/.test(teamsJs)
    && !/navigator\.clipboard/.test(teamsJs));
  check("main chat copyText goes through the shared helper", /function copyText\(s\) \{ return window\.JavisActs\.copy\(s\); \}/.test(app)
    && !/function copyFallback/.test(app));
  check("chat-render.js code and artifact copy use the shared helper when present",
    /window\.JavisActs && window\.JavisActs\.copy\) return window\.JavisActs\.copy\(text\)/.test(render));
  const v = (f) => Number((indexHtml.match(new RegExp(f.replace(".", "\\.") + "\\?v=(\\d+)")) || [])[1] || 0);
  check("cache bumped for chat-acts.js and teams.js", v("chat-acts.js") >= 5 && v("teams.js") >= 20);

  if (fails) { console.log("\nFAIL - test_copy_shared: " + fails); process.exit(1); }
  console.log("\nOK - test_copy_shared");
})();
