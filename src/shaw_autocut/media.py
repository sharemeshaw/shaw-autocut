"""ffprobe / ffmpeg 封装。"""
from __future__ import annotations

import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], cwd: str | Path | None = None, quiet: bool = True) -> str:
    # stdin 必须断开：ffmpeg 默认会读标准输入（等待按键），被 Agent/脚本调用时会吞掉输入甚至卡住
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"命令失败: {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    if not quiet and proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return proc.stdout


def probe(ffprobe: str, src: Path) -> dict:
    out = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(src)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        raise FFmpegError("输入文件没有视频流")
    if a is None:
        raise FFmpegError("输入文件没有音频流，无法识别口播")

    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    if not rotation and v.get("tags", {}).get("rotate"):
        rotation = int(v["tags"]["rotate"])
    w, h = int(v["width"]), int(v["height"])
    if abs(rotation) % 180 == 90:
        w, h = h, w

    def frac(s: str | None) -> Fraction | None:
        try:
            f = Fraction(s)
            return f if f > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    avg = frac(v.get("avg_frame_rate"))
    rfr = frac(v.get("r_frame_rate"))
    fps = avg or rfr or Fraction(30)
    # 常见帧率归一（手机 29.97x 之类）
    for std in (Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                Fraction(50), Fraction(60000, 1001), Fraction(60)):
        if abs(float(fps) - float(std)) < 0.05:
            fps = std
            break
    vfr = bool(avg and rfr and abs(float(avg) - float(rfr)) > 0.5)

    duration = float(data["format"].get("duration") or v.get("duration") or 0)
    return {
        "path": str(src.resolve()),
        "duration": duration,
        "width": w,
        "height": h,
        "orientation": "vertical" if h > w else "horizontal",
        "fps": f"{fps.numerator}/{fps.denominator}",
        "fps_float": float(fps),
        "vfr": vfr,
        "video_codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "color_primaries": v.get("color_primaries"),
        "color_transfer": v.get("color_transfer"),
        "color_space": v.get("color_space"),
        "color_range": v.get("color_range"),
        "audio_codec": a.get("codec_name"),
        "audio_bitrate": int(a.get("bit_rate") or 0),
        "audio_sample_rate": int(a.get("sample_rate") or 48000),
        "audio_channels": int(a.get("channels") or 2),
    }


def extract_audio(ffmpeg: str, src: Path, wav: Path) -> None:
    run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)])
