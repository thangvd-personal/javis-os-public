"""POST /stt và GET /voice/options (Voice V2).

    python tests/run.py stt_route

Không chạm mạng: `stt.groq_nghe` bị thay bằng hàm giả ghi lại tham số; `antigravity_cli.list_models`
thay bằng danh sách giả. Khoá: file lên đúng byte, ngôn ngữ vi-VN rút thành "vi", thiếu key thì
ok=false kèm ly_do; /voice/options trả đúng cờ available theo key đã có.
"""
import os
import tempfile

from _paths import ROOT, SERVER  # noqa: F401

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-stt-"))

import antigravity_cli
import main
import stt
from fastapi.testclient import TestClient

client = TestClient(main.app, base_url="http://127.0.0.1")
_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


calls = []


async def fake_nghe(data, ten, key, model="", ngon_ngu=None, hotwords=""):
    calls.append({"data": data, "ten": ten, "key": key, "model": model, "lang": ngon_ngu, "hotwords": hotwords})
    if not key:
        return {"ok": False, "ly_do": "thieu_key"}
    # Whisper chép "Javis" thành "David": route phải sửa lại theo ngữ cảnh (nghe_sua) trước khi trả.
    return {"ok": True, "text": "xin chào David", "model": model or "whisper-large-v3-turbo"}


_goc_nghe, _goc_cfg, _goc_models = stt.groq_nghe, main.cfgmod.read_settings, antigravity_cli.list_models
stt.groq_nghe = fake_nghe
main.cfgmod.read_settings = lambda: {"model": {"groq_api_key": "gk"}, "voice": {"stt_model": "", "mode": "fast", "brain_provider": "antigravity", "hotwords": "Pancake, OpenRouter"}}
antigravity_cli.list_models = lambda: [{"id": "gemini-3.8-flash-low", "label": "Gemini 3.8 Flash (Low)"}]

r = client.post("/stt", files={"file": ("voice.webm", b"OggS-fake", "audio/webm")}, data={"lang": "vi-VN"})
check("/stt: 200 ok=true kèm text đã sửa David -> Javis", r.status_code == 200 and r.json()["ok"] is True and r.json()["text"] == "xin chào Javis")
check("/stt: mồi hotwords cho Whisper (Javis + từ người dùng khai, đứng cuối sát audio)", calls[-1]["hotwords"].endswith("Javis, Pancake, OpenRouter."))
check("/stt: mồi kèm từ tiếng Anh hay nói xen (GitHub Actions)", "GitHub Actions" in calls[-1]["hotwords"])
check("/stt: byte file tới nguyên vẹn, tên file giữ", calls[-1]["data"] == b"OggS-fake" and calls[-1]["ten"] == "voice.webm")
check("/stt: vi-VN rút thành vi", calls[-1]["lang"] == "vi")
check("/stt: key lấy từ model.groq_api_key", calls[-1]["key"] == "gk")

# ---- 0.64.74: đối chiếu với bản nháp Web Speech ----
# Bản nháp gần đúng về âm ("xin chào Gia vít") -> dùng chữ Groq.
r = client.post("/stt", files={"file": ("voice.webm", b"OggS-fake", "audio/webm")},
                data={"lang": "vi-VN", "draft": "xin chào Gia vít"})
check("/stt: bản nháp gần âm -> dùng chữ Groq", r.json()["ok"] is True and r.json()["text"] == "xin chào Javis")


async def fake_bia(data, ten, key, model="", ngon_ngu=None, hotwords=""):
    return {"ok": True, "text": "Các bạn có thể nhận thêm thông tin về các bài hát trong phần mô tả", "model": "m"}


stt.groq_nghe = fake_bia
r = client.post("/stt", files={"file": ("voice.webm", b"OggS-fake", "audio/webm")},
                data={"lang": "vi-VN", "draft": "today How are you I'm fine thank you chào em nhé"})
check("/stt: Groq bịa câu lệch hẳn bản nháp -> ok=false, ly_do lech_ban_nhap (giữ bản nháp)",
      r.json()["ok"] is False and r.json()["ly_do"] == "lech_ban_nhap" and r.json()["text"] == "")
r = client.post("/stt", files={"file": ("voice.webm", b"OggS-fake", "audio/webm")}, data={"lang": "vi-VN"})
check("/stt: không gửi bản nháp -> không đối chiếu, dùng Groq như cũ", r.json()["ok"] is True)
stt.groq_nghe = fake_nghe

main.cfgmod.read_settings = lambda: {"model": {}, "voice": {}}
r = client.post("/stt", files={"file": ("v.webm", b"x", "audio/webm")})
check("/stt: thiếu key -> ok=false ly_do thieu_key", r.json()["ok"] is False and r.json()["ly_do"] == "thieu_key")

main.cfgmod.read_settings = lambda: {"model": {"groq_api_key": "gk", "gemini_api_key": ""}, "voice": {"mode": "fast", "brain_provider": "antigravity", "live_provider": "gemini"}}
r = client.get("/voice/options")
d = r.json()
check("/voice/options: 200 + voice hiện tại", r.status_code == 200 and d["voice"]["mode"] == "fast")
check("/voice/options: trả hotwords và từ gốc luôn có", "hotwords" in d["voice"] and d["hotwords_goc"] == ["Javis"])
bp = {x["id"]: x for x in d["brain_providers"]}
check("/voice/options: antigravity available kèm model", bp["antigravity"]["available"] is True and bp["antigravity"]["models"][0]["id"] == "gemini-3.8-flash-low")
check("/voice/options: groq available (có key), gemini không", bp["groq"]["available"] is True and bp["gemini"]["available"] is False)
sp = {x["id"]: x for x in d["stt_providers"]}
check("/voice/options: stt groq available theo key", sp["groq"]["available"] is True and sp["browser"]["available"] is True)
lp = {x["id"]: x for x in d["live_providers"]}
check("/voice/options: live gemini không key -> unavailable, có voices", lp["gemini"]["available"] is False and lp["gemini"]["voices"])

antigravity_cli.list_models = lambda: None
r = client.get("/voice/options")
check("/voice/options: chưa cài agy -> unavailable + hint", r.json()["brain_providers"][1]["available"] is False and "agy" in r.json()["brain_providers"][1]["hint"])

stt.groq_nghe, main.cfgmod.read_settings, antigravity_cli.list_models = _goc_nghe, _goc_cfg, _goc_models

if _fails:
    print("\nFAIL:", len(_fails), _fails)
    raise SystemExit(1)
print("\nOK - stt route + voice options")
