/* Test CTA xac nhan & cap nhat lien ket khi click link tro toi file khong ton tai
   trong mot tep da luu trong brain (dashboard/console.js).

       node tests/js/test_missing_link_confirm.js
*/
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..", "..");
const CONSOLE_PATH = path.join(ROOT, "dashboard", "console.js");
const CONSOLE = fs.readFileSync(CONSOLE_PATH, "utf8");
const STYLE = fs.readFileSync(path.join(ROOT, "dashboard", "style.css"), "utf8");
const VI = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "vi.json"), "utf8"));
const EN = JSON.parse(fs.readFileSync(path.join(ROOT, "dashboard", "i18n", "en.json"), "utf8"));

const fails = [];
function check(name, cond) {
  console.log((cond ? "ok   " : "FAIL ") + name);
  if (!cond) fails.push(name);
}

// ============================================================
// 1. i18n keys, CSS & khong dung em-dash
// ============================================================
const KEYS = [
  "cs.ne_miss_confirm",
  "cs.ne_miss_confirm_title",
  "cs.ne_miss_updating",
  "cs.ne_miss_updated",
  "cs.ne_miss_update_notfound",
  "cs.ne_miss_preview",
  "cs.ne_miss_preview_title",
  "cs.ne_miss_preview_err",
  "cs.ne_miss_preview_open",
  "cs.ne_miss_preview_binary",
  "cs.ne_miss_preview_empty",
];
for (const k of KEYS) {
  check(`vi.json co ${k}`, typeof VI[k] === "string" && VI[k].length > 0);
  check(`en.json co ${k}`, typeof EN[k] === "string" && EN[k].length > 0);
  check(`en.json ${k} khong chua tieng Viet`, !/[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/i.test(EN[k] || ""));
  check(`i18n ${k} khong co em-dash`, !/\u2014/.test(VI[k] || "") && !/\u2014/.test(EN[k] || ""));
}
check("style.css co .ne-hit-row, .ne-hit-preview, .ne-hit-confirm va .ne-prev-dlg",
  STYLE.includes(".ne-hit-row") && STYLE.includes(".ne-hit-preview") &&
  STYLE.includes(".ne-hit-confirm") && STYLE.includes(".ne-prev-dlg"));
check(".ne-hit-row giu 2 CTA cung 1 hang (flex-wrap: nowrap)",
  /\.ne-hit-row\s*\{[^}]*flex-wrap:\s*nowrap/.test(STYLE) &&
  /\.ne-hits\s+button\.ne-hit-preview\s*\{[^}]*flex:\s*none[^}]*white-space:\s*nowrap/.test(STYLE) &&
  /\.ne-hits\s+button\.ne-hit-confirm\s*\{[^}]*flex:\s*none[^}]*white-space:\s*nowrap/.test(STYLE));

// ============================================================
// 2. Unit test _neReplaceLinkInMarkdown trich truc tiep tu console.js
// ============================================================
const fnStart = CONSOLE.indexOf("function _neReplaceLinkInMarkdown(");
const fnEnd = CONSOLE.indexOf("async function _neConfirmHit(");
check("tim thay _neReplaceLinkInMarkdown trong console.js", fnStart > 0 && fnEnd > fnStart);
const fnCode = CONSOLE.slice(fnStart, fnEnd);
const ctx = vm.createContext({ window: {} });
vm.runInContext(fnCode + "\nthis.replaceLink = _neReplaceLinkInMarkdown;", ctx);
const replaceLink = ctx.replaceLink;

// 2a. Markdown link co ban
check("cap nhat link markdown co ban",
  replaceLink(
    "Xem [tai lieu](facts/img-uu-tien-khao-sat.md) nhe.",
    { rawTarget: "facts/img-uu-tien-khao-sat.md", isCode: false, isWiki: false },
    "memory/facts/img-uu-tien-khao-sat.md"
  ) === "Xem [tai lieu](memory/facts/img-uu-tien-khao-sat.md) nhe.");

// 2b. Link co ./ o dau
check("cap nhat link co ./ o dau",
  replaceLink(
    "[x](./facts/img.md)",
    { rawTarget: "facts/img.md", isCode: false, isWiki: false },
    "memory/facts/img.md"
  ) === "[x](memory/facts/img.md)");

