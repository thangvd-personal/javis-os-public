/* Reload button between "Mở tab mới" and "Tải file về máy" in the file viewers
 * (console.js #noteEditor and file-editor.js modal).
 *
 *   node tests/js/test_reload_file_viewer.js
 *
 * Uses no em dash. */
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const CON = fs.readFileSync(path.join(ROOT, "dashboard", "console.js"), "utf8");
const FE = fs.readFileSync(path.join(ROOT, "dashboard", "file-editor.js"), "utf8");
const VI = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "vi.json"), "utf8"));
const EN = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "en.json"), "utf8"));

const fails = [];
function check(name, cond, detail) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !detail ? "" : " -> " + detail));
  if (!cond) fails.push(name);
}

function bocHam(src, dauChuKy) {
  const i = src.indexOf(dauChuKy);
  if (i < 0) return "";
  let depth = 0, start = -1;
  for (let k = i; k < src.length; k++) {
    const c = src[k];
    if (c === "{") { if (start < 0) start = k + 1; depth++; }
    else if (c === "}") { depth--; if (depth === 0) return src.slice(start, k); }
  }
  return "";
}

function el(tag) {
  return {
    tag,
    children: [],
    style: {},
    attributes: {},
    _html: "",
    title: "",
    type: "",
    className: "",
    onclick: null,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, f) { f ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k]; },
    appendChild(n) { this.children.push(n); return n; },
    set innerHTML(v) { this._html = String(v); },
    get innerHTML() { return this._html; },
  };
}

// 1. i18n keys
for (const k of ["cs.ne_reload", "cs.ne_reload_discard", "fedit.reload"]) {
  check("vi.json has " + k, typeof VI[k] === "string" && VI[k].length > 0);
  check("en.json has " + k, typeof EN[k] === "string" && EN[k].length > 0);
}

// 2. _neCommonBtns places Reload strictly between "Mở tab mới" and "Tải file về máy"
const thanCommon = bocHam(CON, "function _neCommonBtns(actions, rel, it) {");
check("extracted _neCommonBtns", thanCommon.length > 100);

const actions = el("span");
let reloaded = null;
const doc = {
  createElement: el,
  getElementById: (id) => (id === "neShare" ? el("div") : el("div")),
};
const win = {
  t: (k) => VI[k] || k,
  JavisShareBtn: () => el("button"),
};
const ic = (name) => `<svg data-ic="${name}"></svg>`;
const esc = (s) => String(s == null ? "" : s);

const fn = new Function(
  "actions", "rel", "it", "document", "window", "ic", "esc",
  "_neRenameCur", "_neDeleteCur", "_neReloadCur", "_vtRaw", "_dlFile", "_neSyncFull", "closeNote", "X_ICON", "fbrain",
  thanCommon);
fn(actions, "notes/test.md", { name: "test.md", ext: ".md", type: "file" },
  doc, win, ic, esc,
  () => {}, () => {}, (rel, it) => { reloaded = { rel, it }; }, (r) => "/files/raw?path=" + r, () => {}, () => {}, () => {}, "<svg></svg>", () => "brain");

const titles = actions.children.map((b) => b.title || "");
const iOpenTab = titles.indexOf("Mở tab mới");
const iReload = titles.indexOf("Tải lại nội dung file");
const iDownload = titles.indexOf("Tải file về máy");
check("button order: Mở tab mới < Tải lại < Tải file về máy",
  iOpenTab >= 0 && iReload === iOpenTab + 1 && iDownload === iReload + 1,
  JSON.stringify(titles));

const btnReload = actions.children[iReload];
check("reload button uses rotate-cw icon", /data-ic="rotate-cw"/.test(btnReload.innerHTML));
check("reload button has type=button and aria-label",
  btnReload.type === "button" && btnReload.getAttribute("aria-label") === "Tải lại nội dung file");

btnReload.onclick();
check("clicking reload calls _neReloadCur with rel and it",
  reloaded && reloaded.rel === "notes/test.md" && reloaded.it && reloaded.it.name === "test.md");

