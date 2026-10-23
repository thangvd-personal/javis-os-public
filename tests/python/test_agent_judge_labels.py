"""T3 phần J (0.156.0): dạy bộ phán xử bằng nhãn (thiết kế 7.6, 2; GOAL 8.4, 12 ca 21).

    python tests/run.py agent_judge_labels -v

- Nhãn "Đáng lẽ nên nói" / "Nên im (chen ngang)" dịch độ lệch ngưỡng THEO NGỮ CẢNH (kẹp ±0.25, nửa đời 14 ngày) và thêm ca;
  không đổi ước lượng nội dung (không ghi gì vào kho Cộng hưởng).
- Cộng hưởng tắt: nhãn chỉ lưu, ngưỡng không đổi; chỉ nhãn sau `resumed_at` mới tính; lời gọi trực tiếp không dạy phán xử.
- Trục `axis` của reaction T2 tới `agent_judge.label` qua bộ chiếu phản hồi (đổi/bỏ trục thì hoàn phần đã dịch).
- "Sao em không trả lời?" chỉ GỢI Ý quyết định im gần nhất.
- Qua host thật (main.py): `GET /agents/{slug}/judge`, `POST /agents/{slug}/judge/label`; dựng engine thật là dừng ngay.
"""
import os
import tempfile
import time
from pathlib import Path

os.environ.pop("JAVIS_RESONANCE_CALL_CEILING", None)
import _paths  # noqa: E402,F401  - thư mục tạm riêng, tự xoá khi thoát (xem _paths.py)

os.environ["JAVIS_STATE_DIR"] = tempfile.mkdtemp(prefix="javis-judge-labels-")

from _judge_world import World, batch, check, finish, keys_in  # noqa: E402

ROLES = {"lan": "Điều phối", "minh": "Quảng cáo", "hoa": "Kho hàng"}
DAY = 86400.0

# ═════ reaction trục nói/im -> nhãn -> dịch ngưỡng theo ngữ cảnh ═════
w = World("axis", roles=ROLES, enabled=("minh",))
w.jeng.decide = lambda p: batch(**{k: (("speak", 0.9) if k == w.k("minh") else ("silent", 0.2)) for k in keys_in(p)})
w.now[0] = time.time() + 5          # sau resumed_at của lần bật Cộng hưởng lúc dựng thế giới
w.send("Mọi người ơi ads tuần này thế nào?")
vol = [d for d in w.decisions("minh", "message") if d["volunteer"]][0]
mid = int(vol["message_ref"].split(":")[1])
before_rows = len(w.ledger.rows())
w.runner.react(w.team_id, mid, None, axis="should_silent", nonce="ax1")
d = w.js.get_decision(vol["id"])
check("trục 'Nên im (chen ngang)' trên tin tự lên tiếng: nhãn applied, nguồn reaction",
      d["label"] == "should_silent" and d["label_source"] == "reaction" and d["label_effect"] == "applied", d)
off = w.js.get_offset(w.k("minh"), w.ctx, w.now[0])
check("chen ngang nâng độ lệch ngưỡng +0.08 x 1.5 = +0.12 cho ĐÚNG ngữ cảnh nhóm", abs(off - 0.12) < 1e-6, off)
check("ngưỡng của Minh ở nhóm này thành 0.72", w.judge.view(w.k("minh"))["contexts"][0]["threshold"] == 0.72)
check("độ lệch ở ngữ cảnh khác không đổi", w.js.get_offset(w.k("minh"), f"dm:{w.k('minh')}", w.now[0]) == 0.0)
cases = w.js.cases(w.k("minh"), w.ctx)
check("thêm một ca (nhãn đúng: silent) trong đúng ngữ cảnh", len(cases) == 1 and cases[0]["label"] == "silent", cases)
check("nhật ký changes ghi trước/sau của lần dịch", w.js.changes(w.k("minh"))[0]["kind"] == "label_shift")
check("không đổi ước lượng nội dung: nhãn không thêm lượt hay sổ nào ở kho Cộng hưởng",
      len(w.ledger.rows()) == before_rows)
w.runner.react(w.team_id, mid, None, axis="", nonce="ax2")
d = w.js.get_decision(vol["id"])
check("bỏ trục trên reaction: bỏ nhãn, hoàn phần đã dịch, bỏ ca",
      d["label"] == "" and abs(w.js.get_offset(w.k("minh"), w.ctx, w.now[0])) < 1e-6
      and w.js.cases(w.k("minh"), w.ctx) == [], d)
w.runner.react(w.team_id, mid, "love", axis="should_speak", nonce="ax3")
d = w.js.get_decision(vol["id"])
check("nhãn đồng ý với quyết định (nói và 'Đáng lẽ nên nói'): không dịch, chỉ thêm ca",
      d["label"] == "should_speak" and d["label_delta"] == 0 and len(w.js.cases(w.k("minh"), w.ctx)) == 1, d)

