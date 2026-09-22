# -*- coding: utf-8 -*-
"""文字驱动（GPT-SoVITS）语音服务客户端。

对接 D:\\xm\\wenziqudong 暴露的三个对外接口（见其 接口文档.md）：
  GET  /health            服务是否就绪
  GET  /models            角色列表（只取 ready=true 的 name）
  POST /v1/audio/speech   {voice, input, speed} → 音频二进制（mp3/wav）
地址默认 http://127.0.0.1:18062（服务端口固定不漂移），可用环境变量 TTS_API_BASE 覆盖。
"""
import os

import requests

DEFAULT_BASE = "http://127.0.0.1:18062"
# 文档口径：配音请求超时 ≥300 秒（覆盖长文本合成 + 服务端并发排队）
SPEECH_TIMEOUT = 300


class TTSError(RuntimeError):
    """调用语音服务失败（含服务未启动、角色不存在、合成出错）。"""


def get_base(explicit=None):
    return str(explicit or os.environ.get("TTS_API_BASE") or DEFAULT_BASE).rstrip("/")


def check_ready(base=None):
    """服务不可用时返回中文错误说明，就绪返回 None。"""
    base = get_base(base)
    try:
        r = requests.get("{}/health".format(base), timeout=10)
    except requests.RequestException:
        return ("文字驱动语音服务未启动或不可达（{}）。请先在 wenziqudong 项目里"
                "双击 start.bat 启动，首次加载约需几分钟。".format(base))
    if r.status_code != 200:
        return "语音服务状态异常（HTTP {}）：{}".format(r.status_code, base)
    try:
        status = r.json().get("status")
    except ValueError:
        return "语音服务响应格式异常：{}".format(base)
    if status != "ok":
        return "语音服务尚未就绪（{}），稍后重试。".format(base)
    return None


def list_ready_roles(base=None):
    """返回 (就绪角色名列表, 错误说明)；正常时错误说明为 None。"""
    base = get_base(base)
    err = check_ready(base)
    if err:
        return [], err
    try:
        r = requests.get("{}/models".format(base), timeout=30)
        r.raise_for_status()
        models = r.json().get("models") or []
    except requests.RequestException as e:
        return [], "获取配音角色列表失败：{}".format(e)
    except ValueError:
        return [], "配音角色列表响应不是 JSON：{}".format(base)
    roles = [m.get("name") for m in models if m.get("ready") and m.get("name")]
    return roles, None


def speech(voice, text, speed=1.0, base=None):
    """合成一句台词，返回音频二进制（按响应头可能是 mp3 或 wav）。失败抛 TTSError。"""
    base = get_base(base)
    text = str(text or "").strip()
    if not text:
        raise TTSError("待合成文本为空")
    if len(text) > 1000:
        text = text[:1000]
    # 文档约定：偶发 500 可原样重试一次
    for attempt in (1, 2):
        try:
            r = requests.post(
                "{}/v1/audio/speech".format(base),
                json={"voice": voice, "input": text, "speed": speed},
                timeout=SPEECH_TIMEOUT,
            )
        except requests.RequestException as e:
            raise TTSError("请求语音服务失败：{}".format(e))
        if r.status_code == 200:
            return r.content
        if r.status_code == 500 and attempt == 1:
            continue
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        raise TTSError("HTTP {}：{}".format(r.status_code, detail or "合成失败"))
    raise TTSError("合成失败（重试后仍出错）")
