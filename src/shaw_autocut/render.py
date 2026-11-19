"""渲染：去静音输出（保持原格式）+ 烧录字幕。

“保持原格式”指：同容器后缀、同分辨率、同帧率、同视频编码（H.264→H.264，HEVC→HEVC），
10-bit / HDR（如 iPhone 的 HLG）保留色彩元数据，避免导入剪映后发灰。

去静音的做法：每个保留段单独编码（视频 + PCM 音频，带淡入淡出）→ concat 无损拼接 → 音频统一编码。
不用一个巨大的 trim+concat 滤镜图：长视频几百段时那样会把后面片段的帧全部缓存进内存。
PCM 中间格式避免了 AAC 每段开头的 priming 静音导致的累积音画不同步。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .media import run

CONTAINERS = {".mp4", ".mov", ".m4v", ".mkv"}


def _has_encoder(ffmpeg: str, name: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    except OSError:
        return False
    return f" {name} " in out


def encoder_args(ffmpeg: str, info: dict, prefs_r: dict) -> tuple[list[str], list[str]]:
    """返回 (逐段编码参数, 最终封装附加参数)。"""
    crf = int(prefs_r["crf"])
    tenbit = "10" in (info.get("pix_fmt") or "")
    seg: list[str]
    mux: list[str] = []
    if info.get("video_codec") == "hevc" and _has_encoder(ffmpeg, "libx265"):
        seg = ["-c:v", "libx265", "-crf", str(crf + 2), "-preset", prefs_r["preset"],
               "-pix_fmt", "yuv420p10le" if tenbit else "yuv420p", "-x265-params", "log-level=error"]
        mux = ["-tag:v", "hvc1"]  # 让 macOS / 剪映 正确识别 HEVC
    else:
        seg = ["-c:v", "libx264", "-crf", str(crf), "-preset", prefs_r["preset"], "-pix_fmt", "yuv420p"]
    for opt, key in (("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer"),
                     ("-colorspace", "color_space"), ("-color_range", "color_range")):
        val = info.get(key)
        if val and val != "unknown":
            seg += [opt, val]
    return seg, mux


def audio_args(info: dict) -> list[str]:
    codec = info.get("audio_codec") or "aac"
    if codec.startswith("pcm_"):
        return ["-c:a", codec]
    br = info.get("audio_bitrate") or 192000
    return ["-c:a", "aac", "-b:a", str(max(128000, min(br, 320000)))]


def output_path(src: Path, suffix_name: str = "_cut") -> Path:
    ext = src.suffix.lower() if src.suffix.lower() in CONTAINERS else ".mp4"
    return src.with_name(f"{src.stem}{suffix_name}{ext}")


def cut_video(ffmpeg: str, info: dict, keeps: list[list[float]], out: Path, prefs: dict, progress=None) -> dict:
    src = Path(info["path"])
    pr = prefs["render"]
    fade = prefs["silence"]["fade_ms"] / 1000.0
    seg_args, mux_args = encoder_args(ffmpeg, info, pr)
    workers = pr["workers"] or max(1, min(4, (os.cpu_count() or 2) // 2))
    tmp = Path(tempfile.mkdtemp(prefix=".shaw_autocut_", dir=out.parent))
    try:
        segs = [tmp / f"seg_{i:05d}.mkv" for i in range(len(keeps))]
        cmds = []
        for i, (a, b) in enumerate(keeps):
            dur = b - a
            cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{a:.6f}", "-i", str(src), "-t", f"{dur:.6f}",
                   "-map", "0:v:0", "-map", "0:a:0", "-vf", f"fps={info['fps']}", *seg_args,
                   "-c:a", "pcm_s24le", "-ar", str(info["audio_sample_rate"]), "-ac", str(info["audio_channels"])]
            if fade > 0 and dur > fade * 3:
                cmd += ["-af", f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={dur - fade:.3f}:d={fade:.3f}"]
            cmds.append(cmd + [str(segs[i])])
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(run, cmds):
                done += 1
                if progress:
                    progress(done, len(cmds))
        (tmp / "list.txt").write_text("".join(f"file '{p.name}'\n" for p in segs), encoding="utf-8")
        cont = ["-movflags", "+faststart"] if out.suffix.lower() in (".mp4", ".mov", ".m4v") else []
        run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
             "-c:v", "copy", *mux_args, *audio_args(info), *cont, str(out.resolve())], cwd=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"output": str(out), "segments": len(keeps)}


def burn_subtitles(ffmpeg: str, info: dict, video: Path, ass: Path, out: Path, prefs: dict) -> dict:
    """ass 复制到视频同目录，用相对路径调用 subtitles 滤镜，避开 Windows 盘符转义问题。"""
    seg_args, mux_args = encoder_args(ffmpeg, info, prefs["render"])
    local_ass = video.parent / f".{video.stem}.burn.ass"
    shutil.copyfile(ass, local_ass)
    try:
        cont = ["-movflags", "+faststart"] if out.suffix.lower() in (".mp4", ".mov", ".m4v") else []
        run([ffmpeg, "-y", "-v", "error", "-i", video.name, "-vf", f"subtitles={local_ass.name}",
             *seg_args, *mux_args, "-c:a", "copy", *cont, str(out.resolve())], cwd=video.parent)
    finally:
        local_ass.unlink(missing_ok=True)
    return {"output": str(out)}


def preview_styles(ffmpeg: str, info: dict, cue: dict, styles_to_try: list[str], sub_prefs: dict,
                   font: str, out_dir: Path) -> dict:
    """在视频真实画面上，把同一句字幕按不同样式各渲染一张图，再拼成对比图。"""
    from . import subtitles
    out_dir.mkdir(parents=True, exist_ok=True)
    t = (cue["start"] + cue["end"]) / 2
    frame = out_dir / "frame.png"
    run([ffmpeg, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", info["path"], "-frames:v", "1", frame.name],
        cwd=out_dir)
    shots = []
    for name in styles_to_try:
        prefs = {**sub_prefs, "style": name}
        ass = out_dir / f"preview_{name}.ass"
        ass.write_text(subtitles.to_ass([{"start": 0, "end": 10, "text": cue["text"]}], info, prefs, font),
                       encoding="utf-8-sig")
        png = out_dir / f"preview_{name}.png"
        run([ffmpeg, "-y", "-v", "error", "-i", frame.name, "-vf", f"subtitles={ass.name}",
             "-frames:v", "1", png.name], cwd=out_dir)
        ass.unlink(missing_ok=True)
        shots.append(png)
    sheet = out_dir / "preview_styles.png"
    if len(shots) > 1:
        # 竖屏横向排、横屏纵向排，每格缩到统一尺寸并在左上角标样式名
        vertical = info["height"] > info["width"]
        scale = "scale=-2:900" if vertical else "scale=960:-2"
        inputs, chains = [], []
        for i, (png, name) in enumerate(zip(shots, styles_to_try)):
            inputs += ["-i", png.name]
            chains.append(f"[{i}:v]{scale},drawbox=x=0:y=0:w=iw:h=44:color=black@0.6:t=fill,"
                          f"drawtext=text='{name}':x=14:y=8:fontsize=28:fontcolor=white[s{i}]")
        stack = "hstack" if vertical else "vstack"
        graph = ";".join(chains) + ";" + "".join(f"[s{i}]" for i in range(len(shots))) + f"{stack}=inputs={len(shots)}"
        run([ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-frames:v", "1", sheet.name],
            cwd=out_dir)
    frame.unlink(missing_ok=True)
    return {"previews": [str(p) for p in shots], "sheet": str(sheet) if len(shots) > 1 else None}
