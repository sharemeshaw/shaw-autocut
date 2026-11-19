"""字幕样式：字号 / 宽度 / 位置 / 配色。

排版原则
- 字号先按画面高度给一个舒适值，再用“单行最大宽度”封顶：
  一行最多 max_chars 个字，宽度不超过画面宽 max_width_ratio（竖屏 80%，横屏 70%）
- 竖屏 1080×1920：约 50px、一行 ≤14 字；横屏 1920×1080：约 48px、一行 ≤20 字
- 描边、阴影、底框都按字号比例缩放，任何分辨率观感一致
- 超长行（人工审核时改长了）自动折成两行，不会溢出画面

配色预设
- clean   白字 + 细黑描边 + 柔和阴影：通用，默认
- box     白字 + 半透明黑底框：背景花、亮、杂时最清楚（访谈、户外）
- yellow  暖黄字 + 深色描边：知识/口播类短视频常见，重点感强
- minimal 白字 + 柔光晕（无硬描边）：画面干净、偏高级感的品牌内容；底部很亮时不推荐
自定义：subtitle.color / outline_color / box_color 可覆盖预设，格式 "#RRGGBB"

取值优先级（每一项单独判断）：用户明确设置的值 > 平台预设 > 按横竖屏的默认值
所有尺寸都按画面比例计算，换分辨率不跑偏。
"""
from __future__ import annotations

PRESETS: dict[str, dict] = {
    "clean": {"color": "#FFFFFF", "outline_color": "#000000", "outline": 0.05,
              "shadow": 0.035, "shadow_alpha": 0.55, "bold": True, "blur": 0.6, "box": False},
    "box": {"color": "#FFFFFF", "box_color": "#000000", "box_alpha": 0.55, "outline": 0.22,
            "shadow": 0.0, "shadow_alpha": 1.0, "bold": False, "blur": 0.0, "box": True},
    "yellow": {"color": "#FFE45C", "outline_color": "#1F1F1F", "outline": 0.06,
               "shadow": 0.03, "shadow_alpha": 0.5, "bold": True, "blur": 0.6, "box": False},
    "minimal": {"color": "#FFFFFF", "outline_color": "#000000", "outline": 0.035,
                "shadow": 0.0, "shadow_alpha": 1.0, "bold": False, "blur": 2.5, "box": False},
}


def ass_color(hex_rgb: str, alpha: float = 0.0) -> str:
    """'#RRGGBB' + 透明度(0 不透明 ~ 1 全透明) → ASS 的 &HAABBGGRR"""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"颜色格式应为 #RRGGBB：{hex_rgb}")
    r, g, b = h[0:2], h[2:4], h[4:6]
    a = round(max(0.0, min(1.0, alpha)) * 255)
    return f"&H{a:02X}{b}{g}{r}".upper()


# 平台预设：经验值，用于避开各平台播放界面的遮挡区域。平台界面会改版，首发后请在手机上确认。
PLATFORMS: dict[str, dict] = {
    "douyin":      {"margin_v_ratio": 0.20, "max_width_ratio": 0.72},   # 底部文案 + 右侧按钮列
    "shipinhao":   {"margin_v_ratio": 0.20, "max_width_ratio": 0.72},
    "xiaohongshu": {"margin_v_ratio": 0.20, "max_width_ratio": 0.74},
    "bilibili":    {"margin_v_ratio": 0.08, "max_width_ratio": 0.70},
    "youtube":     {"margin_v_ratio": 0.10, "max_width_ratio": 0.70},   # 播放器控制条
    "screen":      {"margin_v_ratio": 0.035, "max_width_ratio": 0.80,   # 录屏/产品演示：尽量少挡界面
                    "size_scale": 0.85, "style": "box"},
}

# 整体位置：bottom 底部 | lower 中下 | center 画面正中（大字强调风格）
POSITIONS = ("bottom", "lower", "center")


def _pick(sub: dict, key: str, platform: dict, default):
    val = sub.get(key, "auto")
    if val not in (None, "auto"):
        return val
    if key in platform:
        return platform[key]
    return default


