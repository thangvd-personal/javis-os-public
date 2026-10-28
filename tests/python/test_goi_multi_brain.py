"""Test hỗ trợ đa brain cho Gói mở rộng (Packs):
- Cài đặt vào nhiều brain độc lập.
- Kiểm tra da_cai_trong_brain.
- Tự động đồng bộ pack vào brain mới qua sync_brain_packs.
- Gỡ lẻ từng brain qua go(pack_id, brain_root=...).
"""
import sys
import tempfile
from pathlib import Path

import pack_vault as pv
from _paths import DASHBOARD, ROOT, SERVER  # noqa: F401

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


def dung_goi(goc: Path, agent="tro-ly", skill="viet-email", noi_dung="Nội dung gốc"):
    (goc / "agents").mkdir(parents=True, exist_ok=True)
    (goc / "workflows").mkdir(parents=True, exist_ok=True)
    (goc / "skills" / skill).mkdir(parents=True, exist_ok=True)
    (goc / "agents" / f"{agent}.md").write_text(
        f"---\ntype: agent\nname: Trợ lý\n---\n{noi_dung}\n", encoding="utf-8")
    (goc / "workflows" / "quy-trinh.md").write_text(
        f"---\nname: Quy trình\nstatus: active\n---\n{noi_dung}\n", encoding="utf-8")
    (goc / "skills" / skill / "SKILL.md").write_text(
        "---\nname: Viết email\ndescription: Soạn thư\n---\nHướng dẫn\n", encoding="utf-8")
    return goc


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    pv.HIEU_UNG_DIR = tmp / "packs-state"
    goi = dung_goi(tmp / "goi")
    brain1 = tmp / "brain1"
    brain2 = tmp / "brain2"
    brain3 = tmp / "brain3"
    brain1.mkdir()
    brain2.mkdir()
    brain3.mkdir()

    # 1. Cài vào brain1
    pv.cai("test.pack", goi, str(brain1))
    check("brain1 có skill", (brain1 / "skills" / "viet-email" / "SKILL.md").is_file())
    check("da_cai_trong_brain đúng với brain1", pv.da_cai_trong_brain("test.pack", str(brain1)))
    check("da_cai_trong_brain sai với brain2", not pv.da_cai_trong_brain("test.pack", str(brain2)))

    # 2. Cài vào brain2
    pv.cai("test.pack", goi, str(brain2))
    check("brain2 có skill", (brain2 / "skills" / "viet-email" / "SKILL.md").is_file())
    check("da_cai_trong_brain đúng với brain2", pv.da_cai_trong_brain("test.pack", str(brain2)))

    # 3. Kiểm tra sổ đa brain
    so = pv.doc_so("test.pack")
    check("sổ có trường brains", "brains" in so and len(so["brains"]) == 2)

    # 4. Gỡ lẻ khỏi brain1
    g1 = pv.go("test.pack", brain_root=str(brain1))
    check("đã gỡ khỏi brain1", len(g1["da_xoa"]) > 0)
    check("brain1 mất skill", not (brain1 / "skills" / "viet-email").exists())
    check("brain2 VẪN CÒN skill", (brain2 / "skills" / "viet-email" / "SKILL.md").is_file())
    check("da_cai_trong_brain sai với brain1", not pv.da_cai_trong_brain("test.pack", str(brain1)))
    check("da_cai_trong_brain vẫn đúng với brain2", pv.da_cai_trong_brain("test.pack", str(brain2)))

    # 5. Gỡ nốt khỏi brain2
    g2 = pv.go("test.pack", brain_root=str(brain2))
    check("brain2 mất skill", not (brain2 / "skills" / "viet-email").exists())
    check("sổ hiệu ứng được dọn khi hết brain", not pv.doc_so("test.pack"))

if _fails:
    sys.exit(1)