// 3. _neReloadCur behavior: discards local draft only on confirm if dirty, preserves mode, calls openNote with reload: true
const thanReload = bocHam(CON, "async function _neReloadCur(rel, it) {");
check("extracted _neReloadCur", thanReload.length > 50);

async function runReloadCase({ dirty, coSua, confirmAns, bodyCls }) {
  let dropArgs = [];
  let opened = null;
  let confirmCalls = 0;
  const bodyEl = el("div");
  if (bodyCls) bodyCls.forEach((c) => bodyEl.classList.add(c));
  const rf = new Function(
    "rel", "it", "_neAuto", "_neCoSuaChua", "confirm", "window", "_neDropAssist", "document", "openNote",
    "return (async () => {" + thanReload + "})();");
  await rf(
    "a/b.md", { name: "b.md", ext: ".md" },
    { dirty: () => dirty },
    () => coSua,
    () => { confirmCalls++; return confirmAns; },
    { t: (k) => VI[k] || k },
    async (d) => { dropArgs.push(d); },
    { getElementById: (id) => (id === "neBody" ? bodyEl : null) },
    async (rel, it, opts) => { opened = { rel, it, opts }; }
  );
  return { dropArgs, opened, confirmCalls };
}

(async () => {
  const clean = await runReloadCase({ dirty: false, coSua: false, confirmAns: false, bodyCls: ["mode-source"] });
  check("clean file reloads immediately without confirm",
    clean.confirmCalls === 0 && clean.dropArgs[0] === true && clean.opened && clean.opened.opts.reload === true && clean.opened.opts.mode === "source");

  const dirtyCancel = await runReloadCase({ dirty: true, coSua: false, confirmAns: false, bodyCls: ["mode-wys"] });
  check("dirty file + cancel confirm aborts reload",
    dirtyCancel.confirmCalls === 1 && dirtyCancel.dropArgs.length === 0 && dirtyCancel.opened === null);

  const dirtyOk = await runReloadCase({ dirty: true, coSua: true, confirmAns: true, bodyCls: ["mode-preview"] });
  check("dirty file + accept confirm drops draft (discard=true) and reloads preserving mode",
    dirtyOk.confirmCalls === 1 && dirtyOk.dropArgs[0] === true && dirtyOk.opened && dirtyOk.opened.opts.reload === true && dirtyOk.opened.opts.mode === "preview");

  // 4. openNote cache-busting & no-store fetch
  check("console.js openNote fetches /files/read with cache: no-store",
    /fetch\(`\/files\/read\?brain=.*`,\s*\{\s*cache:\s*"no-store"\s*\}\)/.test(CON));
  check("console.js openNote passes reload flag to _neDropAssist",
    /await _neDropAssist\(!!\(opts && opts\.reload\)\)/.test(CON));
  check("console.js openNote busts cache for image/frame raw URLs on reload",
    /opts && opts\.reload \? "&_r=" \+ Date\.now\(\) : ""/.test(CON));

  // 5. file-editor.js consistency
  check("file-editor.js defines reloadBtn with fedit.reload",
    /function reloadBtn\(fn\)/.test(FE) && /tw\("fedit\.reload"\)/.test(FE));
  check("file-editor.js places reloadBtn between openLink and dlLink in all views",
    /openLink\(b, ceil\)\);\s*elActions\.appendChild\(reloadBtn\(reloadCurrent\)\);\s*elActions\.appendChild\(dlLink\(b, ceil\)\)/.test(FE)
    && /openLinkUrl\(url\)\);\s*elActions\.appendChild\(reloadBtn\(reloadCurrent\)\);\s*elActions\.appendChild\(dlLinkUrl\(url, ten\)\)/.test(FE));
  check("file-editor.js fetches /files/read with cache: no-store",
    /fetch\("\/files\/read\?brain=.*\{\s*cache:\s*"no-store"\s*\}\)/.test(FE));

  console.log("");
  if (fails.length) {
    console.log("FAIL: " + fails.length + " errors: " + fails.join(", "));
    process.exit(1);
  }
  console.log("OK - test_reload_file_viewer: all checks passed");
})();

