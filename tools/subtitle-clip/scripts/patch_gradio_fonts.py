# -*- coding: utf-8 -*-
"""给 gradio 前端打字体补丁：把 fonts.googleapis.com 请求桥接到本地空样式表。

背景：gradio 4.44 的前端 JS 会无条件向 fonts.googleapis.com 注入字体样式表；
在被墙/离线环境该请求长时间挂起，页面会一直卡在「加载中...」。
本脚本把运行时 gradio 前端 JS 里的字体 URL 替换为同源空文件（随包静态服务）。

重装/升级 runtime 里的 gradio 后需要重新执行：
  runtime\\Scripts\\python.exe scripts\\patch_gradio_fonts.py
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "runtime" / "lib" / "site-packages" / "gradio" / "templates" / "frontend" / "assets"
TARGET_CHUNK = "Index-DB1XLvMK.js"          # gradio 4.44.1 注入字体的前端 chunk
GOOGLE = "https://fonts.googleapis.com/css2?family="
BRIDGE = "/assets/fonts-bridge.css?family="


def main():
    js = ASSETS / TARGET_CHUNK
    if not js.exists():
        print(f"[skip] 未找到 {js}（gradio 版本可能不同，请人工确认 chunk 文件名）")
        return 1
    src = js.read_text(encoding="utf-8")
    if BRIDGE in src:
        print("[ok] 已打过补丁，无需重复")
        return 0
    if GOOGLE not in src:
        print("[skip] 该 chunk 未引用 google fonts，可能 gradio 已修复")
        return 0
    js.write_text(src.replace(GOOGLE, BRIDGE), encoding="utf-8")
    bridge = ASSETS / "fonts-bridge.css"
    if not bridge.exists():
        bridge.write_text("/* 本地桥接：离线环境不请求 fonts.googleapis.com */\n", encoding="utf-8")
    print(f"[done] 已替换 {src.count(GOOGLE)} 处字体请求为本地桥接")
    return 0


if __name__ == "__main__":
    sys.exit(main())
