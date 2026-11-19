"""字幕：根据最终视频的识别结果生成 SRT / ASS。

每条字幕的起止时间优先用逐字时间戳；拿不到时在整句时长内按字数比例分配。
"""
from __future__ import annotations

import re
import unicodedata


SPLIT_PUNCT = "，。！？；、,.!?;：:…"
STRIP_PUNCT = "，。、；：,.;:…"
KEEP_PUNCT = "？！?!《》“”\"'‘’（）()"


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P") or ch in SPLIT_PUNCT


def normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if not ch.isspace() and not _is_punct(ch))


def display_len(text: str) -> float:
    return sum(0.5 if ord(ch) < 128 else 1.0 for ch in text)


def _char_times(sentence: dict) -> list[tuple[float, float]] | None:
    """把 token 时间戳展开到“去标点后的每个字符”。"""
    asr_norm = normalize(sentence.get("asr_text", sentence["text"]))
    toks = sentence.get("tokens") or []
    if not toks or not asr_norm:
        return None
    times: list[tuple[float, float]] = []
    if all(t.get("t") for t in toks):
        for t in toks:
            n = len(normalize(t["t"])) or 1
            step = (t["e"] - t["s"]) / n
            times += [(t["s"] + i * step, t["s"] + (i + 1) * step) for i in range(n)]
    elif len(toks) == len(asr_norm):
        times = [(t["s"], t["e"]) for t in toks]
    else:
        return None
    if len(times) != len(asr_norm):
        return None
    return times


def _chunks(text: str, max_chars: float) -> list[str]:
    """先按标点断句，再把超长的均匀拆开（英文尽量在空格处断）。"""
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in SPLIT_PUNCT:
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())

    out: list[str] = []
    for p in parts:
        # 太短的碎片和前一条合并（如“嗯，”“对，”）
        if out and display_len(normalize(p)) <= 3 and display_len(out[-1] + p) <= max_chars:
            out[-1] += p
            continue
        if display_len(p) <= max_chars:
            out.append(p)
            continue
        n = int(-(-display_len(p) // max_chars))
        target = display_len(p) / n
        cur = ""
        for ch in p:
            cur += ch
            if display_len(cur) >= target and len(out) < 10_000:
                # 英文单词中间不断
                if ch.isascii() and ch.isalnum():
                    continue
                out.append(cur.strip())
                cur = ""
        if cur.strip():
            if out and display_len(normalize(cur)) <= 2:
                out[-1] += cur
            else:
                out.append(cur.strip())
    return [c for c in out if normalize(c)]


def clean_line(text: str, strip: bool) -> str:
    if not strip:
        return text.strip()
    text = text.strip()
    while text and text[-1] in STRIP_PUNCT:
        text = text[:-1]
    text = re.sub(f"[{re.escape(STRIP_PUNCT)}]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_style(info: dict, sub_prefs: dict) -> dict:
    from .styles import layout
    return layout(info["width"], info["height"], sub_prefs)


def build_cues(transcript: dict, info: dict, sub_prefs: dict) -> list[dict]:
    style = resolve_style(info, sub_prefs)
    cues: list[dict] = []
    for s in transcript["sentences"]:
        s = {**s, "asr_text": s["text"]}
        chunks = _chunks(s["text"], style["max_chars"])
        if not chunks:
            continue
        char_times = _char_times(s)
        total = sum(len(normalize(c)) for c in chunks)
        pos = 0
        for c in chunks:
            n = len(normalize(c))
            if char_times:
                a, b = char_times[pos][0], char_times[pos + n - 1][1]
            else:
                a = s["start"] + (s["end"] - s["start"]) * pos / total
                b = s["start"] + (s["end"] - s["start"]) * (pos + n) / total
            pos += n
            line = clean_line(c, sub_prefs["strip_punctuation"])
            if line and b > a:
                cues.append({"start": a, "end": b, "text": line})
    return fix_timing(cues, float(sub_prefs["min_duration"]), info.get("duration"))


def fix_timing(cues: list[dict], min_d: float, total: float | None = None) -> list[dict]:
    """最短显示时长 + 去重叠；字幕之间 <0.3s 的空隙直接连上，避免闪烁。"""
    cues = sorted(cues, key=lambda c: c["start"])
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else (total or c["end"] + min_d)
        if c["end"] - c["start"] < min_d:
            c["end"] = max(c["end"], min(c["start"] + min_d, nxt))
        if nxt > c["start"]:
            if 0 < nxt - c["end"] < 0.3:
                c["end"] = nxt
            c["end"] = min(c["end"], nxt)
    return cues


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def parse_srt(text: str) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if m:
                g = list(map(int, m.groups()))
                a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                body = "\n".join(lines[i + 1:]).strip()
                if body:
                    cues.append({"start": a, "end": b, "text": body})
                break
    return cues


def _ts_srt(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}", c["text"], ""]
    return "\n".join(lines)


def _ts_ass(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_ass(cues: list[dict], info: dict, sub_prefs: dict, font: str) -> str:
    from .styles import ass_style_line, line_prefix, resolve
    st = resolve(info["width"], info["height"], sub_prefs)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {info["width"]}
PlayResY: {info["height"]}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{ass_style_line(st, font)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    pre = line_prefix(st)
    body = [f"Dialogue: 0,{_ts_ass(c['start'])},{_ts_ass(c['end'])},Default,,0,0,0,,"
            + pre + c["text"].replace("\n", "\\N").replace("{", "（").replace("}", "）") for c in cues]
    return header + "\n".join(body) + "\n"
