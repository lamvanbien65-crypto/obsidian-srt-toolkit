#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  B站一键全流程编排器：下载 →（字幕抓取 / whisper 转写）→ 剪藏 → 出笔记
#
#  流程：
#    resolve(BV提取) → view API(标题/时长/cid)
#      → probe 字幕（b站字幕.py 逻辑）
#      ├─ 有字幕：下载（完整视频/音频）→ 抓字幕三件套
#      └─ 无字幕：下载 → 视频转录.py（whisper）
#      → 按 --mode 剪藏：
#          standard      → 标准字幕剪藏.py 视频名
#          dialogue      → 字幕说话人标记.py 视频名 --speakers A,B --merge
#          en-interview  → 字幕说话人标记.py 视频名 --speakers A,B --lang en --merge --translate
#          download-only → 只下载
#    → result 事件（笔记+视频相对路径）
#
#  所有子步骤通过 importlib 内嵌调用（与现有脚本互嵌模式一致），
#  进度经 progress.emit 上报给 TS 壳（OBSIDIAN_JSON_PROGRESS=1 时启用）。
#
#  用法：
#    python3 b站全流程.py "<链接/BV>" --mode standard|dialogue|en-interview|download-only
#      [--speakers "A,B"] [--quality 1080] [--prefer-sub auto|cc|ai]
#      [--audio-only] [--dir ...] [--out-dir ...] [--prompt 热词]
# ============================================================
import argparse, importlib.util, json, os, re, subprocess, sys
from pathlib import Path

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
HERE = Path(__file__).resolve().parent
VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT_ROOT") or HERE.parent.parent.parent)
# 统一注入给所有子脚本（find_video/输出目录探测用同一 vault 根）
os.environ.setdefault("OBSIDIAN_VAULT_ROOT", str(VAULT_ROOT))
OUT_BASE = VAULT_ROOT / "B站剪藏" / "笔记"   # 通用默认笔记目录（发布版；--out-dir 优先）


def load(name, file):
    """importlib 加载同目录脚本模块"""
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(script_name, argv):
    """以 CLI 方式运行子脚本（复用其 argparse main），异常静默处理"""
    sys.argv = [script_name] + argv
    mod = load(script_name.rstrip(".py"), script_name)
    try:
        mod.main()
    except SystemExit:
        pass
    return mod


def progress_emit(event, **payload):
    try:
        import progress
        progress.emit(event, **payload)
    except Exception:
        pass


def extract_bvid(text):
    m = BV_RE.search(text)
    return m.group(0) if m else None


MEDIA_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".flac", ".wav", ".aac")


def _clean_stem(title):
    return title.replace("/", "／").replace("#", "")


def find_downloaded(out_dir, title):
    """定位下载产物（标题匹配，多扩展名）"""
    stem = _clean_stem(title)
    exact = [c for c in out_dir.iterdir() if c.stem == stem and c.suffix.lower() in MEDIA_EXTS]
    if exact:
        return exact[0]
    for c in out_dir.iterdir():
        if c.suffix.lower() in MEDIA_EXTS and (c.stem[:12] in stem or stem[:12] in c.stem):
            return c
    return None


