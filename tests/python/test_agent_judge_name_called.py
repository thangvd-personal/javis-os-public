"""Cổng thô của bộ phán xử nhận ra lời gọi đúng TÊN thành viên (0.171.4). Không gọi model.

    python tests/run.py agent_judge_name_called

Pilot model thật 2026-10-16: tên "Minh" bỏ dấu thành "minh", trùng từ dừng "mình", nên `prefilter` không bao giờ thấy chủ gọi
"Minh ơi" (không @) và trợ lý Minh luôn im với `no_signal`. Tên được so cả dấu, nguyên từ.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401

import agent_judge as AJ  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  -> {extra}"))
    if not cond:
        fails.append(name)


MINH = {"role_text": "Phân tích số liệu bán hàng", "name": "Minh"}
LAN = {"role_text": "Điều phối nhóm cửa hàng", "name": "Lan"}

ok, feats, code = AJ.prefilter("@Lan Minh ơi, con số bán ống nhựa tuần này tăng 400%.", MINH, mentions=["ag_lan"])
check("gọi 'Minh ơi' không @: qua cổng với name_called (trước 0.171.4 là no_signal)",
      ok and feats.get("name_called") is True and code == "", (ok, feats, code))
check("viết hoa 'MINH' vẫn là gọi tên", AJ.prefilter("MINH kiểm giúp", MINH, mentions=["x"])[1].get("name_called") is True)
check("gõ không dấu 'minh oi' (đúng chữ của tên) vẫn là gọi tên",
      AJ.prefilter("minh oi kiểm giúp", MINH, mentions=["x"])[1].get("name_called") is True)
check("'nhóm mình' KHÔNG phải gọi Minh (tên giữ dấu)",
      "name_called" not in AJ.prefilter("Kế hoạch của nhóm mình ổn rồi.", MINH, mentions=["x"])[1])
check("tên nằm trong từ khác ('Minhanh') không tính",
      "name_called" not in AJ.prefilter("Minhanh gửi hàng rồi.", MINH, mentions=["x"])[1])
check("'lần' KHÔNG phải gọi Lan",
      "name_called" not in AJ.prefilter("Lần trước nhập hàng chậm.", LAN, mentions=["x"])[1])
check("tên trống không khớp gì", AJ.name_called("Minh ơi", "") is False and AJ.name_called("x", None) is False)
check("tin không gọi ai, không trùng vai: vẫn no_signal",
      AJ.prefilter("Hôm nay trời mưa to quá.", MINH, mentions=["x"]) == (False, {}, "no_signal"))

raise SystemExit(1 if fails else 0)
