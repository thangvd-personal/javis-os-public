# SPDX-License-Identifier: Apache-2.0
"""Audio inspection. Standard-library `wave` plus numpy - nothing to install.

Decoding happens in the browser (`AudioContext` handles mp3, m4a, webm, wav and
hands back 16 kHz mono WAV), so the server never needs a codec. That keeps the
install to four packages and means there is no ffmpeg step to explain.

The one thing worth doing here is the noise check. Measured on this pipeline,
extraction is reliable down to about −5 dB SNR and starts *inventing* below
−10 dB: "aisle four, bay seven" came back as "apartment 4 day care" with full
confidence. A tool that guesses at that point is worse than one that says it
cannot hear.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

TARGET_RATE = 16000

# Calibrated against scripts/robustness.py, which degrades a known note and
# re-measures extraction. Two signals, because one was not enough:
#
#   * A percentile SNR estimate separates quiet rooms from loud ones, but it
#     could NOT tell -5 dB (extracts 6/6) from -10 dB (extracts 1/6) - both
#     land near 0 on its scale. That is precisely the boundary that matters.
#   * Speech amplitude-modulates at the syllable rate, roughly 2-16 Hz.
#     Broadband noise does not. How much envelope energy sits in that band
#     tracks extraction quality where the SNR estimate goes flat.
#
# On the 11 measured conditions: everything scoring 6/6 had a modulation index
# above 0.470; everything that degraded was below 0.452.
#
# This is a COARSE GATE, not a calibrated confidence, and it is deliberately
# biased toward letting audio through. The two mistakes are not symmetric:
# wrongly refusing good audio makes the app useless and the person gives up,
# while wrongly passing degraded audio produces a warning telling them to check
# every value against the recording - which they can, because each value plays
# back. So only the cases that collapse entirely (1/6 fields and worse) are
# blocked; partial degradation warns.
MOD_UNUSABLE = 0.455
MOD_POOR = 0.490
SNR_POOR_DB = 5.0
# Above this the recording is simply clean and the modulation test does not get
# a vote. This matters: a real person taps record, pauses, speaks, pauses, then
# reaches for stop. That silence is not noise, but it dominates the amplitude
# envelope below 2 Hz and drags the modulation ratio down - a pristine clip with
# 99 dB SNR and five seconds of trailing silence was being called "too noisy".
# The gate exists to catch noise. With no noise, there is nothing to catch.
SNR_CLEAN_DB = 15.0
# Under this there is not enough envelope to judge modulation - a two-word answer
# has barely two syllables. Fall back to the SNR estimate rather than failing a
# short clip for a measurement that cannot be made.
MOD_MIN_SECONDS = 2.0


@dataclass
class AudioInfo:
    seconds: float
    rate: int
    channels: int
    samples: int
    snr_db: float
    speech_ratio: float          # fraction of the clip that is speech, not silence
    modulation: float            # 2-16 Hz envelope energy share; speech has it, noise doesn't
    quality: str                 # good | poor | unusable | silent
    warning: str                 # empty when quality is good


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV bytes to mono float32 in [-1, 1]."""
    with wave.open(io.BytesIO(data)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"Expected 16-bit audio, got {width * 8}-bit.")
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, rate


def estimate_snr(x: np.ndarray, rate: int) -> tuple[float, float]:
    """Rough SNR in dB, and how much of the clip is speech.

    ponytail: energy-percentile split, not a VAD. It only has to separate "someone
    is talking" from "room tone", which it does well enough to warn on. Swap in a
    real VAD if this ever has to score audio rather than gate it.
    """
    if x.size == 0:
        return -99.0, 0.0
    win = max(1, int(rate * 0.03))                       # 30 ms frames
    n = x.size // win
    if n < 3:
        return -99.0, 0.0
    frames = x[: n * win].reshape(n, win)
    energy = (frames ** 2).mean(axis=1) + 1e-12

    # The quietest fifth is the noise floor; the loudest fifth is speech.
    quiet = np.percentile(energy, 20)
    loud = np.percentile(energy, 80)
    snr = 10 * np.log10(max(loud - quiet, 1e-12) / max(quiet, 1e-12))

    speech = float((energy > quiet * 4).mean())
    return float(np.clip(snr, -99, 99)), speech


