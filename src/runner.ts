// PythonRunner：spawn 子进程 + 协议解析 + 取消（进程组）
import { spawn, execFileSync } from "child_process";
import * as readline from "readline";
import { normalizePath, App } from "obsidian";
import type { ProgressEvent } from "./types";
import { buildEnv, detectPython3, getVaultRoot } from "./env";
import { parseLine } from "./protocol";
import type { SrtSettings } from "./types";

export interface RunningTask {
  done: Promise<number>;
  cancel: () => void;
}

export class PythonRunner {
  private pythonPath: string | null = null;
  constructor(private app: App, private settings: SrtSettings) {}

  async getPython(): Promise<string> {
    if (!this.pythonPath) {
      this.pythonPath = await detectPython3(this.settings.pythonPath);
    }
    return this.pythonPath;
  }

  /** 重新探测（设置页「自动检测」按钮用） */
  async redetect(): Promise<string> {
    this.pythonPath = await detectPython3(this.settings.pythonPath);
    return this.pythonPath;
  }

  getScriptPath(script: string): string {
    const base = getVaultRoot(this.app);
    return normalizePath(
      `${base}/.obsidian/plugins/srt-subtitle-toolkit/python/${script}`
    );
  }

  async spawn(script: string, args: string[], onEvent: (e: ProgressEvent) => void): Promise<RunningTask> {
    const scriptPath = this.getScriptPath(script);
    const py = await this.getPython(); // 用探测到的绝对路径（Finder 启动的 Obsidian 无 shell PATH）
    const child = spawn(py, [scriptPath, ...args], {
      env: buildEnv(this.app, this.settings),
      detached: true, // 进程组组长：取消时 kill(-pid) 全树杀死
      stdio: ["ignore", "pipe", "pipe"],
    });

    const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
    rl.on("line", (line: string) => {
      const ev = parseLine(line);
      if (ev) onEvent(ev);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      for (const line of chunk.toString("utf8").split("\n")) {
        if (line.trim()) onEvent({ t: "log", line });
      }
    });

    const done = new Promise<number>((resolve) => {
      child.on("close", (code: number | null) => resolve(code ?? -1));
      child.on("error", (err) => {
        onEvent({ t: "error", code: "spawn-error", text: err.message });
        resolve(-1);
      });
    });

    let canceled = false;
    const cancel = () => {
      if (canceled || child.exitCode !== null) return;
      canceled = true;
      try {
        process.kill(-child.pid!, "SIGTERM"); // 进程组：连带 whisper/yt-dlp/curl
      } catch {
        try { child.kill("SIGTERM"); } catch { /* ignore */ }
      }
      // 3 秒宽限落断点缓存，再强杀
      setTimeout(() => {
        try { process.kill(-child.pid!, "SIGKILL"); } catch { /* ignore */ }
      }, 3000).unref();
    };

    return { done, cancel };
  }
}
