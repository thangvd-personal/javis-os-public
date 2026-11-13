"""Offline speech-to-text (0.435.0): Whisper running on the machine itself, no API key, nothing leaves the machine.

Owner, 2026-11-01: hear voice messages and the sound of customers' videos without Groq, with a second road when the
first is not there ("không dùng được cái này thì dùng cái khác"). The API ears (Groq, OpenAI, Gemini) live in `stt.py`;
this is the one that needs no key. It is an OPTIONAL tool (`optional_tools`, card on the Tools page): the library is
~450MB and the model another ~500MB, which most installs never need, so nothing is downloaded until the owner presses
Install. Both land in STATE_DIR, so they survive updates and Docker rebuilds.

How it runs: faster-whisper (CTranslate2, int8 on CPU) in a SEPARATE process (`offline_ear/worker.py`) with the
library on its own path, never imported into the server: its numpy/onnxruntime/huggingface versions cannot clash with
the server's, its memory is returned when it exits, and a stuck run can be killed. One run at a time (`_ONE`): on a
small VPS two at once would starve the server itself.

Cost the owner should know (also on the card): a 30 second clip takes roughly 5 to 30 seconds depending on the CPU,
and the "small" model hears Vietnamese less well than Groq's large Whisper. `JAVIS_LOCAL_STT_MODEL` picks another size
(base, small, medium, large-v3-turbo) for a stronger or weaker machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import localefmt
import winproc
from config import STATE_DIR

ROOT = STATE_DIR / "local-ear"
LIB_DIR = ROOT / "lib"
MODEL_DIR = ROOT / "models"
TMP_DIR = ROOT / "tmp"
WORKER = Path(__file__).resolve().parent / "offline_ear" / "worker.py"
TIMEOUT = 600.0  # one transcription; a long clip on a slow CPU, never forever
INSTALL_TIMEOUT = 1800.0  # ~1GB over a slow VPS link

KNOWN_WHISPER_REPOS: dict[str, str] = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
ALL_KNOWN_SLUGS = {"models--" + r.replace("/", "--") for r in KNOWN_WHISPER_REPOS.values()}


def get_model() -> str:
    m = (os.getenv("JAVIS_LOCAL_STT_MODEL") or "").strip()
    if not m:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("JAVIS_LOCAL_STT_MODEL="):
                        m = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
    return m or "small"


MODEL = get_model()


def get_size_hint(model_name: str | None = None) -> str:
    m = (model_name or get_model()).lower()
    if m == "small":
        return "~1 GB"
    if m == "medium":
        return "~2 GB"
    if m in ("large-v3-turbo", "turbo"):
        return "~2.1 GB"
    if m == "base":
        return "~0.6 GB"
    return "~1 - 2 GB"


SIZE_HINT = get_size_hint(MODEL)
_ONE = asyncio.Lock()


def describe() -> dict:
    m = get_model()
    return {
        "id": "local_ear",
        "ten": localefmt.chu("Bộ nghe offline (Whisper)", "Offline ear (Whisper)"),
        "mo_ta": localefmt.chu(
            "Nghe tin thoại và lời nói trong video khách gửi ngay trên máy này, không cần API key nào. Javis chỉ "
            "dùng khi không có gói ChatGPT hay key Groq, OpenAI, Gemini, hoặc tất cả đang lỗi. Tải về khoảng 1 GB (small) hoặc 2 GB (medium). "
            "Máy yếu nghe chậm hơn (một video 30 giây mất vài chục giây) và nghe tiếng Việt kém hơn Groq.\n\n"
            "Hướng dẫn cấu hình và nâng cấp:\n"
            "- Đổi mô hình tốt hơn: thêm JAVIS_LOCAL_STT_MODEL=medium hoặc large-v3-turbo vào file .env (mặc định: small).\n"
            "- Tùy chỉnh số luồng CPU: thêm JAVIS_LOCAL_STT_THREADS=4 vào file .env để xử lý nhanh hơn.\n"
            "- Tải nhanh hơn và tránh giới hạn: thêm HF_TOKEN=hf_... vào file .env.\n"
            "- Áp dụng mô hình mới: sau khi sửa .env, khởi động lại Javis, bấm 'Gỡ bỏ' rồi bấm 'Cài đặt' lại trên thẻ này.",
            "Hears voice messages and the speech in customers' videos on this machine, with no API key. Javis uses "
            "it only when there is no ChatGPT plan and no Groq, OpenAI or Gemini key, or all of them fail. About 1 GB (small) or 2 GB (medium) to download. "
            "A weak machine hears slowly (a 30 second video can take tens of seconds) and less accurately than Groq.\n\n"
            "Configuration and upgrade guide:\n"
            "- Switch to a better model: add JAVIS_LOCAL_STT_MODEL=medium or large-v3-turbo to .env (defaults to small).\n"
            "- Adjust CPU threads: add JAVIS_LOCAL_STT_THREADS=4 to .env for faster processing.\n"
            "- Faster downloads and avoid rate limits: add HF_TOKEN=hf_... to .env.\n"
            "- Apply new model: after updating .env, restart Javis, click 'Remove' then 'Install' on this card.",
        ),
        "dung_luong_uoc": get_size_hint(m),
    }


def _lib_ready() -> bool:
    return (LIB_DIR / "faster_whisper").is_dir()


def _model_dirs(model_name: str | None = None) -> list[Path]:
    m = (model_name or get_model()).strip()
    repo = KNOWN_WHISPER_REPOS.get(m.lower()) or (f"Systran/faster-whisper-{m}" if "/" not in m else m)
    slug = "models--" + repo.replace("/", "--")
    dirs: list[Path] = []
    p1 = MODEL_DIR / slug
    if p1.is_dir():
        dirs.append(p1)
    p2 = MODEL_DIR / m
    if p2.is_dir() and p2 not in dirs:
        dirs.append(p2)
    if not dirs and MODEL_DIR.is_dir():
        for sub in MODEL_DIR.iterdir():
            if sub.is_dir() and m.lower() in sub.name.lower():
                dirs.append(sub)
    if not dirs and MODEL_DIR.is_dir():
        # Fallback only for non-standard test directories (e.g. models--x in tests)
        for sub in MODEL_DIR.iterdir():
            if sub.is_dir() and sub.name not in ALL_KNOWN_SLUGS and any(sub.rglob("model.bin")):
                dirs.append(sub)
                break
    return dirs


def _model_ready(model_name: str | None = None) -> bool:
    try:
        mdirs = _model_dirs(model_name)
        for d in mdirs:
            if any(d.rglob("model.bin")):
                return True
        return False
    except OSError:
        return False


def installed() -> bool:
    """Library and model are both on disk: `transcribe` can run."""
    return _lib_ready() and _model_ready(get_model())


def _size(p: Path) -> int:
    """Calculate true byte size of directory, resolving symlinks to prevent duplicate counts."""
    if not p.exists():
        return 0
    unique_files: set[Path] = set()
    try:
        if p.is_file():
            unique_files.add(p.resolve())
        else:
            for f in p.rglob("*"):
                try:
                    res = f.resolve()
                    if res.is_file():
                        unique_files.add(res)
                except OSError:
                    pass
    except OSError:
        pass
    total = 0
    for f in unique_files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def total_size_bytes(model_name: str | None = None) -> int:
    """True disk size for offline ear: its lib directory + the declared model."""
    total = _size(LIB_DIR)
    for mdir in _model_dirs(model_name):
        total += _size(mdir)
    return total


def status(d: dict, job: dict) -> dict:
    """Fill the card fields of `optional_tools.trang_thai` for this tool."""
    m = get_model()
    is_installed = installed()
    lib_ok = _lib_ready()
    model_ok = _model_ready(m)

    if job.get("dang_chay"):
        tt, why = (
            "dang_cai",
            localefmt.chu(
                "Đang tải bộ nghe về, việc này mất vài phút.",
                "Downloading the offline ear, this takes a few minutes.",
            ),
        )
    elif is_installed:
        tt, why = (
            "san_sang",
            localefmt.chu(f"Sẵn sàng, mô hình {m}.", f"Ready, model {m}."),
        )
    elif lib_ok and not model_ok:
        tt, why = (
            "chua_cai",
            localefmt.chu(
                f"Đã cài thư viện nhưng thiếu mô hình {m}. Bấm cài lại.",
                f"Library installed but model {m} is missing. Install again.",
            ),
        )
    elif not lib_ok and model_ok:
        tt, why = (
            "chua_cai",
            localefmt.chu(
                f"Chưa cài thư viện (đã có sẵn mô hình {m}). Bấm cài để sử dụng.",
                f"Library not installed (model {m} is cached). Click install to use.",
            ),
        )
    elif LIB_DIR.exists():
        tt, why = (
            "chua_cai",
            localefmt.chu(
                "Bản tải về chưa đủ (thiếu thư viện hoặc mô hình). Bấm cài lại.",
                "The download is incomplete (library or model missing). Install again.",
            ),
        )
    else:
        tt, why = (
            "chua_cai",
            localefmt.chu(
                "Chưa cài. Không có key nào thì Javis chưa nghe được tin thoại.",
                "Not installed. Without a key Javis cannot hear voice messages yet.",
            ),
        )

    sz = total_size_bytes(m) if (is_installed or lib_ok) else 0

    d.update(
        {
            "trang_thai": tt,
            "ly_do": why,
            "go_duoc": LIB_DIR.exists() and not job.get("dang_chay"),
            "duong_dan": str(LIB_DIR) if LIB_DIR.exists() else (str(ROOT) if ROOT.exists() else ""),
            "dung_luong": _doc_mb(sz) if sz else "",
        }
    )
    return d


def _doc_mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB" if n else ""


def install_command() -> tuple:
    """(argv, cwd, env, timeout, error when Python cannot run) for `optional_tools._chay_tai`."""
    ROOT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["HF_HOME"] = str(
        ROOT / "hf"
    )  # the Docker user's home may not be writable; STATE_DIR is
    m = get_model()
    return (
        [
            sys.executable,
            "-s",
            str(WORKER),
            "--install",
            "--lib",
            str(LIB_DIR),
            "--model",
            m,
            "--model-dir",
            str(MODEL_DIR),
        ],
        str(ROOT),
        env,
        INSTALL_TIMEOUT,
        localefmt.chu(
            "Không chạy được Python để cài bộ nghe.",
            "Python could not be run to install the ear.",
        ),
    )


def remove() -> dict:
    if not LIB_DIR.exists():
        return {
            "ok": False,
            "error": localefmt.chu(
                "Bộ nghe offline chưa được cài.", "The offline ear is not installed."
            ),
        }
    shutil.rmtree(LIB_DIR, ignore_errors=True)
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    return {"ok": True}


def _threads() -> int:
    """0.444.4: leave one core to the server. Whisper's default takes every core, and on a 1-2 vCPU VPS a voice
    message then starved uvicorn until /health failed. JAVIS_LOCAL_STT_THREADS overrides."""
    val = (os.environ.get("JAVIS_LOCAL_STT_THREADS") or "").strip()
    if not val:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("JAVIS_LOCAL_STT_THREADS="):
                        val = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
    try:
        n = int(val or 0)
    except ValueError:
        n = 0
    return n if n > 0 else max(1, (os.cpu_count() or 2) - 1)


def _last_json(out: str) -> dict:
    for line in reversed((out or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


async def transcribe(
    data: bytes, file_name: str = "audio", lang: str | None = None
) -> dict:
    """Same contract as `stt.groq_nghe`: {"ok", "text", "model"} or {"ok": False, "ly_do", "noi_voi_javis"}."""
    import stt

    if not installed():
        return {
            "ok": False,
            "ly_do": "thieu_key",
            "noi_voi_javis": stt.loi_thanh_dong("thieu_key"),
        }
    if not data:
        return {
            "ok": False,
            "ly_do": "rong",
            "noi_voi_javis": stt.loi_thanh_dong("loi", "file rỗng"),
        }
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(str(file_name or ""))[1][:8] or ".bin"
    src = TMP_DIR / (uuid.uuid4().hex + ext)
    src.write_bytes(data)
    env = dict(
        os.environ,
        HF_HUB_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        PYTHONNOUSERSITE="1",
        HF_HOME=str(ROOT / "hf"),
    )
    try:
        async with _ONE:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-s",
                str(WORKER),
                "--lib",
                str(LIB_DIR),
                "--model",
                get_model(),
                "--model-dir",
                str(MODEL_DIR),
                "--lang",
                str(lang or ""),
                "--threads", str(_threads()),
                str(src),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ROOT),
                env=env,
                **winproc.kwargs_no_window(),
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "ok": False,
                    "ly_do": "loi",
                    "noi_voi_javis": stt.loi_thanh_dong(
                        "loi", "bộ nghe offline chạy quá lâu"
                    ),
                }
        res = _last_json(out.decode("utf-8", "replace"))
        if not res.get("ok"):
            why = str(
                res.get("error")
                or err.decode("utf-8", "replace")[-200:]
                or f"mã {proc.returncode}"
            )
            print(f"[stt local] {why}", file=sys.stderr)
            return {
                "ok": False,
                "ly_do": "loi",
                "noi_voi_javis": stt.loi_thanh_dong("loi", why[:200]),
            }
        text = stt.loc_ao_giac(str(res.get("text") or "").strip())
        if not text:
            return {
                "ok": False,
                "ly_do": "khong_nghe_ro",
                "noi_voi_javis": stt.loi_thanh_dong("khong_nghe_ro"),
            }
        return {"ok": True, "text": text, "model": "whisper-" + get_model() + " (offline)"}
    except Exception as e:  # noqa: BLE001 - a broken ear must never cost the message
        print(f"[stt local] {type(e).__name__}: {e}", file=sys.stderr)
        return {
            "ok": False,
            "ly_do": "loi",
            "noi_voi_javis": stt.loi_thanh_dong("loi", f"{type(e).__name__}"),
        }
    finally:
        src.unlink(missing_ok=True)
