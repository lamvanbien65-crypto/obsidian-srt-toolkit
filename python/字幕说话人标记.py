#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  字幕说话人标记：为对话访谈的字幕时间轴标记「这句是谁说的」
#
#  原理：LLM 语义标注（claude CLI · DeepSeek v4 Pro）
#    · 访谈类视频有天然的交替规律 + 内容语义（提问 vs 回答）
#    · 把字幕分块交给 LLM，逐行标注说话人，再合并成完整标记
#
#  流程：
#    · 读取「视频转录」生成的 .json（whisper 转写，含时间戳）
#    · 按块（默认 120 行/块）并行调用 claude CLI 标注：0=第一人 1=第二人 2=不确定
#    · 合并全部块 → 可选短句平滑 → 重新生成带说话人标记的时间轴笔记
#
#  用法：
#    python3 字幕说话人标记.py "【P68】...正.json" --speakers "方三文,段永平"
#    python3 字幕说话人标记.py 转录.json --speakers "A,B" --out 笔记.md
#    python3 字幕说话人标记.py 转录.json --speakers "A,B" --workers 8 --smooth
#    python3 字幕说话人标记.py 转录.json --speakers "A,B" --model haiku
#        # 模型档位：haiku=deepseek-v4-flash（默认，快）；sonnet/opus=deepseek-v4-pro（慢但更准）
#    python3 字幕说话人标记.py 转录.json --speakers "A,B" --dry-run   # 只标注不写笔记
#    python3 字幕说话人标记.py 转录.json --speakers "A,B" --force     # 忽略缓存重标
#    python3 字幕说话人标记.py 转录.json --speakers "A,B" --merge
#        # 合并模式：同一人连续说的完整一段话 = 一条字幕（[起→止] **说话人**：全文），
#        # 生成「对话字幕」版笔记，避免逐句碎片化
#    python3 字幕说话人标记.py 视频名 --speakers "A,B" --merge
#        # 一键全流程：未转录自动内嵌「视频转录」→ LLM 标注 → 合并出「对话字幕」笔记
#        # （--prompt 可传 whisper 热词给转录步骤）
#    断点续传：每块完成即落盘，中断/失败后直接重跑同一命令，自动只补缺的块
#    缓存复用：标注结果存 .speakers 缓存，重跑（含 --merge）免调 LLM 秒出笔记
# ============================================================
import argparse, concurrent.futures, json, os, re, sys, threading
from pathlib import Path
import llm   # 统一 LLM 调用层（同目录）：claude CLI 优先，无则 HTTP 直连 DeepSeek

CHUNK_SIZE = 120   # 每块行数（LLM 单次标注）
RETRY = 4          # 解析失败重试次数（flash 偶发输出长度不符，多试几次）
                   # 失败概率≈千分之一；真失败也不怕——断点续传，重跑只补缺块
MODEL = os.environ.get("OBSIDIAN_LLM_MODEL") or "haiku"  # 模型档位：haiku→deepseek-v4-flash（快，标注够用）；
                   # sonnet/opus→deepseek-v4-pro（准但慢，思考可达数分钟）


