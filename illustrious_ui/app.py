# -*- coding: utf-8 -*-
r"""
Illustrious 官方风格界面（Gradio 前端 + ComfyUI 引擎后端）
============================================================
界面为 HuggingFace 官方 demo 风格（对话框 + 出图 + 锁角色编辑），
生成走本地 ComfyUI 引擎（模型已配好：Illustrious v1.0 原版 + 翻译器 + IPAdapter）。

前置：二次元引擎已启动（8188）—— start_anime.bat 或总菜单 [2]
启动：venv\Scripts\python.exe app.py
打开：http://127.0.0.1:7860
"""

import os
import sys
import time
import json
import urllib.request
from pathlib import Path

# 禁用系统代理（本地 ComfyUI 直连）
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["NO_PROXY"] = "*"

# 自动定位同级出图项目的 pipeline 脚本（illustrious_ui 与 Illustrious_ImageStudio 同属一个根目录）
# 也可用环境变量 ILL_SCRIPTS_DIR 覆盖指向其他安装位置
_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(os.environ.get("ILL_SCRIPTS_DIR", str(_ROOT / "Illustrious_ImageStudio" / "pipeline" / "scripts")))
for p in (_SCRIPTS, _SCRIPTS.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")


def _check_engine():
    """检查 ComfyUI 引擎是否在线"""
    try:
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
        with urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def generate(prompt, negative, width, height, steps, cfg, seed):
    """文生图（走 ComfyUI + Illustrious v1.0 + 中文翻译）"""
    if not _check_engine():
        raise RuntimeError("ComfyUI 引擎未启动！请先启动二次元引擎（start_anime.bat / 总菜单[2]）")
    import workbench_tools_anime as tools
    tools.COMFY_URL = COMFY_URL
    img = tools.anime_t2i(prompt, width=int(width), height=int(height),
                          steps=int(steps), cfg=float(cfg),
                          seed=int(seed) if int(seed) >= 0 else None)
    out = Path(__file__).parent / "outputs"
    out.mkdir(exist_ok=True)
    dest = out / f"t2i_{int(time.time())}.png"
    dest.write_bytes(Path(img).read_bytes())
    return str(dest)


def edit(image, prompt, negative, denoise, steps, cfg, seed):
    """锁角色编辑（ComfyUI + IPAdapter 强锁）"""
    if not _check_engine():
        raise RuntimeError("ComfyUI 引擎未启动！")
    import workbench_tools_anime as tools
    tools.COMFY_URL = COMFY_URL
    # 保存参考图
    ref = Path(__file__).parent / "outputs" / f"ref_{int(time.time())}.png"
    ref.parent.mkdir(exist_ok=True)
    image.save(str(ref))
    img = tools.anime_ipadapter(str(ref), prompt, width=832, height=1248,
                                steps=int(steps), cfg=float(cfg),
                                weight=0.85, seed=int(seed) if int(seed) >= 0 else None)
    out = Path(__file__).parent / "outputs"
    dest = out / f"i2i_{int(time.time())}.png"
    dest.write_bytes(Path(img).read_bytes())
    return str(dest)


def main():
    import gradio as gr

    with gr.Blocks(title="Illustrious XL v1.0", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🎨 Illustrious XL v1.0")
        gr.Markdown("二次元文生图 · 官方风格界面（ComfyUI 引擎，本地运行）")
        engine_status = gr.Markdown("⚠️ 检查引擎…")
        demo.load(lambda: "✅ 引擎在线" if _check_engine() else "❌ 引擎未启动！请先启动二次元引擎", None, engine_status)
        with gr.Tabs():
            with gr.Tab("🖼 文生图"):
                with gr.Row():
                    with gr.Column():
                        prompt = gr.Textbox(label="提示词（支持中文）", lines=3,
                                            placeholder="一个黑发少女，白色连衣裙，站在樱花树下，微笑")
                        neg = gr.Textbox(label="负面提示词（可留空）", lines=2,
                                         value="lowres, bad anatomy, bad hands, worst quality")
                        with gr.Row():
                            w = gr.Slider(512, 1536, value=832, step=64, label="宽度")
                            h = gr.Slider(512, 1536, value=1248, step=64, label="高度")
                        with gr.Row():
                            steps = gr.Slider(10, 60, value=28, step=1, label="步数")
                            cfg = gr.Slider(1, 15, value=7.0, step=0.5, label="CFG")
                            seed = gr.Number(value=-1, label="Seed(-1随机)")
                        btn = gr.Button("⚡ 生成", variant="primary")
                    with gr.Column():
                        out = gr.Image(label="结果")
                btn.click(generate, [prompt, neg, w, h, steps, cfg, seed], out)
            with gr.Tab("🎭 锁角色编辑"):
                with gr.Row():
                    with gr.Column():
                        ref = gr.Image(label="上传角色参考图", type="pil")
                        eprompt = gr.Textbox(label="编辑指令（支持中文）", lines=3,
                                             placeholder="让角色站起来，全身，站在山巅，保持发型和衣服不变")
                        eneg = gr.Textbox(label="负面提示词", lines=2,
                                          value="lowres, bad anatomy, bad hands, worst quality")
                        denoise = gr.Slider(0.3, 0.9, value=0.55, step=0.05, label="重绘强度(仅普通模式)")
                        esteps = gr.Slider(10, 60, value=28, step=1, label="步数")
                        ecfg = gr.Slider(1, 15, value=7.0, step=0.5, label="CFG")
                        eseed = gr.Number(value=-1, label="Seed(-1随机)")
                        ebtn = gr.Button("🎭 生成编辑", variant="primary")
                    with gr.Column():
                        eout = gr.Image(label="结果")
                ebtn.click(edit, [ref, eprompt, eneg, denoise, esteps, ecfg, eseed], eout)
    demo.launch(server_name="127.0.0.1", server_port=7860)


if __name__ == "__main__":
    main()