def trim_silence(x: np.ndarray, rate: int) -> np.ndarray:
    """Drop leading and trailing silence, keeping everything in between.

    Only the ends: pauses *within* a note are part of how people speak and
    removing them would distort the syllable rhythm we are about to measure.
    """
    win = max(1, int(rate * 0.02))
    n = x.size // win
    if n < 4:
        return x
    energy = (x[: n * win].reshape(n, win) ** 2).mean(axis=1)
    floor = np.percentile(energy, 10)
    active = np.flatnonzero(energy > max(floor * 6, 1e-9))
    if active.size < 2:
        return x
    a = max(0, (active[0] - 2) * win)
    b = min(x.size, (active[-1] + 3) * win)
    return x[a:b]


def modulation_index(x: np.ndarray, rate: int) -> float:
    """Share of the amplitude envelope's energy in the 2-16 Hz syllable band.

    Measured on the speech only. Silence at either end is envelope energy below
    2 Hz, and leaving it in made the score depend on how long somebody took to
    press stop rather than on how noisy the room was.
    """
    x = trim_silence(x, rate)
    win = max(1, int(rate * 0.01))               # 10 ms -> 100 Hz envelope
    n = x.size // win
    if n < 32:
        return 0.0
    env = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1) + 1e-12)
    env = env - env.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(env.size))) ** 2
    freqs = np.fft.rfftfreq(env.size, win / rate)
    band = (freqs >= 2) & (freqs <= 16)
    return float(spec[band].sum() / (spec.sum() + 1e-12))


def inspect(data: bytes) -> AudioInfo:
    x, rate = read_wav(data)
    seconds = len(x) / rate if rate else 0.0
    snr, speech = estimate_snr(x, rate)
    mod = modulation_index(x, rate)
    peak = float(np.max(np.abs(x))) if x.size else 0.0

    quality, warning = "good", ""
    if seconds < 0.4 or peak < 0.005:
        quality = "silent"
        warning = ("That recording is silent or almost silent. Check the right "
                   "microphone is selected and that the browser has permission.")
    elif snr >= SNR_CLEAN_DB:
        # Demonstrably clean. Nothing to warn about.
        pass
    elif seconds >= MOD_MIN_SECONDS and mod < MOD_UNUSABLE:
        quality = "unusable"
        warning = ("Too much background noise to trust this. Past this point the "
                   "model stops reporting errors and starts inventing plausible "
                   "values instead - in testing, \"aisle four, bay seven\" came back "
                   "as \"apartment 4 day care\". Please re-record somewhere quieter.")
    elif (seconds >= MOD_MIN_SECONDS and mod < MOD_POOR) or snr < SNR_POOR_DB:
        quality = "poor"
        warning = ("Noisy. It usually still reads correctly at this level, but play "
                   "back each value against the audio before saving.")
    elif speech < 0.05:
        quality = "poor"
        warning = "Barely any speech detected - mostly silence or background noise."

    return AudioInfo(seconds=round(seconds, 2), rate=rate, channels=1, samples=int(x.size),
                     snr_db=round(snr, 1), speech_ratio=round(speech, 3),
                     modulation=round(mod, 4), quality=quality, warning=warning)


def peaks(data: bytes, buckets: int = 400) -> list[float]:
    """Downsampled peak envelope for drawing a waveform, normalised to 0..1.

    Peaks rather than RMS: peaks keep the shape of speech legible at small
    sizes, where an RMS envelope flattens into a sausage.
    """
    x, _ = read_wav(data)
    if x.size == 0:
        return []
    n = min(buckets, max(1, x.size))
    edges = np.linspace(0, x.size, n + 1).astype(int)
    out = np.array([np.abs(x[a:b]).max() if b > a else 0.0
                    for a, b in zip(edges[:-1], edges[1:])])
    top = out.max()
    return (out / top).round(3).tolist() if top > 0 else out.tolist()