def find_existing(out_dir, title):
    """下载前检查：已有文件（stem 去 # 后 == 目标标题）则跳过 yt-dlp"""
    stem = _clean_stem(title)
    for c in out_dir.iterdir():
        if c.suffix.lower() in MEDIA_EXTS and c.stem.replace("#", "") == stem:
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="B站链接 / 分享短链 / 裸BV号")
    ap.add_argument("--mode", default="standard",
                    choices=["standard", "dialogue", "en-interview", "download-only"])
    ap.add_argument("--speakers", help="对话双方，逗号分隔（dialogue/en-interview 需要）")
    ap.add_argument("--quality", default="1080", help="画质：1080/720/best")
    ap.add_argument("--prefer-sub", default="auto", choices=["auto", "cc", "ai"])
    ap.add_argument("--audio-only", action="store_true", help="有字幕时仅下载音频")
    ap.add_argument("--dir", help="下载目录（默认脚本规范）")
    ap.add_argument("--out-dir", help="笔记输出目录（默认脚本规范）")
    ap.add_argument("--cookies-from-browser", help="浏览器 cookie（如 safari/chrome），会员画质/登录字幕需要")
    ap.add_argument("--prompt", help="whisper 热词（无字幕转写时）")
    args = ap.parse_args()

    bvid = extract_bvid(args.input)
    if not bvid:
        progress_emit("error", code="bad-input", text=f"没找到 BV 号：{args.input}")
        print(f"✗ 没找到 BV 号：{args.input}")
        sys.exit(1)
    url = f"https://www.bilibili.com/video/{bvid}"

    # ---- 1. 视频信息 + 字幕探测 ----
    progress_emit("stage", stage="probe", label="▶ 探测视频与字幕…")
    bsub = load("b站字幕", "b站字幕.py")
    try:
        info = bsub.view_info(bvid)
        subs, need_login = bsub.subtitle_list(bvid, info["cid"])
    except Exception as e:
        progress_emit("error", code="api-fail", text=f"B站 API 失败：{e}")
        print(f"✗ B站 API 失败：{e}")
        sys.exit(1)
    title = info["title"]
    print(f"▶ {title}（时长 {info['duration']}s）")

    target, warn = bsub.choose_subtitle(subs, args.prefer_sub)
    use_sub = target is not None and warn != "ai-generating"
    if warn == "ai-generating":
        print("  · AI 字幕生成中，回退 whisper 转写")
    if use_sub and need_login and not os.environ.get("BILI_COOKIE"):
        print("  · 字幕需要登录态，回退 whisper 转写（设置页填 cookie 后可抓取）")
        use_sub = False
    print(f"  · 字幕：{'抓取 ' + target['lan'] if use_sub else '无（回退 whisper 转写）'}")

    # ---- 2. 下载（已有文件则跳过 yt-dlp，避免重复下载）----
    progress_emit("stage", stage="download", label="▶ 下载中…")
    bdl = load("b站下载", "b站下载.py")
    out_dir = Path(args.dir) if args.dir else bdl.DEFAULT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = find_existing(out_dir, title)
    if existing is not None:
        print(f"  · 已下载 {existing.name}，跳过 yt-dlp 直接复用")
    else:
        fmt = "ba/b" if (args.audio_only and use_sub) else (
            "bv*+ba/b" if args.quality == "best" else f"bv*[height<={args.quality}]+ba/b")
        cmd = ["yt-dlp", "-f", fmt, "-o", "%(title)s.%(ext)s", "--no-playlist",
               "-N", "8", "--retries", "5", url]
        if not (args.audio_only and use_sub):
            cmd += ["--merge-output-format", "mp4"]
        if args.cookies_from_browser:
            cmd += ["--cookies-from-browser", args.cookies_from_browser]
        cookie_path = None
        if os.environ.get("BILI_COOKIE"):
            # 一次性 cookies.txt（0600）给 yt-dlp：会员画质/登录字幕；任务结束删除
            import tempfile
            fd, cookie_path = tempfile.mkstemp(suffix=".txt", prefix="srt-cookies-")
            os.close(fd)
            Path(cookie_path).write_text(bili_cookie_to_netscape(os.environ["BILI_COOKIE"]),
                                         encoding="utf-8")
            os.chmod(cookie_path, 0o600)
            cmd += ["--cookies", cookie_path]
        try:
            r = subprocess.run(cmd, cwd=str(out_dir))
        finally:
            if cookie_path:
                try:
                    Path(cookie_path).unlink(missing_ok=True)
                except OSError:
                    pass
        if r.returncode != 0:
            progress_emit("error", code="download-fail", text="下载失败（检查画质/cookie/yt-dlp）")
            sys.exit(1)
        print(f"✅ 下载完成 → {out_dir}")

    video_path = find_downloaded(out_dir, title)
    if video_path is None:
        progress_emit("error", code="no-video-found", text="下载完成但找不到视频文件")
        sys.exit(1)

    # ---- 2.4 文件名去 #（Obsidian 锚点冲突：![[x#y]] 会把 # 后当锚点，嵌入失效）----
    # 连带改名同 stem 的转录产物（.json/.srt/.txt），保证下游全链路 #-free
    if "#" in video_path.name:
        new_video = video_path.with_name(video_path.name.replace("#", ""))
        for ext in (".json", ".srt", ".txt"):
            old = video_path.with_suffix(ext)
            if old.is_file():
                old.rename(new_video.with_suffix(ext))
        video_path.rename(new_video)
        video_path = new_video
        print(f"  · 文件名含 #（Obsidian 锚点冲突），已去 # 改名：{video_path.name}")

    # ---- 2.5 仅下载：直接出结果（不转写、不剪藏）----
    if args.mode == "download-only":
        try:
            rel = video_path.relative_to(VAULT_ROOT)
        except ValueError:
            rel = video_path
        progress_emit("result", outputs=[{"type": "video", "path": str(rel)}])
        print(f"✅ 下载完成：{video_path.name}")
        sys.exit(0)

    # ---- 3. 字幕（抓取 or whisper 转写）----
    json_path = video_path.with_suffix(".json")
    if use_sub:
        progress_emit("stage", stage="subtitle", label="▶ 抓取字幕…")
        try:
            raw = bsub.download_subtitle_json(target)
            segs = bsub.to_transcription(raw.get("body", []))
        except Exception as e:
            print(f"  · 字幕抓取失败（{e}），回退 whisper")
            use_sub = False
        if use_sub and segs:
            json_path.write_text(json.dumps({"transcription": segs}, ensure_ascii=False),
                                 encoding="utf-8")
            bsub.write_srt(segs, video_path.with_suffix(".srt"))
            bsub.write_txt(segs, video_path.with_suffix(".txt"))
            print(f"✅ 字幕抓取：{target['lan']}（{len(segs)} 条）→ {json_path.name}.json/.srt/.txt")
    if not use_sub:
        if json_path.is_file():
            print(f"  · 已有转录 {json_path.name}，跳过 whisper 直接复用")
        else:
            progress_emit("stage", stage="transcribe", label="▶ whisper 转写中…（首次需加载模型）")
            vt = load("视频转录", "视频转录.py")
            try:
                vt.transcribe(video_path, lang="zh", prompt=args.prompt)
            except SystemExit:
                progress_emit("error", code="transcribe-fail", text="whisper 转写失败")
                sys.exit(1)

    # ---- 4. 剪藏 ----
    name_arg = str(video_path)   # 绝对路径：find_video 直接命中（无需在 vault 内搜名）
    if args.mode == "standard":
        progress_emit("stage", stage="clip", label="▶ 标准字幕剪藏…")
        clip_argv = [name_arg, "--no-drop"]   # 零云模式：跳过 LLM 剔除其他声音，纯本地断句
        if args.out_dir:
            clip_argv += ["--out-dir", args.out_dir]
        run_cli("标准字幕剪藏.py", clip_argv)
    else:  # dialogue / en-interview
        progress_emit("stage", stage="label", label="▶ LLM 说话人标注+合并…")
        speakers = args.speakers or "A,B"
        argv = [name_arg, "--speakers", speakers, "--merge"]
        if args.out_dir:
            argv += ["--out", str(Path(args.out_dir) / (video_path.stem + ".md"))]
        if args.mode == "en-interview":
            argv += ["--lang", "en", "--translate"]
        run_cli("字幕说话人标记.py", argv)

    # ---- 5. 结果上报 ----
    note = None
    stem_norm = video_path.stem.replace("#", "").rstrip("正")
    # 优先规范输出目录（Output&Generated），兜底 json 同目录（自定义下载目录在 vault 外时）
    for base in ([Path(args.out_dir)] if args.out_dir else [OUT_BASE, json_path.parent]):
        if not base.is_dir():
            continue
        cands = [c for c in base.iterdir()
                 if c.suffix == ".md" and c.stem.replace("#", "").startswith(stem_norm)]
        cands.sort(key=lambda c: "#" in c.name)   # #-free 新产物优先，旧 # 名兜底
        if cands:
            note = cands[0]
            break
    outputs = []
    if note:
        try:
            outputs.append({"type": "note", "path": str(note.relative_to(VAULT_ROOT))})
        except ValueError:
            outputs.append({"type": "note", "path": str(note)})
    try:
        outputs.append({"type": "video", "path": str(video_path.relative_to(VAULT_ROOT))})
    except ValueError:
        outputs.append({"type": "video", "path": str(video_path)})
    progress_emit("result", outputs=outputs)
    print(f"✅ 完成：{note.name if note else '（无笔记产出）'}")
    sys.exit(0)


def bili_cookie_to_netscape(cookie_str):
    """B站 cookie 字符串 → Netscape cookies.txt（yt-dlp --cookies 用）"""
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        k, v = k.strip(), v.strip()
        if not k:
            continue
        lines.append(f".bilibili.com\tTRUE\t/\tTRUE\t2147483647\t{k}\t{v}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
