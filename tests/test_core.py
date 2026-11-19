import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shaw_autocut import silence, subtitles, state  # noqa: E402

SR = 16000


def _wav(path, pattern):
    """pattern: [(秒, 是否有声)]"""
    chunks = []
    for dur, voiced in pattern:
        n = int(dur * SR)
        t = np.arange(n) / SR
        x = 0.3 * np.sin(2 * np.pi * 220 * t) if voiced else 0.002 * np.random.randn(n)
        chunks.append(x)
    x = (np.concatenate(chunks) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(x.tobytes())
    return sum(d for d, _ in pattern)


P = {"min_pause": 0.6, "keep_padding": 0.15, "keep_natural": 0.25, "min_cut": 0.12,
     "threshold_db": "auto", "fade_ms": 15}


def test_detects_long_pauses_only(tmp_path):
    wav = tmp_path / "a.wav"
    dur = _wav(wav, [(1.0, False), (2.0, True), (0.3, False), (1.0, True), (2.0, False), (1.5, True), (1.0, False)])
    res = silence.detect(wav, dur, P)
    cuts = res["cuts"]
    # 开头静音、2 秒停顿、结尾静音被剪；0.3 秒短停顿保留
    assert len(cuts) == 3
    assert cuts[0][0] == 0.0 and abs(cuts[0][1] - 0.85) < 0.05
    mid = cuts[1]
    assert abs(mid[0] - (4.3 + 0.15)) < 0.05 and abs(mid[1] - (6.3 - 0.15)) < 0.05
    kept = sum(b - a for a, b in res["keeps"])
    # 0.15 前留白 + 2.0 + 0.3 短停顿 + 1.0 + 0.3 自然停顿 + 1.5 + 0.15 后留白
    assert abs(kept - 5.4) < 0.1


def test_snap_to_frames():
    keeps = silence.snap([[0.012, 1.019], [2.5, 3.333]], 30.0, 10)
    for a, b in keeps:
        assert abs(a * 30 - round(a * 30)) < 1e-9 and abs(b * 30 - round(b * 30)) < 1e-9


def test_chunks_and_punctuation():
    chunks = subtitles._chunks("大家好，今天我们来聊一聊如何把口播视频里面的长时间停顿自动剪掉。", 16)
    assert all(subtitles.display_len(c) <= 17 for c in chunks)
    assert "".join(map(subtitles.normalize, chunks)) == subtitles.normalize("大家好今天我们来聊一聊如何把口播视频里面的长时间停顿自动剪掉")
    assert subtitles.clean_line("你好，世界。", True) == "你好 世界"
    assert subtitles.clean_line("真的吗？", True) == "真的吗？"


def test_build_cues_with_char_times():
    text = "大家好，欢迎来到频道。"
    toks = [{"t": ch, "s": 1 + i * 0.2, "e": 1 + i * 0.2 + 0.18} for i, ch in enumerate("大家好欢迎来到频道")]
    tr = {"sentences": [{"start": 1.0, "end": 2.8, "text": text, "tokens": toks}]}
    info = {"width": 1080, "height": 1920, "duration": 10}
    prefs = state.DEFAULT_PREFS["subtitle"]
    cues = subtitles.build_cues(tr, info, prefs)
    assert [c["text"] for c in cues] == ["大家好", "欢迎来到频道"]
    assert abs(cues[0]["start"] - 1.0) < 1e-6
    assert abs(cues[1]["start"] - 1.6) < 1e-6
    srt = subtitles.to_srt(cues)
    back = subtitles.parse_srt(srt)
    assert [c["text"] for c in back] == ["大家好", "欢迎来到频道"]


def test_prefs_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAW_AUTOCUT_HOME", str(tmp_path))
    state.set_pref("silence.min_pause", "0.8")
    assert state.get_prefs()["silence"]["min_pause"] == 0.8
    with pytest.raises(KeyError):
        state.set_pref("nope.x", "1")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")
def test_cut_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAW_AUTOCUT_HOME", str(tmp_path / "home"))
    src = tmp_path / "talk.mp4"
    # 1s 静音 + 2s 有声 + 2s 静音 + 2s 有声 + 1s 静音
    expr = "if(between(t,1,3)+between(t,5,7),0.3*sin(2*PI*220*t),0)"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=8",
                    "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run([sys.executable, "-m", "shaw_autocut", "cut", str(src)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert Path(out["output"]).exists()
    assert 4.3 < out["after"] < 5.3
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", out["output"]],
                                   capture_output=True, text=True).stdout)
    v = [s for s in pr["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"], v["codec_name"]) == (720, 1280, "h264")
    # 第二次运行走环境缓存
    r2 = subprocess.run([sys.executable, "-m", "shaw_autocut", "doctor"], capture_output=True, text=True, env=env)
    assert json.loads(r2.stdout)["ok"]


def test_layout_never_exceeds_width():
    from shaw_autocut import styles
    for w, h in [(1080, 1920), (1920, 1080), (720, 1280), (3840, 2160), (1080, 1080), (1080, 1350)]:
        lay = styles.layout(w, h, {})
        assert lay["font_size"] * lay["max_chars"] <= w * lay["max_width_ratio"] + 1


def test_style_presets_and_colors():
    from shaw_autocut import styles
    assert styles.ass_color("#FFE45C") == "&H005CE4FF"
    assert styles.ass_color("#000000", 0.5) == "&H80000000"
    for name in styles.PRESETS:
        st = styles.resolve(1080, 1920, {"style": name, "color": "#112233"})
        assert st["color"] == "#112233"
        assert styles.ass_style_line(st, "Noto Sans CJK SC").startswith("Style: Default,")


def test_size_scale_platform_position():
    from shaw_autocut import styles
    base = dict(state.DEFAULT_PREFS["subtitle"])
    d = styles.resolve(1080, 1920, base)
    big = styles.resolve(1080, 1920, {**base, "size_scale": 1.5})
    assert big["font_size"] > d["font_size"] and big["max_chars"] < d["max_chars"]
    assert big["font_size"] * big["max_chars"] <= 1080 * big["max_width_ratio"] + 1
    # 比例缩放：不同分辨率下字号占画面比例一致
    small = styles.resolve(720, 1280, {**base, "size_scale": 1.2})
    big2 = styles.resolve(1080, 1920, {**base, "size_scale": 1.2})
    assert abs(small["font_size"] / 1280 - big2["font_size"] / 1920) < 0.002
    dy = styles.resolve(1080, 1920, {**base, "platform": "douyin"})
    assert dy["margin_v"] > d["margin_v"] and dy["max_width_ratio"] < d["max_width_ratio"]
    sc = styles.resolve(1920, 1080, {**base, "platform": "screen"})
    assert sc["name"] == "box" and sc["size_scale"] == 0.85
    assert styles.resolve(1920, 1080, {**base, "platform": "screen", "style": "yellow"})["name"] == "yellow"
    assert styles.resolve(1080, 1920, {**base, "position": "center"})["alignment"] == 5
    assert styles.resolve(1080, 1920, {**base, "position": "lower"})["margin_v"] > d["margin_v"]
