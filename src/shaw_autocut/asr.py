"""语音识别。统一输出格式（秒）：

{
  "engine": "funasr",
  "sentences": [
    {"start": 0.52, "end": 3.1, "text": "大家好，今天聊聊……",
     "tokens": [{"t": "大", "s": 0.52, "e": 0.70}, ...]}
  ]
}
tokens 只用于找停顿和字幕对齐；"t" 可能缺失。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import state as st

# 英文/数字按词，中文等其它文字按字；标点不算 token
_TOKEN_RE = re.compile(r"[A-Za-z0-9'’]+|[^\W_A-Za-z0-9]")
_MODEL_CACHE: dict = {}


def _device(prefs: dict) -> str:
    dev = prefs["asr"]["device"]
    if dev != "auto":
        return dev
    try:
        import torch  # noqa
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _hotwords(prefs: dict, override: str | None = None) -> list[str]:
    path = override or prefs["asr"].get("hotwords_file")
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    return [w.strip() for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]


def load_model(prefs: dict):
    engine = prefs["asr"]["engine"]
    dev = _device(prefs)
    key = (engine, dev, prefs["asr"].get("whisper_model"))
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if engine == "funasr":
        from funasr import AutoModel
        model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc",
                          device="cuda:0" if dev == "cuda" else "cpu", disable_update=True)
    elif engine == "whisper":
        if prefs["install"].get("hf_endpoint"):
            os.environ.setdefault("HF_ENDPOINT", prefs["install"]["hf_endpoint"])
        from faster_whisper import WhisperModel
        model = WhisperModel(prefs["asr"]["whisper_model"], device=dev,
                             compute_type="float16" if dev == "cuda" else "int8")
    else:
        raise ValueError(f"未知识别引擎: {engine}")
    _MODEL_CACHE[key] = model
    return model


def _split_tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def _funasr(model, wav: Path, hotwords: list[str]) -> list[dict]:
    kwargs = {"input": str(wav), "batch_size_s": 300, "sentence_timestamp": True}
    if hotwords:
        kwargs["hotword"] = " ".join(hotwords)
    res = model.generate(**kwargs)
    sentences = []
    for item in res:
        infos = item.get("sentence_info")
        if not infos:  # 兜底：没有分句信息时整体当一句
            infos = [{"text": item.get("text", ""), "timestamp": item.get("timestamp", []),
                      "start": (item.get("timestamp") or [[0, 0]])[0][0],
                      "end": (item.get("timestamp") or [[0, 0]])[-1][1]}]
        for s in infos:
            stamps = s.get("timestamp") or []
            words = _split_tokens(s.get("text", ""))
            pair = len(words) == len(stamps)
            tokens = [{"t": words[i] if pair else None, "s": a / 1000.0, "e": b / 1000.0}
                      for i, (a, b) in enumerate(stamps)]
            sentences.append({"start": s["start"] / 1000.0, "end": s["end"] / 1000.0,
                              "text": s.get("text", "").strip(), "tokens": tokens})
    return sentences


def _whisper(model, wav: Path, hotwords: list[str]) -> list[dict]:
    kwargs = dict(language="zh", word_timestamps=True, vad_filter=False, beam_size=5,
                  initial_prompt="以下是普通话口播内容，请使用简体中文并加上标点。")
    if hotwords:
        kwargs["hotwords"] = " ".join(hotwords)
    try:
        segments, _info = model.transcribe(str(wav), **kwargs)
    except TypeError:  # 老版本 faster-whisper 没有 hotwords 参数
        kwargs.pop("hotwords", None)
        segments, _info = model.transcribe(str(wav), **kwargs)
    out = []
    for seg in segments:
        tokens = [{"t": w.word.strip(), "s": float(w.start), "e": float(w.end)} for w in (seg.words or [])]
        out.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(),
                    "tokens": tokens})
    return out


def transcribe(wav: Path, prefs: dict | None = None, hotwords_file: str | None = None) -> dict:
    prefs = prefs or st.get_prefs()
    model = load_model(prefs)
    hot = _hotwords(prefs, hotwords_file)
    engine = prefs["asr"]["engine"]
    sentences = _funasr(model, wav, hot) if engine == "funasr" else _whisper(model, wav, hot)
    sentences = [s for s in sentences if s["text"]]
    return {"engine": engine, "hotwords": hot, "sentences": sentences}
