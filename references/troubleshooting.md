# 常见问题

**剪完开头/结尾的字被吞了**
`shaw-autocut config set silence.keep_padding 0.22`

**剪得太碎，像机关枪**
调大 `silence.min_pause`（0.8～1.0）和 `silence.keep_natural`（0.35）。

**有背景噪音（空调、风扇、街道），几乎没剪掉**
先 `--dry-run` 看返回的 `threshold_db`，再手动设一个比它高 5～10 dB 的值：
`shaw-autocut cut 视频.mp4 --threshold -38 --dry-run`

**导入剪映后颜色发灰（iPhone 视频）**
iPhone 默认录 HDR（HLG，10-bit HEVC）。shaw-autocut 会保留 10-bit 和色彩元数据；如果仍发灰，在剪映里检查项目的 HDR 设置，或在 iPhone 相机设置里关闭“HDR 视频”。

**Windows 上装完 ffmpeg 还是提示找不到**
shaw-autocut 会主动去 winget 的默认安装位置找。仍找不到时：
`shaw-autocut config set paths.ffmpeg '"C:/路径/ffmpeg.exe"'`，ffprobe 同理。

**字幕识别错字多 / 专有名词识别错误**
写一个热词文件（每行一个词），`shaw-autocut subs 视频.mp4 --hotwords terms.txt --force`。
长期使用：`shaw-autocut config set asr.hotwords_file '"/path/terms.txt"'`。

**第一次识别很慢**
第一次要下载约 1GB 的模型，之后不用再下载。CPU 识别速度大约是视频时长的 0.1～0.3 倍。

**烧录的字幕太大 / 太小 / 位置不合适**
`shaw-autocut config set subtitle.font_size 56`（像素），位置用 `subtitle.margin_v_ratio`（离底部比例）。改完用 `burn --preview` 几秒钟就能看效果。

**字幕在亮背景上看不清**
换 `box` 样式（半透明底框）：`shaw-autocut config set subtitle.style '"box"'`

**烧录后字体不对或显示方块**
系统缺中文字体。安装思源黑体，然后 `shaw-autocut config set subtitle.font '"Source Han Sans CN"'`。

**想重新检查环境**
`shaw-autocut doctor --recheck`

**想清空所有偏好**
`shaw-autocut config reset`
