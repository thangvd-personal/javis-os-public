"""Meeting STT runtime configuration and status manager.

Reuses the shared Whisper model cache in `STATE_DIR/local-ear/models` and
Hugging Face cache in `STATE_DIR/local-ear/hf` so models are never downloaded
twice. Keeps `torch` + `pyannote-audio==3.1.*` isolated inside
`STATE_DIR/meeting-ear/venv` to avoid `numpy` / `huggingface_hub` version
conflicts with `STATE_DIR/local-ear/lib`, and never imports heavy ML packages
into the main Javis OS process (`test_khoi_dong_nhe.py`).

Note: Do NOT use the em dash character (U+2014) anywhere in this file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import config as cfgmod
import localefmt

SUPPORTED_WHISPER_MODELS = {
    "large-v3-turbo": {
        "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "size_hint": "~1.6 GB",
        "label_vi": "large-v3-turbo (Chính xác cao)",
        "label_en": "large-v3-turbo (High accuracy)",
    },
    "medium": {
        "repo": "Systran/faster-whisper-medium",
        "size_hint": "~1.5 GB",
        "label_vi": "medium (Cân bằng)",
        "label_en": "medium (Balanced)",
    },
    "small": {
        "repo": "Systran/faster-whisper-small",
        "size_hint": "~0.5 GB",
        "label_vi": "small (Nhẹ)",
        "label_en": "small (Light)",
    },
}

VALID_DEVICES = ("auto", "cuda", "cpu")
VALID_LANGUAGES = ("vi", "auto", "en")

TIMEOUT_INSTALL = 2700.0

_GPU_CACHE: dict[str, Any] = {"ts": 0.0, "val": None}
_PENDING_INSTALL: dict[str, Any] = {"mode": "all", "model": "", "force": False}
_LAST_INSTALL: dict[str, Any] = {"mode": "all", "model": "", "force": False}


def set_pending_install(mode: str = "all", model: str = "", force: bool = False) -> None:
    m = str(mode or "all").strip().lower()
    if m not in ("all", "diarize", "model", "whisper", "redownload"):
        m = "all"
    _PENDING_INSTALL["mode"] = m
    _PENDING_INSTALL["model"] = str(model or "").strip()
    _PENDING_INSTALL["force"] = bool(force) or (m == "redownload")


def install_verified() -> bool:
    mode = str(_LAST_INSTALL.get("mode") or "all")
    model = str(_LAST_INSTALL.get("model") or "")
    if mode in ("model", "whisper", "redownload"):
        if model:
            return is_whisper_model_cached(model)
        return status().get("whisper_ready", False)
    ok_diarize = venv_diarize_ready()
    if mode == "diarize":
        return ok_diarize
    if model:
        return ok_diarize and is_whisper_model_cached(model)
    return ok_diarize and status().get("whisper_ready", False)


def local_ear_root() -> Path:
    return Path(cfgmod.STATE_DIR) / "local-ear"


def local_ear_lib() -> Path:
    return local_ear_root() / "lib"


def models_dir() -> Path:
    return local_ear_root() / "models"


def hf_home() -> Path:
    return local_ear_root() / "hf"


def meeting_ear_root() -> Path:
    return Path(cfgmod.STATE_DIR) / "meeting-ear"


def meeting_venv_dir() -> Path:
    return meeting_ear_root() / "venv"


def meeting_venv_python() -> Path:
    if os.name == "nt":
        return meeting_venv_dir() / "Scripts" / "python.exe"
    return meeting_venv_dir() / "bin" / "python"


def audio_dir() -> Path:
    return Path(cfgmod.STATE_DIR) / "meeting-audio"


def uploads_dir() -> Path:
    return Path(cfgmod.STATE_DIR) / "meeting-uploads"


def worker_script() -> Path:
    return Path(__file__).resolve().parent / "meeting_ear" / "worker.py"


def get_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read meeting_stt config with defaults applied (includes secret hf_token internally)."""
    if cfg is None:
        cfg = cfgmod.read_settings()
    voice = (cfg.get("voice") or {}) if isinstance(cfg, dict) else {}
    raw = (voice.get("meeting_stt") or {}) if isinstance(voice, dict) else {}
    model = str(raw.get("whisper_model") or "large-v3-turbo").strip()
    if model not in SUPPORTED_WHISPER_MODELS:
        model = "large-v3-turbo"
    dev = str(raw.get("device") or "auto").strip().lower()
    if dev not in VALID_DEVICES:
        dev = "auto"
    lang = str(raw.get("language") or "vi").strip().lower()
    if lang not in VALID_LANGUAGES:
        lang = "vi"
    diarize = True if "diarize" not in raw else bool(raw.get("diarize"))
    try:
        keep_days = int(raw.get("keep_audio_days", 30))
    except (TypeError, ValueError):
        keep_days = 30
    keep_days = max(1, min(3650, keep_days))
    tok = str(raw.get("hf_token") or "").strip()
    remote_mode = str(raw.get("remote_mode") or "off").strip().lower()
    if remote_mode not in ("off", "server", "client"):
        remote_mode = "off"
    server_base_uri = str(raw.get("server_base_uri") or "").strip()
    server_api_key = str(raw.get("server_api_key") or "").strip()
    client_base_uri = str(raw.get("client_base_uri") or "").strip()
    client_api_key = str(raw.get("client_api_key") or "").strip()
    return {
        "whisper_model": model,
        "device": dev,
        "language": lang,
        "diarize": diarize,
        "keep_audio_days": keep_days,
        "hf_token": tok,
        "remote_mode": remote_mode,
        "server_base_uri": server_base_uri,
        "server_api_key": server_api_key,
        "client_base_uri": client_base_uri,
        "client_api_key": client_api_key,
    }


