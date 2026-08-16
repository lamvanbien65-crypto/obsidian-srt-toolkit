// 构建脚本：esbuild 构建 main.js + 同步 python 脚本到 vault 插件目录
import { execSync } from "child_process";
import { cpSync, existsSync, mkdirSync, rmSync } from "fs";
import { homedir } from "os";
import path from "path";

const VAULT = process.env.OBSIDIAN_VAULT
  || "/Users/skylines/Desktop/SRT-ThinkTank";
const PLUGIN_DIR = path.join(VAULT, ".obsidian", "plugins", "srt-subtitle-toolkit");
const SRC_PYTHON = path.join(VAULT, "Function", "视频转录"); // 事实源脚本
const PLUGIN_PYTHON = path.join(process.cwd(), "python");     // 仓库内快照

// 同步脚本：从 vault 的 Function/视频转录/ 拷贝最新脚本到 python/
// CI/干净环境（无 vault）时跳过：仓库内 python/ 快照即事实源
function syncPython() {
  if (!existsSync(SRC_PYTHON)) {
    console.log("  · vault 脚本目录不存在（CI/干净环境）：跳过同步，使用仓库内 python/ 快照");
    return;
  }
  const files = ["视频转录.py", "字幕说话人标记.py", "标准字幕剪藏.py", "b站下载.py", "llm.py"];
  for (const f of files) {
    const src = path.join(SRC_PYTHON, f);
    if (!existsSync(src)) { console.error(`✗ 源脚本不存在：${src}`); process.exit(1); }
    cpSync(src, path.join(PLUGIN_PYTHON, f));
    console.log(`  · 已同步 ${f}`);
  }
  console.log("✅ python/ 已与 Function/视频转录/ 同步");
}

function deploy() {
  if (!existsSync(VAULT)) {
    console.log("  · vault 不存在（CI/干净环境）：跳过部署");
    return;
  }
  mkdirSync(PLUGIN_DIR, { recursive: true });
  // 清空旧产物（保留 manifest 由主构建负责）
  for (const f of ["main.js", "main.js.map", "styles.css", "manifest.json", "versions.json"]) {
    const p = path.join(PLUGIN_DIR, f);
    if (existsSync(p)) rmSync(p, { force: true });
  }
  rmSync(path.join(PLUGIN_DIR, "python"), { recursive: true, force: true });

  cpSync(path.join(process.cwd(), "main.js"), path.join(PLUGIN_DIR, "main.js"));
  cpSync(path.join(process.cwd(), "styles.css"), path.join(PLUGIN_DIR, "styles.css"));
  cpSync(path.join(process.cwd(), "manifest.json"), path.join(PLUGIN_DIR, "manifest.json"));
  if (existsSync("versions.json")) cpSync("versions.json", path.join(PLUGIN_DIR, "versions.json"));
  cpSync(path.join(process.cwd(), "python"), path.join(PLUGIN_DIR, "python"), {
    recursive: true,
    filter: (p) => !p.includes("__pycache__"),
  });
  console.log(`✅ 已部署到 ${PLUGIN_DIR}`);
}

if (process.argv[2] === "sync-python") {
  syncPython();
} else {
  console.log("▶ esbuild 构建…");
  execSync("node esbuild.config.mjs production", { stdio: "inherit" });
  syncPython();
  deploy();
  console.log("✅ 构建+部署完成（重启 Obsidian 或使用 BRAT 热加载）");
}
