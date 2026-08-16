#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  LLM 统一调用层：本地 Ollama 优先，claude CLI / DeepSeek 云兜底
#
#  用途：让字幕脚本不依赖「Claude Code / 云 API」也能用 LLM。
#    · 本地 Ollama 在线 → 走本地模型（零成本，如 qwen3:8b）
#    · 本机已装 claude CLI → 走 CLI（行为与以前完全一致）
#    · 都没有 → curl 子进程直连 DeepSeek Anthropic 兼容端点
#      （urllib 实测长响应会 SSL 断连，故用 curl）
#
#  Provider 选择（llm_provider）：
#    auto  → Ollama（在线时）→ claude CLI → DeepSeek（默认，逐级降级）
#    ollama → 只用本地模型，失败即报错
#    cli   → 只用 claude CLI
#    cloud → 只用 DeepSeek HTTP
#  配置方式：环境变量 LLM_PROVIDER / OLLAMA_MODEL，或 scripts/config.json
#
#  API Key 读取顺序（只用于 DeepSeek 云直连）：
#    1. 环境变量 DEEPSEEK_API_KEY / ANTHROPIC_AUTH_TOKEN
#    2. ~/.claude/settings.json 的 env（若存在）
#    3. 脚本同目录 config.json 的 api_key 字段
#    4. 当前目录 .env（KEY=xxx 格式，简单实现）
#
#  云模型映射（与 claude CLI 的档位一致）：
#    haiku → deepseek-v4-flash（快，默认）
#    sonnet / opus → deepseek-v4-pro（准但慢）
#
#  用法：
#    import llm
#    text = llm.call("你好", model="haiku")        # 返回文本，失败返回 None
#    arr  = llm.call_json("[1,2]", model="haiku")  # 返回 JSON 解析结果，失败 None
# ============================================================
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

CLAUDE_BIN = shutil.which("claude")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
# 强制走 HTTP 直连（测试用；正常不用设）
FORCE_HTTP = os.environ.get("LLM_FORCE_HTTP", "") == "1"

# ---- Ollama 本地模型 ----
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_NUM_CTX = 4096    # 本地模型上下文窗口（16GB 内存跑 qwen3:8b 时 4096 才稳定）

# claude CLI 档位 → DeepSeek 直连模型名
MODEL_MAP = {
    "haiku": "deepseek-v4-flash",
    "sonnet": "deepseek-v4-pro",
    "opus": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}
DEFAULT_MODEL = "haiku"
TIMEOUT = 600  # 单次请求超时（秒）


# ---------- API Key 探测 ----------

def _load_claude_settings_env():
    """读 ~/.claude/settings.json 里的 env（对方可能从别处拷贝，不依赖装 CLI）"""
    try:
        p = Path.home() / ".claude" / "settings.json"
        if p.is_file():
            env = json.loads(p.read_text(encoding="utf-8")).get("env", {})
            if isinstance(env, dict):
                return env
    except Exception:
        pass
    return {}


def _load_config_file():
    """读脚本同目录 config.json（{api_key: "sk-...", base_url: "..."}）"""
    try:
        p = Path(__file__).resolve().parent / "config.json"
        if p.is_file():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(cfg, dict) and cfg.get("api_key"):
                return cfg
    except Exception:
        pass
    return None


def _load_dotenv():
    """读脚本同目录 .env（KEY=xxx 行）"""
    try:
        p = Path(__file__).resolve().parent / ".env"
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN") and v.strip():
                        return v.strip()
    except Exception:
        pass
    return None


def get_api_key():
    """按优先级取 API key，找不到返回 None"""
    for k in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        v = os.environ.get(k)
        if v:
            return v
    env = _load_claude_settings_env()
    for k in ("ANTHROPIC_AUTH_TOKEN", "DEEPSEEK_API_KEY"):
        v = env.get(k)
        if v:
            return v
    cfg = _load_config_file()
    if cfg:
        return cfg.get("api_key")
    return _load_dotenv()


def get_base_url():
    """API 端点：环境变量 > config.json > 默认 DeepSeek"""
    env_url = os.environ.get("ANTHROPIC_BASE_URL")
    if env_url:
        return env_url
    cfg = _load_config_file()
    if cfg and cfg.get("base_url"):
        return cfg["base_url"]
    return BASE_URL


# ---------- Provider 选择 ----------

