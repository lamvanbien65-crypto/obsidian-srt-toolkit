#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  B站字幕抓取：CC 字幕（UP主上传）+ AI 字幕（B站自动生成）
#
#  流程：
#    1. view API（x/web-interface/view）拿 aid/cid/title/duration
#    2. player API（x/player/wbi/v2）拿字幕列表（CC + AI 都在 subtitle.subtitles）
#       · 实测未签名可调用；403 时降级 wbi 签名重试（算法照搬 yt-dlp extractor）
#    3. 按偏好选字幕（auto=CC中文 > AI中文 > CC英文 > AI英文）
#    4. 下载字幕 JSON → 转 whisper 同款 transcription 格式（offsets 毫秒）
#       + .srt + .txt 三件套 → 下游剪藏脚本无感知复用
#
#  Cookie：环境变量 BILI_COOKIE（B站登录态，AI 字幕/会员字幕需要），
#  以 Cookie 请求头形式携带，绝不写入命令行。
#
#  用法：
#    python3 b站字幕.py "BV1xxxx"                  # 抓取 → 同名 .json/.srt/.txt
#    python3 b站字幕.py BV号 --out-dir <dir>       # 指定输出目录
#    python3 b站字幕.py BV号 --prefer cc|ai|auto   # 字幕偏好
#    python3 b站字幕.py BV号 --probe               # 只探测，stdout 输出字幕清单
# ============================================================
import argparse, hashlib, json, os, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")

# wbi 签名 mixin 表（照搬 yt-dlp bilibili extractor / B站前端 getMixinKey）
MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
_wbi_key_cache = {"key": None, "ts": 0}


def extract_bvid(text):
    m = BV_RE.search(text)
    return m.group(0) if m else None


def cookie_header():
    c = os.environ.get("BILI_COOKIE", "").strip()
    return {"Cookie": c} if c else {}


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **cookie_header()})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_wbi_key():
    """wbi mixin key（nav API 取 img/sub key + 64 位置换表，进程内缓存）"""
    if _wbi_key_cache["key"] and time.time() - _wbi_key_cache["ts"] < 600:
        return _wbi_key_cache["key"]
    data = get_json("https://api.bilibili.com/x/web-interface/nav")["data"]["wbi_img"]
    lookup = "".join(url.rsplit("/", 1)[1].split(".")[0] for url in (data["img_url"], data["sub_url"]))
    key = "".join(lookup[i] for i in MIXIN_TAB)[:32]
    _wbi_key_cache.update({"key": key, "ts": time.time()})
    return key


def sign_wbi(params):
    """wbi 签名（参数净化 + 排序 + wts + md5）"""
    params = {k: v for k, v in params.items()}
    params["wts"] = round(time.time())
    params = {k: "".join(c for c in str(v) if c not in "!'()*")
              for k, v in sorted(params.items())}
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5(f"{query}{get_wbi_key()}".encode()).hexdigest()
    return params


def view_info(bvid):
    """view API：标题/时长/cid（实测无需签名）"""
    data = get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if data.get("code") != 0:
        raise RuntimeError(f"view API 错误：{data.get('message', data.get('code'))}")
    d = data["data"]
    return {"title": d["title"], "duration": d.get("duration", 0),
            "cid": d["cid"], "aid": d["aid"], "pages": d.get("pages", [d])}


def subtitle_list(bvid, cid):
    """player API 字幕列表；403 时 wbi 签名重试"""
    url = f"https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}"
    try:
        data = get_json(url)
    except Exception:
        url2 = "https://api.bilibili.com/x/player/wbi/v2?" + urllib.parse.urlencode(
            sign_wbi({"bvid": bvid, "cid": cid}))
        data = get_json(url2)
    if data.get("code") != 0:
        raise RuntimeError(f"player API 错误：{data.get('message', data.get('code'))}")
    d = data["data"]
    return d["subtitle"]["subtitles"], d["subtitle"].get("need_login_subtitle", False)


def choose_subtitle(subs, prefer="auto", lang_hint="zh"):
    """按偏好选字幕：CC（lan 无 ai 前缀）与 AI（lan=ai-zh 等）都支持"""
    def is_ai(s):
        return s.get("lan", "").startswith("ai-") or s.get("lan", "").startswith("ai_")
    if not subs:
        return None, None
    # prefer 过滤
    pool = subs
    if prefer == "cc":
        pool = [s for s in subs if not is_ai(s)]
    elif prefer == "ai":
        pool = [s for s in subs if is_ai(s)]
    if not pool:
        pool = subs
    # 打分：CC 中文 > AI 中文 > CC 其他 > AI 其他
    def score(s):
        lan = s.get("lan", "")
        ai = is_ai(s)
        zh = "zh" in lan or "cn" in lan or "hans" in lan or "hant" in lan
        return (0 if zh else 2) + (1 if ai else 0)
    best = min(pool, key=score)
    # 若选中的是 AI 且生成中
    if is_ai(best) and best.get("ai_status") == 2:
        return None, "ai-generating"
    return best, None


