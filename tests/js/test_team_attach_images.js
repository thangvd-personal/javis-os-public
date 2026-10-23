// Pictures the owner sent in the Team chat open in the in-app viewer (chat-render.js lightbox), a picture that cannot load
// becomes the "not available" box, and every picture carries the message time as its version so an overwritten file is
// not shown from the browser cache (main chat parity, 0.65.33 anhV).
//
//     node tests/js/test_team_attach_images.js
"use strict";
const fs = require("fs");
const path = require("path");
const D = path.join(__dirname, "..", "..", "dashboard");
const T = require(path.join(D, "teams.js"));
const render = fs.readFileSync(path.join(D, "chat-render.js"), "utf8");
const css = fs.readFileSync(path.join(D, "console.css"), "utf8");
const vi = JSON.parse(fs.readFileSync(path.join(D, "i18n", "vi.json"), "utf8"));
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "ok   " : "FAIL ") + name + (cond || !extra ? "" : "  " + extra));
  if (!cond) fails++;
}

let h = T.fileChipHtml({ path: "attachments/a b.png", name: "Ảnh <1>.png", image: true }, 1792000000);
check("picture chip: the lightbox link class and the sender's file name",
  /class="tm-file is-img jv-img-link"/.test(h) && /data-img-ten="Ảnh &lt;1&gt;\.png"/.test(h), h);
check("picture chip: the lightbox opens what the selector in chat-render.js looks for",
  /closest\("a\.jv-img-link"\)/.test(render) && /getAttribute\("data-img-ten"\)/.test(render));
check("picture chip: a picture that fails becomes the main chat's box (jvImgGone)", /onerror="jvImgGone\(this\)"/.test(h)
  && /window\.jvImgGone = imgGone;/.test(render));
check("picture chip: versioned by the message time", /path=attachments%2Fa%20b\.png&amp;v=1792000000"/.test(h), h);
check("picture chip: zoom hint", h.includes('title="' + vi["chat.att_zoom"] + '"'));
h = T.fileChipHtml({ path: "docs/report.pdf", name: "report.pdf" }, 0);
check("other file: a plain link, no lightbox, no version when the time is unknown",
  /^<a class="tm-file" href="\/files\/raw\?brain=brain&amp;path=docs%2Freport\.pdf"/.test(h) && !/jv-img-link/.test(h), h);

// An owner's message renders its files with the message time.
h = T.bodyHtml({ author_kind: "owner", ts: 1792000123, content: "x\n\n📎 p.png", meta: { files: [{ path: "p.png", image: true }] } }, true);
check("owner message: chips carry the message time", /&amp;v=1792000123/.test(h), h);

// An assistant's markdown is drawn with the message time as anhV.
let seen = null;
global.window = { mdToHtml: (t, b, o) => { seen = o; return "<p>" + t + "</p>"; } };
T.bodyHtml({ author_kind: "agent", ts: 1792000456, content: "![x](a.png)" }, false);
check("assistant message: mdToHtml gets the message time as anhV", seen && seen.anhV === "1792000456", JSON.stringify(seen));
seen = "unset";
T.bodyHtml({ author_kind: "agent", content: "hi" }, false);
check("assistant message without a time: no anhV", seen === undefined);
delete global.window;

check("CSS: a failed picture shows one name, not two", /\.tm-file\.is-img \.chat-img-gone \+ span \{ display: none; \}/.test(css));

if (fails) { console.log("\nFAIL - test_team_attach_images: " + fails); process.exit(1); }
console.log("\nOK - test_team_attach_images");
