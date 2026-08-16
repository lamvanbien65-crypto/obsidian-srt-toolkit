// 协议解析：Python 端以 \x1e 开头的 JSON 行 = 事件；其余 stdout 行 = 日志
import type { ProgressEvent } from "./types";

export const EVENT_PREFIX = "\x1e";

export function parseLine(line: string): ProgressEvent | null {
  if (line.startsWith(EVENT_PREFIX)) {
    try {
      const obj = JSON.parse(line.slice(1));
      if (obj && typeof obj === "object" && typeof obj.t === "string") {
        return obj as ProgressEvent;
      }
    } catch {
      // 解析失败按日志处理
    }
    return { t: "log", line: line.slice(1) };
  }
  if (line.trim() === "") return null;
  return { t: "log", line };
}
