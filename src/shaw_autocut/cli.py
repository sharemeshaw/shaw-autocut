"""shaw-autocut 命令行。

三步工作流：
  1) shaw-autocut cut  原视频.mp4          → 原视频_cut.mp4（去掉没声音的部分，格式不变）
  2) 你在剪映里精剪，导出 最终.mp4
  3) shaw-autocut subs 最终.mp4            → 最终.srt（识别字幕）
     shaw-autocut review 最终.mp4 --serve  → 人工审核字幕
     shaw-autocut burn 最终.mp4            → 最终_subtitled.mp4（可选：烧录字幕）

对 Agent 友好的约定：
- 进度/日志 → stderr；最终结果 → stdout 一行 JSON
- 退出码：0 成功 | 1 运行错误 | 2 输入错误 | 3 环境未就绪 | 4 等待人工审核
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from . import doctor
from . import state as st

EXIT_OK, EXIT_ERR, EXIT_INPUT, EXIT_ENV, EXIT_REVIEW = 0, 1, 2, 3, 4


class Stop(Exception):
    def __init__(self, code: int, payload: dict):
        self.code, self.payload = code, payload


def log(msg: str) -> None:
    print(f"[shaw-autocut] {msg}", file=sys.stderr, flush=True)


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


class Work:
    """中间文件放在 <视频名>.shaw-autocut/ 里；交付物（视频、SRT）放在原视频旁边。"""

    def __init__(self, video: str):
        self.video = Path(video).expanduser().resolve()
        if not self.video.is_file():
            raise Stop(EXIT_INPUT, {"ok": False, "error": "input_not_found", "path": str(self.video)})
        self.dir = self.video.parent / f"{self.video.stem}.shaw-autocut"
        self.wav = self.dir / "audio.wav"
        self.probe = self.dir / "probe.json"
        self.cuts = self.dir / "cuts.json"
        self.transcript = self.dir / "transcript.json"
        self.review_flag = self.dir / "review.json"
        self.review_html = self.dir / "review.html"
        self.srt = self.video.with_suffix(".srt")
        self.ass = self.dir / "subs.ass"

    def mk(self):
        self.dir.mkdir(parents=True, exist_ok=True)

    def reviewed(self) -> bool:
        if not (self.review_flag.exists() and self.srt.exists()):
            return False
        return json.loads(self.review_flag.read_text(encoding="utf-8")).get("reviewed", False)

    def set_reviewed(self, ok: bool):
        self.mk()
        self.review_flag.write_text(json.dumps({"reviewed": ok, "at": st.now_iso()}), encoding="utf-8")


def require_env(need, recheck=False) -> dict:
    rep = doctor.ensure_env(tuple(need), recheck=recheck)
    if not rep["ready"]:
        failed = [c for c in rep.get("checks", []) if not c["ok"] and c["blocking"]]
        raise Stop(EXIT_ENV, {"ok": False, "error": "env_not_ready", "missing": failed,
                              "next": "shaw-autocut setup   # 先看安装计划；用户同意后加 --yes 执行"})
    if not rep.get("cached"):
        log("环境检查通过，已记住，之后不再重复检查")
    return rep


def probe(w: Work, env: dict) -> dict:
    from . import media
    w.mk()
    if w.probe.exists() and w.probe.stat().st_mtime >= w.video.stat().st_mtime:
        return json.loads(w.probe.read_text(encoding="utf-8"))
    info = media.probe(env["ffprobe"], w.video)
    w.probe.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def ensure_wav(w: Work, env: dict) -> None:
    from . import media
    if not w.wav.exists() or w.wav.stat().st_mtime < w.video.stat().st_mtime:
        log("抽取音频…")
        media.extract_audio(env["ffmpeg"], w.video, w.wav)


def _font(prefs: dict) -> str:
    f = prefs["subtitle"]["font"]
    return doctor.default_font() if f in (None, "auto") else f

# ------------------------------------------------------------------ 命令


def cmd_cut(a, prefs) -> dict:
    from . import render, silence
    w = Work(a.video)
    env = require_env([doctor.GROUP_BASE], a.recheck)
    info = probe(w, env)
    ensure_wav(w, env)
    p = dict(prefs["silence"])
    if a.min_pause is not None:
        p["min_pause"] = a.min_pause
    if a.threshold is not None:
        p["threshold_db"] = a.threshold
    det = silence.detect(w.wav, info["duration"], p)
    keeps = silence.snap(det["keeps"], info["fps_float"], info["duration"])
    after = sum(b - a_ for a_, b in keeps)
    summary = {"original": round(info["duration"], 2), "after": round(after, 2),
               "removed": round(info["duration"] - after, 2), "cuts": len(det["cuts"]),
               "threshold_db": det["threshold_db"], "params": p}
    w.cuts.write_text(json.dumps({**summary, "cut_ranges": det["cuts"], "keep_ranges": keeps},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    if a.dry_run:
        return {"ok": True, "dry_run": True, **summary, "cuts_file": str(w.cuts)}
    out = Path(a.out).resolve() if a.out else render.output_path(w.video)
    log(f"剪掉 {summary['cuts']} 处静音，共 {summary['removed']} 秒；开始输出（{len(keeps)} 段）…")
    t0 = time.time()
    res = render.cut_video(env["ffmpeg"], info, keeps, out, prefs,
                           progress=lambda d, n: log(f"  {d}/{n}") if d % 25 == 0 or d == n else None)
    return {"ok": True, **summary, **res, "seconds": round(time.time() - t0, 1),
            "next": "把输出视频放进剪映精剪，导出最终视频后运行：shaw-autocut subs <最终视频>"}


def cmd_subs(a, prefs) -> dict:
    from . import asr, subtitles
    w = Work(a.video)
    if w.reviewed() and not a.force:
        return {"ok": True, "skipped": True, "srt": str(w.srt),
                "reason": "字幕已人工审核，未覆盖（确实要重新识别请加 --force）"}
    need_asr = (not w.transcript.exists() or a.force
                or w.transcript.stat().st_mtime < w.video.stat().st_mtime)
    env = require_env([doctor.GROUP_BASE] + ([doctor.GROUP_ASR] if need_asr else []), a.recheck)
    info = probe(w, env)
    if need_asr:
        ensure_wav(w, env)
        log(f"语音识别中（{prefs['asr']['engine']}；第一次会下载模型）…")
        t0 = time.time()
        try:
            data = asr.transcribe(w.wav, prefs, a.hotwords)
        except ImportError as e:
            doctor.invalidate()
            raise Stop(EXIT_ENV, {"ok": False, "error": "env_not_ready", "detail": str(e), "next": "shaw-autocut setup"})
        doctor.mark_model_ready(prefs["asr"]["engine"], True)
        w.transcript.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"识别完成，用时 {time.time() - t0:.0f} 秒")
    data = json.loads(w.transcript.read_text(encoding="utf-8"))
    cues = subtitles.build_cues(data, info, prefs["subtitle"])
    if w.srt.exists():
        w.srt.replace(w.srt.with_suffix(".prev.srt"))
    w.srt.write_text(subtitles.to_srt(cues), encoding="utf-8")
    w.set_reviewed(False)
    return {"ok": True, "srt": str(w.srt), "cues": len(cues),
            "next": f"人工审核：shaw-autocut review \"{w.video}\" --serve"}


def cmd_review(a, prefs) -> dict:
    from . import review, subtitles
    w = Work(a.video)
    if not w.srt.exists():
        raise Stop(EXIT_INPUT, {"ok": False, "error": "srt_missing", "next": "先运行 shaw-autocut subs"})
    if a.done:  # 用户在剪映/文本编辑器里自己改完了
        w.set_reviewed(True)
        return {"ok": True, "reviewed": True, "srt": str(w.srt)}
    w.mk()
    info = json.loads(w.probe.read_text(encoding="utf-8")) if w.probe.exists() else {"width": 1080, "height": 1920}
    max_chars = subtitles.resolve_style(info, prefs["subtitle"])["max_chars"]
    if not a.serve:
        review.write_static(w.video, w.srt, w.review_html, max_chars, w.reviewed())
        return {"ok": True, "html": str(w.review_html), "reviewed": w.reviewed(),
                "hint": "推荐加 --serve：保存直接写回 SRT，点“审核完成”后自动继续"}
    res = review.serve(w.video, w.srt, max_chars, on_finish=lambda: w.set_reviewed(True),
                       port=a.port, open_browser=not a.no_browser)
    out = {"ok": True, "reviewed": w.reviewed(), "srt": str(w.srt), "url": res.get("url")}
    if out["reviewed"]:
        out["next"] = (f"二选一：① 直接把 {w.srt.name} 导入剪映；"
                       f"② 烧录：shaw-autocut burn \"{w.video}\"")
    return out


def cmd_burn(a, prefs) -> dict:
    from . import render, subtitles
    w = Work(a.video)
    if not w.srt.exists():
        raise Stop(EXIT_INPUT, {"ok": False, "error": "srt_missing", "next": "先运行 shaw-autocut subs"})
    if not w.reviewed() and not a.skip_review and not a.preview:
        raise Stop(EXIT_REVIEW, {"ok": False, "error": "awaiting_review",
                                 "next": f"先审核字幕：shaw-autocut review \"{w.video}\" --serve "
                                         "（已在别处改好可用 --done 标记）"})
    env = require_env([doctor.GROUP_BASE], a.recheck)
    info = probe(w, env)
    cues = subtitles.parse_srt(w.srt.read_text(encoding="utf-8-sig"))
    sub_prefs = dict(prefs["subtitle"])
    for key in ("style", "platform", "position", "size_scale"):
        if getattr(a, key, None) is not None:
            sub_prefs[key] = getattr(a, key)
    if a.preview:
        from .styles import PRESETS
        names = list(PRESETS) if not a.style else [a.style]
        from .styles import layout
        lay = layout(info["width"], info["height"], sub_prefs)
        cue = max(cues, key=lambda c: subtitles.display_len(c["text"]))  # 用最长的一句，最能看出问题
        res = render.preview_styles(env["ffmpeg"], info, cue, names, sub_prefs, _font(prefs), w.dir / "preview")
        return {"ok": True, **res, "sample_text": cue["text"],
                "layout": {k: lay[k] for k in ("platform", "position", "font_size", "max_chars", "margin_v", "size_scale")},
                "next": "选定后：shaw-autocut config set subtitle.style '\"<样式名>\"'，再运行 burn"}
    prefs = {**prefs, "subtitle": sub_prefs}
    w.ass.write_text(subtitles.to_ass(cues, info, prefs["subtitle"], _font(prefs)), encoding="utf-8-sig")
    out = Path(a.out).resolve() if a.out else render.output_path(w.video, "_subtitled")
    log("烧录字幕中…")
    t0 = time.time()
    res = render.burn_subtitles(env["ffmpeg"], info, w.video, w.ass, out, prefs)
    return {"ok": True, **res, "cues": len(cues), "style": sub_prefs.get("style"), "seconds": round(time.time() - t0, 1)}


def cmd_status(a, prefs) -> dict:
    w = Work(a.video)
    from . import render
    cut_out = render.output_path(w.video)
    return {"ok": True, "video": str(w.video),
            "cut_output_exists": cut_out.exists(), "srt_exists": w.srt.exists(),
            "srt_reviewed": w.reviewed(),
            "burned_exists": render.output_path(w.video, "_subtitled").exists()}


def cmd_version(a, prefs) -> dict:
    """报告“当前实际运行的是哪一份 shaw-autocut”，升级后用来确认没有旧版本残留在起作用。"""
    import importlib.metadata as md
    import shutil
    pkg_dir = Path(__file__).resolve().parent
    repo = pkg_dir.parent.parent
    vfile = repo / "VERSION"
    try:
        pip_ver = md.version("shaw-autocut")
    except md.PackageNotFoundError:
        pip_ver = None
    on_path = shutil.which("shaw-autocut")
    res = {"ok": True, "version": __version__, "package_dir": str(pkg_dir),
           "skill_dir": str(repo) if (repo / "SKILL.md").exists() else None,
           "version_file": vfile.read_text(encoding="utf-8").strip() if vfile.exists() else None,
           "pip_installed_version": pip_ver, "command_on_path": on_path, "python": sys.executable,
           "state_file": str(st.state_path())}
    warn = []
    if res["version_file"] and res["version_file"] != __version__:
        warn.append(f"VERSION 文件({res['version_file']})与代码版本({__version__})不一致，文件可能没有完整替换")
    if pip_ver and pip_ver != __version__:
        warn.append(f"系统里 pip 安装的是 {pip_ver}，与当前运行的 {__version__} 不同；"
                    "直接敲 shaw-autocut 命令可能跑的是旧版本，请 pip uninstall 后重装")
    res["warnings"] = warn
    return res


def cmd_doctor(a, prefs) -> dict:
    rep = doctor.ensure_env((doctor.GROUP_BASE, doctor.GROUP_ASR), recheck=a.recheck)
    if rep.get("cached"):
        return {"ok": True, "ready": True, "cached": True, "checked_at": rep.get("checked_at"),
                "hint": "使用已记住的检查结果；加 --recheck 重新完整检查"}
    return {"ok": True, **rep}


def cmd_setup(a, prefs) -> dict:
    from . import setup_env
    if a.engine:
        st.set_pref("asr.engine", json.dumps(a.engine))
    if a.mirror:
        st.set_pref("install.pip_mirror", json.dumps(a.mirror))
    prefs = st.get_prefs()
    rep = doctor.run_full_and_save(prefs)
    plan = setup_env.build_plan(rep, prefs)
    if a.only:
        plan = [p for p in plan if p["id"] in set(a.only.split(","))]
    if not plan:
        return {"ok": True, "ready": rep["ready"], "plan": [], "message": "环境已就绪，无需安装"}
    if not a.yes:
        return {"ok": True, "ready": rep["ready"], "plan": plan, "next": "用户确认后运行：shaw-autocut setup --yes"}
    results = setup_env.run_plan(plan)
    rep = doctor.run_full_and_save(st.get_prefs())
    return {"ok": rep["ready"], "ready": rep["ready"], "results": results,
            "missing": [c for c in rep["checks"] if not c["ok"] and c["blocking"]]}


def cmd_config(a, prefs) -> dict:
    if a.action == "path":
        return {"ok": True, "state_file": str(st.state_path())}
    if a.action == "show":
        return {"ok": True, "prefs": prefs, "state_file": str(st.state_path())}
    if a.action == "reset":
        s = st.load_state()
        s["prefs"] = {}
        st.save_state(s)
        return {"ok": True, "prefs": st.get_prefs()}
    if not a.key:
        raise Stop(EXIT_INPUT, {"ok": False, "error": "需要配置项名称，如 silence.min_pause"})
    try:
        if a.action == "get":
            node = prefs
            for k in a.key.split("."):
                node = node[k]
            return {"ok": True, "key": a.key, "value": node}
        if a.action == "set":
            if a.value is None:
                raise Stop(EXIT_INPUT, {"ok": False, "error": "需要 value"})
            new = st.set_pref(a.key, a.value)
            node = new
            for k in a.key.split("."):
                node = node[k]
            return {"ok": True, "key": a.key, "value": node}
        st.unset_pref(a.key)
        return {"ok": True, "key": a.key, "unset": True}
    except KeyError as e:
        raise Stop(EXIT_INPUT, {"ok": False, "error": f"未知配置项 {e}"})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shaw-autocut",
                                description="口播视频：去静音 → (剪映精剪) → 识别字幕 → 人工审核 → SRT / 烧录")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True,
                         metavar="{cut,subs,review,burn,status,version,doctor,setup,config}")

    x = sub.add_parser("cut", help="① 自动剪掉没声音的部分，输出相同格式的视频")
    x.add_argument("video")
    x.add_argument("--min-pause", type=float, help="本次覆盖：静音超过多少秒才剪（默认 0.6）")
    x.add_argument("--threshold", type=float, help="本次覆盖：静音阈值 dB，如 -40（默认自适应）")
    x.add_argument("--out", help="输出路径（默认 原文件名_cut.原后缀）")
    x.add_argument("--dry-run", action="store_true", help="只分析，不输出视频")

    x = sub.add_parser("subs", help="③ 对最终视频识别字幕，输出同名 .srt")
    x.add_argument("video")
    x.add_argument("--hotwords", help="热词文件（每行一个专有名词）")
    x.add_argument("--force", action="store_true", help="重新识别（会覆盖已审核的字幕）")

    x = sub.add_parser("review", help="③ 人工审核字幕")
    x.add_argument("video")
    x.add_argument("--serve", action="store_true", help="启动本地审核页面，审核完成后返回")
    x.add_argument("--port", type=int, default=0)
    x.add_argument("--no-browser", action="store_true")
    x.add_argument("--done", action="store_true", help="已在别处改好 SRT，直接标记为已审核")

    x = sub.add_parser("burn", help="③ 把已审核的字幕烧录进视频")
    x.add_argument("video")
    x.add_argument("--out")
    x.add_argument("--skip-review", action="store_true", help="跳过审核闸门（不推荐）")
    x.add_argument("--style", choices=["clean", "box", "yellow", "minimal"], help="本次使用的字幕样式")
    x.add_argument("--platform", choices=["auto", "douyin", "shipinhao", "xiaohongshu", "bilibili", "youtube", "screen"],
                   help="本次按哪个平台的界面避让")
    x.add_argument("--position", choices=["bottom", "lower", "center"], help="本次字幕位置：底部/中下/正中")
    x.add_argument("--size-scale", dest="size_scale", type=float, help="本次字号缩放，如 1.2 = 放大 20%%")
    x.add_argument("--preview", action="store_true", help="不烧录，只在真实画面上出各样式对比图")

    x = sub.add_parser("status", help="查看某个视频处理到哪一步")
    x.add_argument("video")

    sub.add_parser("version", help="显示当前运行的版本和安装位置（升级后用来核对）")

    x = sub.add_parser("doctor", help="检查环境（结果会被记住）")
    x.add_argument("--recheck", action="store_true", help="忽略已记住的结果，重新完整检查")

    x = sub.add_parser("setup", help="协助安装缺失环境（默认只显示计划，--yes 才执行）")
    x.add_argument("--yes", action="store_true")
    x.add_argument("--engine", choices=["funasr", "whisper"], help="设置并记住识别引擎")
    x.add_argument("--mirror", help="pip 镜像：tuna / aliyun / ustc / URL（会被记住）")
    x.add_argument("--only", help="只执行指定步骤，逗号分隔")

    x = sub.add_parser("config", help="查看/修改偏好（永久保存）")
    x.add_argument("action", choices=["show", "get", "set", "unset", "path", "reset"])
    x.add_argument("key", nargs="?")
    x.add_argument("value", nargs="?")

    sub.add_parser("_warmup")

    for name in ("cut", "subs", "burn"):
        sub.choices[name].add_argument("--recheck", action="store_true", help="重新检查环境")
    return p


COMMANDS = {"version": cmd_version, "cut": cmd_cut, "subs": cmd_subs, "review": cmd_review, "burn": cmd_burn,
            "status": cmd_status, "doctor": cmd_doctor, "setup": cmd_setup, "config": cmd_config}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "_warmup":
            from . import setup_env
            emit(setup_env.warmup())
        else:
            emit(COMMANDS[a.cmd](a, st.get_prefs()))
        return EXIT_OK
    except Stop as s:
        emit(s.payload)
        return s.code
    except Exception as e:  # noqa: BLE001
        from .media import FFmpegError
        emit({"ok": False, "error": type(e).__name__, "detail": str(e)})
        if not isinstance(e, FFmpegError):
            import traceback
            traceback.print_exc(file=sys.stderr)
        return EXIT_ERR