# ═════ lịch sử quyết định: ca im không có tin ═════
hoa_silent = [x for x in w.decisions("hoa", "message")][0]
got = w.judge.label(hoa_silent["id"], "should_speak", "owner", at=w.now[0])
check("ca im không tin, Cộng hưởng của Hoa TẮT: nhãn stored_only, ngưỡng không đổi",
      got["effect"] == "stored_only" and w.js.get_offset(w.k("hoa"), w.ctx, w.now[0]) == 0.0, got)
w.gs.agent_set_enabled(w.owner, "hoa", True)
resumed = w.gs.resumed_at(w.k("hoa"), w.ctx)
got = w.judge.label(hoa_silent["id"], "should_silent", "owner", at=resumed - 10)
check("nhãn có trước resumed_at: stored_before_resume (bật lại không phát lại)", got["effect"] == "stored_before_resume", got)
got = w.judge.label(hoa_silent["id"], "should_speak", "owner", at=resumed + 10)
off = w.js.get_offset(w.k("hoa"), w.ctx, resumed + 10)
check("nhãn sau resumed_at: 'Đáng lẽ nên nói' trên ca im hạ ngưỡng -0.05 x 1.5", got["effect"] == "applied"
      and abs(off + 0.075) < 1e-6, (got, off))

direct = w.decisions("lan", "direct")[0]
got = w.judge.label(direct["id"], "should_silent", "owner", at=w.now[0] + 100)
check("lời gọi trực tiếp không dạy phán xử: stored_direct, không dịch", got["effect"] == "stored_direct"
      and w.js.get_offset(w.k("lan"), w.ctx, w.now[0]) == 0.0, got)

# ═════ kẹp và nửa đời ═════
js, key, ctx = w.js, w.k("minh"), "team:clamp"
t0 = w.now[0]
for i in range(6):
    did = js.add_decision({"agent_key": key, "context_ref": ctx, "source_event": f"msg:{9000 + i}", "kind": "message",
                           "verdict": "silent"}, now=t0)
    w.judge.label(did, "should_speak", "owner", at=t0 + 10)
check("kẹp: 6 nhãn 'nên nói' dừng ở -0.25", abs(js.get_offset(key, ctx, t0 + 10) + 0.25) < 1e-6,
      js.get_offset(key, ctx, t0 + 10))
check("nửa đời 14 ngày: sau 14 ngày còn một nửa", abs(js.get_offset(key, ctx, t0 + 10 + 14 * DAY) + 0.125) < 1e-6)
w.judge.set_offset(key, ctx, 0.9, "review")
check("set_offset (phần L) cũng bị kẹp ±0.25 và ghi changes", js.get_offset(key, ctx, w.now[0]) == 0.25
      and js.changes(key)[0]["kind"] == "offset_set")
check("nhãn sai giá trị bị từ chối", w.judge.label(vol["id"], "maybe", "owner")["reason"] == "bad_label")

# ═════ gợi ý "sao em không trả lời" ═════
w.send("Sao Hoa không trả lời vậy?", mentions=["hoa"])
owner_msgs = [m for m in w.messages() if m["author_kind"] == "owner"]
hint = (owner_msgs[-1].get("meta") or {}).get("judge_hint")
check("'Sao ... không trả lời?' chỉ GỢI Ý quyết định im gần nhất của người được nhắc (không tự sửa)",
      hint and hint["agent_key"] == w.k("hoa") and w.js.get_decision(hint["decision_id"])["verdict"] in ("silent", "wait"),
      hint)

# ═════ qua host thật: route lịch sử quyết định và nút sửa ═════
BR = Path(tempfile.mkdtemp(prefix="javis-judge-labels-brain-"))
(BR / "agents").mkdir()
for slug in ("lan", "minh"):
    (BR / "agents" / f"{slug}.md").write_text(f"---\nname: {slug.capitalize()}\nrole: Thành viên\n---\nBạn là {slug}.\n",
                                              encoding="utf-8", newline="\n")
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class RealEngineUsed(AssertionError):
    pass


def _no_real(*a, **k):
    raise RealEngineUsed("test tried to build or call a REAL engine")


main.team_engine.build = _no_real
main._reply_policy_sandbox_engine = _no_real
ORIG_ASK = main._agent_judge_ask
main._agent_judge_ask = _no_real
root = main._brain_key(str(BR))
rs = main._resonance_store()
owner = main.resonance_store.Principal("owner", "owner", root)
a_lan = rs.agent_register(owner, "lan")
a_minh = rs.agent_register(owner, "minh")
J = main._agent_judge()
did = J.record_direct(a_lan["agent_key"], "team:tm_x", "msg:1")
st = main._agent_judge_store(create=False)
sil = st.add_decision({"agent_key": a_lan["agent_key"], "context_ref": "team:tm_x", "source_event": "msg:2",
                       "kind": "message", "verdict": "silent", "silence_code": "no_signal"})
