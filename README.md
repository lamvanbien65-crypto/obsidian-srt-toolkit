# 零云剪藏 (ZeroCloud Clip)

> **零云剪藏**：B站视频 → 结构化字幕笔记，一条命令全自动，**零云模式——全本地处理，不调用任何云 LLM / API**。

# ZeroCloud Clip

**ZeroCloud Clip** is an Obsidian plugin that turns Bilibili videos into structured subtitle notes in one command. **Zero-cloud by design**: every step — downloading, subtitle fetching, whisper transcription, and sentence segmentation — runs 100% locally. No cloud LLM, no API keys, no data leaves your machine.

**Key features:**
- One-command pipeline: download → (CC/AI subtitles or local whisper) → segmented notes
- Bilibili download (1080P default), subtitle fetching, local video transcription
- Task queue with live progress and clean cancellation
- Built-in environment check (python3 / yt-dlp / ffmpeg / whisper-cli / model)
- macOS first (Apple Metal whisper acceleration, ~15-20x realtime)

## 功能特性 / Features

- 🎬 **B站一键剪藏**：下载 →（CC/AI 字幕抓取 或 whisper 本地转写）→ 标准断句 → 字幕笔记，全自动
- 📥 **B站下载**：链接 / b23.tv 短链 / 「稍后再看」列表 / 裸 BV 号，默认 1080P
- 💬 **B站抓字幕**：CC（UP主上传）与 AI 字幕抓取，`auto|cc|ai` 偏好
- 🗒 **标准字幕剪藏**：断句规范（25 字/条目标），纯算法，零 LLM
- 📺 **本地视频转录**：whisper（Apple Metal 加速，约 15~20 倍速）→ .txt/.srt/.json 三件套
- ⏱ **任务系统**：串行队列、阶段链进度、取消无残留进程、重试
- 🔒 **零云保证**：UI 无云入口 + `LLM_PROVIDER=none` 注入 + 构建产物零云引用（三层硬保证）
- 🪟 设置页含**环境自检**：一键检查 python3 / yt-dlp / ffmpeg / whisper-cli / 模型，缺失给出安装命令

## 安装 / Install

1. **BRAT**（推荐）：BRAT → Add Beta plugin → 输入仓库地址
2. **手动**：下载 Releases 的 `main.js`、`manifest.json`、`styles.css` 与 `python/`，放入 `.obsidian/plugins/zero-cloud-clip/`，启用「零云剪藏」

## 依赖 / Dependencies

macOS 优先。首次使用前运行设置页「环境自检」，或手动安装：

```bash
brew install python3 yt-dlp ffmpeg whisper-cpp
# 烧录硬字幕（可选，需要 libass）：
brew install ffmpeg-full
# whisper 模型（large-v3-turbo，约 1.6GB）：
curl -L -C - -o ~/.cache/whisper/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
```

## 使用 / Usage

| 命令 | 说明 |
|------|------|
| B站一键剪藏 | 输入链接/BV号 → 全自动出字幕笔记 |
| B站下载视频 | 只下载，不剪藏 |
| B站抓取字幕 | 独立抓取 CC/AI 字幕三件套 |
| 视频转录 | 本地视频 → 三件套 |
| 标准字幕剪藏 | 本地已转录视频 → 断句笔记 |
| 打开任务面板 / 取消全部 | 任务管理 |

**提示**：B站 cookie（设置页填入）可解锁 AI 字幕抓取与会员画质——有字幕的视频直接跳过 whisper 转写。

## 零云架构 / Zero-Cloud Architecture

1. **UI 层**：无任何 LLM 模式入口，剪藏固定标准模式（`--no-drop`）
2. **env 层**：所有子进程注入 `LLM_PROVIDER=none`（llm.py none 分支直接返回 None，物理上无法发起云调用）
3. **产物层**：构建产物不含任何云端 API 引用

说话人标注、翻译等需要 LLM 的能力**不在本插件范围**——保持零云承诺，也保持产品边界清晰。

## 开发 / Development

```bash
git clone <repo> && cd obsidian-srt-toolkit
npm install
node build.mjs      # esbuild + python 快照同步 + 部署到 vault 插件目录
npm run check:load  # 严格契约回归（default 导出 + onload 无异常）
```

## 许可证 / License

MIT