def generate_diarize_key() -> str:
    import secrets
    return "sdk_live_" + secrets.token_hex(16)


def resolve_server_key(cfg: dict[str, Any] | None = None) -> str:
    mc = get_config(cfg)
    if mc.get("server_api_key"):
        return mc["server_api_key"]
    return os.getenv("SPEAKER_DIARIZATION_KEY", "").strip()


def resolve_client_key(cfg: dict[str, Any] | None = None) -> str:
    mc = get_config(cfg)
    return mc.get("client_api_key") or ""


def resolve_hf_token(cfg: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return `(token, source)` from encrypted settings or `HF_TOKEN` env var."""
    mc = get_config(cfg)
    if mc["hf_token"]:
        return mc["hf_token"], "settings"
    env_tok = os.getenv("HF_TOKEN", "").strip()
    if env_tok:
        return env_tok, "env"
    return "", ""


def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Persist sanitized meeting_stt settings into settings.json (hf_token encrypted at rest)."""
    patch = patch or {}
    clean: dict[str, Any] = {}
    if "whisper_model" in patch:
        m = str(patch["whisper_model"] or "").strip()
        if m in SUPPORTED_WHISPER_MODELS:
            clean["whisper_model"] = m
    if "device" in patch:
        d = str(patch["device"] or "").strip().lower()
        if d in VALID_DEVICES:
            clean["device"] = d
    if "language" in patch:
        l = str(patch["language"] or "").strip().lower()
        if l in VALID_LANGUAGES:
            clean["language"] = l
    if "diarize" in patch:
        clean["diarize"] = bool(patch["diarize"])
    if "keep_audio_days" in patch:
        try:
            clean["keep_audio_days"] = max(1, min(3650, int(patch["keep_audio_days"])))
        except (TypeError, ValueError):
            pass
    if patch.get("clear_hf_token"):
        clean["hf_token"] = ""
    elif "hf_token" in patch:
        tok = str(patch["hf_token"] or "").strip()
        if tok and not tok.startswith("••••"):
            clean["hf_token"] = tok
    if "remote_mode" in patch:
        rm = str(patch["remote_mode"] or "off").strip().lower()
        if rm in ("off", "server", "client"):
            clean["remote_mode"] = rm
    if "server_base_uri" in patch:
        clean["server_base_uri"] = str(patch["server_base_uri"] or "").strip().rstrip("/")
    if "client_base_uri" in patch:
        clean["client_base_uri"] = str(patch["client_base_uri"] or "").strip().rstrip("/")
    if patch.get("clear_server_api_key"):
        clean["server_api_key"] = ""
    elif "server_api_key" in patch:
        sk = str(patch["server_api_key"] or "").strip()
        if sk and not sk.startswith("••••"):
            clean["server_api_key"] = sk
    if patch.get("clear_client_api_key"):
        clean["client_api_key"] = ""
    elif "client_api_key" in patch:
        ck = str(patch["client_api_key"] or "").strip()
        if ck and not ck.startswith("••••"):
            clean["client_api_key"] = ck

    if clean:
        new_cfg = cfgmod.update_settings({"voice": {"meeting_stt": clean}})
    else:
        new_cfg = cfgmod.read_settings()
    return status(cfg=new_cfg)


def gpu_info(force: bool = False) -> dict[str, Any]:
    """Detect NVIDIA GPU via nvidia-smi without importing PyTorch."""
    now = time.time()
    ttl = 60.0 if (_GPU_CACHE["val"] or {}).get("has_gpu") else 10.0
    if not force and _GPU_CACHE["val"] is not None and (now - _GPU_CACHE["ts"]) < ttl:
        return dict(_GPU_CACHE["val"])
    info = {"has_gpu": False, "gpu_name": "", "vram_mb": 0}
    nvsmi = shutil.which("nvidia-smi") or ("/usr/bin/nvidia-smi" if Path("/usr/bin/nvidia-smi").is_file() else "")
    if nvsmi:
        try:
            out = subprocess.check_output(
                [nvsmi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=6.0,
            )
            line = (out.strip().splitlines() or [""])[0].strip()
            if line:
                parts = [p.strip() for p in line.split(",")]
                info["has_gpu"] = True
                info["gpu_name"] = parts[0]
                if len(parts) > 1:
                    try:
                        info["vram_mb"] = int(float(parts[1]))
                    except ValueError:
                        pass
        except Exception:
            pass
    _GPU_CACHE["ts"] = now
    _GPU_CACHE["val"] = dict(info)
    return info


def is_whisper_model_cached(model_name: str) -> bool:
    spec = SUPPORTED_WHISPER_MODELS.get(model_name)
    if not spec:
        return False
    slug = "models--" + spec["repo"].replace("/", "--")
    root = models_dir() / slug
    if not root.is_dir():
        return False
    try:
        if any(root.rglob("*.incomplete")):
            return False
        for mb in root.rglob("model.bin"):
            try:
                if not mb.is_file() or mb.stat().st_size <= 0:
                    continue
                if (mb.parent / "config.json").is_file():
                    return True
            except OSError:
                continue
        return False
    except OSError:
        return False


def delete_cached_whisper_model(model_name: str) -> dict[str, Any]:
    """Remove a cached Whisper model snapshot and any non-shared HF blobs inside models_dir."""
    spec = SUPPORTED_WHISPER_MODELS.get(model_name)
    if not spec:
        return {"ok": False, "error": f"Unsupported model: {model_name}"}
    base = models_dir().resolve()
    slug = "models--" + spec["repo"].replace("/", "--")
    root = base / slug
    removed = 0
    if root.exists():
        candidate_targets: list[Path] = []
        try:
            for item in root.rglob("*"):
                if item.is_symlink():
                    try:
                        resolved = item.resolve()
                        if resolved != base and resolved.is_relative_to(base):
                            candidate_targets.append(resolved)
                    except OSError:
                        pass
        except OSError:
            pass

        other_refs: set[Path] = set()
        try:
            for sibling in base.glob("models--*"):
                if sibling.name == slug or not sibling.is_dir():
                    continue
                for s_item in sibling.rglob("*"):
                    if s_item.is_symlink():
                        try:
                            other_refs.add(s_item.resolve())
                        except OSError:
                            pass
        except OSError:
            pass

        for tgt in candidate_targets:
            if tgt in other_refs:
                continue
            try:
                if tgt.is_file() or tgt.is_symlink():
                    tgt.unlink(missing_ok=True)
                    removed += 1
                    for suffix in (".refs", ".lock", ".incomplete"):
                        Path(str(tgt) + suffix).unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(root, ignore_errors=True)
        removed += 1
    locks_dir = base / ".locks" / slug
    if locks_dir.exists():
        shutil.rmtree(locks_dir, ignore_errors=True)
    return {"ok": True, "model": model_name, "removed": removed}


def cached_whisper_models() -> dict[str, bool]:
    return {name: is_whisper_model_cached(name) for name in SUPPORTED_WHISPER_MODELS}


def local_lib_ready() -> bool:
    lib = local_ear_lib()
    return (lib / "faster_whisper").is_dir() and (lib / "ctranslate2").is_dir()


def venv_python_ready() -> bool:
    return meeting_venv_python().is_file()


def _site_has_diarize_stack(sp: Path) -> bool:
    return (
        (sp / "pyannote" / "audio").is_dir()
        and (sp / "torch").is_dir()
        and (sp / "faster_whisper").is_dir()
        and (sp / "typing_extensions.py").is_file()
    )


def venv_diarize_ready() -> bool:
    py = meeting_venv_python()
    if not py.is_file():
        return False
    site_candidates = [
        meeting_venv_dir() / "lib",
        meeting_venv_dir() / "Lib" / "site-packages",
    ]
    for cand in site_candidates:
        if not cand.exists():
            continue
        if _site_has_diarize_stack(cand):
            return True
        try:
            for sp in cand.glob("python*/site-packages"):
                if _site_has_diarize_stack(sp):
                    return True
        except OSError:
            pass
    return False


def venv_whisper_ready() -> bool:
    py = meeting_venv_python()
    if not py.is_file():
        return False
    for cand in (meeting_venv_dir() / "lib", meeting_venv_dir() / "Lib" / "site-packages"):
        if not cand.exists():
            continue
        if (cand / "faster_whisper").is_dir():
            return True
        try:
            for sp in cand.glob("python*/site-packages"):
                if (sp / "faster_whisper").is_dir():
                    return True
        except OSError:
            pass
    return False


def resolve_worker_python(need_diarize: bool) -> tuple[str, str]:
    """Choose execution interpreter & optional PYTHONPATH cleanly.

    - If diarization is needed and meeting-ear venv is ready -> `(venv_python, '')`.
    - Otherwise reuse `sys.executable` + `STATE_DIR/local-ear/lib` when present,
      or `(venv_python, '')` if venv exists with faster_whisper.
    """
    if need_diarize and venv_diarize_ready():
        return str(meeting_venv_python()), ""
    if local_lib_ready():
        return sys.executable, str(local_ear_lib())
    if venv_whisper_ready():
        return str(meeting_venv_python()), ""
    return "", ""


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file() and not f.is_symlink():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def status(cfg: dict[str, Any] | None = None, install_job: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public capability snapshot (NEVER includes hf_token plaintext)."""
    mc = get_config(cfg)
    tok, tok_src = resolve_hf_token(cfg)
    cached = cached_whisper_models()
    any_cached_model = any(cached.values())
    lib_ok = local_lib_ready() or venv_whisper_ready()
    venv_diarize_ok = venv_diarize_ready()
    whisper_ready = lib_ok and any_cached_model
    diarize_ready = venv_diarize_ok and bool(tok)
    gpu = gpu_info()
    has_ffmpeg = bool(shutil.which("ffmpeg"))

    models_list = []
    for mid, spec in SUPPORTED_WHISPER_MODELS.items():
        models_list.append({
            "id": mid,
            "repo": spec["repo"],
            "cached": cached[mid],
            "size_hint": spec["size_hint"],
            "label": localefmt.chu(spec["label_vi"], spec["label_en"]),
        })

    if install_job is None:
        try:
            import optional_tools
            install_job = optional_tools._viec.get("meeting_ear") or {}
        except Exception:
            install_job = {}

    return {
        "ok": True,
        "installed": whisper_ready and venv_diarize_ok,
        "whisper_ready": whisper_ready,
        "lib_ready": lib_ok,
        "venv_diarize_ready": venv_diarize_ok,
        "diarize_ready": diarize_ready,
        "has_hf_token": bool(tok),
        "hf_token_source": tok_src,
        "has_ffmpeg": has_ffmpeg,
        "has_gpu": gpu["has_gpu"],
        "gpu_name": gpu["gpu_name"],
        "vram_mb": gpu["vram_mb"],
        "cached_whisper_models": cached,
        "models": models_list,
        "model_dir": str(models_dir()),
        "venv_dir": str(meeting_venv_dir()),
        "installing": bool((install_job or {}).get("dang_chay")),
        "install_ok": (install_job or {}).get("thanh_cong"),
        "install_mode": str(_LAST_INSTALL.get("mode") or ""),
        "install_model": str(_LAST_INSTALL.get("model") or ""),
        "install_progress": str((install_job or {}).get("tien_do") or ""),
        "install_log": str((install_job or {}).get("log") or ""),
        "install_error": str((install_job or {}).get("loi") or ""),
        "remote_diarize": {
            "mode": mc["remote_mode"],
            "server_base_uri": mc["server_base_uri"],
            "has_server_key": bool(resolve_server_key(cfg)),
            "server_ready": bool(resolve_server_key(cfg) and diarize_ready),
            "client_base_uri": mc["client_base_uri"],
            "has_client_key": bool(resolve_client_key(cfg)),
            "client_ready": bool(mc["client_base_uri"] and resolve_client_key(cfg)),
        },
        "config": {
            "whisper_model": mc["whisper_model"],
            "device": mc["device"],
            "language": mc["language"],
            "diarize": mc["diarize"],
            "keep_audio_days": mc["keep_audio_days"],
            "remote_mode": mc["remote_mode"],
            "server_base_uri": mc["server_base_uri"],
            "client_base_uri": mc["client_base_uri"],
        },
    }


def worker_env(cfg: dict[str, Any] | None = None, extra_pythonpath: str = "") -> dict[str, str]:
    env = dict(os.environ)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["HF_HOME"] = str(hf_home())
    env["TORCH_HOME"] = str(meeting_ear_root() / "torch-cache")
    tok, _ = resolve_hf_token(cfg)
    if tok:
        env["HF_TOKEN"] = tok
    if extra_pythonpath:
        old = env.get("PYTHONPATH", "").strip()
        env["PYTHONPATH"] = f"{extra_pythonpath}{os.pathsep}{old}" if old else extra_pythonpath
    return env


def describe() -> dict[str, Any]:
    return {
        "id": "meeting_ear",
        "ten": localefmt.chu("Bộ nghe Cuộc họp (Whisper + Diarization)", "Meeting ear (Whisper + Diarization)"),
        "mo_ta": localefmt.chu(
            "Nhận dạng giọng nói cuộc họp dài bằng faster-whisper (tận dụng model sẵn có trong STATE_DIR/local-ear/models) "
            "kết hợp tách người nói pyannote/speaker-diarization-3.1 trong venv riêng hỗ trợ GPU CUDA.",
            "Long meeting transcription with faster-whisper (reusing cached models in STATE_DIR/local-ear/models) "
            "plus pyannote/speaker-diarization-3.1 speaker diarization inside an isolated CUDA-capable venv.",
        ),
        "dung_luong_uoc": "~3 - 5 GB",
    }


def tool_status(d: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    st = status(install_job=job)
    if job.get("dang_chay"):
        tt = "dang_cai"
        why = localefmt.chu("Đang cài đặt bộ nghe Cuộc họp...", "Installing Meeting ear...")
    elif st["installed"]:
        tt = "san_sang"
        if st["has_hf_token"]:
            why = localefmt.chu("Sẵn sàng tách người nói (Whisper GPU + pyannote).", "Ready for meeting transcription and speaker diarization.")
        else:
            why = localefmt.chu(
                "Đã cài pyannote + Whisper, hãy dán HF_TOKEN ở trang Ghi âm để bật tách người nói.",
                "pyannote + Whisper installed; add HF_TOKEN on the Record page to enable speaker diarization.",
            )
    elif st["whisper_ready"]:
        tt = "chua_cai"
        why = localefmt.chu(
            "Whisper sẵn sàng, bấm Cài đặt để bổ sung pyannote tách người nói và model large-v3-turbo.",
            "Whisper is ready; click Install to add pyannote speaker diarization and large-v3-turbo.",
        )
    else:
        tt = "chua_cai"
        why = localefmt.chu("Chưa cài bộ nghe Cuộc họp.", "Meeting ear is not installed yet.")

    sz = _dir_size_bytes(meeting_ear_root())
    d.update({
        "trang_thai": tt,
        "ly_do": why,
        "go_duoc": meeting_ear_root().exists() and not job.get("dang_chay"),
        "duong_dan": str(meeting_ear_root()) if meeting_ear_root().exists() else "",
        "dung_luong": f"{sz / 1024 / 1024:.0f} MB" if sz else "",
    })
    return d


def install_command(mode: str | None = None, model: str | None = None, force: bool | None = None) -> tuple:
    """Return `(argv, cwd, env, timeout, error_msg)` for `optional_tools._chay_tai`."""
    meeting_ear_root().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)
    hf_home().mkdir(parents=True, exist_ok=True)
    cfg = cfgmod.read_settings()
    eff_mode = str(mode or _PENDING_INSTALL.get("mode") or "all").strip().lower()
    eff_model = str(model or _PENDING_INSTALL.get("model") or get_config(cfg)["whisper_model"] or "large-v3-turbo").strip()
    eff_force = bool(_PENDING_INSTALL.get("force") if force is None else force) or (eff_mode == "redownload")
    _PENDING_INSTALL["mode"] = "all"
    _PENDING_INSTALL["model"] = ""
    _PENDING_INSTALL["force"] = False
    if eff_model not in SUPPORTED_WHISPER_MODELS:
        eff_model = "large-v3-turbo"
    _LAST_INSTALL["mode"] = eff_mode
    _LAST_INSTALL["model"] = eff_model
    _LAST_INSTALL["force"] = eff_force
    need_local_lib = eff_mode in ("model", "whisper", "redownload") and local_lib_ready() and not venv_diarize_ready()
    env = worker_env(cfg, extra_pythonpath=str(local_ear_lib()) if need_local_lib else "")
    argv = [
        sys.executable,
        "-u",
        str(worker_script()),
        "--install",
        "--install-mode",
        eff_mode,
        "--venv-dir",
        str(meeting_venv_dir()),
        "--model-dir",
        str(models_dir()),
        "--hf-home",
        str(hf_home()),
        "--model",
        eff_model,
    ]
    if eff_force:
        argv.append("--force")
    return (
        argv,
        str(meeting_ear_root()),
        env,
        TIMEOUT_INSTALL,
        localefmt.chu("Không tìm thấy Python để cài bộ nghe Cuộc họp.", "Python executable not found."),
    )


def remove() -> dict[str, Any]:
    """Remove only STATE_DIR/meeting-ear (preserves shared STATE_DIR/local-ear/models and meeting-audio)."""
    root = meeting_ear_root()
    if not root.exists():
        return {"ok": False, "error": localefmt.chu("Chưa có bộ nghe Cuộc họp để gỡ.", "Meeting ear is not installed.")}
    try:
        shutil.rmtree(root, ignore_errors=True)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