def layout(width: int, height: int, sub: dict) -> dict:
    vertical = height > width
    plat_name = sub.get("platform") or "auto"
    if plat_name != "auto" and plat_name not in PLATFORMS:
        raise ValueError(f"未知平台 {plat_name}，可选：auto, {', '.join(PLATFORMS)}")
    plat = PLATFORMS.get(plat_name, {})

    max_w = float(_pick(sub, "max_width_ratio", plat, 0.80 if vertical else 0.70))
    user_scale = sub.get("size_scale")
    # 1.0 是默认值，视为“没设置”，这时用平台预设（如录屏 0.85）
    scale = float(user_scale) if user_scale not in (None, "auto", 1, 1.0) else float(plat.get("size_scale", 1.0))
    position = _pick(sub, "position", plat, "bottom")
    if position not in POSITIONS:
        raise ValueError(f"未知位置 {position}，可选：{', '.join(POSITIONS)}")

    chars_auto = sub.get("max_chars", "auto") in (None, "auto")
    max_chars = float(14 if vertical else 20) if chars_auto else float(sub["max_chars"])

    size = sub.get("font_size", "auto")
    if size in (None, "auto"):
        size = height * (0.026 if vertical else 0.044) * scale
        cap = int(width * max_w / max_chars)
        if size > cap:
            if chars_auto and scale > 1.0:
                # 用户要更大的字：字号照放大，每行少放几个字，保证仍不超宽
                max_chars = float(max(6, int(width * max_w / size)))
            else:
                size = cap
    size = int(round(float(size)))

    if position == "center":
        alignment, margin = 5, 0.0
    elif position == "lower":
        alignment, margin = 2, 0.30 if vertical else 0.25
    else:
        alignment, margin = 2, float(_pick(sub, "margin_v_ratio", plat, 0.18 if vertical else 0.08))
    if position != "bottom" and sub.get("margin_v_ratio", "auto") not in (None, "auto"):
        margin = float(sub["margin_v_ratio"])

    return {"vertical": vertical, "platform": plat_name, "position": position, "alignment": alignment,
            "max_chars": max_chars, "max_width_ratio": max_w, "size_scale": scale,
            "font_size": size, "margin_v_ratio": margin, "margin_v": int(round(height * margin)),
            "margin_lr": int(round(width * (1 - max_w) / 2))}


def resolve(width: int, height: int, sub: dict) -> dict:
    lay = layout(width, height, sub)
    plat = PLATFORMS.get(lay["platform"], {})
    name = _pick(sub, "style", plat, "clean")
    if name not in PRESETS:
        raise ValueError(f"未知字幕样式 {name}，可选：{', '.join(PRESETS)}")
    p = dict(PRESETS[name])
    for k in ("color", "outline_color", "box_color"):
        if sub.get(k):
            p[k] = sub[k]
    if sub.get("bold") is not None:
        p["bold"] = bool(sub["bold"])
    s = lay["font_size"]
    return {**lay, "name": name, **p,
            "outline_px": round(s * p["outline"], 1),
            "shadow_px": round(s * p["shadow"], 1)}


def ass_style_line(st: dict, font: str) -> str:
    primary = ass_color(st["color"])
    if st["box"]:
        # libass：BorderStyle=3 时用 OutlineColour 画底框，Outline 是底框内边距
        outline_c = ass_color(st["box_color"], st["box_alpha"])
        border_style = 3
    else:
        outline_c = ass_color(st.get("outline_color", "#000000"))
        border_style = 1
    back = ass_color("#000000", st["shadow_alpha"])
    bold = -1 if st["bold"] else 0
    return (f"Style: Default,{font},{st['font_size']},{primary},{primary},{outline_c},{back},"
            f"{bold},0,0,0,100,100,0,0,{border_style},{st['outline_px']},{st['shadow_px']},{st['alignment']},"
            f"{st['margin_lr']},{st['margin_lr']},{st['margin_v']},1")


def line_prefix(st: dict) -> str:
    return f"{{\\blur{st['blur']}}}" if st.get("blur") else ""
