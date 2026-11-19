# 配置项与调参

偏好保存在 `state.json`（位置：`shaw-autocut config path`），修改：`shaw-autocut config set <键> <值>`。
值按 JSON 解析：数字直接写，字符串要加引号，如 `'"PingFang SC"'`。

## silence：去静音

| 键 | 默认 | 说明 |
|---|---|---|
| `min_pause` | 0.6 | 静音超过多少秒才剪。语速快、想更紧凑 → 0.4～0.5；想保留呼吸感 → 0.8～1.0 |
| `keep_padding` | 0.15 | 每段说话前后留的余量。出现吞字（开头/结尾的字被切掉）→ 调到 0.2～0.25 |
| `keep_natural` | 0.25 | 两句之间剪完后保留的停顿。听起来像机关枪 → 调大到 0.35 |
| `min_cut` | 0.12 | 小于这个长度的剪切忽略 |
| `threshold_db` | "auto" | 静音阈值。auto 按底噪自适应；环境噪声大（空调、风扇）导致剪不掉时，可手动设为 -35 之类 |
| `fade_ms` | 15 | 剪辑点音频淡入淡出（毫秒），防止爆音/咔哒声 |

调参前先用 `--dry-run` 看效果（不出视频，只报剪多少）。

## subtitle：字幕

### 样式预设（`subtitle.style`）

| 样式 | 外观 | 适合 |
|---|---|---|
| `clean`（默认，`style` 为 auto 时） | 白字 + 细黑描边 + 柔和阴影 | 绝大多数口播，最通用 |
| `box` | 白字 + 半透明黑色底框 | 背景花、亮、杂（户外、展会、白墙强光） |
| `yellow` | 暖黄字 + 深色描边 | 知识类、干货类短视频，重点感强 |
| `minimal` | 白字 + 柔光晕，无硬描边 | 画面干净、底部偏暗的品牌内容；底部很亮时不推荐 |

**先预览再烧录**：`shaw-autocut burn 视频.mp4 --preview` 会在你视频的真实画面上，用最长的一句字幕把四种样式各渲染一张图并拼成对比图，几秒钟就能看完，不用等整片烧录。

### 平台预设（`subtitle.platform`）

选一个发布平台，自动带上适合它的位置和文字区宽度，用来避开平台播放界面的遮挡（底部文案、右侧按钮、播放控制条）。

| 平台 | 字幕底边离底部 | 文字区宽 | 其它 |
|---|---|---|---|
| `auto`（默认） | 竖屏 18% / 横屏 8% | 竖屏 80% / 横屏 70% | |
| `douyin` 抖音 / `shipinhao` 视频号 | 20% | 72% | 收窄避开右侧按钮列 |
| `xiaohongshu` 小红书 | 20% | 74% | |
| `bilibili` B 站 | 8% | 70% | |
| `youtube` | 10% | 70% | 避开播放控制条 |
| `screen` 录屏 / 产品演示 | 3.5% | 80% | 字号 ×0.85、默认 box 样式，尽量少挡界面 |

这些是经验值，平台界面会改版，第一次发布后请在手机上确认。
**你明确设置过的值永远优先于平台预设**：比如选了 `screen` 又设了 `style yellow`，就用 yellow。

### 尺寸与位置

所有尺寸都按画面比例计算，720p 和 4K 上字幕占画面的比例一致。

| 键 | 默认 | 说明 |
|---|---|---|
| `size_scale` | 1.0 | **推荐的调字号方式**：在自动字号基础上缩放，1.2 = 放大 20%。任何分辨率都按比例生效。放大到一行放不下时，会自动减少每行字数 |
| `position` | "auto" | bottom 底部（默认）/ lower 中下（竖屏离底 30%、横屏 25%）/ center 画面正中（大字强调风格） |
| `max_chars` | "auto" | 每行最多字数（英文算半个）。auto：竖屏 14 / 横屏 20 |
| `max_width_ratio` | "auto" | 一行最宽占画面宽度。auto：跟随平台，否则竖屏 0.80 / 横屏 0.70 |
| `margin_v_ratio` | "auto" | 字幕底边离画面底部的比例。auto：跟随平台，否则竖屏 0.18 / 横屏 0.08 |
| `font_size` | "auto" | 固定像素（高级用法）。只适合所有视频分辨率都一样时使用，否则请用 `size_scale` |
| `font` | "auto" | Windows 微软雅黑 / macOS 苹方 / Linux Noto Sans CJK |
| `bold` | null | null 跟随样式；true / false 强制 |

自动字号的算法：竖屏取画面高度 2.6%（1080×1920 约 50px），横屏取 4.4%（1920×1080 约 48px），再乘以 `size_scale`，并保证一行不超过文字区宽度。
文字过长时折成两行：底部/中下位置时底边不动、向上长；正中位置时上下居中。

### 颜色

| 键 | 说明 |
|---|---|
| `color` | 文字颜色，如 `'"#FFFFFF"'` |
| `outline_color` | 描边颜色 |
| `box_color` | 底框颜色（box 样式） |

配色建议：
- 文字和背景要拉开**明暗**差，不只是色相差：深色描边或底框是可读性的保证
- 品牌色适合做**文字颜色**（如 yellow 预设的思路），不要用作大面积底框
- 避免纯红、纯绿字幕：压缩后边缘容易糊，而且对色弱观众不友好
- 一个账号固定一种样式，形成辨识度；设一次 `config set` 以后都会沿用

### 其它

| 键 | 默认 | 说明 |
|---|---|---|
| `min_duration` | 1.0 | 每条字幕最短显示秒数 |
| `strip_punctuation` | true | 去掉行尾标点，行内逗号句号换成空格（保留 ？！） |

商用视频注意字体版权：思源黑体 / Noto Sans CJK 为 OFL 协议，可免费商用；微软雅黑、苹方用于商业发布前请自行确认授权。

## asr：语音识别

| 键 | 默认 | 说明 |
|---|---|---|
| `engine` | "funasr" | funasr（阿里 Paraformer，中文效果最好，模型从魔搭下载，国内快）/ whisper（faster-whisper，多语种） |
| `device` | "auto" | auto / cpu / cuda |
| `whisper_model` | "large-v3" | 仅 whisper 引擎；机器较弱可用 "medium" |
| `hotwords_file` | null | 默认热词文件路径（每行一个专有名词、品牌名、人名） |

## render：编码

| 键 | 默认 | 说明 |
|---|---|---|
| `crf` | 18 | 画质，越小越清晰、文件越大。18 基本看不出差别 |
| `preset` | "veryfast" | 编码速度。追求更小文件可用 "medium"（慢 2～3 倍） |
| `workers` | 0 | 并行编码段数，0 = 自动 |

## install：安装来源

| 键 | 默认 | 说明 |
|---|---|---|
| `pip_mirror` | null | tuna / aliyun / ustc / 自定义 URL。中国大陆建议 tuna |
| `hf_endpoint` | null | whisper 模型下载镜像，如 "https://hf-mirror.com" |

## paths

ffmpeg 不在 PATH 里时手动指定：`shaw-autocut config set paths.ffmpeg '"D:/tools/ffmpeg/bin/ffmpeg.exe"'`（ffprobe 同理）。