// 2c. Link co %20
check("cap nhat link ma hoa %20",
  replaceLink(
    "[x](facts/my%20note.md)",
    { rawTarget: "facts/my note.md", isCode: false, isWiki: false },
    "memory/facts/my note.md"
  ) === "[x](memory/facts/my note.md)");

// 2d. Link boc ngoac nhon <>
check("cap nhat link boc ngoac nhon <> giu nguyen <>",
  replaceLink(
    "[x](<facts/my note.md>)",
    { rawTarget: "facts/my note.md", isCode: false, isWiki: false },
    "memory/facts/my note.md"
  ) === "[x](<memory/facts/my note.md>)");

// 2e. Link co title phia sau
check("giu nguyen title sau link",
  replaceLink(
    '[x](facts/a.md "Ghi chu")',
    { rawTarget: "facts/a.md", isCode: false, isWiki: false },
    "memory/facts/a.md"
  ) === '[x](memory/facts/a.md "Ghi chu")');

// 2f. Link co dau ngoac don can bang trong ten file
check("cap nhat link co ngoac don () trong ten file",
  replaceLink(
    "[x](facts/Note (v1).md)",
    { rawTarget: "facts/Note (v1).md", isCode: false, isWiki: false },
    "memory/facts/Note (v1).md"
  ) === "[x](memory/facts/Note (v1).md)");

// 2g. Nhan trung duong dan cu -> cap nhat ca nhan
check("nhan trung het duong dan cu cung duoc cap nhat",
  replaceLink(
    "[facts/a.md](facts/a.md)",
    { rawTarget: "facts/a.md", isCode: false, isWiki: false },
    "memory/facts/a.md"
  ) === "[memory/facts/a.md](memory/facts/a.md)");

// 2h. Inline code path `facts/a.md`
check("cap nhat inline code path khi isCode=true",
  replaceLink(
    "Mo `facts/a.md` de xem.",
    { rawTarget: "facts/a.md", isCode: true, isWiki: false },
    "memory/facts/a.md"
  ) === "Mo `memory/facts/a.md` de xem.");

// 2i. Wikilink [[facts/a|Nhan]]
check("cap nhat wikilink giu nguyen alias",
  replaceLink(
    "Xem [[facts/a|Nhan]] nha.",
    { rawTarget: "facts/a", isCode: false, isWiki: true },
    "memory/facts/a.md"
  ) === "Xem [[memory/facts/a.md|Nhan]] nha.");

// 2j. Khong dung vao anh ![alt](...) va khoi code fence ```...```
const sampleComplex = [
  "Anh: ![hinh](facts/a.md)",
  "```md",
  "[vi du](facts/a.md)",
  "```",
  "Link that: [mo](facts/a.md)",
].join("\n");
const expectedComplex = [
  "Anh: ![hinh](facts/a.md)",
  "```md",
  "[vi du](facts/a.md)",
  "```",
  "Link that: [mo](memory/facts/a.md)",
].join("\n");
check("khong sua anh ![]() hay khoi code fence ```...```",
  replaceLink(
    sampleComplex,
    { rawTarget: "facts/a.md", isCode: false, isWiki: false },
    "memory/facts/a.md"
  ) === expectedComplex);

// 2k. Link khong khop -> tra nguyen noi dung
check("link khong khop tra ve nguyen van",
  replaceLink(
    "[mo](other/b.md)",
    { rawTarget: "facts/a.md", isCode: false, isWiki: false },
    "memory/facts/a.md"
  ) === "[mo](other/b.md)");

