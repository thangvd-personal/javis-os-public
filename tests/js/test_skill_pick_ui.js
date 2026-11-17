// Test logic của renderSkillPick với skill chưa cài đặt
const assert = require("assert");

function runTest() {
  const t = (k, p) => {
    const dict = {
      "studio.sp_count": "đã chọn " + (p ? p.a + "/" + p.b : ""),
      "studio.sp_uninstalled_group": "Chưa cài đặt",
      "studio.sp_uninstalled_tag": "chưa cài đặt",
      "studio.sp_uninstalled_desc": "Kỹ năng chưa có trong bộ não",
    };
    return dict[k] || k;
  };
  const _spNoAccent = (s) => (s || "").toLowerCase();
  const ic = (name) => `<i class="ic-${name}"></i>`;
  const esc = (s) => s;
  const LOC = () => "vi";

  // Giả lập DOM
  function createElement(tag) {
    const el = {
      tagName: tag.toUpperCase(),
      className: "",
      innerHTML: "",
      textContent: "",
      title: "",
      value: "",
      checked: false,
      children: [],
      classList: {
        toggle(cls, v) {
          if (v === undefined) {
            if (el.className.includes(cls)) el.className = el.className.replace(cls, "").trim();
            else el.className += " " + cls;
          } else if (v) {
            if (!el.className.includes(cls)) el.className += " " + cls;
          } else {
            el.className = el.className.replace(cls, "").trim();
          }
        },
        has(cls) { return el.className.includes(cls); }
      },
      appendChild(c) { el.children.push(c); return c; },
      querySelector(sel) {
        if (sel === "#skillPick") return el._skillPick || (el._skillPick = createElement("div"));
        if (sel === "#spCount") return el._spCount || (el._spCount = createElement("span"));
        if (sel === "#spSearch") return el._spSearch || (el._spSearch = createElement("input"));
        if (sel === "#spClear") return el._spClear || (el._spClear = createElement("button"));
        if (sel === ".sp-g-body") return el.children.find(c => c.className === "sp-g-body") || createElement("div");
        if (sel === "input") return el._input || (el._input = createElement("input"));
        return null;
      },
      querySelectorAll() { return []; }
    };
    return el;
  }

  // Logic renderSkillPick từ studio.js
  function renderSkillPick(box, skills, chosen) {
    const host = box.querySelector("#skillPick");
    if (!host) return;
    const countEl = box.querySelector("#spCount");
    const searchEl = box.querySelector("#spSearch");
    const knownSlugs = new Set(skills.map(s => s.slug));
    const uninstalledGroup = t("studio.sp_uninstalled_group") || "Chưa cài đặt";
    const uninstalledItems = [...chosen]
      .filter(slug => !knownSlugs.has(slug))
      .map(slug => ({
        slug,
        name: slug,
        group: uninstalledGroup,
        description: t("studio.sp_uninstalled_desc") || "Kỹ năng chưa có trong bộ não",
        uninstalled: true,
      }));
    const allSkills = uninstalledItems.length ? skills.concat(uninstalledItems) : skills;
    const openGroups = new Set(allSkills.filter(s => chosen.has(s.slug)).map(s => s.group || "Chung"));
    let q = "";

    const draw = () => {
      const nq = _spNoAccent(q.trim());
      const hop = (s) => !nq || nq.split(/\s+/).every(word => _spNoAccent(`${s.name} ${s.slug} ${s.group || ""} ${s.description || ""}`).includes(word));
      const groups = new Map();
      allSkills.forEach(s => {
        if (!hop(s)) return;
        const g = s.group || "Chung";
        if (!groups.has(g)) groups.set(g, []);
        groups.get(g).push(s);
      });
      if (countEl) countEl.textContent = t("studio.sp_count", { a: chosen.size, b: allSkills.length });
      if (!groups.size) { host.children = []; return; }
      host.children = [];
      [...groups.keys()].sort((x, y) => {
        if (x === uninstalledGroup) return 1;
        if (y === uninstalledGroup) return -1;
        return x.localeCompare(y, LOC());
      }).forEach(g => {
        const list = groups.get(g);
        const nSel = list.filter(s => chosen.has(s.slug)).length;
        const wrap = createElement("div");
        wrap.groupName = g;
        wrap.nSel = nSel;
        wrap.total = list.length;
        wrap.headerHtml = `<button type="button" class="sp-g-head"><span class="sp-g-name">${g}</span><span class="sp-g-n">${nSel}/${list.length}</span></button>`;
        host.children.push(wrap);
      });
    };

    draw();
    return { draw, allSkills, getCount: () => countEl.textContent, getGroups: () => host.children };
  }

  // Test 1: Agent "Kiến trúc sư phần mềm" có 2 skill api-design, backend-patterns chưa cài
  const installedSkills = [
    { slug: "ai-prompt", name: "AI Prompt", group: "AI" },
    { slug: "notes", name: "Ghi chú", group: "Chung" },
    { slug: "sales-flow", name: "Bán hàng", group: "Bán hàng" }
  ];
  const chosenEcc = new Set(["api-design", "backend-patterns"]);
  const box1 = createElement("div");
  const res1 = renderSkillPick(box1, installedSkills, chosenEcc);

  assert.strictEqual(res1.getCount(), "đã chọn 2/5");
  const groups1 = res1.getGroups();
  const uninstalledG = groups1.find(g => g.groupName === "Chưa cài đặt");
  assert.ok(uninstalledG, "Phải có nhóm Chưa cài đặt");
  assert.strictEqual(uninstalledG.nSel, 2);
  assert.strictEqual(uninstalledG.total, 2);

  const aiG = groups1.find(g => g.groupName === "AI");
  assert.strictEqual(aiG.nSel, 0);
  assert.strictEqual(aiG.total, 1);

  // Test 2: Agent bình thường chỉ có skill đã cài
  const chosenNorm = new Set(["notes"]);
  const box2 = createElement("div");
  const res2 = renderSkillPick(box2, installedSkills, chosenNorm);
  assert.strictEqual(res2.getCount(), "đã chọn 1/3");
  const groups2 = res2.getGroups();
  assert.strictEqual(groups2.find(g => g.groupName === "Chưa cài đặt"), undefined, "Không được có nhóm Chưa cài đặt");
  const chungG = groups2.find(g => g.groupName === "Chung");
  assert.strictEqual(chungG.nSel, 1);
  assert.strictEqual(chungG.total, 1);

  console.log("ALL TESTS PASSED!");
}

runTest();
