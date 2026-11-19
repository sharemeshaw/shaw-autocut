"""用户级状态：环境检查结果缓存 + 偏好设置。

文件位置（可用环境变量 SHAW_AUTOCUT_HOME 覆盖）：
  Windows : %APPDATA%\\shaw-autocut\\state.json
  macOS   : ~/Library/Application Support/shaw-autocut/state.json
  Linux   : ${XDG_CONFIG_HOME:-~/.config}/shaw-autocut/state.json

设计要点：
- 第一次运行做完整环境检查（doctor），通过后把结果和“环境指纹”写入 state.json。
- 之后每次运行只做毫秒级的指纹比对（Python 路径/版本、shaw-autocut 版本、ffmpeg 路径是否还在、
  ASR 引擎是否没变），不再重复完整检查。
- 指纹不匹配、用户加 --recheck、或运行中真的缺依赖时，才重新完整检查。
"""
from __future__ import annotations

import copy
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

SCHEMA = 1

DEFAULT_PREFS: dict[str, Any] = {
    "asr": {
        "engine": "funasr",          # funasr（中文推荐）| whisper（faster-whisper）
        "device": "auto",            # auto | cpu | cuda
        "whisper_model": "large-v3",
        "hotwords_file": None,       # 每行一个热词/专有名词
    },
    "silence": {
        "min_pause": 0.6,            # 静音超过该时长才剪（秒）
        "keep_padding": 0.15,        # 每段说话前后保留，防止吞字
        "keep_natural": 0.25,        # 剪完后两句之间保留的自然停顿
        "min_cut": 0.12,             # 小于该时长的剪切忽略
        "threshold_db": "auto",      # 静音阈值：auto 按底噪自适应，或填 -40 之类
        "fade_ms": 15,               # 剪辑点音频淡入淡出，防爆音
    },
    "subtitle": {
        "platform": "auto",          # auto | douyin | shipinhao | xiaohongshu | bilibili | youtube | screen
        "style": "auto",             # auto(跟随平台，默认 clean) | clean | box | yellow | minimal
        "position": "auto",          # auto(底部) | bottom 底部 | lower 中下 | center 正中
        "size_scale": 1.0,           # 在自动字号基础上缩放，1.2 = 放大 20%，任何分辨率都按比例生效
        "max_chars": "auto",         # 每行最多字数。auto: 竖屏 14 / 横屏 20
        "max_width_ratio": "auto",   # 一行最宽占画面宽度。auto: 竖屏 0.80 / 横屏 0.70
        "font_size": "auto",         # 固定像素（高级）。auto: 竖屏≈高度 2.6%，横屏≈高度 4.4%，受宽度上限约束
        "font": "auto",              # auto: 按系统选择中文字体
        "bold": None,                # None 跟随样式预设
        "color": None,               # 覆盖文字颜色，如 "#FFFFFF"
        "outline_color": None,       # 覆盖描边颜色
        "box_color": None,           # 覆盖底框颜色（box 样式）
        "margin_v_ratio": "auto",    # 离底部距离。auto: 竖屏 0.18（避开平台按钮）/ 横屏 0.08
        "min_duration": 1.0,
        "strip_punctuation": True,
    },
    "render": {
        "crf": 18,                   # 越小越清晰、文件越大；18 基本视觉无损
        "preset": "veryfast",
        "workers": 0,                # 0 = 自动
    },
    "install": {
        "pip_mirror": None,          # None | tuna | aliyun | <自定义 URL>
        "hf_endpoint": None,         # whisper 模型下载镜像，如 https://hf-mirror.com
    },
    "paths": {
        "ffmpeg": None,              # 自定义 ffmpeg 路径（不在 PATH 时）
        "ffprobe": None,
    },
}


def config_dir() -> Path:
    if os.environ.get("SHAW_AUTOCUT_HOME"):
        return Path(os.environ["SHAW_AUTOCUT_HOME"]).expanduser()
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "shaw-autocut"


def state_path() -> Path:
    return config_dir() / "state.json"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_state() -> dict:
    p = state_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("schema") == SCHEMA:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"schema": SCHEMA, "env": {}, "prefs": {}}


def save_state(state: dict) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def get_prefs(state: dict | None = None) -> dict:
    state = state or load_state()
    return _deep_merge(DEFAULT_PREFS, state.get("prefs", {}))


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def set_pref(key: str, raw_value: str) -> dict:
    """key 形如 pause.min_pause；值按 JSON 解析（0.8 / true / null / "文本"）。"""
    parts = key.split(".")
    node: Any = DEFAULT_PREFS
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"未知配置项: {key}")
        node = node[part]
    state = load_state()
    cur = state.setdefault("prefs", {})
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = _parse_value(raw_value)
    # 改了引擎或依赖路径，旧的环境缓存就不可信了
    if parts[0] in ("asr", "paths"):
        state["env"] = {}
    save_state(state)
    return get_prefs(state)


def unset_pref(key: str) -> dict:
    state = load_state()
    parts = key.split(".")
    cur = state.get("prefs", {})
    for part in parts[:-1]:
        cur = cur.get(part, {})
    cur.pop(parts[-1], None)
    if parts[0] in ("asr", "paths"):
        state["env"] = {}
    save_state(state)
    return get_prefs(state)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(prefs: dict, ffmpeg: str | None, ffprobe: str | None) -> dict:
    return {
        "python": sys.executable,
        "python_version": platform.python_version(),
        "autocut": __version__,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "engine": prefs["asr"]["engine"],
    }


def fingerprint_matches(saved: dict, prefs: dict) -> bool:
    """毫秒级快速校验：不调用任何外部程序，只看路径和版本是否没变。"""
    if not saved:
        return False
    if saved.get("python") != sys.executable:
        return False
    if saved.get("python_version") != platform.python_version():
        return False
    if saved.get("autocut") != __version__:
        return False
    if saved.get("engine") != prefs["asr"]["engine"]:
        return False
    for key in ("ffmpeg", "ffprobe"):
        path = saved.get(key)
        if not path or not os.path.exists(path):
            return False
    return True