// ============================================================
// 3. Flow test _neTimFileGan + _neConfirmHit trong VM gia lap DOM
// ============================================================
function makeEl(tag) {
  const el = {
    id: "",
    tagName: tag.toUpperCase(),
    className: "",
    children: [],
    parentNode: null,
    _innerHTML: "",
    _textContent: "",
    disabled: false,
    title: "",
    type: "",
    isConnected: true,
    onclick: null,
    addEventListener() {},
    appendChild(ch) { ch.parentNode = this; this.children.push(ch); return ch; },
    remove() {
      this.isConnected = false;
      if (this.parentNode) {
        const i = this.parentNode.children.indexOf(this);
        if (i >= 0) this.parentNode.children.splice(i, 1);
      }
    },
    querySelector(sel) {
      if (this._sub && this._sub[sel]) return this._sub[sel];
      return this.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel) {
      const out = [];
      const m = /\.([a-zA-Z0-9_-]+)/.exec(sel || "");
      const cls = m ? m[1] : "";
      const walk = (n) => {
        for (const c of (n.children || [])) {
          if (!cls || (c.className || "").split(/\s+/).includes(cls)) out.push(c);
          walk(c);
        }
      };
      walk(this);
      return out;
    },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return el._innerHTML; },
    set(v) {
      el._innerHTML = String(v);
      el.children = [];
      if (el._innerHTML.includes("ne-prev-dlg-body")) {
        el._sub = {
          ".ne-prev-dlg-scrim": makeEl("div"),
          ".ne-prev-dlg-x": makeEl("button"),
          ".ne-prev-dlg-close": makeEl("button"),
          ".ne-prev-dlg-open": makeEl("button"),
          ".ne-prev-dlg-confirm": el._innerHTML.includes("ne-prev-dlg-confirm") ? makeEl("button") : null,
          ".ne-prev-dlg-body": makeEl("div"),
        };
      }
    },
  });
  Object.defineProperty(el, "textContent", {
    get() { return el._textContent; },
    set(v) { el._textContent = String(v); el._innerHTML = String(v); },
  });
  return el;
}

