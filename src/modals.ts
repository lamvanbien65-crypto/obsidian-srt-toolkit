// 输入 Modal：BV 链接 / 说话人 / 视频选择
import { App, Modal, Notice, Setting, SuggestModal } from "obsidian";
import type { TaskKind } from "./types";
import type { SrtPlugin } from "./main";

const BILI_RE = /BV[0-9A-Za-z]{10}|b23\.tv\/\S+|bilibili\.com\/video\/[A-Za-z0-9]+/;

export class BiliModal extends Modal {
  constructor(app: App, private plugin: SrtPlugin, private mode: TaskKind) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    const titles: Record<string, string> = {
      "bili-onestop": "B站一键剪藏",
      "bili-download": "B站下载视频",
      "bili-subtitle": "B站抓取字幕",
    };
    contentEl.createEl("h3", { text: titles[this.mode] });
    if (this.mode === "bili-onestop") {
      // 零云模式说明：一键剪藏固定为标准字幕（无 LLM，纯本地）
      contentEl.createEl("p", {
        text: "标准字幕剪藏 · 零云纯本地：下载 → 字幕/whisper 转写 → 断句 → 笔记（不使用任何云 LLM）",
        cls: "srt-modal-hint",
      });
    }

    let url = "";
    new Setting(contentEl).setName("B站链接 / BV号 / 分享短链").addText((t) => {
      t.setPlaceholder("https://www.bilibili.com/video/BVxxxx 或 BVxxxx 或 b23.tv/xxx")
        .onChange((v) => (url = v.trim()));
      t.inputEl.addClass("srt-wide");
    });

    new Setting(contentEl).addButton((b) =>
      b.setButtonText("开始").setCta().onClick(() => {
        if (!BILI_RE.test(url)) {
          new Notice("请输入有效的 B站链接或 BV 号");
          return;
        }
        this.plugin.enqueueBili(this.mode, url);
        this.close();
      })
    );
  }

  onClose(): void {
    const { contentEl } = this;
    contentEl.empty();
  }
}

export class FileSuggestModal extends SuggestModal<string> {
  constructor(app: App, private plugin: SrtPlugin, private mode: TaskKind) {
    super(app);
    this.setPlaceholder("输入视频文件名关键字…");
    this.setInstructions([
      { command: "↑↓", purpose: "选择" },
      { command: "↵", purpose: "开始任务" },
    ]);
  }

  getSuggestions(query: string): string[] {
    const files = this.app.vault.getFiles();
    const exts = ["mp4", "mov", "mkv", "webm", "m4a", "mp3", "flac", "wav", "aac", "m4b"];
    return files
      .filter((f) => {
        const ext = f.extension.toLowerCase();
        return exts.includes(ext) && (!query || f.path.toLowerCase().includes(query.toLowerCase()));
      })
      .slice(0, 20)
      .map((f) => f.path);
  }

  renderSuggestion(item: string, el: HTMLElement): void {
    el.setText(item);
  }

  onChooseSuggestion(item: string, evt: MouseEvent | KeyboardEvent): void {
    this.plugin.enqueueClip(this.mode, item);
  }
}
