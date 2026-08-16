#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  标准字幕剪藏：个人独白视频 → 按标准断句的完整字幕笔记
#
#  适用：单人独白类视频（口播/解说/演讲/课程），无需说话人标注，
#        按时间戳把字幕完整列出，关键是「断句标准」——不长不短、可读性好
#
#  【自动剔除其他声音】默认开启（--no-drop 关闭）：
#    · 独白类视频常混入主持人/提问/应和/环境音，LLM 逐句判定
#      0=主体独白(保留) / 1=其他声音(删除) / 2=不确定(保留)
#    · 判定结果缓存 {json}.dropothers，重跑免调 LLM
#    · 剔除后相邻保留段若原先后还隔了被删内容，不再强行拼接
#
#  断句标准（可调）：
#    · 字数：全角字符 = 1 字，半角（字母/数字） = 0.5 字（与「字幕单行显示」一致）
#    · 目标 25 字/条（--target），最短 10 字（--min），最长 40 字（--max）
#    · 过短：相邻不足最短字数的段自动合并，用「，」连接，取 [起→止] 时间
#    · 过长：优先在标点（。！？；，、：…）后断开，取最接近目标字数处；
#      无合适标点则按字数硬切；时间戳按字数比例在段内分配
#
#  输出：笔记默认进 json 同目录；若在「1,whisper 视频转录/」下则进
#        「Output&Generated(生成结果)/」（--out-dir 可改）
#
#  流程：未转录自动转录（whisper）→ 剔除其他声音 → 标准断句 → 输出笔记
#
#  用法：
#    python3 标准字幕剪藏.py 义乌跨境电商商机考察
#    python3 标准字幕剪藏.py 视频名 --no-drop           # 不剔除其他声音
#    python3 标准字幕剪藏.py 视频名 --target 30 --min 12 --max 45
#    python3 标准字幕剪藏.py 已转录.json                # 已有转录文件则直接处理
#    python3 标准字幕剪藏.py 视频名 --prompt "热词"     # 热词传给转录步骤
# ============================================================
import argparse, concurrent.futures, json, os, re, sys
from pathlib import Path
import llm   # 统一 LLM 调用层（同目录）：claude CLI 优先，无则 HTTP 直连 DeepSeek

PUNCTS = "。！？；，、：…"          # 断句标点（在该标点之后断开）
TARGET_W = 25.0                    # 目标段长（全角字）
MIN_W = 10.0                       # 最短段长
MAX_W = 40.0                       # 最长段长

DROP_CHUNK = 120                   # 剔除判定：每块行数
DROP_RETRY = 4                     # 判定失败重试次数
MODEL = os.environ.get("OBSIDIAN_LLM_MODEL") or "haiku"   # claude CLI 档位：haiku=deepseek-v4-flash

VAULT_ROOT = Path(os.environ.get("OBSIDIAN_VAULT_ROOT") or Path(__file__).resolve().parent.parent.parent)


def width(s):
    """全角字符算 1 字，半角（字母/数字/半角符号）算 0.5 字"""
    return sum(1.0 if ord(ch) > 0xFF else 0.5 for ch in s)


