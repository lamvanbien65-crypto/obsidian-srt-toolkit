#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  视频转录：音视频 → 逐字稿 + 字幕 + 时间戳（Whisper × FFmpeg）
#
#  流程：
#    · 查找视频：按文件名在 vault 内搜索（可不带扩展名；也支持绝对路径；
#      多个匹配时自动优先取离 vault 根目录最近的，如「whisper 视频转录/」下的素材）
#    · ffmpeg 提取音频（16kHz 单声道 wav，临时文件用后即删）
#    · whisper-cli（whisper.cpp · large-v3-turbo · Apple Metal 加速）转写
#    · 输出三件套（与视频同目录同名，重复执行自动覆盖重转）：
#        .txt   逐字稿（纯文字）
#        .srt   字幕（带时间戳，播放器/剪辑软件可直接用）
#        .json  结构化时间戳（笔记锚点、片段剪辑等二次利用）
#
#  热词纠错（建议）：--prompt 注入专有名词，大幅减少同音字错误
#    例：python3 视频转录.py 视频.mp4 --prompt "方三文 段永平 雪球 茅台"
#
#  用法：
#    python3 视频转录.py 义乌跨境电商商机考察
#    python3 视频转录.py "义乌跨境电商商机考察.mp4"
#    python3 视频转录.py "【P68】方三文对话段永平：做自己能够喜欢的事情很重要正"
#    python3 视频转录.py /绝对/路径/视频.mp4 --prompt "热词"
#    python3 视频转录.py 视频.mp4 --lang en     # 默认 zh
#    python3 视频转录.py 视频1.mp4 视频2.mp4    # 批量
# ============================================================
import os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT_ROOT") or Path(__file__).resolve().parent.parent.parent)
MODEL_DEFAULT = Path(os.environ.get("OBSIDIAN_WHISPER_MODEL") or Path.home() / ".cache" / "whisper" / "ggml-large-v3-turbo.bin")
WHISPER_BIN = shutil.which("whisper-cli")
FFMPEG_BIN  = shutil.which("ffmpeg")
FFPROBE_BIN = shutil.which("ffprobe")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".flv",
              ".wmv", ".m2ts", ".mts", ".ts", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".m4b"}


