// TaskQueue：任务排队/串行调度/取消/事件（内存态；断点续传在 Python 文件层）
import type { TaskRuntime, TaskSpec, ProgressEvent, StageState } from "./types";
import { PythonRunner } from "./runner";

type Listener = () => void;

export class TaskQueue {
  private tasks: TaskRuntime[] = [];
  private listeners: Listener[] = [];
  private running = 0;

  constructor(private runner: PythonRunner, private getMaxParallel: () => number) {}

  get all(): TaskRuntime[] {
    return this.tasks;
  }

  get activeCount(): number {
    return this.tasks.filter((t) => t.status === "running").length;
  }

  get queuedCount(): number {
    return this.tasks.filter((t) => t.status === "queued").length;
  }

  onChange(cb: Listener): void {
    this.listeners.push(cb);
  }

  private notify(): void {
    for (const cb of this.listeners) cb();
  }

  enqueue(spec: TaskSpec): TaskRuntime {
    const rt: TaskRuntime = {
      ...spec,
      status: "queued",
      stages: [],
      logTail: [],
      createdAt: Date.now(),
    };
    this.tasks.unshift(rt);
    this.notify();
    this.pump();
    return rt;
  }

  retry(id: string): void {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) return;
    if (t.status !== "failed" && t.status !== "canceled") return;
    t.status = "queued";
    t.stages = [];
    t.logTail = [];
    t.error = undefined;
    t.result = undefined;
    t.cancelRequested = false;
    t.progress = undefined;
    this.notify();
    this.pump();
  }

  cancel(id: string): void {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) return;
    if (t.status === "queued") {
      t.status = "canceled";
      this.notify();
    } else if (t.status === "running") {
      t.cancelRequested = true;
      t.cancelFn?.();
    }
  }

  cancelAll(): void {
    for (const t of this.tasks) {
      if (t.status === "queued") t.status = "canceled";
      else if (t.status === "running") {
        t.cancelRequested = true;
        t.cancelFn?.();
      }
    }
    this.notify();
  }

  clearFinished(): void {
    this.tasks = this.tasks.filter(
      (t) => t.status === "queued" || t.status === "running"
    );
    this.notify();
  }

  private pump(): void {
    const max = Math.max(1, this.getMaxParallel());
    while (this.running < max) {
      const next = this.tasks.find((t) => t.status === "queued");
      if (!next) break;
      this.start(next);
    }
  }

  private async start(t: TaskRuntime): Promise<void> {
    t.status = "running";
    t.stages = [];
    this.running++;
    this.notify();

    const onEvent = (e: ProgressEvent) => {
      this.handleEvent(t, e);
      this.notify();
    };
    try {
      const { done, cancel } = await this.runner.spawn(t.script, t.args, onEvent);
      t.cancelFn = cancel;
      const code = await done;
      this.running--;
      if (t.cancelRequested) {
        t.status = "canceled";
        t.error = undefined;
      } else if (code === 0) {
        t.status = "succeeded";
      } else {
        t.status = "failed";
        t.error = t.error ?? { code: `exit-${code}`, text: `进程退出码 ${code}` };
      }
    } catch (err) {
      this.running--;
      t.status = "failed";
      t.error = { code: "spawn-error", text: (err as Error).message };
    }
    delete t.cancelFn;
    this.notify();
    this.pump();
  }

  private handleEvent(t: TaskRuntime, e: ProgressEvent): void {
    switch (e.t) {
      case "stage": {
        // 阶段链：同名去重、状态流转
        const existing = t.stages.find((s) => s.id === e.stage);
        if (existing) {
          existing.status = "active";
          existing.label = e.label ?? existing.label;
        } else {
          for (const s of t.stages) if (s.status === "active") s.status = "done";
          t.stages.push({
            id: e.stage ?? "unknown",
            label: e.label ?? e.stage ?? "",
            status: "active",
          });
        }
        break;
      }
      case "progress":
        if (typeof e.done === "number" && typeof e.total === "number") {
          t.progress = { done: e.done, total: e.total };
        }
        break;
      case "result":
        t.result = { outputs: e.outputs ?? [] };
        break;
      case "error":
        t.error = { code: e.code ?? "unknown", text: e.text ?? "" };
        t.logTail.push(`[错误] ${e.text ?? e.code ?? ""}`);
        break;
      case "log":
        t.logTail.push(e.line ?? "");
        if (t.logTail.length > 200) t.logTail.shift();
        break;
    }
  }
}
