// 设置页
import { App, PluginSettingTab, Setting, Notice } from "obsidian";
import { execFileSync } from "child_process";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import type { SrtPlugin } from "./main";
import type { SrtSettings } from "./types";

export class SrtSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: SrtPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "零云剪藏" });
    containerEl.createEl("p", {
      text: "B站视频下载、CC/AI 字幕抓取、whisper 转录与标准字幕剪藏一体化。零云模式：全部处理本地完成，不调用任何云 LLM。依赖：python3、yt-dlp、ffmpeg、whisper-cli（brew install 安装）。",
    });

    // ---------- 环境 ----------
    containerEl.createEl("h3", { text: "环境" });
    new Setting(containerEl)
      .setName("环境自检")
      .setDesc("检查 python3 / yt-dlp / ffmpeg / whisper-cli / whisper 模型是否就绪")
      .addButton((b) =>
        b.setButtonText("检测全部").onClick(() => {
          const el = containerEl.createDiv({ cls: "srt-env-check" });
          el.empty();
          const has = (cmd: string): boolean => {
            try {
              execFileSync("which", [cmd], { stdio: "pipe" });
              return true;
            } catch {
              return false;
            }
          };
          const rows: [string, boolean, string][] = [
            ["python3", has("python3"), "brew install python3"],
            ["yt-dlp", has("yt-dlp"), "brew install yt-dlp"],
            ["ffmpeg", has("ffmpeg"), "brew install ffmpeg（烧录硬字幕需 ffmpeg-full）"],
            ["whisper-cli", has("whisper-cli"), "brew install whisper-cpp"],
          ];
          const model = this.plugin.settings.whisperModel ||
            join(homedir(), ".cache", "whisper", "ggml-large-v3-turbo.bin");
          const modelOk = existsSync(model);
          rows.push(["whisper 模型（large-v3-turbo）", modelOk,
            `curl -L -C - -o "${model}" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin`]);
          for (const [name, ok, fix] of rows) {
            el.createEl("div", {
              text: ok ? `✅ ${name}` : `❌ ${name}  →  ${fix}`,
              cls: ok ? "srt-env-ok" : "srt-env-missing",
            });
          }
        })
      );
    new Setting(containerEl)
      .setName("python3 路径")
      .setDesc("留空自动探测（/opt/homebrew/bin → /usr/local/bin → /usr/bin）")
      .addText((t) => {
        t.setPlaceholder("自动探测")
          .setValue(this.plugin.settings.pythonPath)
          .onChange(async (v) => {
            this.plugin.settings.pythonPath = v.trim();
            await this.plugin.saveSettings();
          });
      })
      .addButton((b) =>
        b.setButtonText("检测").onClick(async () => {
          try {
            const p = await this.plugin.runner.redetect();
            new Notice(`✅ python3: ${p}`);
          } catch (e) {
            new Notice(`✗ ${(e as Error).message}`);
          }
        })
      );
    new Setting(containerEl)
      .setName("whisper 模型路径")
      .setDesc("留空 = ~/.cache/whisper/ggml-large-v3-turbo.bin")
      .addText((t) =>
        t.setPlaceholder("~/.cache/whisper/ggml-large-v3-turbo.bin")
          .setValue(this.plugin.settings.whisperModel)
          .onChange(async (v) => {
            this.plugin.settings.whisperModel = v.trim();
            await this.plugin.saveSettings();
          })
      );
    new Setting(containerEl)
      .setName("下载目录")
      .setDesc("B站视频/音频与字幕产物目录（vault 相对路径，留空 = B站剪藏/下载）")
      .addText((t) =>
        t.setPlaceholder("B站剪藏/下载")
          .setValue(this.plugin.settings.downloadDir)
          .onChange(async (v) => {
            this.plugin.settings.downloadDir = v.trim();
            await this.plugin.saveSettings();
          })
      );
    new Setting(containerEl)
      .setName("笔记输出目录")
      .setDesc("剪藏笔记输出目录（vault 相对路径，留空 = B站剪藏/笔记）")
      .addText((t) =>
        t.setPlaceholder("B站剪藏/笔记")
          .setValue(this.plugin.settings.noteDir)
          .onChange(async (v) => {
            this.plugin.settings.noteDir = v.trim();
            await this.plugin.saveSettings();
          })
      );

    // ---------- B站 ----------
    containerEl.createEl("h3", { text: "B站" });
    new Setting(containerEl)
      .setName("B站 Cookie")
      .setDesc(
        "AI 字幕/会员字幕/会员画质需要登录态。从浏览器 DevTools → Network → 任意 B站请求的 Cookie 头复制（至少含 SESSDATA=...; bili_jct=...; DedeUserID=...）。明文存于插件 data.json，勿外传。"
      )
      .addTextArea((t) => {
        t.setPlaceholder("SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx")
          .setValue(this.plugin.settings.biliCookie)
          .onChange(async (v) => {
            this.plugin.settings.biliCookie = v.trim();
            await this.plugin.saveSettings();
          });
        t.inputEl.rows = 3;
        t.inputEl.style.width = "100%";
        t.inputEl.style.fontFamily = "monospace";
        t.inputEl.style.fontSize = "11px";
      });
    new Setting(containerEl)
      .setName("浏览器 Cookie（替代方案）")
      .setDesc("透传 yt-dlp --cookies-from-browser，与上方文本框二选一或互补")
      .addDropdown((d) => {
        const opts: Record<string, string> = { "": "不使用", chrome: "Chrome", safari: "Safari", firefox: "Firefox", edge: "Edge" };
        for (const [k, v] of Object.entries(opts)) d.addOption(k, v);
        d.setValue(this.plugin.settings.cookiesBrowser)
          .onChange(async (v) => {
            this.plugin.settings.cookiesBrowser = v;
            await this.plugin.saveSettings();
          });
      });
    new Setting(containerEl)
      .setName("画质")
      .addDropdown((d) => {
        d.addOption("1080", "1080P（默认）")
          .addOption("720", "720P")
          .addOption("best", "最高画质")
          .setValue(this.plugin.settings.quality)
          .onChange(async (v) => {
            this.plugin.settings.quality = v;
            await this.plugin.saveSettings();
          });
      });
    new Setting(containerEl)
      .setName("字幕偏好")
      .addDropdown((d) => {
        d.addOption("auto", "自动（CC中文>AI中文>CC英文>AI英文）")
          .addOption("cc", "优先 CC 字幕（UP主上传）")
          .addOption("ai", "优先 AI 字幕（B站生成）")
          .setValue(this.plugin.settings.preferSub)
          .onChange(async (v) => {
            this.plugin.settings.preferSub = v;
            await this.plugin.saveSettings();
          });
      });
    new Setting(containerEl)
      .setName("有字幕时仅下载音频")
      .setDesc("开启后：抓到字幕时只下音频轨省空间（笔记不嵌视频）；关闭则下完整视频")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.audioOnly).onChange(async (v) => {
          this.plugin.settings.audioOnly = v;
          await this.plugin.saveSettings();
        })
      );

    // ---------- 默认参数 ----------
    containerEl.createEl("h3", { text: "默认参数" });
    new Setting(containerEl)
      .setName("断句目标字数")
      .addText((t) =>
        t.setValue(String(this.plugin.settings.breakTarget))
          .onChange(async (v) => {
            const n = parseInt(v, 10);
            if (n > 0) {
              this.plugin.settings.breakTarget = n;
              await this.plugin.saveSettings();
            }
          })
      );

    // ---------- 行为 ----------
    containerEl.createEl("h3", { text: "行为" });
    new Setting(containerEl)
      .setName("完成后自动打开笔记")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.autoOpenNote).onChange(async (v) => {
          this.plugin.settings.autoOpenNote = v;
          await this.plugin.saveSettings();
        })
      );
    new Setting(containerEl)
      .setName("系统通知")
      .addToggle((t) =>
        t.setValue(this.plugin.settings.systemNotify).onChange(async (v) => {
          this.plugin.settings.systemNotify = v;
          await this.plugin.saveSettings();
        })
      );
    new Setting(containerEl)
      .setName("最大并发任务数")
      .setDesc("whisper 转写独占 CPU/Metal，建议 1（串行）；纯下载类可调 2-3")
      .addDropdown((d) => {
        for (const n of [1, 2, 3]) d.addOption(String(n), String(n));
        d.setValue(String(this.plugin.settings.maxParallel))
          .onChange(async (v) => {
            this.plugin.settings.maxParallel = parseInt(v, 10);
            await this.plugin.saveSettings();
          });
      });
  }
}
