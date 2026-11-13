"""Test chuc nang xoa mot tin nhan khoi cuoc tro chuyen.

    python tests/run.py xoa_tin_nhan      (no network)

Kiem tra:
  1. SessionStore.delete_message xoa dung mot tin, cap nhat msg_count va updated_at.
  2. Xoa kem cascade pin/reaction.
  3. Tin khong ton tai hoac cua phien khac thi tra ve False / 404.
  4. Endpoint POST va DELETE /sessions/{id}/messages/{id}/delete hoat dong dung,
     xoa mach native threads va dieu chinh compact_count neu can.
"""
import os
import sys
import tempfile
from pathlib import Path

from _paths import ROOT, SERVER  # noqa: F401

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-xoatin-")
os.environ["JAVIS_ALLOWED_HOSTS"] = "testserver,127.0.0.1,localhost"
os.environ["JAVIS_REQUIRE_LOGIN"] = "0"
os.environ["JAVIS_HOST"] = "127.0.0.1"
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import sessions

_fails = []


def check(name, cond, them=""):
    print(("ok   " if cond else "FAIL ") + name + (("  [" + str(them) + "]") if them and not cond else ""))
    if not cond:
        _fails.append(name)


def noi_dung(st, sid):
    return [(m["id"], m["role"], m["content"]) for m in st.get_messages(sid)]


# ---- 1. SessionStore ----
store = sessions.SessionStore(Path(tempfile.mkdtemp(prefix="sess-")) / "s.db")
sid = store.create_session(brain="brain", engine="cli", title="t", channel="web")
mid1 = store.append_message(sid, "user", "cau hoi mot")
mid2 = store.append_message(sid, "assistant", "cau tra loi mot")
mid3 = store.append_message(sid, "user", "cau hoi hai")
mid4 = store.append_message(sid, "assistant", "cau tra loi hai")

check("ban dau co 4 tin", len(store.get_messages(sid)) == 4)
check("msg_count ban dau la 4", int(store.get_session(sid)["msg_count"]) == 4)

# Ghim tin 2 de test cascade
store.set_message_pinned(sid, mid2, True)
check("tin 2 da duoc ghim", mid2 in store.pinned_message_ids(sid, [mid2]))

# Xoa tin khong ton tai
check("xoa tin 99999 tra ve False", store.delete_message(sid, 99999) is False)

# Xoa tin cua phien khac
sid2 = store.create_session(brain="brain")
check("xoa tin cua phien khac tra ve False", store.delete_message(sid2, mid2) is False)
check("tin van con nguyen sau do", len(store.get_messages(sid)) == 4)

# Xoa tin tra loi (mid2)
ok_del = store.delete_message(sid, mid2)
check("xoa tin mid2 thanh cong", ok_del is True)
con_lai = noi_dung(store, sid)
check("sau khi xoa mid2 con 3 tin", len(con_lai) == 3)
check("mid2 khong con trong danh sach", all(m[0] != mid2 for m in con_lai))
check("mid1, mid3, mid4 van con nguyen", [m[0] for m in con_lai] == [mid1, mid3, mid4])
check("msg_count giam con 3", int(store.get_session(sid)["msg_count"]) == 3)
check("pin cua mid2 da bi don sach", mid2 not in store.pinned_message_ids(sid, [mid2]))

# Xoa tin nguoi dung (mid3)
ok_del3 = store.delete_message(sid, mid3)
check("xoa tin nguoi dung mid3 thanh cong", ok_del3 is True)
con_lai2 = noi_dung(store, sid)
check("sau khi xoa mid3 con 2 tin", len(con_lai2) == 2)
check("mid3 khong con trong danh sach", all(m[0] != mid3 for m in con_lai2))
check("mid1 va mid4 van con nguyen", [m[0] for m in con_lai2] == [mid1, mid4])
check("msg_count giam con 2", int(store.get_session(sid)["msg_count"]) == 2)


# ---- 2. HTTP Endpoints ----
import main
from fastapi.testclient import TestClient

cl = TestClient(main.app, base_url="http://127.0.0.1")
ms = main.get_store()

s_api = ms.create_session(brain="brain", engine="cli", title="test_api", channel="web")
m1 = ms.append_message(s_api, "user", "hoi api 1")
m2 = ms.append_message(s_api, "assistant", "dap api 1")
m3 = ms.append_message(s_api, "user", "hoi api 2")
m4 = ms.append_message(s_api, "assistant", "dap api 2")
ms.set_cli_session_id(s_api, "thread-cli")
ms.set_codex_thread_id(s_api, "thread-codex")
ms.set_compact(s_api, "tom tat cuoc tro chuyen", 2)

# 404 phien khong ton tai
r = cl.post("/sessions/phien-khong-ton-tai/messages/1/delete")
check("POST phien khong ton tai tra ve 404", r.status_code == 404)

# 404 tin khong ton tai
r = cl.post(f"/sessions/{s_api}/messages/999999/delete")
check("POST tin khong ton tai tra ve 404", r.status_code == 404)

# Xoa hop le bang POST .../delete
r = cl.post(f"/sessions/{s_api}/messages/{m2}/delete")
check("POST xoa hop le tra ve 200", r.status_code == 200 and r.json().get("ok") is True)
check("deleted bang True", r.json().get("deleted") is True)
check("remaining la 3", r.json().get("remaining") == 3)
check("native threads da bi xoa", "mach-claude" in r.json().get("threads", []) or len(r.json().get("threads", [])) >= 1)

sess_row = ms.get_session(s_api)
check("compact_count giu nguyen khi con du tin", sess_row.get("compact_summary") != "")

# Xoa tiep bang DELETE method
r_del = cl.delete(f"/sessions/{s_api}/messages/{m4}")
check("DELETE endpoint hoat dong va tra ve 200", r_del.status_code == 200 and r_del.json().get("ok") is True)
check("remaining la 2", r_del.json().get("remaining") == 2)

# Xoa tiep m3: tong so tin con 1 < compact_count (4) -> compact summary bi reset
r3 = cl.post(f"/sessions/{s_api}/messages/{m3}/delete")
check("xoa m3 thanh cong", r3.status_code == 200)
sess_row2 = ms.get_session(s_api)
check("compact summary bi reset khi so tin con lai it hon compact_count", sess_row2.get("compact_summary") == "")

print("\n" + (f"{len(_fails)} test HONG" if _fails else "Tat ca test deu dat"))
sys.exit(1 if _fails else 0)
