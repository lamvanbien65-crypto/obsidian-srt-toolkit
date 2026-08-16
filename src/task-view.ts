// 右侧栏任务面板：任务卡片（状态徽章/阶段链/进度条/取消/重试/日志）
import { ItemView, WorkspaceLeaf, Notice } from "obsidian";
import type { SrtPlugin } from "./main";
import type { TaskRuntime } from "./types";

export const TASK_VIEW_TYPE = "srt-subtitle-toolkit-tasks";

export class TaskView extends ItemView {
  constructor(leaf: WorkspaceLeaf, private plugin: SrtPlugin) {
    super(leaf);
  }

  getViewType(): string {
    return TASK_VIEW_TYPE;
  }

  getDisplayText(): string {
    return "零云剪藏 · 任务";
  }

  getIcon(): string {
    return "list-checks";
  }

  async onOpen(): Promise<void> {
    this.plugin.queue.onChange(() => this.render());
    this.render();
  }

  private render(): void {
    const c = this.containerEl.children[1] as HTMLElement;
    c.empty();
    c.addClass("srt-toolkit-view");

    const tasks = this.plugin.queue.all;
    if (tasks.length === 0) {
      c.createEl("div", { text: "暂无任务。使用命令面板或 ribbon 按钮开始。", cls: "srt-empty" });
      return;
    }

    const header = c.createEl("div", { cls: "srt-task-header" });
    header.createEl("span", { text: `任务（${tasks.length}）` });
    const clearBtn = header.createEl("button", { text: "清除已完成", cls: "srt-btn" });
    clearBtn.onclick = () => this.plugin.queue.clearFinished();

    for (const t of tasks) {
      c.appendChild(this.card(t));
    }
  }

  private card(t: TaskRuntime): HTMLElement {
    const el = document.createElement("div");
    el.addClass("srt-task-card");

    const head = el.createDiv({ cls: "srt-task-head" });
    head.createEl("span", { text: t.label, cls: "srt-task-title" });
    const badge = head.createEl("span", { text: statusText(t.status), cls: `srt-badge srt-badge-${t.status}` });

    // 阶段链
    if (t.stages.length > 0) {
      const chain = el.createDiv({ cls: "srt-stages" });
      for (const s of t.stages) {
        const chip = chain.createEl("span", {
          text: s.label,
          cls: `srt-stage srt-stage-${s.status}`,
        });
        if (s.status === "active") chip.addClass("srt-stage-active");
      }
    }

    // 块级进度条
    if (t.progress && t.progress.total > 0) {
      const bar = el.createDiv({ cls: "srt-progress" });
      const fill = bar.createDiv({ cls: "srt-progress-fill" });
      const pct = Math.min(100, Math.round((t.progress.done / t.progress.total) * 100));
      fill.style.width = `${pct}%`;
      bar.createEl("span", { text: `${t.progress.done}/${t.progress.total}`, cls: "srt-progress-text" });
    }

    // 错误
    if (t.error && (t.status === "failed" || t.status === "running")) {
      el.createEl("div", { text: `⚠ ${t.error.text ?? t.error.code}`, cls: "srt-error" });
    }

    // 结果
    if (t.result?.outputs?.length) {
      const res = el.createDiv({ cls: "srt-result" });
      for (const o of t.result.outputs) {
        const a = res.createEl("a", { text: o.path, cls: "srt-result-link" });
        a.onclick = () => this.app.workspace.openLinkText(o.path, "", false);
      }
    }

    // 操作按钮
    const ops = el.createDiv({ cls: "srt-ops" });
    if (t.status === "running" || t.status === "queued") {
      const cancel = ops.createEl("button", { text: "取消", cls: "srt-btn" });
      cancel.onclick = () => this.plugin.queue.cancel(t.id);
    }
    if (t.status === "failed" || t.status === "canceled") {
      const retry = ops.createEl("button", { text: "重试", cls: "srt-btn srt-btn-cta" });
      retry.onclick = () => this.plugin.queue.retry(t.id);
    }
    if (t.logTail.length > 0) {
      const toggle = ops.createEl("button", { text: "日志", cls: "srt-btn" });
      const logBox = el.createEl("pre", { cls: "srt-log" });
      logBox.hidden = true;
      logBox.setText(t.logTail.slice(-30).join("\n"));
      toggle.onclick = () => {
        logBox.hidden = !logBox.hidden;
        logBox.setText(t.logTail.slice(-30).join("\n"));
      };
    }

    return el;
  }
}

function statusText(s: TaskRuntime["status"]): string {
  switch (s) {
    case "queued": return "排队中";
    case "running": return "进行中";
    case "succeeded": return "完成";
    case "failed": return "失败";
    case "canceled": return "已取消";
  }
}

export function activateTaskView(plugin: SrtPlugin): void {
  const { workspace } = plugin.app;
  const existing = workspace.getLeavesOfType(TASK_VIEW_TYPE);
  if (existing.length > 0) {
    workspace.revealLeaf(existing[0]);
    return;
  }
  const leaf = workspace.getRightLeaf(false);
  if (!leaf) {
    new Notice("无法打开右侧栏");
    return;
  }
  leaf.setViewState({ type: TASK_VIEW_TYPE, active: true });
  workspace.revealLeaf(leaf);
}
