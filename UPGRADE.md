# 安装与升级指南（给 Agent 执行）

当前版本：**0.1.2**

本文件写给执行安装/升级的 Agent。目标：**旧版本完全移除，只保留新版本**，用户的偏好设置不丢。

## 版本号写在哪里

新旧版本都可以从下面任一位置读到版本号，读不到时按后面的顺序依次尝试：

| 位置 | 说明 |
|---|---|
| `shaw-autocut version` | 首选。返回实际运行的版本、代码位置、pip 安装的版本，以及残留警告 |
| `VERSION` 文件 | 0.1.2 起才有 |
| `src/shaw_autocut/__init__.py` 里的 `__version__` | 所有版本都有 |
| `python -m pip show shaw-autocut` | 只有用 pip 安装过才有 |

0.1.0 和 0.1.1 没有 `VERSION` 文件，也没有 `version` 命令，请看 `__init__.py`。

## 不能删的东西

- **偏好和环境缓存 `state.json`**：不在 skill 目录里，升级不需要动它。位置：
  - Windows：`%APPDATA%\shaw-autocut\state.json`
  - macOS：`~/Library/Application Support/shaw-autocut/state.json`
  - Linux：`~/.config/shaw-autocut/state.json`
- **用户的视频和字幕**：工作文件都在视频旁边的 `<视频名>.shaw-autocut/` 目录和同名 `.srt`，与 skill 目录无关。
- **语音识别模型**：在 ModelScope / HuggingFace 的缓存目录里，升级不需要重新下载。

## 升级步骤

下面用 `<SKILL_DIR>` 表示 skill 安装目录，常见位置：
- Claude Code 个人 skill：`~/.claude/skills/shaw-autocut`（Windows：`%USERPROFILE%\.claude\skills\shaw-autocut`）
- 项目内 skill：`<项目>/.claude/skills/shaw-autocut`
- 其它 Agent：以该 Agent 的 skills 目录为准；找不到就问用户装在哪里

用 `<NEW_ZIP>` 表示新版本完整包 `shaw-autocut-0.1.2.zip`（解压后顶层目录是 `shaw-autocut/`）。

### 1. 记录旧版本

```bash
python <SKILL_DIR>/scripts/shaw-autocut version      # 0.1.2 之前的版本没有这个命令，会报错，改看下一行
python -c "import re;print(re.search(r'\d+\.\d+\.\d+', open(r'<SKILL_DIR>/src/shaw_autocut/__init__.py',encoding='utf-8').read()).group())"
python -m pip show shaw-autocut                       # 有输出说明用 pip 装过，记下 Version 和 Location
```

如果旧版本已经是 0.1.2 或更新，告诉用户无需升级，停止。

### 2. 卸载 pip 安装（如果有）

```bash
python -m pip uninstall -y shaw-autocut
```

用 pip 装过时必须卸载：否则命令行里的 `shaw-autocut` 可能仍指向旧版本。

### 3. 删除旧的 skill 目录

先改名备份，第 5 步确认新版本正常后再删除备份。

macOS / Linux：
```bash
mv <SKILL_DIR> <SKILL_DIR>.bak-旧版本号
```

Windows（PowerShell）：
```powershell
Rename-Item "<SKILL_DIR>" "shaw-autocut.bak-旧版本号"
```

### 4. 安装新版本

把 `<NEW_ZIP>` 解压，使顶层的 `shaw-autocut/` 目录正好落在 `<SKILL_DIR>`，**目录名必须是 `shaw-autocut`**。

macOS / Linux：
```bash
unzip <NEW_ZIP> -d "$(dirname <SKILL_DIR>)"
```

Windows（PowerShell）：
```powershell
Expand-Archive "<NEW_ZIP>" -DestinationPath (Split-Path "<SKILL_DIR>")
```

可选：用户希望在任意终端直接敲 `shaw-autocut` 时，再执行：
```bash
python -m pip install -e <SKILL_DIR>
```

### 5. 核对

```bash
python <SKILL_DIR>/scripts/shaw-autocut version
```

必须同时满足：
- `version` 和 `version_file` 都是 `0.1.2`
- `skill_dir` 是 `<SKILL_DIR>`，不是备份目录
- `warnings` 为空数组
- 如果第 4 步做了 pip 安装：`pip_installed_version` 也是 `0.1.2`

有任何一项不满足：不要删除备份，把 `version` 的完整输出告诉用户。
全部满足：删除第 3 步的备份目录，告诉用户“已从 旧版本 升级到 0.1.2”，并附上 `CHANGELOG.md` 里对应版本的更新要点。

### 6. 首次运行

升级后第一次运行任何命令，会自动重新检查一次环境，这是预期行为，之后又会记住，不再重复检查。

## 只想打补丁？

`shaw-autocut-0.1.2-update.zip` 只包含改动过的文件，解压覆盖也能升级。但“覆盖”不会删除旧版本里多出来的文件，所以**推荐用上面的完整替换流程**。
