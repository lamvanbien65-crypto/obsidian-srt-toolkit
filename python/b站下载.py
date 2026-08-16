#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  B站视频下载：把 B站视频下载到本地（yt-dlp 封装）
#
#  支持三种输入：
#    · 视频链接：https://www.bilibili.com/video/BV1jCpizuEPg/?spm_id_from=...
#    · 分享短链：https://b23.tv/xxxxxx
#    · 「稍后再看」等列表链接：自动提取 bvid，只下目标视频，不会把整个列表下下来
#    · 裸 BV 号：BV1jCpizuEPg
#
#  下载参数：
#    · 默认 1080P（--quality 1080 / 720 / best=最高画质）
#    · 默认保存到 vault 的「1,whisper 视频转录/Edit&Translate（开发过程）/」
#      （--dir 可改），文件名用视频标题
#    · 会员画质/60帧 需登录：--cookies-from-browser safari（或 chrome）
#
#  用法：
#    python3 b站下载.py BV1jVEm6yED6
#    python3 b站下载.py "https://www.bilibili.com/video/BVxxxx/?spm_id_from=..."
#    python3 b站下载.py "https://b23.tv/xxxx"
#    python3 b站下载.py "https://www.bilibili.com/list/watchlater?bvid=BVxxxx..."
#    python3 b站下载.py BV号 --quality best --dir /自定义/目录
#    python3 b站下载.py BV号 --dry-run        # 只显示视频信息不下载
# ============================================================
import argparse, os, re, subprocess, sys
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT_ROOT") or Path(__file__).resolve().parent.parent.parent)
DEFAULT_DIR = VAULT_ROOT / "1,whisper 视频转录" / "Edit&Translate（开发过程）"
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")


def extract_bvid(text):
    m = BV_RE.search(text)
    return m.group(0) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="B站视频链接 / 分享短链 / 稍后再看链接 / 裸BV号")
    ap.add_argument("--quality", default="1080", help="画质：1080（默认）/720/best")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="下载目录")
    ap.add_argument("--cookies-from-browser", help="如 safari/chrome（会员画质需要登录）")
    ap.add_argument("--cookies-file", help="Netscape 格式 cookie 文件路径（优先于 --cookies-from-browser）")
    ap.add_argument("--audio-only", action="store_true", help="仅下载音频（mp3，字幕场景省空间）")
    ap.add_argument("--dry-run", action="store_true", help="只显示视频信息不下载")
    args = ap.parse_args()

    bvid = extract_bvid(args.input)
    if not bvid:
        print(f"✗ 没找到 BV 号：{args.input}")
        sys.exit(1)
    # 统一转成标准视频链接（列表链接只取目标 bvid，不会下载整个列表）
    url = f"https://www.bilibili.com/video/{bvid}"

    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先看视频信息
    info_cmd = ["yt-dlp", "--skip-download",
                "--print", "标题: %(title)s",
                "--print", "时长: %(duration_string)s",
                "--print", "UP主: %(uploader)s", url]
    if args.cookies_file:
        info_cmd += ["--cookies", args.cookies_file]
    elif args.cookies_from_browser:
        info_cmd += ["--cookies-from-browser", args.cookies_from_browser]
    r = subprocess.run(info_cmd, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print("✗ 获取视频信息失败：", r.stderr.strip()[-300:])
        sys.exit(1)
    if args.dry_run:
        return

    # 画质选择（audio-only 时只取音频轨）
    if args.audio_only:
        fmt = "ba/b"
        out_tpl = "%(title)s.%(ext)s"
        merge = None
    else:
        fmt = "bv*+ba/b" if args.quality == "best" else f"bv*[height<={args.quality}]+ba/b"
        out_tpl = "%(title)s.%(ext)s"
        merge = "mp4"
    cmd = ["yt-dlp", "-f", fmt, "-o", out_tpl,
           "--no-playlist", "-N", "8", "--retries", "5", url]   # 并发分片：防 B站 CDN 单连接限速
    if merge:
        cmd += ["--merge-output-format", merge]
    if args.cookies_file:
        cmd += ["--cookies", args.cookies_file]
    elif args.cookies_from_browser:
        cmd += ["--cookies-from-browser", args.cookies_from_browser]
    print(f"▶ 下载中（{args.quality}）→ {out_dir}")
    r = subprocess.run(cmd, cwd=str(out_dir))
    if r.returncode != 0:
        print("✗ 下载失败。若因会员画质受限，可加 --cookies-from-browser safari 重试")
        sys.exit(1)
    print("✅ 下载完成")


if __name__ == "__main__":
    main()