c = TestClient(main.app, base_url="http://127.0.0.1:8080")
Q = f"?brain={BR}"
r = c.get(f"/agents/lan/judge{Q}")
v = r.json().get("judge") or {}
ctxs = {x["context_ref"]: x for x in v.get("contexts") or []}
check("GET /agents/{slug}/judge: 200, ngữ cảnh nhóm có cả ca im không tin", r.status_code == 200
      and any(x["decision_id"] == sil and x["verdict"] == "silent" for x in ctxs.get("team:tm_x", {}).get("decisions", [])),
      r.text[:300])
check("GET judge: luôn có ngữ cảnh chat riêng (nơi áp dụng)", f"dm:{a_lan['agent_key']}" in ctxs)
r = c.get(f"/agents/lan/judge{Q}&context_ref=team:tm_x")
check("GET judge lọc một ngữ cảnh (hàng thành viên nhóm)",
      [x["context_ref"] for x in r.json()["judge"]["contexts"]] == ["team:tm_x"])
r = c.post(f"/agents/lan/judge/label{Q}", json={"decision_id": sil, "label": "should_speak"})
check("POST label: 200, nhãn lưu (Cộng hưởng tắt: stored_only)", r.status_code == 200
      and r.json()["effect"] == "stored_only" and st.get_decision(sil)["label"] == "should_speak", r.text[:300])
r = c.post(f"/agents/minh/judge/label{Q}", json={"decision_id": sil, "label": "should_speak"})
check("POST label quyết định của trợ lý khác: 404", r.status_code == 404, r.status_code)
r = c.post(f"/agents/lan/judge/label{Q}", json={"decision_id": sil, "label": "promote"})
check("POST label sai nhãn: 400", r.status_code == 400)
r = c.post(f"/agents/lan/judge/label{Q}", json={"label": "should_speak"})
check("POST label thiếu mã: 400", r.status_code == 400)
r = c.post(f"/agents/lan/judge/label{Q}", json={"decision_id": sil, "label": ""})
check("POST label rỗng: bỏ nhãn", r.status_code == 200 and st.get_decision(sil)["label"] == "")

# ═════ chốt chung: engine phán xử thật không bao giờ được dựng trong test ═════
import asyncio as _asyncio  # noqa: E402

check("_paths đặt JAVIS_NO_REAL_JUDGE_ENGINE cho mọi test Python", os.environ.get("JAVIS_NO_REAL_JUDGE_ENGINE") == "1")
built = []
main._reply_policy_sandbox_engine = lambda *a, **k: built.append(1) or _no_real()
src_main = (Path(main.__file__)).read_text(encoding="utf-8")
ask_src = src_main[src_main.index("async def _agent_judge_ask"):src_main.index("def _agent_judge_engine_label")]
check("_agent_judge_ask kiểm chốt TRƯỚC khi dựng engine", ask_src.index("JAVIS_NO_REAL_JUDGE_ENGINE")
      < ask_src.index("_reply_policy_sandbox_engine("))
try:
    _asyncio.run(ORIG_ASK("x", "agent_judge", None))
    stopped = False
except main.agent_judge.EngineUnavailable:
    stopped = True
check("có chốt: lượt phán xử dừng bằng EngineUnavailable (hoàn lượt), không dựng engine", stopped and built == [])

# ═════ bộ chuyển đăng ký phần "judge" vào khung nhịp của phần P ═════
import sys as _sys  # noqa: E402
import types as _types  # noqa: E402

check("chưa có phần P (agent_pulse_host): bộ chuyển không làm gì, không lỗi",
      "agent_pulse_host" in _sys.modules or main._register_judge_view_part() is False)
fake = _types.ModuleType("agent_pulse_host")
parts = {}
fake.register_view_part = lambda name, fn: parts.__setitem__(name, fn)
_sys.modules["agent_pulse_host"] = fake
main._JUDGE_VIEW_REGISTERED[0] = False
check("có phần P: đăng ký đúng một phần tên 'judge'", main._register_judge_view_part() is True and list(parts) == ["judge"])
pv = parts["judge"](a_lan["agent_key"])
check("phần 'judge' trả khung nhìn quyết định của đúng agent", pv["name"] == "judge" and pv["agent_key"] == a_lan["agent_key"]
      and any(c["context_ref"] == "team:tm_x" for c in pv["contexts"]), pv)
check("đăng ký một lần (gọi lại không đăng ký thêm)", main._register_judge_view_part() is True and len(parts) == 1)
del _sys.modules["agent_pulse_host"]

finish()
