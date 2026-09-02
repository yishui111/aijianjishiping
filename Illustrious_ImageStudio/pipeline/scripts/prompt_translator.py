# -*- coding: utf-8 -*-
r"""
提示词翻译器 v2 — 中文/自然语言 → Danbooru 英文标签（规范版）
=================================================================
Illustrious / animagine 等二次元模型是"标签驱动"的。本模块用本地 qwen3
把中文翻译成 Danbooru 规范标签，并做词汇/顺序/质量词规范化。

v2 改进（修复"画面内容对不上"）：
  1. 强制 1girl/1boy 开头（Danbooru 规范）
  2. 词汇规范化：gold hair→blonde hair、cherry blossom tree→sakura 等
  3. 剔除无效标签（2d、detailed、highres 等 Danbooru 不认的词）
  4. 质量词前置：masterpiece, best quality 放最前
  5. 锁角色指令：保留"keep appearance/same outfit"语义
  6. Ollama 不可用时降级返回原提示词

用法：
  from prompt_translator import translate_prompt
  tags = translate_prompt("一个黑发少女站在樱花树下微笑")
"""

import json
import logging
import os
import re
import sys
import urllib.request
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPTS_DIR.parent
for _p in (_PIPELINE_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_TRANSLATE_MODEL", "qwen3:8b")

LOG_DIR = _PIPELINE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "translate.log"), level=logging.INFO,
                    encoding="utf-8", format="%(asctime)s [%(levelname)s] %(message)s")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

QUALITY_FRONT = "masterpiece, best quality"
QUALITY_BACK = ""

# ---- Danbooru 词汇规范化表（错误/口语 → 标准标签）----
VOCAB_FIX = {
    "gold hair": "blonde hair",
    "golden hair": "blonde hair",
    "cherry blossom tree": "sakura",
    "cherry blossom": "sakura",
    "2d": "",
    "detailed": "",
    "highres": "",
    "high quality": "",
    "best quality": "",
    "masterpiece": "",
    "white shirt": "white shirt",
    "school uniform": "school uniform",
}

# ---- 无效标签（Danbooru 不认，直接剔除）----
INVALID_TAGS = {
    "2d", "3d", "detailed", "highres", "high quality", "high-quality",
    "best quality", "best-quality", "masterpiece", "hd", "4k", "8k",
    "anime style", "anime-style", "anime", "cartoon", "drawing",
}

# ---- 锁角色语义词（img2img 时保留，提示模型"维持外观"）----
KEEP_WORDS = ("keep appearance", "same outfit", "same clothes", "same character",
              "preserve appearance", "identical appearance", "same face",
              "same hairstyle", "same hair", "same outfit as reference",
              "same design", "same look", "original appearance")


def _ollama_alive(timeout=2.0):
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _chat(prompt, timeout=120):
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("message", {}).get("content", "")


TRANSLATE_PROMPT = """你是二次元绘画提示词翻译器，把中文描述翻译成 Danbooru 风格的英文标签。
Danbooru 标签规范（必须遵守）：
1. 第一词必须是 1girl 或 1boy（按人物性别）
2. 只输出英文标签，逗号分隔，不要解释、不要引号、不要编号
3. 用 Danbooru 标准词汇：金色头发=blonde hair，樱花=sakura，微笑=smile，
   白色衬衫=white shirt，教室=classroom，图书馆=library
4. 顺序：性别(1girl/1boy) → 头发 → 眼睛 → 服装 → 场景 → 动作 → 表情 → 光线
5. 如果是修改指令（含"保持/让/把/换成/远/近"等），先描述画面，指令用
   keep appearance / same outfit / full body / close-up 等标签表达
6. 不要输出 masterpiece/best quality 等质量词（我会自动加）

中文：{prompt}
英文标签："""


def _normalize(tags_str):
    """标签规范化：词汇修正 + 剔除无效 + 强制顺序 + 质量词前置"""
    # 拆标签
    parts = [p.strip() for p in re.split(r"[,，]", tags_str) if p.strip()]
    # 词汇修正 + 剔无效
    cleaned = []
    keep_flags = []
    for p in parts:
        low = p.lower()
        fixed = None
        for k, v in VOCAB_FIX.items():
            if low == k or low == k.replace(" ", "_"):
                fixed = v
                break
        if fixed == "":
            continue  # 剔除
        if fixed:
            cleaned.append(fixed)
            continue
        if low in INVALID_TAGS:
            continue
        cleaned.append(p)
        if any(k in low for k in KEEP_WORDS):
            keep_flags.append(p)
    # 提取 1girl/1boy 到最前
    gender = [p for p in cleaned if re.match(r"^1girl$|^1boy$", p, re.I)]
    rest = [p for p in cleaned if not re.match(r"^1girl$|^1boy$", p, re.I)]
    # 去重（保序）
    seen = set()
    dedup = []
    for p in gender + rest:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(p)
    # 质量词前置
    final = [QUALITY_FRONT] + dedup
    if QUALITY_BACK:
        final.append(QUALITY_BACK)
    return ", ".join(final)


def translate_prompt(prompt, force=False):
    """中文/自然语言 → Danbooru 英文标签（规范化）。force=True 强制翻译。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return prompt

    # 纯英文（无中文）→ 规范化后透传
    if not force and not _CJK_RE.search(prompt):
        if any(h in prompt.lower() for h in ("masterpiece", "best quality")):
            return _normalize(prompt)
        return _normalize(f"{QUALITY_FRONT}, {prompt}")

    if not _ollama_alive():
        logging.warning("Ollama 不可用，跳过翻译（原样返回）")
        return prompt

    t0 = time.time()
    try:
        out = _chat(TRANSLATE_PROMPT.format(prompt=prompt))
        out = out.strip().strip('"').strip("'")
        out = out.split("\n")[0].strip()
        if not out:
            return prompt
        out = _normalize(out)
        logging.info(f"翻译[{time.time()-t0:.1f}s]: {prompt} -> {out}")
        return out
    except Exception as e:
        logging.error(f"翻译失败: {e}")
        return prompt


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(translate_prompt(sys.argv[1]))
    else:
        for p in [
            "一个黑发少女穿着白衬衫站在樱花树下微笑",
            "一个金色头发的少女穿着红色连衣裙站在海边微笑",
            "让角色坐在山巅岩石上远眺夕阳，保持红发剑客的外貌和黑色劲装不变",
            "远距离看这张人物的全身",
            "1girl, blue hair, white dress",
        ]:
            print(f"IN : {p}")
            print(f"OUT: {translate_prompt(p)}")
            print()