def find_video(name):
    """按名字在 vault 内找音视频；name 可直接是路径（扩展名可省略）"""
    p = Path(name)
    if p.is_file():
        return p
    if p.suffix.lower() not in (VIDEO_EXTS | AUDIO_EXTS):
        for ext in sorted(VIDEO_EXTS | AUDIO_EXTS):
            if p.with_suffix(ext).is_file():
                return p.with_suffix(ext)
    if p.is_absolute():
        print(f"✗ 文件不存在：{name}")
        sys.exit(1)
    # vault 内模糊搜索（文件名含关键字即可命中）
    key = p.stem.lower().replace("#", "")   # 忽略 #（Obsidian 锚点字符，改名后搜索仍可命中）
    matches = []
    for root, dirs, files in os.walk(VAULT_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            ext = Path(f).suffix.lower()
            if ext not in (VIDEO_EXTS | AUDIO_EXTS):
                continue
            stem = Path(f).stem.lower().replace("#", "")
            if stem.endswith("-字幕") or stem.endswith("-字幕轨"):
                continue   # 派生产物（烧录/内挂字幕），不作为源视频候选
            if stem == key or stem.startswith(key) or key in stem:
                matches.append(Path(root) / f)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # 多个匹配时优先取离 vault 根目录最近的（如「whisper 视频转录/」下的素材）
        matches.sort(key=lambda m: len(m.relative_to(VAULT_ROOT).parts))
        if len(matches) > 1 and len(matches[0].relative_to(VAULT_ROOT).parts) < len(matches[1].relative_to(VAULT_ROOT).parts):
            print(f"· 多个匹配，自动选用最近的：{matches[0]}")
            return matches[0]
        print(f"✗ 找到多个匹配，请写得更具体：")
        for m in matches:
            print(f"   {m}")
        sys.exit(1)
    # 视频文件可能已被删除（剪藏完成省空间）：找同名 .json 转录文件兜底，可直接重跑剪藏
    for root, dirs, files in os.walk(VAULT_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if Path(f).suffix.lower() != ".json":
                continue
            stem = Path(f).stem.lower().replace("#", "")
            if stem.endswith("-字幕") or stem.endswith("-字幕轨"):
                continue
            if stem == key or stem.startswith(key) or key in stem:
                print(f"▶ 视频文件不存在，但找到同名转录 {Path(root) / f}，直接复用（无需视频）")
                return Path(root) / f
    print(f"✗ 在 vault 里没找到「{name}」，vault 内的音视频有：")
    shown = 0
    for root, dirs, files in os.walk(VAULT_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if Path(f).suffix.lower() in (VIDEO_EXTS | AUDIO_EXTS):
                print(f"   {Path(root) / f}")
                shown += 1
                if shown >= 15:
                    print("   …（更多省略）")
                    sys.exit(1)
    sys.exit(1)


def duration_of(video):
    """ffprobe 读取时长（秒）"""
    out = subprocess.run([FFPROBE_BIN, "-v", "error",
                          "-show_entries", "format=duration",
                          "-of", "default=noprint_wrappers=1:nokey=1",
                          str(video)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def fmt_seconds(s):
    if s is None:
        return "?"
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}小时{m}分{sec}秒" if h else f"{m}分{sec}秒"


def transcribe(video, lang="zh", prompt=None, model=MODEL_DEFAULT):
    video = Path(video)
    try:
        import progress
        progress.emit("stage", stage="transcribe", label="▶ whisper 转写中…")
    except ImportError:
        pass
    print(f"▶ 视频：{video.name}")
    dur = duration_of(video)
    if dur:
        print(f"  时长：{fmt_seconds(dur)}（{dur:.0f} 秒）")

    if not WHISPER_BIN:
        print("✗ 未找到 whisper-cli，请先安装：brew install whisper-cpp")
        try:
            import progress
            progress.emit_error("whisper-missing", "未找到 whisper-cli，请先安装：brew install whisper-cpp")
        except ImportError:
            pass
        sys.exit(1)
    if not FFMPEG_BIN or not FFPROBE_BIN:
        print("✗ 未找到 ffmpeg，请先安装：brew install ffmpeg")
        try:
            import progress
            progress.emit_error("ffmpeg-missing", "未找到 ffmpeg，请先安装：brew install ffmpeg")
        except ImportError:
            pass
        sys.exit(1)
    if not model.is_file():
        print(f"✗ 模型文件不存在：{model}")
        print("  下载：curl -L -C - -o ~/.cache/whisper/ggml-large-v3-turbo.bin \\")
        print("    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin")
        try:
            import progress
            progress.emit_error("model-missing", f"whisper 模型文件不存在：{model}")
        except ImportError:
            pass
        sys.exit(1)

    # 提取音频（16kHz 单声道）
    tmp_wav = Path(tempfile.gettempdir()) / f"视频转录_{os.getpid()}.wav"
    print("  提取音频（16kHz 单声道）…")
    r = subprocess.run([FFMPEG_BIN, "-y", "-v", "error", "-i", str(video),
                        "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(tmp_wav)])
    if r.returncode != 0:
        print("✗ 音频提取失败（视频可能没有音轨）")
        sys.exit(1)

    # 转写
    prefix = video.with_suffix("")
    cmd = [WHISPER_BIN, "-m", str(model), "-l", lang, "-f", str(tmp_wav),
           "-otxt", "-osrt", "-oj", "-of", str(prefix)]
    if prompt:
        cmd += ["--prompt", prompt]
    print("  转写中（whisper large-v3-turbo）…")
    t0 = time.time()
    r = subprocess.run(cmd)
    cost = time.time() - t0

    tmp_wav.unlink(missing_ok=True)
    if r.returncode != 0:
        print("✗ 转写失败（whisper-cli 退出码非 0）")
        try:
            import progress
            progress.emit_error("transcribe-fail", "whisper 转写失败（whisper-cli 退出码非 0）")
        except ImportError:
            pass
        sys.exit(1)

    print(f"\n✅ 完成：{video.name}")
    for ext in (".txt", ".srt", ".json"):
        f = Path(str(prefix) + ext)
        size = f.stat().st_size / 1024
        print(f"  · {f.name}（{size:.0f} KB）")
    if dur:
        print(f"  转写耗时 {cost:.0f} 秒（约 {dur / cost:.0f} 倍速）")

    # 结果上报（插件壳自动打开/展示产物）
    try:
        import progress

        def _rel(p):
            try:
                return str(p.resolve().relative_to(VAULT_ROOT.resolve()))
            except ValueError:
                return str(p)

        progress.emit_result([
            {"type": "json", "path": _rel(Path(str(prefix) + ".json"))},
            {"type": "srt", "path": _rel(Path(str(prefix) + ".srt"))},
            {"type": "txt", "path": _rel(Path(str(prefix) + ".txt"))},
        ])
    except ImportError:
        pass


def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)
    lang, prompt, files = "zh", None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--lang" and i + 1 < len(args):
            i += 1
            lang = args[i]
        elif a == "--prompt" and i + 1 < len(args):
            i += 1
            prompt = args[i]
        elif a.startswith("--lang="):
            lang = a.split("=", 1)[1]
        elif a.startswith("--prompt="):
            prompt = a.split("=", 1)[1]
        else:
            files.append(a)
        i += 1
    if not files:
        print("✗ 请给出视频文件名，如：python3 视频转录.py 义乌跨境电商商机考察")
        sys.exit(1)
    for name in files:
        transcribe(find_video(name), lang=lang, prompt=prompt)


if __name__ == "__main__":
    main()
