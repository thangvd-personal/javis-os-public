// The Copy button of a code block works in every container that renders markdown (main chat, Team chat, file viewer),
// not only inside the main chat's #chatArea. chat-render.js owns one delegated listener on the document.
//
//     node tests/js/test_code_copy_anywhere.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const D = path.join(__dirname, "..", "..", "dashboard");
const src = fs.readFileSync(path.join(D, "chat-render.js"), "utf8");
const actsSrc = fs.readFileSync(path.join(D, "chat-acts.js"), "utf8");
const app = fs.readFileSync(path.join(D, "app.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(D, "index.html"), "utf8");
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

// A minimal browser: chat-render.js only needs addEventListener on document and window while it loads.
function load(secure) {
  const listeners = { click: [] };
  const copied = [];
  const execs = [];
  const document = {
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    createElement() {
      return { style: {}, select() {}, remove() {}, set value(v) { this._v = v; }, get value() { return this._v; } };
    },
    querySelector() { return null; },
    execCommand(cmd) { execs.push(cmd); return true; },
    body: { appendChild(n) { execs.push("append:" + n.value); }, classList: { add() {}, remove() {} } },
  };
  const window = {
    t: (k) => (k === "app.copied" ? "Copied" : k),
    ic: (n) => "<svg data-ic=\"" + n + "\"></svg>",
    isSecureContext: secure,
    addEventListener() {},
  };
  const navigator = { clipboard: { writeText: (s) => { copied.push(s); return Promise.resolve(); } } };
  const ctx = { window, document, navigator, history: {}, setTimeout, clearTimeout, console, Promise };
  window.document = document;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  vm.runInContext(actsSrc, ctx);   // index.html loads chat-acts.js (the shared copy helper) after chat-render.js
  return { listeners, copied, execs, window };
}

// The button as chat-render.js draws it: .code-wrap > .code-head > button.code-copy, and a <pre> in the wrap.
function codeButton(container) {
  const pre = { innerText: "print(1)\n" };
  const wrap = { querySelector: (s) => (s === "pre" ? pre : null), parentNode: container };
  const btn = {
    textContent: "⧉ Copy", innerHTML: "⧉ Copy", tagName: "BUTTON",
    classList: { contains: (c) => c === "code-copy" },
    closest: (s) => (s === ".code-wrap" ? wrap : null),
  };
  return btn;
}

function click(env, target) {
  const e = { target, button: 0, preventDefault() {}, stopPropagation() {}, defaultPrevented: false };
  env.listeners.click.forEach((fn) => { try { fn(e); } catch (err) { /* other handlers need more DOM */ } });
}

(async function () {
  const env = load(true);
  check("chat-render.js registers click listeners on the document", env.listeners.click.length > 0);
  const btn = codeButton({ id: "team-msgs" });   // a Team message list, not #chatArea
  click(env, btn);
  await new Promise((r) => setTimeout(r, 5));
  check("clicking Copy of a code block in the Team chat copies the code", env.copied.length === 1 && env.copied[0] === "print(1)\n",
    JSON.stringify(env.copied));
  check("the button says Copied", /data-ic="check"/.test(btn.innerHTML) && /Copied/.test(btn.innerHTML), btn.innerHTML);

  const plain = load(false);
  const b2 = codeButton({});
  click(plain, b2);
  await new Promise((r) => setTimeout(r, 5));
  check("plain HTTP (no secure context): falls back to a hidden textarea and execCommand",
    plain.copied.length === 0 && plain.execs.indexOf("copy") >= 0 && plain.execs.indexOf("append:print(1)\n") >= 0,
    JSON.stringify(plain.execs));

  const other = load(true);
  click(other, { classList: { contains: () => false }, closest: () => null, tagName: "SPAN" });
  check("a click elsewhere copies nothing", other.copied.length === 0);

  check("app.js no longer carries its own code-copy handler on #chatArea", !/classList\.contains\("code-copy"\)/.test(app));
  check("the document-level handler lives in chat-render.js",
    /document\.addEventListener\("click", function \(e\) \{\s*var t = e\.target;\s*if \(!t \|\| !t\.classList \|\| !t\.classList\.contains\("code-copy"\)\) return;/.test(src));
  check("chat-render.js cache bumped", Number((indexHtml.match(/chat-render\.js\?v=(\d+)/) || [])[1] || 0) >= 22);

  if (fails) { console.log("\nFAIL - test_code_copy_anywhere: " + fails); process.exit(1); }
  console.log("\nOK - test_code_copy_anywhere");
})();
