---
name: shaw-autocut
metadata:
  version: "0.1.2"
description: 口播/讲解类视频后期：自动剪掉没声音的停顿并输出相同格式的视频；对（剪映精剪后的）最终视频识别中文字幕，人工审核后输出 SRT 或把字幕烧录进视频。Use when the user wants to remove silences/pauses from a talking-head video, generate Chinese subtitles (SRT) for a video, or burn subtitles into a video. 触发词：去停顿、剪掉静音、去气口、口播剪辑、加字幕、生成字幕、SRT、烧录字幕、硬字幕。
---

# shaw-autocut（版本 0.1.2）

口播视频三步走。所有动作都通过命令行完成，每条命令最后在 stdout 输出一行 JSON，看 `ok`、`next` 字段决定下一步。

```
① cut     原视频.mp4            → 原视频_cut.mp4      去掉没声音的部分，格式不变
② （用户自己）放进剪映，剪掉重复/多余的话，导出 最终.mp4
③ subs    最终.mp4              → 最终.srt            识别字幕
   review 最终.mp4 --serve      → 用户在网页里审核字幕   ← 必须等用户完成
   burn   最终.mp4（可选）       → 最终_subtitled.mp4  烧录字幕
```

## 怎么调用

在本 skill 目录下用免安装启动器（`pip install -e .` 安装过的话也可以直接用 `shaw-autocut`）：

```bash
python <skill目录>/scripts/shaw-autocut <命令> ...
```

下文用 `shaw-autocut` 代指上面这条命令。

## 环境：只在需要时检查，结果会被记住

**不要每次都主动跑 doctor。** 直接执行用户要的命令即可，每条命令内部都会先做毫秒级的缓存校验：
- 之前检查通过、环境没变 → 直接执行，不再检查
- 第一次使用或环境变了 → 自动完整检查一次并记住结果

只有命令返回退出码 **3**（`"error": "env_not_ready"`）时才处理环境：

1. 运行 `shaw-autocut setup`，它**只输出安装计划**，不会安装任何东西
2. 把计划用通俗的话告诉用户（装什么、大概多大），**征得同意**
   - 用户在中国大陆：建议加 `--mirror tuna`（pip 清华镜像，会被记住）
3. 用户同意后运行 `shaw-autocut setup --yes`（可再带 `--mirror tuna`）
4. 安装 ffmpeg 需要 sudo 或管理员权限时，把命令交给用户自己执行
5. 装好后重新执行原来的命令

注意：①去静音只需要 ffmpeg 和 numpy；③识别字幕才需要语音识别模型（第一次约 1GB 下载）。不要因为用户只想去静音就安装识别模型。

## ① 去静音

```bash
shaw-autocut cut "<视频路径>"                   # 输出到原视频旁边：<名字>_cut.<原后缀>
shaw-autocut cut "<视频路径>" --dry-run         # 只分析：会剪掉多少秒、几处
shaw-autocut cut "<视频路径>" --min-pause 0.8   # 本次放宽：静音超过 0.8 秒才剪
```

完成后向用户汇报：原长 → 剪后时长、剪掉几处，然后告诉用户下一步是放进剪映精剪。
用户觉得剪得太碎或太狠 → 调大 `--min-pause`（如 0.8～1.0）；剪得不够干净 → 调小（如 0.4～0.5）。
用户想以后一直用某个值 → `shaw-autocut config set silence.min_pause 0.8`。

## ③ 字幕

```bash
shaw-autocut subs "<最终视频>"                      # 生成 <最终视频同名>.srt
shaw-autocut subs "<最终视频>" --hotwords terms.txt # 专有名词多时：每行一个词，提高识别准确率
```

**人工审核是必须的步骤，不能跳过。** 生成 SRT 后：

```bash
shaw-autocut review "<最终视频>" --serve
```

这条命令会在浏览器打开审核页面，并**一直阻塞到用户点“审核完成”**，然后返回 JSON。先告诉用户：“审核页面已打开，改完错字后点视频下方的‘审核完成’”，再运行这条命令并等待。
- 如果在没有浏览器的环境里运行：去掉 `--serve`，把生成的 review.html 路径告诉用户
- 用户说自己已经在剪映或文本编辑器里改好了 SRT：`shaw-autocut review "<最终视频>" --done`

审核完成后，**问用户选哪种输出**（不要替用户决定）：
- **只要 SRT**：告诉用户 SRT 文件的位置，可以在剪映里用“文本 → 本地字幕 → 导入”加载，再调字体样式
- **烧录进视频**：
  1. 第一次烧录（或用户没定过样式）先出对比图：`shaw-autocut burn "<最终视频>" --preview`，
     把返回的 `sheet` 图片给用户看，让用户从 clean / box / yellow / minimal 里选
  2. 用户选定后记住：`shaw-autocut config set subtitle.style '"<样式名>"'`（以后不再问）
  3. `shaw-autocut burn "<最终视频>"` → `<名字>_subtitled.<原后缀>`
  - 第一次烧录时顺便问发布平台，记住：`config set subtitle.platform '"douyin"'`
    （douyin / shipinhao / xiaohongshu / bilibili / youtube / screen 录屏演示），自动避开平台界面遮挡
  - 字太大/太小：`config set subtitle.size_scale 1.2`（按比例缩放，不要用固定像素 font_size）
  - 位置：`config set subtitle.position '"lower"'`（bottom 底部 / lower 中下 / center 正中）
  - 颜色：`config set subtitle.color '"#RRGGBB"'`
  - 只想试一次不想记住：`burn --preview --platform douyin --size-scale 1.2 --position lower`
  - 每次改完都重新 `--preview` 给用户看

`burn` 在字幕未审核时会返回退出码 **4**，这时回到上面的审核步骤，**不要**用 `--skip-review` 绕过，除非用户明确要求。

## 退出码

| 码 | 含义 | 处理 |
|---|---|---|
| 0 | 成功 | 读 JSON 结果汇报 |
| 1 | 运行错误 | 把 `detail` 告诉用户；常见原因见 references/troubleshooting.md |
| 2 | 输入错误（文件不存在等） | 核对路径 |
| 3 | 环境未就绪 | 走上面的“环境”流程 |
| 4 | 等待人工审核 | 运行 review |

## 偏好（永久记住）

```bash
shaw-autocut config show                                   # 查看全部
shaw-autocut config set silence.min_pause 0.8
shaw-autocut config set subtitle.style '"box"'             # 字幕样式：clean / box / yellow / minimal
shaw-autocut config set subtitle.max_chars 12              # 每行字数，默认 auto：竖屏 14 / 横屏 20
shaw-autocut config set subtitle.font '"思源黑体 CN"'       # 字符串值要带引号
shaw-autocut config set asr.engine '"whisper"'             # 换识别引擎（默认 funasr，中文最好）
```

全部配置项和调参建议见 `references/parameters.md`。

## 版本与升级

当前版本 **0.1.2**（同时写在 `VERSION`、`src/shaw_autocut/__init__.py`、`pyproject.toml`）。
确认实际运行的版本和位置：`shaw-autocut version`（返回的 `warnings` 不为空就说明有旧版本残留）。
用户要求升级或安装新版本时，严格按 `UPGRADE.md` 的步骤执行：先卸载/删除旧版本，再装新版本，最后核对版本号。
