// 零云剪藏 — 主入口
import { Notice, Plugin, normalizePath } from "obsidian";
import type { SrtSettings, TaskKind } from "./types";
import { DEFAULT_SETTINGS } from "./types";
import { PythonRunner } from "./runner";
import { getVaultRoot } from "./env";
import { TaskQueue } from "./queue";
import { SrtSettingTab } from "./settings";
import { registerCommands } from "./commands";
import { TaskView, TASK_VIEW_TYPE, activateTaskView } from "./task-view";
import { notifyDone, notifyFailed } from "./notify";

let seq = 0;

export class SrtPlugin extends Plugin {
  settings: SrtSettings = DEFAULT_SETTINGS;
  runner!: PythonRunner;
  queue!: TaskQueue;
  private statusEl: HTMLElement | null = null;
  private statusTimer: number | null = null;

  async onload(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.runner = new PythonRunner(this.app, this.settings);
    this.queue = new TaskQueue(this.runner, () => this.settings.maxParallel);

    this.registerView(TASK_VIEW_TYPE, (leaf) => new TaskView(leaf, this));

    registerCommands(this);
    this.addSettingTab(new SrtSettingTab(this.app, this));

    this.addRibbonIcon("list-checks", "零云剪藏 · 任务面板", () => activateTaskView(this));
    this.setupStatusBar();
    this.subscribeQueue();
  }

  onunload(): void {
    this.queue.cancelAll(); // 杀残留子进程
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }

  // ---------- 任务入队 ----------

  enqueueBili(mode: TaskKind, url: string): void {
    const s = this.settings;
    const label = url.match(/BV[0-9A-Za-z]{10}/)?.[0] ?? url;
    const base = getVaultRoot(this.app);
    // 设置页目录为 vault 相对路径 → 绝对路径（Python cwd 非 vault 根，相对路径会错位）
    const absDir = (p: string): string | undefined =>
      p ? normalizePath(`${base}/${p}`) : undefined;
    // 通用默认目录（发布版：不依赖任何个人 vault 命名；设置页可改）
    const dlDir = absDir(s.downloadDir) || normalizePath(`${base}/B站剪藏/下载`);
    const noteDir = absDir(s.noteDir) || normalizePath(`${base}/B站剪藏/笔记`);

    if (mode === "bili-onestop") {
      // 一键剪藏（标准字幕，零云）：画质/字幕偏好/音频开关/输出目录全部透传
      const args = [url, "--mode", "standard", "--quality", s.quality, "--prefer-sub", s.preferSub];
      if (s.audioOnly) args.push("--audio-only");
      if (s.cookiesBrowser) args.push("--cookies-from-browser", s.cookiesBrowser);
      args.push("--dir", dlDir);
      args.push("--out-dir", noteDir);
      this.enqueue("bili-onestop", label, "b站全流程.py", args);
    } else if (mode === "bili-download") {
      const args = [url, "--mode", "download-only", "--quality", s.quality, "--dir", dlDir];
      if (s.cookiesBrowser) args.push("--cookies-from-browser", s.cookiesBrowser);
      this.enqueue("bili-download", label, "b站全流程.py", args);
    } else if (mode === "bili-subtitle") {
      // 独立抓字幕：输出到下载目录（脚本默认 cwd 可能是插件不可写目录）
      this.enqueue("bili-subtitle", label, "b站字幕.py",
        [url, "--out-dir", dlDir, "--prefer", s.preferSub]);
    }
  }

  enqueueClip(mode: TaskKind, file: string): void {
    switch (mode) {
      case "transcribe":
        this.enqueue("transcribe", file, "视频转录.py", [file]);
        break;
      case "standard":
        // 零云：--no-drop 跳过 LLM 剔除其他声音，纯本地断句
        this.enqueue("standard", file, "标准字幕剪藏.py", [file, "--no-drop"]);
        break;
      default:
        new Notice(`未支持的模式：${mode}`);
    }
  }

  private enqueue(kind: TaskKind, label: string, script: string, args: string[]): void {
    const t = this.queue.enqueue({
      id: `${Date.now()}-${seq++}`,
      kind,
      label,
      script,
      args,
    });
    // 完成通知订阅（在 subscribeQueue 统一处理）
  }

  // ---------- 状态栏 / 通知 ----------

  private setupStatusBar(): void {
    this.statusEl = this.addStatusBarItem();
    this.statusEl.addClass("srt-statusbar");
    this.statusEl.setText("零云剪藏");
    this.statusEl.onclick = () => activateTaskView(this);
    this.updateStatusBar();
  }

  private subscribeQueue(): void {
    let lastNotifiedId: string | null = null;
    this.queue.onChange(() => {
      this.updateStatusBar();
      // 完成/失败通知（每个任务只通知一次）
      for (const t of this.queue.all) {
        if (t.id === lastNotifiedId) continue;
        if (t.status === "succeeded") {
          lastNotifiedId = t.id;
          notifyDone(this.app, t, {
            autoOpenNote: this.settings.autoOpenNote,
            systemNotify: this.settings.systemNotify,
          });
        } else if (t.status === "failed") {
          lastNotifiedId = t.id;
          notifyFailed(t);
        }
      }
    });
  }

  private updateStatusBar(): void {
    if (!this.statusEl) return;
    const running = this.queue.activeCount;
    const queued = this.queue.queuedCount;
    if (running + queued === 0) {
      this.statusEl.setText("零云剪藏");
    } else {
      this.statusEl.setText(`零云剪藏：${running} 进行中${queued ? ` · ${queued} 排队` : ""}`);
    }
  }
}

// Obsidian 加载契约：main.js 必须 default 导出 Plugin 子类
export default SrtPlugin;