def fmt(ms):
    ms = max(0, int(ms))
    h, rem = divmod(ms // 1000, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def note_dir_for(json_path):
    """输出规范：json 在「1,whisper 视频转录/」或其子目录下时，
    笔记进其 Output&Generated(生成结果)/"""
    ws = json_path.parent
    base = ws if ws.name == "1,whisper 视频转录" else \
        (ws.parent if ws.parent.name == "1,whisper 视频转录" else None)
    if base is not None:
        d = base / "Output&Generated(生成结果)"
        d.mkdir(exist_ok=True)
        return d
    return ws


# ---------- 剔除其他声音（LLM 判定） ----------

def build_drop_prompt(title, chunk):
    lines = [f"[{i}] [{fmt(s['offsets']['from'])}] {s['text'].strip()}"
             for i, s in enumerate(chunk)]
    return (
        f"你是字幕筛选器。这是视频「{title}」的转写，主体是演讲者/讲述者的独白。\n"
        f"标出每一行：0=主体独白（保留），1=其他声音（主持人、提问、应和、"
        f"环境音、非主体说话等，应删除），2=不确定（保留）。\n"
        f"行格式：[行号] [时间] 内容\n"
        f"只输出一个 JSON 数组（如 [0,0,1,...]），数量必须与行数完全一致，"
        f"连续同行也必须逐句输出，禁止合并省略。不要输出任何其他文字。\n\n"
        + "\n".join(lines)
    )


def call_claude(prompt):
    """调用 LLM 返回 0/1/2 判定列表或 None（Ollama 本地 / claude CLI / DeepSeek 云）"""
    arr = llm.call_json(prompt, model=MODEL)
    return llm.coerce_labels(arr)


def label_chunk(title, chunk):
    """单块判定（含重试）；仍失败则对半拆分递归；最终失败返回 None"""
    for _ in range(DROP_RETRY + 1):
        labels = call_claude(build_drop_prompt(title, chunk))
        if labels and len(labels) == len(chunk) and all(x in (0, 1, 2) for x in labels):
            return labels
    if len(chunk) >= 20:
        mid = len(chunk) // 2
        left = label_chunk(title, chunk[:mid])
        right = label_chunk(title, chunk[mid:])
        if left is not None and right is not None:
            return left + right
    return None


def drop_other_voices(segs, json_path, workers=8, force=False):
    """LLM 判定并剔除其他声音；返回 (保留段, 剔除数)。结果缓存 {json}.dropothers"""
    cache = json_path.with_suffix(json_path.suffix + ".dropothers")
    labels = None
    if cache and cache.is_file() and not force:
        try:
            cached = json.load(open(cache, encoding="utf-8"))
            if len(cached.get("labels", [])) == len(segs):
                labels = cached["labels"]
                print(f"▶ 复用其他声音判定缓存（{len(labels)} 条，免调 LLM）")
        except (ValueError, json.JSONDecodeError):
            labels = None
    if labels is None:
        workers = llm.suggest_workers(workers)   # CLI=原值 / 云=≤4 / Ollama=≤2
        chunk_n = llm.suggest_chunk(DROP_CHUNK, "label")   # Ollama 本地上下文有限，判定块≤40 行
        chunks = [segs[i:i + chunk_n] for i in range(0, len(segs), chunk_n)]
        print(f"▶ LLM 判定其他声音：{len(segs)} 条，{len(chunks)} 块，并行 {workers}")
        labels = [None] * len(segs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(label_chunk, str(json_path.stem), c): idx
                    for idx, c in enumerate(chunks)}
            for n_done, f in enumerate(concurrent.futures.as_completed(futs), 1):
                idx = futs[f]
                res = f.result()
                if res is None:
                    print(f"✗ 第 {idx + 1} 块判定失败，重跑即可（结果已缓存于断点前）")
                    try:
                        import progress
                        if not llm.CLAUDE_BIN and not llm.get_api_key():
                            progress.emit_error("llm-no-key",
                                                "未找到 LLM API Key：设置页填 DeepSeek Key，"
                                                "或安装 claude CLI 后在 ~/.claude/settings.json 配 env")
                        else:
                            progress.emit_error("llm-fail", f"第 {idx + 1} 块其他声音判定失败（已重试），重跑即可续传")
                    except ImportError:
                        pass
                    sys.exit(1)
                start = idx * chunk_n
                labels[start:start + len(res)] = res
                try:
                    import progress
                    progress.emit("progress", stage="drop", done=n_done, total=len(chunks))
                except ImportError:
                    pass
        assert None not in labels, "判定不完整"
        if cache:
            cache.write_text(json.dumps({"labels": labels}, ensure_ascii=False),
                             encoding="utf-8")
    kept, dropped = [], 0
    for i, (s, lab) in enumerate(zip(segs, labels)):
        if lab == 1:
            dropped += 1
        else:
            s = dict(s)
            s["idx"] = i
            kept.append(s)
    print(f"  剔除其他声音 {dropped} 条，保留 {len(kept)} 条")
    return kept, dropped


# ---------- 标准断句 ----------

def merge_short(segs, min_w, target_w, max_w):
    """相邻段合并：不足 min_w 必须合并；不足 target_w 且并入下条不超 max_w 也合并。
    用「，」连接，取 [起→止] 时间；带 idx 时仅相邻原文（中间无被删段）才合并"""
    out = []
    for s in segs:
        text = s["text"].strip()
        if not text:
            continue
        if out:
            acc = width(out[-1]["text"])
            adj = True
            if "idx" in out[-1] and "idx" in s:
                adj = (out[-1]["idx"] + 1 == s["idx"])
            if adj and (acc < min_w or (acc < target_w and acc + width(text) <= max_w)):
                prev = out[-1]
                prev["text"] = prev["text"] + "，" + text
                prev["to"] = s["offsets"]["to"]
                prev["idx"] = s.get("idx", prev["idx"])   # idx 更新为最后并入段的原始下标
                continue
        out.append({"from": s["offsets"]["from"], "to": s["offsets"]["to"], "text": text,
                    "idx": s.get("idx", -1)})
    return out


def split_piece(piece, min_w, target_w, max_w):
    """把超长片段按标点断句；返回各部分列表"""
    parts = []
    while width(piece) > max_w:
        best = None                     # (断点下标, 与目标的差距)
        for i, ch in enumerate(piece):
            if ch in PUNCTS:
                wl, wr = width(piece[:i + 1]), width(piece[i + 1:])
                if wl < min_w or wr < min_w:
                    continue
                if wl > max_w:
                    break               # 越往后左段越长，后续标点都不可能满足
                score = abs(wl - target_w)
                if best is None or score < best[1]:
                    best = (i, score)
        if best is None:
            # 无合适标点：按字数硬切（保证后半段 ≥ min_w；尾部不足则整段保留）
            acc, idx = 0.0, None
            for j, ch in enumerate(piece):
                acc += width(ch)
                if acc >= max_w and width(piece[j + 1:]) >= min_w:
                    idx = j + 1
                    break
            if idx is None:
                break                   # 尾部不足，整体保留（循环后的 parts.append(piece) 收尾，勿重复 append）
            parts.append(piece[:idx])
            piece = piece[idx:]
        else:
            parts.append(piece[:best[0] + 1])
            piece = piece[best[0] + 1:]
    parts.append(piece)
    # 尾部过短并入上一段（上限 1.3 倍最长）
    while len(parts) > 1 and width(parts[-1]) < min_w and \
            width(parts[-2]) + width(parts[-1]) <= max_w * 1.3:
        parts[-2] += parts[-1]
        parts.pop()
    return parts


def resegment(segs, target_w=TARGET_W, min_w=MIN_W, max_w=MAX_W):
    """whisper 分段 → 标准断句（合并过短 + 拆分过长），返回新字幕列表"""
    merged = merge_short(segs, min_w, target_w, max_w)
    out = []
    for s in merged:
        w = width(s["text"])
        if w <= max_w:
            out.append(s)
            continue
        pieces = split_piece(s["text"], min_w, target_w, max_w)
        dur = s["to"] - s["from"]
        total_w = width(s["text"])
        cur = s["from"]
        for k, p in enumerate(pieces):
            nxt = s["to"] if k == len(pieces) - 1 else \
                s["from"] + round(dur * width(p) / total_w)
            out.append({"from": cur, "to": max(nxt, cur + 1), "text": p})
            cur = nxt
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="视频名 或 已转录的 .json 文件")
    ap.add_argument("--target", dest="target_w", type=float,
                    default=float(os.environ.get("OBSIDIAN_BREAK_TARGET") or TARGET_W),
                    help=f"目标段长（默认 {TARGET_W:.0f} 字）")
    ap.add_argument("--min", dest="min_w", type=float, default=MIN_W, help=f"最短段长（默认 {MIN_W:.0f} 字）")
    ap.add_argument("--max", dest="max_w", type=float, default=MAX_W, help=f"最长段长（默认 {MAX_W:.0f} 字）")
    ap.add_argument("--prompt", help="未转录时自动转录的 whisper 热词")
    ap.add_argument("--no-drop", action="store_true", help="不剔除其他声音（保留全部字幕）")
    ap.add_argument("--drop-force", action="store_true", help="忽略其他声音判定缓存，重新判定")
    ap.add_argument("--workers", type=int, default=8, help="判定并行块数")
    ap.add_argument("--out-dir", help="输出笔记目录（默认按规范自动选择）")
    ap.add_argument("--dry-run", action="store_true", help="只统计断句结果，不写笔记")
    args = ap.parse_args()

    json_path = Path(args.target)
    if json_path.is_file() and json_path.suffix.lower() != ".json":
        json_path = json_path.with_suffix(".json")   # 传的是视频路径：换同名 .json
    if not json_path.is_file():
        # 传的是视频名：先在 vault 里定位视频，再按视频路径找同名 .json（已转录则跳过转录）
        import importlib.util
        tscript = Path(__file__).resolve().parent / "视频转录.py"
        spec = importlib.util.spec_from_file_location("视频转录", tscript)
        vt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vt)
        video = vt.find_video(args.target)    # 找不到会报错并列出候选
        json_path = video.with_suffix(".json")
        if not json_path.is_file():
            # 未转录：自动内嵌「视频转录」
            print(f"▶ 未找到转录文件，自动先执行视频转录…")
            vt.transcribe(video, lang="zh", prompt=args.prompt)
            if not json_path.is_file():
                print(f"✗ 转录完成但未找到 {json_path}，请直接指定 json 文件路径")
                sys.exit(1)

    segs = json.load(open(json_path, encoding="utf-8"))["transcription"]
    title = json_path.stem

    # 剔除其他声音（独白主体保留）
    dropped = 0
    if not args.no_drop:
        segs, dropped = drop_other_voices(segs, json_path,
                                          workers=args.workers, force=args.drop_force)

    sub = resegment(segs, args.target_w, args.min_w, args.max_w)
    widths = [width(s["text"]) for s in sub]
    total_ms = max(s["to"] for s in sub) if sub else 0
    print(f"▶ {json_path.name}：whisper 原分段 → 标准字幕 {len(sub)} 条")
    print(f"  段长分布：最短 {min(widths):.0f} 字 · 平均 {sum(widths)/len(widths):.1f} 字 · 最长 {max(widths):.0f} 字")
    if args.dry_run:
        for s in sub[:10]:
            print(f"   [{fmt(s['from'])} → {fmt(s['to'])}] {s['text'][:36]}")
        return

    out_dir = Path(args.out_dir) if args.out_dir else note_dir_for(json_path)
    out_dir.mkdir(parents=True, exist_ok=True)   # --out-dir 可能是尚不存在的自定义目录
    out = out_dir / (json_path.stem + "-标准字幕.md")
    drop_note = "" if args.no_drop else f"（已剔除其他声音 {dropped} 条）"
    # 视频嵌入名：优先找实际媒体文件（音频-only 时是 .m4a/.mp3，不是 .mp4）
    media_name = json_path.stem + ".mp4"
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".flac", ".wav", ".aac"):
        if (json_path.parent / (json_path.stem + ext)).is_file():
            media_name = json_path.stem + ext
            break
    lines = [
        f"# {title}",
        "",
        f"![[{media_name}]]",
        "",
        f"> 视频时长 {fmt(total_ms)} · 标准字幕 {len(sub)} 条"
        f"（最短 {min(widths):.0f} / 平均 {sum(widths)/len(widths):.1f} / 最长 {max(widths):.0f} 字）"
        f"· whisper 转写 + 标准断句{drop_note}",
        "",
        "## 标准字幕",
        "",
    ]
    for s in sub:
        lines.append(f"[{fmt(s['from'])} → {fmt(s['to'])}] {s['text']}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n✅ 笔记已生成：{out}")
    try:
        import progress
        try:
            note_path = str(out.resolve().relative_to(VAULT_ROOT.resolve()))
        except ValueError:
            note_path = str(out)
        progress.emit_result([{"type": "note", "path": note_path}])
    except ImportError:
        pass


if __name__ == "__main__":
    main()
