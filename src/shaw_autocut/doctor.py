"""环境检查。

ensure_env() 是所有命令的入口守卫：
  1) state.json 里有 ready=True 且指纹匹配 → 直接放行（不跑任何外部命令）
  2) 否则跑完整检查 full_check()，把结果写回 state.json
"""
from __future__ import annotations

import glob
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import state as st

# 各检查项所属分组：命令只要求自己用得到的分组就绪
GROUP_BASE = "base"      # python / numpy / ffmpeg / ffprobe
GROUP_ASR = "asr"        # 语音识别引擎依赖


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _windows_extra_ffmpeg_dirs() -> list[str]:
    """winget 安装后当前终端 PATH 不会立刻刷新，这里主动去常见位置找。"""
    pats = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        pats.append(os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin"))
        pats.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
    pats += [r"C:\ffmpeg\bin", r"C:\ProgramData\chocolatey\bin", os.path.expanduser(r"~\scoop\shims")]
    out: list[str] = []
    for p in pats:
        out.extend(glob.glob(p))
    return out


def find_binary(name: str, prefs: dict) -> str | None:
    custom = prefs.get("paths", {}).get(name)
    if custom and os.path.exists(custom):
        return os.path.abspath(custom)
    found = shutil.which(name)
    if found:
        return os.path.abspath(found)
    extra: list[str] = []
    system = platform.system()
    if system == "Windows":
        extra = _windows_extra_ffmpeg_dirs()
        name = name + ".exe"
    elif system == "Darwin":
        extra = ["/opt/homebrew/bin", "/usr/local/bin"]
    for d in extra:
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return None


def _ffmpeg_version(path: str) -> str | None:
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"version\s+(\S+)", out)
    return m.group(1) if m else "unknown"


def default_font() -> str:
    system = platform.system()
    if system == "Windows":
        return "Microsoft YaHei"
    if system == "Darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


def _check(name, ok, group, detail="", fix="", blocking=True):
    return {"name": name, "ok": bool(ok), "group": group, "blocking": blocking,
            "detail": detail, "fix": fix}


def full_check(prefs: dict, state: dict) -> dict:
    checks = []
    engine = prefs["asr"]["engine"]

    pyver = sys.version_info
    checks.append(_check("python", pyver >= (3, 9), GROUP_BASE,
                         f"{platform.python_version()} @ {sys.executable}",
                         "需要 Python 3.9+，建议 3.10/3.11"))
    checks.append(_check("numpy", _has_module("numpy"), GROUP_BASE, "",
                         "shaw-autocut setup 会自动安装"))

    ffmpeg = find_binary("ffmpeg", prefs)
    ffprobe = find_binary("ffprobe", prefs)
    checks.append(_check("ffmpeg", bool(ffmpeg), GROUP_BASE,
                         f"{ffmpeg} (version {_ffmpeg_version(ffmpeg)})" if ffmpeg else "未找到",
                         "shaw-autocut setup 会给出安装命令；或 shaw-autocut config set paths.ffmpeg <路径>"))
    checks.append(_check("ffprobe", bool(ffprobe), GROUP_BASE, ffprobe or "未找到",
                         "通常随 ffmpeg 一起安装"))

    if engine == "funasr":
        for mod in ("torch", "torchaudio", "funasr", "modelscope"):
            checks.append(_check(mod, _has_module(mod), GROUP_ASR, "", "shaw-autocut setup 会自动安装"))
    elif engine == "whisper":
        checks.append(_check("faster_whisper", _has_module("faster_whisper"), GROUP_ASR, "",
                             "shaw-autocut setup 会自动安装"))
    else:
        checks.append(_check("asr_engine", False, GROUP_ASR, f"未知引擎 {engine}",
                             "shaw-autocut config set asr.engine '\"funasr\"'"))

    model_ready = state.get("env", {}).get("model_ready", {}).get(engine, False)
    checks.append(_check("asr_model", model_ready, GROUP_ASR,
                         "已下载" if model_ready else "尚未下载（首次识别会自动下载，约 1GB）",
                         "shaw-autocut setup 会预先下载模型", blocking=False))

    groups = {}
    for c in checks:
        if c["blocking"]:
            groups.setdefault(c["group"], True)
            groups[c["group"]] = groups[c["group"]] and c["ok"]
    return {
        "ready": all(groups.values()),
        "groups": groups,
        "checks": checks,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "engine": engine,
    }


def run_full_and_save(prefs: dict | None = None) -> dict:
    state = st.load_state()
    prefs = prefs or st.get_prefs(state)
    report = full_check(prefs, state)
    env = state.setdefault("env", {})
    env.update({
        "ready": report["ready"],
        "groups": report["groups"],
        "checked_at": st.now_iso(),
        "fingerprint": st.fingerprint(prefs, report["ffmpeg"], report["ffprobe"]),
        "ffmpeg": report["ffmpeg"],
        "ffprobe": report["ffprobe"],
    })
    st.save_state(state)
    report["cached"] = False
    report["state_file"] = str(st.state_path())
    return report


def ensure_env(need: tuple[str, ...] = (GROUP_BASE,), recheck: bool = False) -> dict:
    """返回 {'ready': bool, 'cached': bool, 'ffmpeg':..., 'ffprobe':..., ...}"""
    state = st.load_state()
    prefs = st.get_prefs(state)
    env = state.get("env", {})
    if (not recheck and env.get("fingerprint")
            and st.fingerprint_matches(env["fingerprint"], prefs)
            and all(env.get("groups", {}).get(g) for g in need)):
        return {"ready": True, "cached": True, "ffmpeg": env["ffmpeg"], "ffprobe": env["ffprobe"],
                "checked_at": env.get("checked_at")}
    report = run_full_and_save(prefs)
    report["ready"] = all(report["groups"].get(g, False) for g in need)
    return report


def mark_model_ready(engine: str, ok: bool = True) -> None:
    state = st.load_state()
    state.setdefault("env", {}).setdefault("model_ready", {})[engine] = ok
    st.save_state(state)


def invalidate() -> None:
    """运行中发现缺依赖时调用：下次强制完整检查。"""
    state = st.load_state()
    state["env"] = {k: v for k, v in state.get("env", {}).items() if k == "model_ready"}
    st.save_state(state)
