"""Pure shared rendering for ordinary main prompt blocks.

Callers own reads, authorization, completeness, ordering and volatile snapshots. The
legacy defaults retain production caps. Full-protected mode preserves raw profile,
memory and project instruction text; it does not claim a complete production surface.
No callable, runtime discovery, settings, filesystem read or context guard lives here.
"""
from __future__ import annotations

import re
from pathlib import Path

_MEM_ITEM_RE = re.compile(r'^(\s*-\s*\[[^\]]*\]\([^)]*\))\s*[-–—]?\s*(.*)$')


def render_profile_block(text: str, *, profile_max: int = 4000, preserve_full_protected: bool = False) -> str:
    """Owner profile comes before brain memory, which wins in a disagreement."""
    content = text if preserve_full_protected else text.strip()[:profile_max]
    return ("\n\n# === HỒ SƠ NGƯỜI DÙNG (chung mọi brain, do người dùng tự viết) ===\n"
            "Cách xưng hô, ngôn ngữ, cách trình bày và mức chi tiết người dùng muốn. Bộ nhớ riêng của brain bên dưới "
            "thắng khi hai bên khác nhau.\n" + content) if content else ""


def render_project_instructions(name: str, text: str, *, instructions_max: int,
                                preserve_full_protected: bool = False) -> str:
    """Render only protected instructions, never source documents or fixture contents."""
    if not (text if preserve_full_protected else text.strip()):
        return ""
    content = text if preserve_full_protected else text.strip()[:instructions_max]
    return f"\n\n# === HƯỚNG DẪN RIÊNG CỦA PROJECT: {name} ===\n" + content


