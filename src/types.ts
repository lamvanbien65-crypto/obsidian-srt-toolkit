// 共享类型定义

export type TaskId = string;
export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";
export type TaskKind =
  | "bili-onestop"
  | "bili-download"
  | "bili-subtitle"
  | "transcribe"
  | "standard";

export interface TaskSpec {
  id: TaskId;
  kind: TaskKind;
  label: string; // 视频标题或 BV 号
  script: string; // python/ 下的脚本名
  args: string[]; // CLI 参数（数组传参，不拼 shell）
}

export interface StageState {
  id: string;
  label: string;
  status: "pending" | "active" | "done" | "failed" | "skipped";
}

export interface ProgressEvent {
  t: "stage" | "progress" | "result" | "error" | "log";
  stage?: string;
  label?: string;
  done?: number;
  total?: number;
  outputs?: { type: "note" | "video" | "audio" | "json" | "srt" | "txt"; path: string }[];
  code?: string;
  text?: string;
  line?: string;
}

export interface TaskRuntime extends TaskSpec {
  status: TaskStatus;
  stages: StageState[];
  progress?: { done: number; total: number };
  logTail: string[];
  result?: { outputs: ProgressEvent["outputs"] };
  error?: { code: string; text: string };
  createdAt: number;
  cancelFn?: () => void; // 运行时注入
  cancelRequested?: boolean; // 取消请求标记（进程退出时归入 canceled 而非 failed）
}

export interface SrtSettings {
  pythonPath: string; // 留空 = 自动探测
  whisperModel: string; // 留空 = ~/.cache/whisper/ggml-large-v3-turbo.bin
  downloadDir: string; // 留空 = B站剪藏/下载（vault 相对）
  noteDir: string; // 留空 = B站剪藏/笔记（vault 相对）
  biliCookie: string; // B站 cookie（SESSDATA=...; bili_jct=...; DedeUserID=...）
  cookiesBrowser: string; // 浏览器 cookie："" / chrome / safari / firefox / edge
  quality: string; // 1080 / 720 / best
  preferSub: string; // auto / cc / ai
  audioOnly: boolean; // 有字幕时仅下载音频
  breakTarget: number; // 断句目标字数（纯本地算法，无 LLM）
  autoOpenNote: boolean;
  systemNotify: boolean;
  maxParallel: number;
}

export const DEFAULT_SETTINGS: SrtSettings = {
  pythonPath: "",
  whisperModel: "",
  downloadDir: "B站剪藏/下载",
  noteDir: "B站剪藏/笔记",
  biliCookie: "",
  cookiesBrowser: "",
  quality: "1080",
  preferSub: "auto",
  audioOnly: false,
  breakTarget: 25,
  autoOpenNote: true,
  systemNotify: true,
  maxParallel: 1,
};