def resolve_provider():
    """返回 provider：auto / ollama / cli / cloud / none（环境变量 LLM_PROVIDER > config.json）
    none = 零云模式：不发起任何 LLM 调用（插件「SRT 字幕工具箱」的默认注入值）"""
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if p not in ("auto", "ollama", "cli", "cloud", "none"):
        cfg = _load_config_file()
        if cfg:
            p = str(cfg.get("llm_provider", "")).strip().lower()
    return p if p in ("auto", "ollama", "cli", "cloud", "none") else "auto"


def get_ollama_model():
    """本地模型名：环境变量 OLLAMA_MODEL > config.json > 默认 qwen3:8b"""
    env = os.environ.get("OLLAMA_MODEL", "").strip()
    if env:
        return env
    cfg = _load_config_file()
    if cfg and cfg.get("ollama_model"):
        return str(cfg["ollama_model"])
    return OLLAMA_MODEL


def _ollama_online(timeout=2):
    """探测 Ollama 服务是否在线（只探活，不加载模型）"""
    try:
        r = subprocess.run(["curl", "-s", "--connect-timeout", str(timeout),
                            OLLAMA_BASE.rstrip("/") + "/api/tags"],
                           capture_output=True, text=True, timeout=6)
        return r.returncode == 0 and '"models"' in r.stdout
    except Exception:
        return False


def is_cli():
    """当前实际 LLM 路径是否走 claude CLI"""
    if FORCE_HTTP or not CLAUDE_BIN:
        return False
    return resolve_provider() in ("auto", "cli")


def ollama_mode():
    """当前是否实际走 Ollama 本地模型（仅显式 LLM_PROVIDER=ollama 时启用；
    auto 默认不用本地模型——本地模型速度/质量暂不成熟）"""
    return not FORCE_HTTP and resolve_provider() == "ollama"


def suggest_workers(n=8):
    """按 provider 建议并发：CLI=原值，HTTP=≤4，Ollama=1（实测并发排队会让 llama-server 卡死）"""
    if is_cli():
        return n
    if ollama_mode():
        return 1
    return min(n, 4)


def suggest_chunk(default, kind="label"):
    """按 provider 调整 LLM 块行数：Ollama 本地上下文有限（4096），标注≤40 行、翻译≤4 行；
    CLI/云返回原值"""
    if not ollama_mode():
        return default
    return min(default, 40 if kind == "label" else 4)


# ---------- 调用 ----------

def _call_ollama(prompt):
    """调用本地 Ollama（OpenAI 兼容端点）；失败返回 None"""
    m = get_ollama_model()
    url = OLLAMA_BASE.rstrip("/") + "/v1/chat/completions"
    body = {
        "model": m,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        # 关思考提速（qwen3 思考型模型实测拖慢数倍）；顶层 think 会让兼容端点挂起，只放 options
        "options": {"num_ctx": OLLAMA_NUM_CTX, "think": False},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
        body_path = f.name
    try:
        r = subprocess.run(["curl", "-s", "--connect-timeout", "10", "--max-time", str(TIMEOUT),
                            "-X", "POST", url,
                            "-H", "Content-Type: application/json",
                            "-d", f"@{body_path}"],
                           capture_output=True, text=True, timeout=TIMEOUT + 30)
        if r.returncode != 0 or not r.stdout.strip():
            print(f"  …Ollama curl 失败（exit={r.returncode}，stderr={r.stderr[:120]}）")
            return None
        d = json.loads(r.stdout)
        content = d.get("choices", [{}])[0].get("message", {}).get("content")
        if content is None:
            print(f"  …Ollama 返回异常结构：{r.stdout[:150]}")
        return content or None
    except Exception as e:
        print(f"  …Ollama 调用异常：{e}")
        return None
    finally:
        try:
            os.unlink(body_path)
        except OSError:
            pass


def _call_http(prompt, model=DEFAULT_MODEL):
    """HTTP 直连 DeepSeek Anthropic 兼容端点"""
    key = get_api_key()
    if not key:
        print("✗ 未找到 LLM API Key：请设置环境变量 DEEPSEEK_API_KEY，"
              "或在本目录 config.json / .env 中填写（参考 config.example.json）")
        return None
    url = get_base_url().rstrip("/") + "/v1/messages"
    body = {
        "model": MODEL_MAP.get(model, MODEL_MAP[DEFAULT_MODEL]),
        "max_tokens": 12000,
        # 关掉推理（thinking）：翻译/标注任务实测提速约 9 倍，且避免长响应断连
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}],
    }
    # 用 curl 子进程（系统自带）直连：实测 urllib 在长响应/并发下会 SSL 断连挂起，curl 稳定
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
        body_path = f.name
    cmd = ["curl", "-s", "--connect-timeout", "30", "--max-time", str(TIMEOUT),
           "-X", "POST", url,
           "-H", "Content-Type: application/json",
           "-H", f"x-api-key: {key}",
           "-H", "anthropic-version: 2023-06-01",
           "-d", f"@{body_path}"]
    # 大请求/并发下服务端可能断开连接，重试 3 次指数退避
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 60)
            if r.returncode != 0 or not r.stdout.strip():
                raise ConnectionError(f"curl exit={r.returncode}")
            data = json.loads(r.stdout)
            # 推理模型返回的 content 可能含 thinking 块，取第一个 text 块
            for block in data.get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    return block["text"]
            return None
        except Exception as e:
            if attempt == 2:
                print(f"✗ LLM HTTP 调用失败（重试 3 次后放弃）：{e}")
                return None
            print(f"  …HTTP 调用重试 {attempt + 1}/3（{e}）")
            time.sleep(2 * (attempt + 1))
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass


def _call_cli(prompt, model=DEFAULT_MODEL):
    """走 claude CLI（老路径，行为不变）"""
    cmd = [CLAUDE_BIN, "-p", "--model", model, prompt]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        return r.stdout
    except Exception:
        return None


def call(prompt, model=DEFAULT_MODEL):
    """调用 LLM 返回文本；失败返回 None。
    none（零云）：直接返回 None，不发起任何调用（插件零云模式的硬保证）；
    降级链（auto）：claude CLI → DeepSeek HTTP；显式 ollama 时走本地模型"""
    prov = resolve_provider()
    if prov == "none":
        return None
    if prov == "ollama":
        r = _call_ollama(prompt)
        if r is not None:
            return r
        print(f"✗ Ollama 调用失败（{get_ollama_model()}），请检查本地模型是否就绪")
        return None
    if CLAUDE_BIN and not FORCE_HTTP:
        r = _call_cli(prompt, model=model)
        if r is not None:
            return r
    return _call_http(prompt, model=model)


def coerce_labels(arr):
    """把 LLM 返回的任意 JSON 结构归一化为 0/1/2 数字列表（本地小模型常输出对象数组等花式格式）
    支持：纯数字数组 [0,1,0] / 字符串数组 ["0","1"] / 对象数组 [{"speaker":"0"},...] / 混合；失败返回 None"""
    if not isinstance(arr, list):
        return None
    out = []
    for x in arr:
        v = None
        if isinstance(x, (int, float)):
            v = int(x)
        elif isinstance(x, str) and x.strip().isdigit():
            v = int(x.strip())
        elif isinstance(x, dict):
            # 常见键优先：speaker / label / 说话人 / 0
            for k in ("speaker", "speaker_id", "label", "说话人", "who", "0"):
                if k in x:
                    val = x[k]
                    if isinstance(val, (int, float)):
                        v = int(val)
                    elif isinstance(val, str) and val.strip().isdigit():
                        v = int(val.strip())
                    break
            if v is None:   # 找不到已知键：取第一个数字值
                for val in x.values():
                    if isinstance(val, (int, float)):
                        v = int(val)
                        break
                    if isinstance(val, str) and val.strip().isdigit():
                        v = int(val.strip())
                        break
        if v is None:
            return None
        out.append(v)
    return out


def call_json(prompt, model=DEFAULT_MODEL):
    """调用 LLM 并解析返回的 JSON；失败返回 None"""
    text = call(prompt, model=model)
    if not text:
        return None
    # 优先用 JSONDecoder.raw_decode 严格解析（找第一个合法 JSON 值，正确处理嵌套）
    try:
        dec = json.JSONDecoder()
        start = 0
        while start < len(text):
            while start < len(text) and text[start] not in "[{":
                start += 1
            if start >= len(text):
                break
            try:
                obj, _ = dec.raw_decode(text[start:])
                return obj
            except json.JSONDecodeError:
                start += 1
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # 自测：python3 llm.py "你好" [haiku]
    q = sys.argv[1] if len(sys.argv) > 1 else "只回复两个字：正常"
    m = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL
    print(call(q, model=m))
