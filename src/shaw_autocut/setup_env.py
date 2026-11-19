"""协助安装缺失的环境。

默认只输出安装计划（JSON），不执行；加 --yes 才执行。
这样 Agent 可以先把计划给用户看、得到同意后再执行。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from . import doctor
from . import state as st

MIRRORS = {
    "tuna": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "ustc": "https://pypi.mirrors.ustc.edu.cn/simple/",
}


def _mirror_args(prefs: dict) -> list[str]:
    m = prefs["install"].get("pip_mirror")
    if not m:
        return []
    return ["-i", MIRRORS.get(m, m)]


def _ffmpeg_step() -> dict:
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return {"cmd": ["brew", "install", "ffmpeg"], "desc": "用 Homebrew 安装 ffmpeg"}
        return {"cmd": None, "desc": "请先安装 Homebrew (https://brew.sh)，再运行 brew install ffmpeg"}
    if system == "Windows":
        if shutil.which("winget"):
            return {"cmd": ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                            "--accept-source-agreements", "--accept-package-agreements"],
                    "desc": "用 winget 安装 ffmpeg（安装后无需重开终端，shaw-autocut 会自动找到）"}
        if shutil.which("choco"):
            return {"cmd": ["choco", "install", "ffmpeg", "-y"], "desc": "用 Chocolatey 安装 ffmpeg（需管理员）"}
        return {"cmd": None, "desc": "请从 https://www.gyan.dev/ffmpeg/builds/ 下载并解压，"
                                     "然后 shaw-autocut config set paths.ffmpeg '\"C:/ffmpeg/bin/ffmpeg.exe\"'"}
    for pm, cmd in (("apt-get", ["sudo", "apt-get", "install", "-y", "ffmpeg"]),
                    ("dnf", ["sudo", "dnf", "install", "-y", "ffmpeg"]),
                    ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "ffmpeg"])):
        if shutil.which(pm):
            return {"cmd": cmd, "desc": f"用 {pm} 安装 ffmpeg（需要 sudo）"}
    return {"cmd": None, "desc": "请用系统包管理器安装 ffmpeg"}


def _has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


def build_plan(report: dict, prefs: dict) -> list[dict]:
    failed = {c["name"] for c in report["checks"] if not c["ok"]}
    plan: list[dict] = []
    pip = [sys.executable, "-m", "pip", "install", "-U"]
    mirror = _mirror_args(prefs)
    engine = prefs["asr"]["engine"]

    if failed & {"ffmpeg", "ffprobe"}:
        step = _ffmpeg_step()
        plan.append({"id": "ffmpeg", **step})

    if "numpy" in failed:
        plan.append({"id": "numpy", "cmd": pip + ["numpy"] + mirror, "desc": "安装 numpy"})

    if engine == "funasr":
        if failed & {"torch", "torchaudio"}:
            cmd = pip + ["torch", "torchaudio"]
            system = platform.system()
            if system in ("Windows", "Linux") and not _has_nvidia():
                # CPU 版体积小很多（约 200MB vs 2GB+）
                cmd += ["--index-url", "https://download.pytorch.org/whl/cpu"]
                desc = "安装 PyTorch（CPU 版，未检测到 NVIDIA 显卡）"
            else:
                cmd += mirror
                desc = "安装 PyTorch"
            plan.append({"id": "torch", "cmd": cmd, "desc": desc})
        if failed & {"funasr", "modelscope"}:
            plan.append({"id": "funasr", "cmd": pip + ["funasr", "modelscope"] + mirror,
                         "desc": "安装 FunASR 中文语音识别"})
    elif engine == "whisper" and "faster_whisper" in failed:
        plan.append({"id": "faster_whisper", "cmd": pip + ["faster-whisper"] + mirror,
                     "desc": "安装 faster-whisper"})

    if "asr_model" in failed:
        plan.append({"id": "model", "cmd": [sys.executable, "-m", "shaw_autocut", "_warmup"],
                     "desc": f"预下载 {engine} 识别模型（约 1GB，只需一次）"})
    return plan


def run_plan(plan: list[dict], only: set[str] | None = None, skip_optional: bool = False) -> list[dict]:
    results = []
    env = os.environ.copy()
    prefs = st.get_prefs()
    if prefs["install"].get("hf_endpoint"):
        env["HF_ENDPOINT"] = prefs["install"]["hf_endpoint"]
    for step in plan:
        if only and step["id"] not in only:
            continue
        if skip_optional and step.get("optional"):
            continue
        if not step.get("cmd"):
            results.append({"id": step["id"], "ok": False, "manual": step["desc"]})
            continue
        print(f"[shaw-autocut setup] {step['desc']}\n  $ {' '.join(step['cmd'])}", file=sys.stderr, flush=True)
        try:
            # 模型下载步骤要在 src 布局下也能找到 shaw_autocut 包
            if step["id"] == "model":
                pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                env["PYTHONPATH"] = pkg_root + os.pathsep + env.get("PYTHONPATH", "")
            rc = subprocess.run(step["cmd"], env=env, stdout=sys.stderr, stderr=sys.stderr).returncode
        except OSError as e:
            rc = -1
            print(f"  失败: {e}", file=sys.stderr)
        results.append({"id": step["id"], "ok": rc == 0, "returncode": rc})
        if rc != 0 and not step.get("optional"):
            break
    return results


def warmup() -> dict:
    """下载并加载一次识别模型，成功后记入 state.json。"""
    from . import asr
    prefs = st.get_prefs()
    engine = prefs["asr"]["engine"]
    asr.load_model(prefs)
    doctor.mark_model_ready(engine, True)
    return {"ok": True, "engine": engine}