def download_subtitle_json(sub):
    """下载字幕 JSON（AI 字幕在 aisubtitle.hdslb.com，需登录 cookie）"""
    url = sub["subtitle_url"]
    if not url.startswith("http"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, **cookie_header()})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 412):
            raise RuntimeError("subtitle-403",
                               "字幕接口需要登录态（AI 字幕/会员字幕），请在插件设置页填入 B站 cookie")
        raise


def to_transcription(body):
    """B站字幕 body[{from,to(秒),content}] → whisper 同款 [{"offsets":{from,to(毫秒)},"text"}]"""
    segs = []
    for s in body:
        content = (s.get("content") or "").strip()
        if not content:
            continue
        segs.append({
            "offsets": {"from": int(round(s["from"] * 1000)),
                        "to": int(round(s.get("to", s["from"]) * 1000))},
            "text": content,
        })
    return segs


def fmt_srt_time(ms):
    ms = max(0, int(ms))
    h, rem = divmod(ms // 1000, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms % 1000:03d}"


def _rel(path):
    """vault 相对路径（插件壳展示用）；vault 外返回绝对路径"""
    root = os.environ.get("OBSIDIAN_VAULT_ROOT")
    if root:
        try:
            return str(Path(path).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            pass
    return str(path)


def write_srt(segs, path):
    lines = []
    for i, s in enumerate(segs, 1):
        lines.append(str(i))
        lines.append(f"{fmt_srt_time(s['offsets']['from'])} --> {fmt_srt_time(s['offsets']['to'])}")
        lines.append(s["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_txt(segs, path):
    path.write_text("\n".join(s["text"] for s in segs), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="B站链接 / 分享短链 / 裸BV号")
    ap.add_argument("--out-dir", help="输出目录（默认与视频同目录或 --dir）")
    ap.add_argument("--prefer", default="auto", choices=["auto", "cc", "ai"],
                    help="字幕偏好：auto（默认，CC中文>AI中文）/cc/ai")
    ap.add_argument("--lang", default="zh", help="语言提示（zh/en）")
    ap.add_argument("--probe", action="store_true", help="只探测，不下载，stdout 输出字幕清单 JSON")
    args = ap.parse_args()

    bvid = extract_bvid(args.input)
    if not bvid:
        print(f"✗ 没找到 BV 号：{args.input}")
        sys.exit(1)

    info = view_info(bvid)
    subs, need_login = subtitle_list(bvid, info["cid"])
    if args.probe:
        print(json.dumps({
            "bvid": bvid, "title": info["title"],
            "duration": info["duration"],
            "need_login": need_login,
            "subtitles": [
                {"lan": s.get("lan"), "lan_doc": s.get("lan_doc"),
                 "ai_status": s.get("ai_status"), "url": s.get("subtitle_url")}
                for s in subs
            ],
        }, ensure_ascii=False, indent=1))
        return

    target, warn = choose_subtitle(subs, args.prefer, args.lang)
    if warn == "ai-generating":
        print("✗ AI 字幕生成中，请稍后再试（或回退 whisper 转写）")
        try:
            import progress
            progress.emit_error("ai-generating", "AI 字幕生成中，请稍后再试（或回退 whisper 转写）")
        except ImportError:
            pass
        sys.exit(2)
    if target is None:
        print("✗ 该视频无可用字幕（无 CC 字幕且无 AI 字幕），请回退 whisper 转写")
        try:
            import progress
            progress.emit_error("no-subtitles", "该视频无可用字幕（无 CC 且无 AI 字幕），一键流程会自动回退 whisper 转写")
        except ImportError:
            pass
        sys.exit(2)
    if need_login and not os.environ.get("BILI_COOKIE"):
        print("✗ 字幕需要登录态（AI 字幕/会员字幕），请设置 BILI_COOKIE 环境变量或在插件设置页填入 cookie")
        try:
            import progress
            progress.emit_error("login-required",
                                "字幕需要登录态（AI 字幕/会员字幕），请在插件设置页填入 B站 cookie")
        except ImportError:
            pass
        sys.exit(3)

    try:
        import progress
        progress.emit("stage", stage="subtitle", label="▶ 抓取 B站字幕…")
    except ImportError:
        pass

    raw = download_subtitle_json(target)
    segs = to_transcription(raw.get("body", []))
    if not segs:
        print("✗ 字幕内容为空")
        sys.exit(2)

    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        out_dir = Path.cwd()
    stem = out_dir / (info["title"].replace("/", "／").replace("#", "") or bvid)
    data = {"transcription": segs}
    (out_dir / f"{stem.name}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    write_srt(segs, out_dir / f"{stem.name}.srt")
    write_txt(segs, out_dir / f"{stem.name}.txt")
    print(f"✅ 字幕已抓取：{target.get('lan', '?')}（{len(segs)} 条）→ {out_dir / stem.name}.json/.srt/.txt")
    try:
        import progress
        progress.emit_result([
            {"type": "json", "path": _rel(out_dir / f"{stem.name}.json")},
            {"type": "srt", "path": _rel(out_dir / f"{stem.name}.srt")},
            {"type": "txt", "path": _rel(out_dir / f"{stem.name}.txt")},
        ])
    except ImportError:
        pass


if __name__ == "__main__":
    main()
