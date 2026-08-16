// 子进程环境构造：敏感信息（key/cookie）只走 env，绝不进 argv
import { App, FileSystemAdapter } from "obsidian";
import type { SrtSettings } from "./types";

const BREW_DIRS = ["/opt/homebrew/bin", "/usr/local/bin"];

export function getVaultRoot(app: App): string {
  return (app.vault.adapter as FileSystemAdapter).getBasePath();
}

export function buildEnv(app: App, s: SrtSettings): Record<string, string> {
  const env: Record<string, string> = {
    ...process.env,
    OBSIDIAN_VAULT_ROOT: getVaultRoot(app),
    OBSIDIAN_JSON_PROGRESS: "1",
  };
  // PATH 注入 brew 目录（Finder 启动的 Obsidian 无 homebrew PATH）
  const pathParts = [...BREW_DIRS];
  if (s.pythonPath) {
    const dir = s.pythonPath.replace(/\/[^/]+$/, "");
    pathParts.unshift(dir);
  }
  if (process.env.PATH) pathParts.push(process.env.PATH);
  env.PATH = pathParts.join(":");

  // 零云硬保证：插件注入 LLM_PROVIDER=none，任何脚本都不会发起云 LLM 调用
  //（llm.py 的 none 分支直接返回 None；Claudian 环境不设此变量，行为不变）
  env.LLM_PROVIDER = "none";
  if (s.biliCookie) env.BILI_COOKIE = s.biliCookie;
  if (s.whisperModel) env.OBSIDIAN_WHISPER_MODEL = s.whisperModel;
  if (s.breakTarget > 0) env.OBSIDIAN_BREAK_TARGET = String(s.breakTarget);
  return env;
}

// 探测 python3 绝对路径（Finder 启动的 Obsidian 无 shell PATH）
export async function detectPython3(override: string): Promise<string> {
  if (override && override.trim()) return override.trim();
  const candidates = [
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
  ];
  for (const c of candidates) {
    try {
      const { execFileSync } = await import("child_process");
      execFileSync(c, ["--version"], { stdio: "pipe" });
      return c;
    } catch {
      // 继续尝试下一个
    }
  }
  // 兜底：zsh login shell 探测
  try {
    const { execFileSync } = await import("child_process");
    const out = execFileSync("/bin/zsh", ["-lc", "command -v python3"], {
      encoding: "utf8",
      timeout: 5000,
    });
    const p = out.trim().split("\n")[0];
    if (p) return p;
  } catch {
    // ignore
  }
  throw new Error("未找到 python3，请在设置页手动指定路径");
}