def slice_wav(data: bytes, start_s: float, end_s: float, pad_s: float = 0.25) -> bytes:
    """Cut out one field's window, so a person can hear just that phrase.

    Padded slightly at both ends because a word clipped at the boundary is
    hard to recognise, and the point is for a human to verify the value.
    """
    x, rate = read_wav(data)
    a = max(0, int((start_s - pad_s) * rate))
    b = min(len(x), int((end_s + pad_s) * rate))
    if b <= a:
        a, b = 0, min(len(x), rate)
    clip = (np.clip(x[a:b], -1, 1) * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(clip.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Javis OS adapter - added 2026-11-16. The code above is the original module
# verbatim (inspection only: it measures noise, it does NOT remove it).
# The server side differs in one respect: uploads arrive as .m4a/.webm/.mp3,
# not as browser-decoded WAV, so this adapter adds the one ffmpeg decode
# step (16 kHz mono PCM, same as bench.py prep_audio / Javis
# _ensure_16k_mono_wav) and a CLI gate around inspect().
#
# Thresholds are NOT recalibrated here: MOD_UNUSABLE / MOD_POOR / SNR_* were
# calibrated on short field-extraction notes, not on a 79-minute multi-speaker
# meeting. Treat the gate as a warning signal on meeting audio until it is
# re-measured against NoteX on meeting clips - do not block a meeting job
# solely on `quality == "unusable"` without listening first.
# ---------------------------------------------------------------------------

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

GATE_DECISION = {"good": "proceed", "poor": "warn", "unusable": "block", "silent": "block"}
# Exit codes for shell / job-queue use: 0 proceed, 1 warn, 2 block.
GATE_EXIT = {"proceed": 0, "warn": 1, "block": 2}


def decode_to_wav16k(src: str | Path, out_wav: str | Path,
                     start: float = 0.0, duration: float = 0.0) -> Path:
    """ffmpeg decode any upload to 16 kHz mono 16-bit WAV.

    Same normalisation as bench.py `prep_audio`: `-ss`/`-t` cut first, then
    `-ar 16000 -ac 1 -c:a pcm_s16le`. The output is exactly what `inspect()`
    expects (read_wav requires 16-bit WAV) and what faster-whisper /
    PhoWhisper-CT2 consume downstream, so one decode serves both.
    """
    src, out_wav = Path(src), Path(out_wav)
    if not src.is_file():
        raise FileNotFoundError(f"audio not found: {src}")
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(src), "-ar", str(TARGET_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(out_wav)]
    subprocess.run(cmd, check=True)
    return out_wav


def inspect_file(src: str | Path, start: float = 0.0, duration: float = 0.0,
                 keep_wav: str | Path | None = None) -> tuple[AudioInfo, Path | None]:
    """Decode `src` to 16 kHz mono WAV (if needed) and run inspect() on it.

    Returns (info, wav_path). `wav_path` is the kept file when `keep_wav`
    is given, else None (the temp decode is deleted). Reuse the kept WAV as
    the bench / STT input to avoid decoding twice.
    """
    src = Path(src)
    if keep_wav is not None:
        wav = decode_to_wav16k(src, keep_wav, start, duration)
        return inspect(wav.read_bytes()), wav
    with tempfile.TemporaryDirectory(prefix="inspect16k-") as td:
        wav = decode_to_wav16k(src, Path(td) / "clip.wav", start, duration)
        return inspect(wav.read_bytes()), None


def gate_decision(info: AudioInfo) -> str:
    """Map inspect() quality to a pipeline decision: proceed | warn | block."""
    return GATE_DECISION[info.quality]


def _self_test() -> None:
    # Round-trip a synthetic clip and check the noise gate actually gates.
    rate = TARGET_RATE
    t = np.linspace(0, 4, rate * 4, endpoint=False)
    # A steady tone is NOT speech-like and the gate is right to reject it. What
    # makes speech speech, as far as this measure cares, is the ~5 Hz syllable
    # envelope - so the fixture has to have one.
    syllables = (0.5 + 0.5 * np.sin(2 * np.pi * 5 * t)) ** 2
    speech = 0.4 * np.sin(2 * np.pi * 180 * t) * syllables
    speech[: rate // 2] = 0                       # a little leading silence

    def to_wav(x: np.ndarray) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
        return buf.getvalue()

    clean = inspect(to_wav(speech))
    assert clean.quality == "good", f"clean audio judged {clean.quality}: {clean.warning}"
    assert 3.9 < clean.seconds < 4.1, clean.seconds

    rng = np.random.default_rng(0)
    loud_noise = speech + rng.standard_normal(len(t)) * 0.6
    bad = inspect(to_wav(loud_noise))
    assert bad.quality in ("poor", "unusable"), f"noisy audio judged {bad.quality} (snr {bad.snr_db})"
    assert bad.warning, "a bad recording must explain itself"

    # The gate must catch the case where the model invents rather than errors.
    # Pure noise has no syllable-rate structure at all.
    noise_only = inspect(to_wav(rng.standard_normal(len(t)) * 0.3))
    assert noise_only.quality == "unusable", \
        f"pure noise judged {noise_only.quality} (mod {noise_only.modulation})"
    assert "inventing" in noise_only.warning

    # The regression that made the app unusable: somebody taps record, pauses,
    # speaks, pauses, then reaches for stop. That silence is not noise, but it
    # dominates the envelope below 2 Hz. A pristine clip was being refused for
    # "too much background noise" at 99 dB SNR.
    for lead, tail in ((1, 1), (2, 5), (5, 8)):
        padded = np.concatenate([np.zeros(rate * lead), speech, np.zeros(rate * tail)])
        got = inspect(to_wav(padded))
        assert got.quality == "good", (
            f"{lead}s lead + {tail}s trailing silence judged {got.quality} "
            f"(mod {got.modulation}, snr {got.snr_db}) - silence is not noise"
        )

    silent = inspect(to_wav(np.zeros_like(t)))
    assert silent.quality == "silent", silent.quality

    # A short clip must not be failed for a modulation estimate that cannot be made.
    short = inspect(to_wav(speech[: int(rate * 1.2)]))
    assert short.quality != "unusable", f"short clip wrongly rejected ({short.modulation})"

    piece = slice_wav(to_wav(speech), 0.5, 1.0)
    got, _ = read_wav(piece)
    # 0.5s of speech plus 0.25s padding either side
    assert 0.9 < len(got) / rate < 1.1, len(got) / rate

    print(f"ok - clean mod {clean.modulation} good · noisy {bad.quality} · "
          f"pure noise {noise_only.quality} · silence caught · slice {len(got)/rate:.2f}s")


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect audio quality (noise gate) for Javis OS")
    ap.add_argument("audio", nargs="?", default=None, help="Source audio (.m4a/.webm/.mp3/.wav)")
    ap.add_argument("--start", type=float, default=0.0, help="Clip start in seconds")
    ap.add_argument("--duration", type=float, default=0.0, help="Clip length in seconds (0 = whole file)")
    ap.add_argument("--keep-wav", default=None, help="Keep the decoded 16kHz mono WAV here (reuse as STT input)")
    ap.add_argument("--json", action="store_true", help="Print the result as JSON")
    ap.add_argument("--self-test", action="store_true", help="Run the module's built-in synthetic self-test")
    args = ap.parse_args()

    if args.self_test:
        _self_test()
        return 0
    if args.audio is None:
        ap.error("audio is required (unless --self-test)")

    info, wav = inspect_file(args.audio, args.start, args.duration, args.keep_wav)
    decision = gate_decision(info)
    if args.json:
        print(json.dumps({**info.__dict__, "decision": decision,
                          "wav": str(wav) if wav else None}, ensure_ascii=False))
    else:
        print(f"{args.audio}: {info.seconds}s, SNR {info.snr_db} dB, "
              f"speech {info.speech_ratio:.3f}, modulation {info.modulation} "
              f"-> quality={info.quality}, decision={decision}")
        if info.warning:
            print(f"warning: {info.warning}")
        if wav:
            print(f"decoded 16kHz WAV kept at: {wav}")
    return GATE_EXIT[decision]


if __name__ == "__main__":
    raise SystemExit(main())