def fit_memory_index(mem: str, cap: int) -> str:
    """Ép chỉ mục bộ nhớ xuống dưới trần mà KHÔNG làm mất ký ức nào cho tới phút chót.

    Hạ dần theo bậc: giữ nguyên -> rút mô tả còn 100 ký tự -> còn 60 -> chỉ còn tiêu đề+link
    -> (cùng lắm) cắt bớt dòng kèm lời chỉ đường. Rút mô tả KHÔNG mất năng lực nhớ: tiêu đề và
    đường dẫn file vẫn còn nguyên, chi tiết đầy đủ vẫn nằm trong memory/facts/*.md và đọc được
    bất cứ lúc nào. Mất hẳn dòng mới là mất trí nhớ, nên đó là bậc CUỐI.
    """
    if len(mem) <= cap:
        return mem
    lines = mem.split("\n")

    def rebuild(desc_cap):
        out = []
        for l in lines:
            m = _MEM_ITEM_RE.match(l)
            if not m:
                out.append(l)
                continue
            head, desc = m.group(1), m.group(2).strip()
            if not desc:
                out.append(head)
            elif desc_cap is None:
                out.append(head)
            elif len(desc) <= desc_cap:
                out.append(f"{head} - {desc}")
            else:
                out.append(f"{head} - {desc[:desc_cap].rstrip()}…")
        return "\n".join(out)

    for desc_cap in (100, 60, None):
        got = rebuild(desc_cap)
        if len(got) <= cap:
            note = ("\n\n> (Mô tả trong chỉ mục đã rút gọn cho vừa ngữ cảnh. Chi tiết đầy đủ của "
                    "từng ký ức nằm trong file tương ứng ở memory/facts/ - cứ đọc khi cần.)")
            return got + note

    # Bậc cuối: buộc phải bỏ bớt dòng. Giữ các dòng ĐẦU (ký ức nền tảng ghi sớm nhất) và nói rõ
    # còn bao nhiêu, kèm đường đọc tiếp - đừng để mất im lặng.
    # 0.222.0: also NAME the hidden memories (file names only, within a small budget). A model
    # cannot look up a memory it does not know exists: with only a count, "who is Lan?" got "I do
    # not know" while facts/lan-em-gai.md sat on disk unread.
    hidden_budget = min(1200, cap // 5)
    kept, total = [], 0
    items = 0
    rebuilt = rebuild(None).split("\n")
    for l in rebuilt:
        if total + len(l) + 1 > cap - 300 - hidden_budget:
            break
        kept.append(l)
        total += len(l) + 1
        if _MEM_ITEM_RE.match(l):
            items += 1
    con_lai = sum(1 for l in lines if _MEM_ITEM_RE.match(l)) - items
    names = []
    used = 0
    for l in rebuilt[len(kept):]:
        m = _MEM_ITEM_RE.match(l)
        link = re.search(r"\]\(([^)]*)\)", m.group(1)) if m else None
        if not link:
            continue
        slug = Path(link.group(1).strip()).stem
        if not slug:
            continue
        if used + len(slug) + 2 > hidden_budget:
            names.append("…")
            break
        names.append(slug)
        used += len(slug) + 2
    ten = (f" Tên file của chúng: {', '.join(names)}. Cần ký ức nào thì đọc memory/facts/<tên>.md."
           if names else "")
    return "\n".join(kept) + (
        f"\n\n> (Chỉ mục quá dài nên còn {con_lai} ký ức chưa liệt kê ở đây.{ten} "
        "Đọc memory/MEMORY.md để xem đủ danh sách, và memory/facts/ để xem chi tiết.)")


def render_memory_block(text: str, *, memory_cap: int, preserve_full_protected: bool = False) -> str:
    """Keep the legacy memory formatter unless the host requests full protected text."""
    if not (text if preserve_full_protected else text.strip()):
        return ""
    return "\n\n# === BỘ NHỚ DÀI HẠN (nạp sẵn) ===\n" + (text if preserve_full_protected else fit_memory_index(text, memory_cap))


def render_agentic_paths(root, agents, workflows, loops, skills) -> str:
    """Render captured paths without resolving or creating any directory."""
    ag, wf, lp, sk = agents, workflows, loops, skills
    return (
        "\n\n# === LỚP AGENTIC (vault đang làm việc) ===\n"
        f"Vault root: {root}\n"
        f"- AGENT: tạo/sửa tại `{ag}/<slug>.md`\n"
        f"- WORKFLOW: tạo/sửa tại `{wf}/<slug>.md`\n"
        f"- LOOP (nhiệm vụ lặp vô hạn): tạo/sửa tại `{lp}/<slug>.md`\n"
        f"- SKILL: tạo/sửa tại `{sk}/<slug>/SKILL.md` (tự mirror sang .claude/skills cho Claude native)\n"
        f"- FILE KHÁC (bài viết, báo cáo, nháp, dữ liệu): ghi VÀO TRONG vault root ở trên bằng đường "
        f"dẫn TUYỆT ĐỐI `{root}/<thư mục>/<tên file>`. TUYỆT ĐỐI không ghi tương đối theo thư mục "
        "làm việc và không ghi ra ngoài vault: file ngoài vault không hiện ở trang Tệp tin và mất "
        "khi cập nhật. Trong câu trả lời, dẫn link bằng đường dẫn TƯƠNG ĐỐI so với vault root "
        "(vd `bai-viet/bai-1.txt`), KHÔNG dùng giao thức `file://` và không mã hoá %20 dù harness "
        "có dặn khác (khung chat và Telegram mở không được), và CHỈ dẫn link tới file đã ghi xong "
        "thật. Khi đếm hay liệt kê "
        "file (vd \"còn bao nhiêu bài chưa xong\"), liệt kê lại thư mục bằng tool ngay lúc đó, "
        "đừng nhớ lại từ lượt trước.\n"
        "Khi user yêu cầu tạo/sửa agent, workflow hoặc loop qua chat, ghi file .md đúng định dạng "
        "(xem mục 'Tạo/sửa Agent & Workflow qua chat' và 'Điều phối' trong system prompt) bằng "
        "ĐƯỜNG DẪN TUYỆT ĐỐI ở trên. Trang Agents/Workflows/Việc định kỳ sẽ tự nhận file mới."
    )


def render_capability_summary(c: dict, latest_workflow_line: str = "") -> str:
    """Render already observed capabilities; never discover or load plugins here."""
    if not any(c.values()):
        return ""
    parts = ["\n\n# === NĂNG LỰC JAVIS HIỆN CÓ (đọc `Javis/index.md` để biết chi tiết + trigger) ==="]
    if c["agents"]:
        parts.append("Agents: " + ", ".join(a["name"] for a in c["agents"][:30]))
    if c["skills"]:
        groups = sorted({s["group"] for s in c["skills"] if s["enabled"]})
        parts.append(f"Skills: {sum(1 for s in c['skills'] if s['enabled'])} kỹ năng (nhóm: {', '.join(groups[:12])})")
    if c["workflows"]:
        parts.append("Workflows: " + ", ".join(w["name"] for w in c["workflows"][:20] if w["status"] == "active"))
    _lc = latest_workflow_line
    if _lc:
        parts.append(_lc)
    if c["loops"]:
        parts.append("Loops: " + ", ".join(f"{l['name']}({'bật' if l['enabled'] else 'tắt'})" for l in c["loops"][:20]))
    live_plugins = [p for p in c.get("plugins", []) if p.get("loaded")]
    if live_plugins:
        tool_names = [t for p in live_plugins for t in p.get("tools", [])]
        parts.append("Plugins đang chạy: " + ", ".join(p["name"] for p in live_plugins[:12])
                     + (f" (tool: {', '.join(tool_names[:12])})" if tool_names else ""))
    parts.append("Trước khi tạo năng lực mới, kiểm chỉ mục này để khỏi trùng.")
    return "\n".join(parts)


def render_skill_router(visible_metas: list, *, skills_dir, hidden_count: int = 0, description_max: int = 150) -> str:
    """Render a host-selected, already ordered/budgeted router list."""
    if not visible_metas and hidden_count <= 0:
        return ""
    sk_dir = skills_dir
    lines = ["\n\n# === SKILL KHẢ DỤNG (router - dùng được trên MỌI engine) ==="]
    for s in visible_metas:
        desc = (s.get("description") or "").replace("\n", " ")[:description_max]
        lines.append(f"- {s['slug']} ({s['name']}): {desc}")
    if hidden_count > 0:
        # Nói rõ CÁCH LẤY chứ không chỉ nói còn bao nhiêu: `javis_use_skill` nạp được theo
        # slug kể cả skill không nằm trong danh sách trên, nên một skill bị cắt vẫn dùng được
        # nếu model biết tên. Câu cũ chỉ trỏ sang một file mà model không đọc.
        lines.append(f"…(+{hidden_count} skill nữa chưa liệt kê ở đây - xem đủ danh sách trong "
                     f"`Javis/index.md`, và nạp thẳng bằng `javis_use_skill(name=<slug>)` "
                     f"nếu đã biết tên)")
    lines.append(
        "CÁCH DÙNG: khi yêu cầu của user KHỚP mô tả 1 skill ở trên, hãy NẠP skill đó rồi LÀM THEO - "
        "gọi tool `javis_use_skill(name=<slug>)` nếu engine có tool này; nếu không, mở file "
        f"`{sk_dir}/<slug>/SKILL.md` bằng công cụ đọc file rồi tuân theo hướng dẫn trong đó. "
        "Chỉ nạp khi thực sự khớp, không nạp tràn lan."
    )
    return "\n".join(lines)


def render_clock_language(clock_text: str, language_block: str) -> str:
    """Render the frozen clock followed by its already resolved language contract."""
    return "\n\n# === BÂY GIỜ ===\n" + clock_text + language_block


def render_usage_block(today_total: dict) -> str:
    """Render an observed daily total; zero usage keeps the legacy empty block."""
    _t = today_total
    if _t.get("in") or _t.get("out"):
        _c = f", ~${_t.get('cost', 0):.4f}" if _t.get("cost") else ""
        return (f"\n\n# === MỨC DÙNG HÔM NAY (Javis tự đo) ===\n"
                f"{_t.get('in', 0):,} token vào + {_t.get('out', 0):,} token ra qua "
                f"{_t.get('turns', 0)} lượt{_c}. Đây là token Javis TỰ ĐO, KHÔNG phải hạn mức gói "
                f"thuê bao (đa số nhà cung cấp không cho lấy hạn mức tài khoản qua API). Chi tiết "
                f"từng nhà cung cấp ở panel 'Mức dùng' trên dashboard.")
    return ""
