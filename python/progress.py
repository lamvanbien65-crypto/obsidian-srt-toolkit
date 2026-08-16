#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
#  JSON 进度发射器：TS 壳（Obsidian 插件）↔ Python 进程的通信协议
#
#  协议：stdout 输出一行以 ASCII 记录分隔符 \x1e 开头的 JSON：
#    \x1e{"t":"stage","stage":"download","label":"▶ 下载中…"}
#    \x1e{"t":"progress","stage":"label","done":1,"total":2}
#    \x1e{"t":"result","outputs":[{"type":"note","path":"..."}]}
#    \x1e{"t":"error","code":"login-required","text":"..."}
#  非 \x1e 开头的 stdout 行 = 普通中文日志（原样展示）。
#
#  仅当环境变量 OBSIDIAN_JSON_PROGRESS=1 时启用（插件注入）；
#  未启用时 emit 完全静默——命令行/Claudian 用法零影响。
# ============================================================
import json, os, sys

ENABLED = os.environ.get("OBSIDIAN_JSON_PROGRESS") == "1"
_SEP = "\x1e"


def emit(event, **payload):
    """发射一个协议事件；未启用时静默"""
    if not ENABLED:
        return
    payload["t"] = event
    try:
        sys.stdout.write(_SEP + json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        pass


def emit_error(code, text):
    emit("error", code=code, text=text)


def emit_result(outputs):
    emit("result", outputs=outputs)