def fmt(ms):
    ms = max(0, int(ms))
    h, rem = divmod(ms // 1000, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_prompt(speaker_a, speaker_b, chunk):
    """构造标注提示词：给 LLM 访谈背景 + 行清单，要求返回 JSON 数组"""
    lines = []
    for i, s in enumerate(chunk):
        lines.append(f"[{i}] [{fmt(s['offsets']['from'])}] {s['text'].strip()}")
    return (
        f"你是说话人标注器。这是访谈「{speaker_a}对话{speaker_b}」的字幕转写片段。\n"
        f"{speaker_a}=主持人/提问者；{speaker_b}=受访嘉宾。两人交替说话：通常一人提问一人回答，"
        f"但嘉宾也可能反问或补充，请根据内容语义判断每句是谁说的。\n"
        f"行格式：[行号] [时间] 内容\n"
        f"给每一行标注说话人：0={speaker_a}，1={speaker_b}，无法判断标 2=不确定。\n"
        f"只输出一个 JSON 数组（如 [0,1,1,0,...]），数量必须与行数完全一致；"
        f"即使连续多句都是同一人说的，也必须每句各输出一个标签，禁止合并省略。"
        f"不要输出任何其他文字。\n\n"
        + "\n".join(lines)
    )


def call_claude(prompt, model=MODEL):
    """调用 LLM 返回标签列表或 None（Ollama 本地 / claude CLI / DeepSeek 云）"""
    arr = llm.call_json(prompt, model=model)
    return llm.coerce_labels(arr)


def label_chunk(a, b, chunk, model=MODEL):
    """单块标注（含重试）；仍失败则对半拆分成子块递归标注；最终失败返回 None"""
    for _ in range(RETRY + 1):
        labels = call_claude(build_prompt(a, b, chunk), model=model)
        if labels and len(labels) == len(chunk) and all(x in (0, 1, 2) for x in labels):
            return labels
    if len(chunk) >= 20:   # 兜底：拆成两半各自标注后拼接（小块的省略概率骤降）
        mid = len(chunk) // 2
        left = label_chunk(a, b, chunk[:mid], model=model)
        right = label_chunk(a, b, chunk[mid:], model=model)
        if left is not None and right is not None:
            return left + right
    return None


def load_checkpoint(partial_path):
    """读取断点文件：{块号: [标签...]}；失败返回空 dict"""
    if partial_path.is_file():
        try:
            return {int(k): v for k, v in json.load(open(partial_path, encoding="utf-8")).items()}
        except (ValueError, json.JSONDecodeError):
            pass
    return {}


_CP_LOCK = threading.Lock()   # 多线程同时落盘会互相覆盖 tmp 文件，必须加锁


def save_checkpoint(partial_path, done):
    """把已完成块号 → 标签 落盘（每块完成后调用，失败重跑可续传）"""
    with _CP_LOCK:
        tmp = partial_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        tmp.replace(partial_path)


def call_claude_json(prompt, model=MODEL):
    """调用 LLM 解析返回的 JSON 字符串数组；失败返回 None（优先 claude CLI，无则 HTTP 直连）"""
    arr = llm.call_json(prompt, model=model)
    if isinstance(arr, list) and arr and all(isinstance(x, str) for x in arr):
        return arr
    return None


TRANS_CHUNK = 8    # 翻译每块行数（小块输出更短、JSON 格式更稳定）


def translate_chunk(texts):
    """单块翻译（含重试 + 子块递归）；失败返回 None"""
    prompt = (
        "你是字幕翻译器。把下面每一行字幕翻译成自然、口语化的中文。\n"
        "要求：保留专有名词（人名/公司名/产品名保留英文原文）；术语可意译或保留原文；"
        "口语语气自然，不要逐字硬译。\n"
        "行格式：[行号] 内容\n"
        "只输出一个 JSON 字符串数组（如 [\"译文1\",\"译文2\"]），"
        "数量必须与行数一致，不要输出任何其他文字。\n"
        "重要：每条字幕必须独立翻译成一条译文，即使相邻行语义连贯也绝对禁止合并，"
        "译文条数必须与输入行数完全一致。\n\n"
        + "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
    )
    for _ in range(RETRY + 3):   # 并发下偶发输出格式不稳，多试几次（实测 8 行块平均 2-3 次成功）
        arr = call_claude_json(prompt, model=MODEL)
        if arr and len(arr) == len(texts):
            return arr
    if len(texts) >= 4:
        mid = len(texts) // 2
        left = translate_chunk(texts[:mid])
        right = translate_chunk(texts[mid:])
        if left is not None and right is not None:
            return left + right
    if len(texts) == 2:   # 终极兜底：拆成两条单行各自翻译
        a = translate_chunk(texts[:1])
        b = translate_chunk(texts[1:])
        if a is not None and b is not None:
            return a + b
    return None


def translate_all(texts, workers=8):
    """批量翻译；返回翻译后的列表"""
    workers = llm.suggest_workers(workers)   # CLI=原值 / 云=≤4 / Ollama=≤2
    chunk_n = llm.suggest_chunk(TRANS_CHUNK, "translate")   # Ollama 本地上下文有限，翻译块≤4 行
    chunks = [texts[i:i + chunk_n] for i in range(0, len(texts), chunk_n)]
    print(f"▶ LLM 翻译成中文：{len(texts)} 行，{len(chunks)} 块，并行 {workers}")
    try:
        import progress
        progress.emit("stage", stage="translate", label="▶ LLM 翻译成中文…")
        progress.emit("progress", stage="translate", done=0, total=len(chunks))
    except ImportError:
        pass
    out = [None] * len(texts)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(translate_chunk, c): idx for idx, c in enumerate(chunks)}
        for n_done, f in enumerate(concurrent.futures.as_completed(futs), 1):
            idx = futs[f]
            res = f.result()
            if res is None:
                print(f"✗ 第 {idx + 1} 块翻译失败")
                try:
                    import progress
                    if not llm.CLAUDE_BIN and not llm.get_api_key():
                        progress.emit_error("llm-no-key",
                                            "未找到 LLM API Key：设置页填 DeepSeek Key，"
                                            "或安装 claude CLI 后在 ~/.claude/settings.json 配 env")
                    else:
                        progress.emit_error("llm-fail", f"第 {idx + 1} 块翻译失败（已重试），重跑即可续传")
                except ImportError:
                    pass
                sys.exit(1)
            start = idx * chunk_n
            out[start:start + len(res)] = res
            try:
                import progress
                progress.emit("progress", stage="translate", done=n_done, total=len(chunks))
            except ImportError:
                pass
    assert None not in out, "翻译不完整"
    return out


