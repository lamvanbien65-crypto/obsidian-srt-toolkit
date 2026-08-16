// 完成通知 + 自动打开笔记
import { App, Notice } from "obsidian";
import type { TaskRuntime } from "./types";

export function notifyDone(app: App, t: TaskRuntime, settings: {
  autoOpenNote: boolean;
  systemNotify: boolean;
}): void {
  const note = t.result?.outputs?.find((o) => o.type === "note");
  const name = note ? note.path.split("/").pop() : t.label;
  if (settings.systemNotify) {
    try {
      if (typeof Notification !== "undefined") {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        new (Notification as any)("零云剪藏", {
          body: `✅ ${name}`,
        });
      }
    } catch {
      // 系统通知不可用时退化为 Notice
    }
  }
  new Notice(`✅ 任务完成：${name}`);
  if (settings.autoOpenNote && note) {
    app.workspace.openLinkText(note.path, "", false);
  }
}

export function notifyFailed(t: TaskRuntime): void {
  new Notice(`✗ 任务失败：${t.label}（${t.error?.text ?? t.error?.code ?? "未知错误"}）`);
}
