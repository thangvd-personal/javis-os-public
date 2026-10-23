// Swipe to reply in the Team chat uses the main chat's gesture (dashboard/msg-swipe.js) instead of a hand-written copy:
// the bubble follows the finger, the quote mark shows, vertical moves keep scrolling, a wide code block scrolls itself.
// Own and assistant messages both reply, as before.
//
//     node tests/js/test_team_swipe.js
"use strict";
const fs = require("fs");
const path = require("path");
const D = path.join(__dirname, "..", "..", "dashboard");
const SW = require(path.join(D, "msg-swipe.js"));
const teamsJs = fs.readFileSync(path.join(D, "teams.js"), "utf8");
const css = fs.readFileSync(path.join(D, "console.css"), "utf8");
const style = fs.readFileSync(path.join(D, "style.css"), "utf8");
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

// The options teams.js passes to JavisMsgSwipe.bind, taken from the source and run with a recording startReply.
const m = teamsJs.match(/S\.unswipe = SW\.bind\(el, (\{[\s\S]*?\n      \})\);/);
check("teams.js binds msg-swipe.js on its page root", !!m);
if (!m) process.exit(1);
const replied = [];
const opts = new Function("startReply", "return " + m[1])((mid) => replied.push(mid));
check("options: Team message and bubble selectors", opts.selector === ".tm-msg" && opts.bubble === ".tm-bubble");

// A small fake DOM, as in test_msg_swipe.js.
function El(cls, parent) {
  this.className = cls; this.parentElement = parent || null; this.parentNode = parent || null; this.nodeType = 1;
  this.children = []; this.style = {}; this.listeners = {}; this.scrollWidth = 100; this.clientWidth = 100; this.attrs = {};
  if (parent) parent.children.push(this);
  const self = this;
  this.classList = {
    add: (...c) => { c.forEach((x) => { if (!self.has(x)) self.className += " " + x; }); },
    remove: (...c) => { self.className = self.className.split(" ").filter((x) => !c.includes(x)).join(" "); },
    contains: (c) => self.has(c),
    toggle: (c, on) => { if (on === undefined) on = !self.has(c); if (on) self.classList.add(c); else self.classList.remove(c); return on; },
  };
}
El.prototype.has = function (c) { return this.className.split(" ").includes(c); };
El.prototype.matches = function (sel) {
  return sel.split(",").some((one) => {
    one = one.trim();
    if (one.startsWith(".")) return this.has(one.slice(1).split("[")[0]);
    return this.className.split(" ")[0] === one;
  });
};
El.prototype.closest = function (sel) { for (let e = this; e; e = e.parentElement) if (e.matches(sel)) return e; return null; };
El.prototype.querySelector = function (sel) {
  for (const c of this.children) { if (c.matches(sel)) return c; const f = c.querySelector(sel); if (f) return f; }
  return null;
};
El.prototype.contains = function (x) { for (let e = x; e; e = e.parentElement) if (e === this) return true; return false; };
El.prototype.addEventListener = function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
El.prototype.removeEventListener = function (t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); };
El.prototype.appendChild = function (c) { c.parentElement = this; c.parentNode = this; this.children.push(c); return c; };
El.prototype.removeChild = function (c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = v; };
El.prototype.getAttribute = function (k) { return this.attrs[k] == null ? null : this.attrs[k]; };
El.prototype.fire = function (t, ev) { (this.listeners[t] || []).forEach((f) => f(ev)); };
global.document = { createElement: () => new El("span"), querySelector: () => null };
global.getComputedStyle = (el) => ({ overflowX: el.ox || "visible" });
Object.defineProperty(global, "navigator", { value: {}, configurable: true, writable: true });
global.setTimeout = (f) => f();

const root = new El("tm-page");
function msg(kind, id) {
  const el = new El("tm-msg tm-msg-" + kind, root);
  el.setAttribute("data-mid", String(id));
  const col = new El("tm-col", el);
  const bubble = new El("tm-bubble", col);
  const text = new El("tm-text", bubble);
  return { el, bubble, text };
}
const unbind = SW.bind(root, opts);
function swipe(target, pathXY, type) {
  root.fire("pointerdown", { pointerType: type || "touch", target, clientX: 300, clientY: 200 });
  for (const [x, y] of pathXY) root.fire("pointermove", { clientX: 300 + x, clientY: 200 + y });
}

const own = msg("owner", 11);
swipe(own.text, [[-20, 2], [-50, 3], [-80, 4]]);
check("own message: the bubble follows the finger and the quote mark shows",
  own.bubble.style.transform === "translateX(-80px)" && own.el.children.some((c) => c.className === "msg-swipe-mark"));
root.fire("pointerup", {});
check("own message: a full swipe replies to it", replied.join(",") === "11", replied.join(","));

const agent = msg("agent", 12);
swipe(agent.text, [[-20, 2], [-75, 4]]);
root.fire("pointerup", {});
check("assistant message: a full swipe replies to it", replied.join(",") === "11,12", replied.join(","));

swipe(agent.text, [[-20, 2], [-40, 3]]);
root.fire("pointerup", {});
check("a short swipe does nothing and the bubble springs back", replied.length === 2 && agent.bubble.style.transform === "");

swipe(agent.text, [[-3, 20], [-5, 70]]);
root.fire("pointerup", {});
check("a vertical move keeps scrolling", replied.length === 2 && !agent.bubble.style.transform);

swipe(agent.text, [[-20, 2], [-80, 4]], "mouse");
root.fire("pointerup", {});
check("a mouse drag selects text, never swipes", replied.length === 2);

const sheet = msg("agent", 13);
sheet.el.classList.add("is-sheet");
swipe(sheet.text, [[-20, 2], [-80, 4]]);
root.fire("pointerup", {});
check("a message with its keyboard sheet open is not swiped", replied.length === 2);

const wide = msg("agent", 14);
const pre = new El("pre", wide.bubble);
pre.scrollWidth = 700; pre.clientWidth = 280; pre.ox = "auto";
swipe(pre, [[-20, 2], [-80, 4]]);
root.fire("pointerup", {});
check("a touch in a wide code block scrolls that block (new: the old Team swipe stole it)", replied.length === 2);

unbind();
swipe(own.text, [[-20, 2], [-80, 4]]);
root.fire("pointerup", {});
check("leaving the page unbinds the gesture", replied.length === 2);

check("the hand-written Team swipe is gone", !/var SWIPE_AT = 60;/.test(teamsJs) && !/function \(fire\) \{\s*if \(!swipe\) return;/.test(teamsJs));
check("leave() unbinds the swipe", /if \(S\.unswipe\) \{ S\.unswipe\(\); S\.unswipe = null; \}/.test(teamsJs));
check("CSS: the old reply dot is gone; Team uses the main chat's quote mark",
  !/\.tm-msg\.is-armed::after/.test(css) && /\.tm-msg\.is-armed \.msg-swipe-mark \{ background: var\(--accent\)/.test(style));

if (fails) { console.log("\nFAIL - test_team_swipe: " + fails); process.exit(1); }
console.log("\nOK - test_team_swipe");