def smooth(labels, segs, max_dur_ms=1200):
    """短句平滑：孤立翻转且时长极短的段，归并到邻居的说话人"""
    n = len(labels)
    out = labels[:]
    for i in range(1, n - 1):
        if out[i] in (0, 1) and out[i-1] == out[i+1] and out[i] != out[i-1]:
            if segs[i]["offsets"]["to"] - segs[i]["offsets"]["from"] <= max_dur_ms:
                out[i] = out[i-1]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", help="视频转录生成的 .json 文件")
    ap.add_argument("--speakers", required=True, help="对话双方，逗号分隔，如「方三文,段永平」")
    ap.add_argument("--out", help="输出笔记路径（默认与 json 同名 .md）")
    ap.add_argument("--workers", type=int, default=5, help="并行块数")
    ap.add_argument("--model", default=MODEL,
                    help="claude CLI 模型档位：haiku(快)/sonnet/opus(慢但准)，默认 haiku=deepseek-v4-flash")
    ap.add_argument("--smooth", action="store_true", help="开启短句平滑修正（≤1.2s 的孤立翻转并入邻居）")
    ap.add_argument("--dry-run", action="store_true", help="只标注并打印统计，不写笔记")
    ap.add_argument("--force", action="store_true", help="忽略已有标注缓存，全部重新标注")
    ap.add_argument("--merge", action="store_true",
                    help="合并模式：同一人连续说的完整一段话 = 一条字幕，生成「对话字幕」版笔记")
    ap.add_argument("--prompt", help="未转录时自动转录的 whisper 热词（人名/地名等，已转录则忽略）")
    ap.add_argument("--lang", default="zh", help="转录语言：zh（默认）/en（英文视频）")
    ap.add_argument("--translate", action="store_true",
                    help="英文视频：把合并后的字幕翻译成中文（LLM 批量翻译）")
    args = ap.parse_args()

    json_path = Path(args.json)
    if json_path.is_file() and json_path.suffix.lower() != ".json":
        json_path = json_path.with_suffix(".json")   # 传的是视频路径：换同名 .json
    if not json_path.is_file():
        # 传的是视频名：先在 vault 里定位视频，再按视频路径找同名 .json（已转录则跳过转录）
        import importlib.util
        tscript = Path(__file__).resolve().parent / "视频转录.py"
        spec = importlib.util.spec_from_file_location("视频转录", tscript)
        vt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vt)
        video = vt.find_video(args.json)      # 找不到会报错并列出候选
        json_path = video.with_suffix(".json")
        if not json_path.is_file():
            # 未转录：自动内嵌「视频转录」（找视频 → 提音频 → whisper 转写）
            print(f"▶ 未找到转录文件，自动先执行视频转录…（语言 {args.lang}）")
            vt.transcribe(video, lang=args.lang, prompt=args.prompt)
            if not json_path.is_file():
                print(f"✗ 转录完成但未找到 {json_path}，请直接指定 json 文件路径")
                sys.exit(1)
    segs = json.load(open(json_path, encoding="utf-8"))["transcription"]
    names = [x.strip() for x in re.split(r"[,，]", args.speakers) if x.strip()]
    a, b = (names[0], names[1]) if len(names) >= 2 else (names[0], "对方")
    chunk_n = llm.suggest_chunk(CHUNK_SIZE, "label")   # Ollama 本地上下文有限，标注块≤40 行
    chunks = [segs[i:i + chunk_n] for i in range(0, len(segs), chunk_n)]
    cache_path = json_path.with_suffix(json_path.suffix + ".speakers")

    # 标注结果缓存：已标过直接复用（重生成笔记免调 LLM）；--force 全量重标
    labels = None
    if cache_path.is_file() and not args.force:
        try:
            cached = json.load(open(cache_path, encoding="utf-8"))
            if cached.get("speakers") == [a, b] and len(cached["labels"]) == len(segs):
                labels = cached["labels"]
                print(f"▶ 复用已有标注缓存：{len(labels)} 条（免调 LLM，--force 可重标）")
        except (ValueError, json.JSONDecodeError):
            labels = None

    if labels is None:
        # 断点续传：已完成的块直接复用；失败重跑只补缺块
        partial_path = json_path.with_suffix(json_path.suffix + ".labels.partial")
        done = {} if args.force else load_checkpoint(partial_path)
        todo = [i for i in range(len(chunks)) if i not in done]
        print(f"▶ {json_path.name}：{len(segs)} 条字幕，{len(chunks)} 块，"
              f"已完成 {len(done)} 块，待标 {len(todo)} 块，并行 {args.workers}（模型 {args.model}）")

        def work(idx):
            res = label_chunk(a, b, chunks[idx], model=args.model)
            if res is not None:
                done[idx] = res
                save_checkpoint(partial_path, done)   # 每块落盘，崩溃/超时可续传
            return idx, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(work, idx) for idx in todo]
            done_n = len(done)
            for f in concurrent.futures.as_completed(futs):
                idx, res = f.result()
                if res is None:
                    print(f"✗ 块 {idx + 1} 标注失败（已重试）。其余 {len(done)} 块已存断点，可直接重跑续传")
                    try:
                        import progress
                        if not llm.CLAUDE_BIN and not llm.get_api_key():
                            progress.emit_error("llm-no-key",
                                                "未找到 LLM API Key：设置页填 DeepSeek Key，"
                                                "或安装 claude CLI 后在 ~/.claude/settings.json 配 env")
                        else:
                            progress.emit_error("llm-fail", f"块 {idx + 1} 标注失败（已重试），其余已存断点，重跑续传")
                    except ImportError:
                        pass
                    sys.exit(1)
                done_n += 1
                print(f"  ✓ 块 {idx + 1}/{len(chunks)} 完成（已存断点）", flush=True)
                try:
                    import progress
                    progress.emit("progress", stage="label", done=done_n, total=len(chunks))
                except ImportError:
                    pass

        labels = [None] * len(segs)
        for idx, res in done.items():
            start = idx * CHUNK_SIZE
            labels[start:start + len(res)] = res
        assert None not in labels, "标注不完整"
        cache_path.write_text(json.dumps({"speakers": [a, b], "labels": labels},
                                         ensure_ascii=False), encoding="utf-8")
        partial_path.unlink(missing_ok=True)
    if args.smooth:
        labels = smooth(labels, segs)

    c0 = sum(1 for x in labels if x == 0)
    c1 = sum(1 for x in labels if x == 1)
    c2 = sum(1 for x in labels if x == 2)
    print(f"\n✅ 标注完成：{a} {c0} 句 · {b} {c1} 句 · 不确定 {c2} 句")

    def name_of(x):
        return a if x == 0 else (b if x == 1 else "？")

    for i in (0, 3, 7, 12, 20, 30, 40, 50):
        if i < len(segs):
            print(f"   [{fmt(segs[i]['offsets']['from'])}] {name_of(labels[i])}：{segs[i]['text'].strip()[:28]}")
    if args.dry_run:
        return

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)   # --out 目录可能不存在
    else:
        # 输出规范：json 在「1,whisper 视频转录/」或其子目录下时，
        # 笔记进 Output&Generated(生成结果)/
        ws = json_path.parent
        base = ws if ws.name == "1,whisper 视频转录" else \
            (ws.parent if ws.parent.name == "1,whisper 视频转录" else None)
        out_dir = base / "Output&Generated(生成结果)" if base else ws
        if base:
            out_dir.mkdir(exist_ok=True)
        out = out_dir / (json_path.stem + ".md")
    total_ms = segs[-1]["offsets"]["to"]
    # 视频嵌入名：# 会被 Obsidian 当作标题锚点导致嵌入失败，检测实际 mp4 文件名（支持去 # 改名）
    mp4_name = json_path.stem + ".mp4"
    alt_mp4 = mp4_name.replace("#", "")
    if mp4_name != alt_mp4:
        if (json_path.parent / alt_mp4).is_file():
            mp4_name = alt_mp4
            print(f"▶ 视频文件名含 #，嵌入使用去 # 后的新名：{alt_mp4}")
        else:
            print(f"⚠ 文件名含 # 会导致 Obsidian 视频嵌入失败，建议把视频重命名为：{alt_mp4}")
    if args.merge:
        # 合并模式：同一人连续说的完整一段话 = 一条字幕
        runs = []
        cur_s, cur_i = labels[0], 0
        for i in range(1, len(segs) + 1):
            if i == len(segs) or labels[i] != cur_s:
                runs.append((cur_s, cur_i, i - 1))
                if i < len(segs):
                    cur_s, cur_i = labels[i], i
        na = sum(1 for r in runs if r[0] == 0)
        nb = sum(1 for r in runs if r[0] == 1)
        trans_note = " · 已翻译成中文" if args.translate else ""
        lines = [
            f"# {json_path.stem.replace('正', '')}",
            "",
            f"![[{mp4_name}]]",
            "",
            f"> 视频时长 {fmt(total_ms)} · 完整对白 {len(runs)} 条"
            f"（{a} {na} 条 / {b} {nb} 条）· whisper 转写 + LLM 说话人标记{trans_note}",
            "",
            "## 对话字幕",
            "",
        ]
        texts = ["，".join(segs[i]["text"].strip() for i in range(i0, i1 + 1))
                 for sp, i0, i1 in runs]
        if args.translate:
            texts = translate_all(texts)
        for (sp, i0, i1), text in zip(runs, texts):
            who = name_of(sp)
            lines.append(f"[{fmt(segs[i0]['offsets']['from'])} → {fmt(segs[i1]['offsets']['to'])}] "
                         f"**{who}**：{text}")
    else:
        lines = [
            f"# {json_path.stem.replace('正', '')}",
            "",
            f"![[{mp4_name}]]",
            "",
            f"> 视频时长 {fmt(total_ms)} · 字幕 {len(segs)} 条 · 对话：**{a}** × **{b}**"
            f"（{name_of(0)} {c0} 句 / {name_of(1)} {c1} 句）· whisper 转写 + LLM 说话人标记",
            "",
            "## 字幕时间轴",
            "",
        ]
        for s, lab in zip(segs, labels):
            who = name_of(lab)
            lines.append(f"[{fmt(s['offsets']['from'])}] **{who}**：{s['text'].strip()}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n✅ 笔记已生成：{out}")
    try:
        import progress
        import os as _os
        vault_root = _os.environ.get("OBSIDIAN_VAULT_ROOT")
        if vault_root:
            try:
                note_path = str(out.resolve().relative_to(Path(vault_root).resolve()))
            except ValueError:
                note_path = str(out)
        else:
            note_path = str(out)
        progress.emit_result([{"type": "note", "path": note_path}])
    except ImportError:
        pass


if __name__ == "__main__":
    main()
