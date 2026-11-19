"""静音检测（只看音量，不需要语音识别模型）。

做法：
1. 音频按 20ms 分帧，算每帧响度（dBFS）
2. 阈值自适应：底噪（第 10 百分位）+ max(10dB, 动态范围 × 35%)；也可手动指定
3. 高于阈值的帧算“有声”，有声片段前后各扩 keep_padding 防吞字；<60ms 的瞬态（碰麦、咔哒）忽略
4. 两段有声之间的静音 ≥ min_pause → 剪掉，只留 keep_natural 的自然停顿
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

FRAME = 0.02


def load_db(wav: Path) -> np.ndarray:
    with wave.open(str(wav), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("需要 16-bit PCM wav")
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sr * FRAME)
    m = len(x) // n
    if m == 0:
        return np.array([-120.0])
    rms = np.sqrt(np.mean(x[: m * n].reshape(m, n) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def threshold(db: np.ndarray, manual) -> float:
    if manual not in (None, "auto"):
        return float(manual)
    floor = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 95))
    return floor + max(10.0, (loud - floor) * 0.35)


def voiced_spans(db: np.ndarray, thr: float, min_blip: float = 0.06, bridge: float = 0.12) -> list[list[float]]:
    on = db > thr
    spans: list[list[float]] = []
    i, n = 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            spans.append([i * FRAME, j * FRAME])
            i = j
        else:
            i += 1
    # 字与字之间极短的间隙合并
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= bridge:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [s for s in merged if s[1] - s[0] >= min_blip]


def detect(wav: Path, duration: float, p: dict) -> dict:
    db = load_db(wav)
    thr = threshold(db, p.get("threshold_db"))
    spans = voiced_spans(db, thr)
    pad, min_cut = p["keep_padding"], p["min_cut"]
    keep_each = max(pad, p["keep_natural"] / 2)
    cuts: list[list[float]] = []
    if not spans:
        return {"threshold_db": round(thr, 1), "cuts": [], "keeps": [[0.0, duration]]}
    if spans[0][0] - pad >= min_cut:
        cuts.append([0.0, spans[0][0] - pad])
    for (_, e), (s, _) in zip(spans, spans[1:]):
        if s - e >= p["min_pause"]:
            a, b = e + keep_each, s - keep_each
            if b - a >= min_cut:
                cuts.append([a, b])
    if duration - (spans[-1][1] + pad) >= min_cut:
        cuts.append([spans[-1][1] + pad, duration])
    return {"threshold_db": round(thr, 1), "cuts": [[round(a, 3), round(b, 3)] for a, b in cuts],
            "keeps": complement(cuts, duration)}


def complement(cuts: list[list[float]], duration: float, min_keep: float = 0.05) -> list[list[float]]:
    keeps, cur = [], 0.0
    for a, b in sorted(cuts):
        if a > cur:
            keeps.append([cur, a])
        cur = max(cur, b)
    if cur < duration:
        keeps.append([cur, duration])
    return [[round(a, 4), round(b, 4)] for a, b in keeps if b - a >= min_keep]


def snap(keeps: list[list[float]], fps: float, duration: float) -> list[list[float]]:
    """对齐到帧边界，保证每段音视频等长，拼接不漂移。"""
    out = []
    for a, b in keeps:
        a2, b2 = round(a * fps) / fps, min(round(b * fps) / fps, duration)
        if b2 - a2 >= 1 / fps:
            out.append([a2, b2])
    return out
