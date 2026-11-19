# 更新记录

## 0.1.2
- 新增 `subtitle.size_scale`：按比例放大/缩小字幕（推荐替代固定像素 font_size），放大时自动减少每行字数，保证不超宽
- 新增 `subtitle.platform` 平台预设：douyin / shipinhao / xiaohongshu / bilibili / youtube / screen（录屏），自动避开平台界面遮挡
- 新增 `subtitle.position`：bottom 底部 / lower 中下 / center 正中
- `burn` 新增 `--platform` `--position` `--size-scale`，可单次试用不保存
- `subtitle.style` 默认值改为 auto（跟随平台，否则 clean）
- 新增 `shaw-autocut version`：显示实际运行的版本、代码位置、pip 安装版本，有旧版本残留时给出警告
- 新增 `VERSION` 文件、SKILL.md 的 `metadata.version`、`UPGRADE.md`（给 Agent 执行的卸载旧版 → 安装新版 → 核对流程）
- 修复：调用 ffmpeg 时断开标准输入，避免被 Agent/脚本调用时吞掉输入或卡住

## 0.1.1
- 字幕排版：字号改为“按画面高度给舒适值 + 单行宽度封顶”（竖屏 50px/14 字，横屏 48px/20 字），描边阴影按字号比例缩放；超长行自动折行
- 字幕配色：新增 4 种样式预设 clean / box / yellow / minimal，支持自定义文字/描边/底框颜色
- 新增 `burn --preview`：在真实画面上出四种样式对比图；`burn --style` 指定本次样式
- 新增配置项：subtitle.style / font_size / max_width_ratio / bold / color / outline_color / box_color
- 移除配置项：subtitle.font_size_ratio / outline（旧值会被忽略，无需处理）

## 0.1.0
- 首个版本：去静音、识别字幕、审核页面、烧录、环境检查与偏好记忆