(async () => {
  const fnConfirmStart = CONSOLE.indexOf("function _neRenderMissing(");
  const blockEnd = CONSOLE.indexOf("// Nạp turndown", fnConfirmStart);
  const block = CONSOLE.slice(fnConfirmStart, blockEnd);

  let diskFiles = {
    "memory/notes/index.md": "Doc [khao sat](facts/img-uu-tien.md) chi tiet.",
    "memory/facts/img-uu-tien.md": "# Ban moi\nNoi dung chinh trong thu muc memory.",
    "archive/facts/img-uu-tien.md": "# Ban cu\nNoi dung luu tru trong archive.",
    "wiki/Guide.md": "# Guide\nNoi dung guide o goc brain.",
    "memory/same.md": "# Memory same",
    "same.md": "# Root same",
  };
  let diskDirs = new Set(["memory", "memory/facts", "memory/notes", "archive", "archive/facts", "wiki"]);
  let openedCalls = [];
  let vaultOpenedCalls = [];
  let filesAtCalls = [];
  let revealedCalls = [];
  let toasts = [];

  class FakeFormData {
    constructor() { this.fields = {}; }
    append(k, v) { this.fields[k] = v; }
  }

  const fakeBody = makeEl("body");
  const fakeDoc = {
    body: fakeBody,
    createElement: (t) => makeEl(t),
    getElementById: (id) => fakeBody.children.find(c => c.id === id) || null,
    querySelectorAll: (sel) => {
      if (sel.includes("#nePreviewDlg")) {
        const dlg = fakeBody.children.find(c => c.id === "nePreviewDlg");
        return dlg && dlg._sub && dlg._sub[".ne-prev-dlg-confirm"] ? [dlg._sub[".ne-prev-dlg-confirm"]] : [];
      }
      return [];
    },
  };

  const sandbox = vm.createContext({
    FormData: FakeFormData,
    document: fakeDoc,
    window: {
      t: (k, vars) => {
        let s = VI[k] || k;
        if (vars) for (const [vk, vv] of Object.entries(vars)) s = s.replace(`{${vk}}`, String(vv));
        return s;
      },
      mdToHtml: (txt) => `<p>${txt}</p>`,
    },
    ic: (name) => `[ic:${name}]`,
    esc: (s) => String(s == null ? "" : s),
    _fileIcon: () => "[ico]",
    CHECK_ICON: "[v]",
    X_ICON: "[x]",
    VT_IMG_EXTS: [".png", ".jpg"],
    VT_FRAME_EXTS: [".pdf"],
    VT_TEXT_EXTS: [".md", ".txt"],
    _vtRaw: (p) => `/files/raw?path=${encodeURIComponent(p)}`,
    fbrain: () => "brain",
    _vtHome: "",
    _vtChuanHoaRel: (p) => String(p || "").replace(/^\/+/, ""),
    _neLichSu: [
      { rel: "memory/notes/index.md", name: "index.md", ext: ".md" },
      { rel: "facts/img-uu-tien.md", name: "img-uu-tien.md", ext: ".md" },
    ],
    _neViTri: 1,
    _neDangDiLichSu: false,
    _nePendingLink: null,
    _goFileUri: (p) => String(p == null ? "" : p).replace(/^file:(?:\/\/[^/\\]*)?(?=[/\\])/i, "").replace(/^\/+/, ""),
    openVaultPath: (p) => vaultOpenedCalls.push(p),
    openFilesAt: (p) => filesAtCalls.push(p),
    toast: (msg, isErr) => toasts.push({ msg, isErr: !!isErr }),
    openNote: (p, it) => openedCalls.push({ p, it }),
    _vtRevealInTree: (p) => revealedCalls.push(p),
    _neSyncFull: () => {},
    closeNote: () => {},
    fetch: async (url, init) => {
      if (url.startsWith("/files/resolve")) {
        const m = /[?&]path=([^&]+)/.exec(url);
        const p = decodeURIComponent(m[1]);
        if (diskFiles[p] != null) return { ok: true, status: 200, json: async () => ({ ok: true, path: p }) };
        return { ok: false, status: 400, json: async () => ({ ok: false, error: "not_found" }) };
      }
      if (url.startsWith("/files/list")) {
        const m = /[?&]path=([^&]+)/.exec(url);
        const p = decodeURIComponent(m[1]).replace(/\/+$/, "");
        if (diskDirs.has(p)) return { ok: true, status: 200, json: async () => ({ items: [], focus: "", missing: "" }) };
        return { ok: true, status: 200, json: async () => ({ items: [], focus: "", missing: p }) };
      }
      if (url.startsWith("/files/search")) {
        return {
          ok: true,
          json: async () => ({
            items: [
              { path: "memory/facts/img-uu-tien.md", name: "img-uu-tien.md", ext: ".md" },
              { path: "archive/facts/img-uu-tien.md", name: "img-uu-tien.md", ext: ".md" },
            ],
          }),
        };
      }
      if (url.startsWith("/files/read")) {
        const m = /[?&]path=([^&]+)/.exec(url);
        const p = decodeURIComponent(m[1]);
        if (diskFiles[p] == null) return { ok: false, status: 404, json: async () => ({ error: "not found" }) };
        return { ok: true, status: 200, json: async () => ({ content: diskFiles[p] }) };
      }
      if (url === "/files/write" && init && init.body) {
        const { path: p, content } = init.body.fields;
        diskFiles[p] = content;
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return { ok: false, status: 500, json: async () => ({}) };
    },
  });
  vm.runInContext(block, sandbox);

  // 3a. Khi co linkOrigin -> moi goi y co: nut mo file + CTA Xem truoc + CTA Xac nhan
  const hostWithOrigin = makeEl("div");
  await sandbox._neTimFileGan("img-uu-tien.md", hostWithOrigin, {
    sourceRel: "memory/notes/index.md",
    rawTarget: "facts/img-uu-tien.md",
    isCode: false,
    isWiki: false,
  });
  check("tao 2 dong goi y (.ne-hit-row)", hostWithOrigin.children.length === 2);
  check("dong 1 co du 3 nut: mo file, Xem truoc, Xac nhan",
    hostWithOrigin.children[0].children.length === 3 &&
    hostWithOrigin.children[0].children[0].className === "ne-hit-open" &&
    hostWithOrigin.children[0].children[1].className === "ne-hit-preview" &&
    hostWithOrigin.children[0].children[2].className === "ne-hit-confirm");
  check("nut mo file co title duong dan day du va boc ten trong .ne-hit-name",
    hostWithOrigin.children[0].children[0].title === "memory/facts/img-uu-tien.md" &&
    hostWithOrigin.children[0].children[0]._innerHTML.includes('class="ne-hit-name"'));
  check("nut CTA Xem truoc hien dung nhan & icon eye",
    hostWithOrigin.children[0].children[1]._innerHTML.includes("Xem trước") &&
    hostWithOrigin.children[0].children[1]._innerHTML.includes("[ic:eye]"));
  check("nut CTA Xac nhan hien chu Xac nhan",
    hostWithOrigin.children[0].children[2]._innerHTML.includes("Xác nhận"));

  // 3b. Bam Xem truoc cho goi y thu 2 (archive) -> mo popup dialog xem nhanh dung noi dung tep do
  await hostWithOrigin.children[1].children[1].onclick();
  const dlg2 = fakeDoc.getElementById("nePreviewDlg");
  check("bam Xem truoc mo popup dialog #nePreviewDlg",
    !!dlg2 && dlg2._innerHTML.includes("archive/facts/img-uu-tien.md"));
  check("popup dialog xem nhanh hien dung noi dung tep archive",
    dlg2._sub[".ne-prev-dlg-body"]._innerHTML.includes("Noi dung luu tru trong archive."));
  check("popup dialog co nut Xac nhan khi co linkOrigin",
    !!dlg2._sub[".ne-prev-dlg-confirm"]);
  // Dong popup bang nut Dong
  dlg2._sub[".ne-prev-dlg-close"].onclick();
  check("bam nut Dong tat popup dialog xem nhanh", !fakeDoc.getElementById("nePreviewDlg"));

  // 3c. Mo Xem truoc cho goi y thu 1 (memory) roi bam Xac nhan ngay trong popup -> cap nhat link & dong popup
  await hostWithOrigin.children[0].children[1].onclick();
  const dlg1 = fakeDoc.getElementById("nePreviewDlg");
  check("popup dialog goi y 1 hien dung noi dung tep memory",
    !!dlg1 && dlg1._sub[".ne-prev-dlg-body"]._innerHTML.includes("Noi dung chinh trong thu muc memory."));
  await dlg1._sub[".ne-prev-dlg-confirm"].onclick();
  check("xac nhan trong popup da dong popup xem nhanh", !fakeDoc.getElementById("nePreviewDlg"));
  check("file nguon da duoc cap nhat dung duong dan moi",
    diskFiles["memory/notes/index.md"] === "Doc [khao sat](memory/facts/img-uu-tien.md) chi tiet.");
  check("da mo file da xac nhan",
    openedCalls.length === 1 && openedCalls[0].p === "memory/facts/img-uu-tien.md");
  check("lich su buoc hien tai duoc thay bang file da xac nhan",
    sandbox._neLichSu[1].rel === "memory/facts/img-uu-tien.md");
  check("co thong bao toast thanh cong",
    toasts.some(t => !t.isErr && t.msg.includes("memory/facts/img-uu-tien.md")));

  // 3d. Khi khong co linkOrigin (vi du bam tu khung Chat) -> van co nut mo + Xem truoc, KHONG hien CTA Xac nhan
  const hostNoOrigin = makeEl("div");
  await sandbox._neTimFileGan("img-uu-tien.md", hostNoOrigin, null);
  check("khong co file nguon thi co nut mo + Xem truoc, khong co CTA Xac nhan",
    hostNoOrigin.children.length === 2 &&
    hostNoOrigin.children[0].children.length === 2 &&
    hostNoOrigin.children[0].children[0].className === "ne-hit-open" &&
    hostNoOrigin.children[0].children[1].className === "ne-hit-preview");
  await hostNoOrigin.children[0].children[1].onclick();
  const dlgNoOrigin = fakeDoc.getElementById("nePreviewDlg");
  check("popup khi khong co file nguon van xem truoc duoc va khong co nut Xac nhan",
    !!dlgNoOrigin && !dlgNoOrigin._sub[".ne-prev-dlg-confirm"]);
  dlgNoOrigin._sub[".ne-prev-dlg-x"].onclick();
  check("nut X dong duoc popup", !fakeDoc.getElementById("nePreviewDlg"));

  // ============================================================
  // 4. Giai duong dan tuong doi theo thu muc cha cua _neOpenRel trong #noteEditor
  // ============================================================
  check("_neGhepDuong thu gon . va .. dung chuan",
    sandbox._neGhepDuong("memory/notes", "./a.md") === "memory/notes/a.md" &&
    sandbox._neGhepDuong("memory/notes", "../facts/a.md") === "memory/facts/a.md" &&
    sandbox._neGhepDuong("memory", "../../ngoai.md") === null);

  check("_neUngVienDuongDan uu tien thu muc cha cua _neOpenRel truoc goc brain",
    JSON.stringify(sandbox._neUngVienDuongDan("facts/img-uu-tien.md", "memory/MEMORY.md")) ===
      JSON.stringify(["memory/facts/img-uu-tien.md", "facts/img-uu-tien.md"]) &&
    JSON.stringify(sandbox._neUngVienDuongDan("../wiki/Guide.md", "memory/MEMORY.md")) ===
      JSON.stringify(["wiki/Guide.md"]));

  // 4a. memory/MEMORY.md bam link facts/img-uu-tien.md -> tim thay o memory/facts/img-uu-tien.md
  vaultOpenedCalls.length = 0;
  await sandbox._neMoLinkTuEditor("facts/img-uu-tien.md", "memory/MEMORY.md", {
    sourceRel: "memory/MEMORY.md",
    rawTarget: "facts/img-uu-tien.md",
    isCode: false,
    isWiki: false,
    time: Date.now(),
  });
  check("bam link facts/... trong memory/MEMORY.md mo dung memory/facts/...",
    vaultOpenedCalls.length === 1 && vaultOpenedCalls[0] === "memory/facts/img-uu-tien.md");

  // 4b. memory/MEMORY.md bam link wiki/Guide.md (chi ton tai o goc brain) -> lui ve goc brain
  vaultOpenedCalls.length = 0;
  await sandbox._neMoLinkTuEditor("wiki/Guide.md", "memory/MEMORY.md", {
    sourceRel: "memory/MEMORY.md",
    rawTarget: "wiki/Guide.md",
    isCode: false,
    isWiki: false,
    time: Date.now(),
  });
  check("bam link goc brain (wiki/Guide.md) trong memory/MEMORY.md van mo dung goc brain",
    vaultOpenedCalls.length === 1 && vaultOpenedCalls[0] === "wiki/Guide.md");

  // 4c. memory/MEMORY.md bam link memory/facts/img-uu-tien.md -> khong bi ghep dup memory/memory/
  vaultOpenedCalls.length = 0;
  await sandbox._neMoLinkTuEditor("memory/facts/img-uu-tien.md", "memory/MEMORY.md", {
    sourceRel: "memory/MEMORY.md",
    rawTarget: "memory/facts/img-uu-tien.md",
    isCode: false,
    isWiki: false,
    time: Date.now(),
  });
  check("link da co tien to memory/facts/... trong memory/MEMORY.md van mo dung",
    vaultOpenedCalls.length === 1 && vaultOpenedCalls[0] === "memory/facts/img-uu-tien.md");

  // 4d. Khi ca thu muc cha va goc brain deu co file trung ten -> uu tien thu muc chua file dang mo
  vaultOpenedCalls.length = 0;
  await sandbox._neMoLinkTuEditor("same.md", "memory/MEMORY.md", {
    sourceRel: "memory/MEMORY.md",
    rawTarget: "same.md",
    isCode: false,
    isWiki: false,
    time: Date.now(),
  });
  check("trung ten o ca thu muc cha va goc brain -> uu tien file cung thu muc cha",
    vaultOpenedCalls.length === 1 && vaultOpenedCalls[0] === "memory/same.md");

  // 4e. Link tro toi thu muc tuong doi facts/ trong memory/MEMORY.md -> mo memory/facts/
  filesAtCalls.length = 0;
  await sandbox._neMoLinkTuEditor("facts/", "memory/MEMORY.md", null);
  check("link thu muc tuong doi facts/ trong memory/MEMORY.md mo memory/facts/",
    filesAtCalls.length === 1 && filesAtCalls[0] === "memory/facts/");

  // 4f. Link khong ton tai o ca hai noi -> tra ve ung vien goc de man 404 + goi y + CTA Xac nhan chay tiep
  vaultOpenedCalls.length = 0;
  const missPending = {
    sourceRel: "memory/MEMORY.md",
    rawTarget: "facts/khong-co.md",
    isCode: false,
    isWiki: false,
    time: Date.now(),
  };
  await sandbox._neMoLinkTuEditor("facts/khong-co.md", "memory/MEMORY.md", missPending);
  check("link that su bi thieu van di qua openVaultPath va giu resolvedTargets cho CTA Xac nhan",
    vaultOpenedCalls.length === 1 &&
    vaultOpenedCalls[0] === "facts/khong-co.md" &&
    Array.isArray(sandbox._nePendingLink.resolvedTargets) &&
    sandbox._nePendingLink.resolvedTargets.includes("memory/facts/khong-co.md"));

  check("console.js noi su kien click a.jv-floc trong #noteEditor qua _neMoLinkTuEditor",
    CONSOLE.includes("_neMoLinkTuEditor(vp, _neOpenRel, pending)"));

  console.log("");
  if (fails.length) {
    console.log(`FAIL - ${fails.length} loi: ${fails.join(", ")}`);
    process.exit(1);
  }
  console.log("OK - test_missing_link_confirm: tat ca pass");
})();

