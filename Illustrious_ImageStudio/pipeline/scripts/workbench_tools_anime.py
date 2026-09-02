# -*- coding: utf-8 -*-
r"""
二次元出图工具（Illustrious XL v0.1）— 独立项目专用
====================================================
模型：illustriousXL_v01.safetensors（SDXL 架构完整 checkpoint，专攻二次元，角色质量强）
- anime_t2i(prompt)       文生图
- anime_img2img(图,prompt) 图生图/锁角色（denoise 低=保持角色，高=重绘）
工作流：CheckpointLoaderSimple + KSampler（标准 SDXL，兼容性最好）
"""

import json
import logging
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPTS_DIR.parent
for _p in (_PIPELINE_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backends.base import get_backend
from prompt_translator import translate_prompt

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
OUT_DIR = _PIPELINE_DIR / "output" / "tools"
LOG_DIR = _PIPELINE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "anime.log"), level=logging.INFO,
                    encoding="utf-8", format="%(asctime)s [%(levelname)s] %(message)s")

# ---- 模型常量 ----
# v1.0：原生支持 1536 高分辨率 + 自然语言理解（比 v0.1 强，中文长句也能听懂）
ANIME_CKPT = "Illustrious-XL-v1.0.safetensors"   # models/checkpoints/
ANIME_CLIP_VISION = "CLIP-ViT-bigG-14-laion2B-39B-b160k.safetensors"  # models/clip_vision/
ANIME_IPADAPTER = "ip-adapter-plus_sdxl_vit-h.safetensors"             # models/ipadapter/

NEG = ("lowres, bad anatomy, bad hands, text, error, missing fingers, cropped, "
       "worst quality, low quality, jpeg artifacts, signature, watermark, blurry, "
       "extra digits, fewer digits, bad feet")


def _log(m):
    logging.info(m)
    print(f"[Anime] {m}", flush=True)


def _log_err(m):
    logging.error(m)
    print(f"[Anime] 错误: {m}", flush=True)


def _save_output(b, suffix, tag):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        out = b.fetch_output(suffix=suffix)
    except TypeError:
        out = b.fetch_output()
    dest = OUT_DIR / f"{tag}_{uuid.uuid4().hex[:8]}{out.suffix}"
    dest.write_bytes(out.read_bytes())
    return dest


def _upload_image(b, image_path):
    """上传图片到 ComfyUI/input，返回文件名"""
    import shutil
    src = Path(image_path)
    in_dir = (_PIPELINE_DIR.parent / "ComfyUI_windows_portable" / "ComfyUI" / "input")
    in_dir.mkdir(parents=True, exist_ok=True)
    name = f"up_{uuid.uuid4().hex[:8]}{src.suffix}"
    shutil.copy2(str(src), str(in_dir / name))
    return name


def _wait_ok(b, pid, tag):
    res = b.wait(pid, timeout_s=1200)
    if res.get("status") != "success":
        _log_err(f"{tag}失败: {json.dumps(res, ensure_ascii=False)[:500]}")
        raise RuntimeError(f"{tag}失败: {json.dumps(res, ensure_ascii=False)[:400]}")


def anime_t2i(prompt, width=832, height=480, steps=25, cfg=7.0, seed=None):
    """二次元文生图（自动把中文翻译成 Danbooru 标签）"""
    prompt = translate_prompt(prompt)
    _log(f"文生图: {prompt[:120]}")
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ANIME_CKPT}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": NEG}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
            "latent_image": ["4", 0], "seed": seed or int(time.time() % 100000),
            "steps": steps, "cfg": cfg, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "anime_t2i"}},
    }
    b._set_filename_prefix(wf, node_id="7")
    pid = b.submit(wf)
    _wait_ok(b, pid, "二次元文生图")
    return _save_output(b, ".png", "anime_t2i")


def anime_img2img(image_path, prompt, denoise=0.55, width=832, height=480,
                  steps=25, cfg=7.0, seed=None):
    """二次元图生图/锁角色（自动把中文指令翻译成 Danbooru 标签）：
    denoise 0.45~0.6 = 保持角色外观改姿势/场景（锁角色）
    denoise 0.7~0.85 = 大幅重绘/换风格
    """
    prompt = translate_prompt(prompt)
    _log(f"图生图: {prompt[:120]}")
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ANIME_CKPT}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": NEG}},
        "4": {"class_type": "LoadImage",
              "inputs": {"image": _upload_image(b, image_path)}},
        "5": {"class_type": "ImageScale",
              "inputs": {"image": ["4", 0], "width": width, "height": height,
                         "upscale_method": "lanczos", "crop": "center"}},
        "6": {"class_type": "VAEEncode",
              "inputs": {"pixels": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
            "latent_image": ["6", 0], "seed": seed or int(time.time() % 100000),
            "steps": steps, "cfg": cfg, "sampler_name": "euler",
            "scheduler": "normal", "denoise": denoise}},
        "8": {"class_type": "VAEDecode",
              "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "anime_i2i"}},
    }
    b._set_filename_prefix(wf, node_id="9")
    pid = b.submit(wf)
    _wait_ok(b, pid, "二次元图生图")
    return _save_output(b, ".png", "anime_i2i")


def anime_ipadapter(image_path, prompt, width=832, height=480, steps=25,
                    cfg=7.0, weight=0.85, seed=None, denoise=1.0):
    """二次元锁角色（IPAdapter 特征注入，最强锁角色）：
    参考图特征注入采样（类似 1-image LoRA），构图/姿势由提示词全新决定。
    - 角色保持强度：weight 0.7~1.0（越高越像参考图）
    - "让角色站起来变全身"这类改姿势/构图指令 → 用这个函数
    - 效果：角色长相接近参考图 + 姿势/场景完全听指令
    """
    prompt = translate_prompt(prompt)
    _log(f"IPAdapter锁角色: {prompt[:120]} weight={weight}")
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ANIME_CKPT}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": NEG}},
        "4": {"class_type": "LoadImage",
              "inputs": {"image": _upload_image(b, image_path)}},
        # 统一加载器：PLUS 预设 = ip-adapter-plus_sdxl_vit-h + ViT-H clip vision
        "5": {"class_type": "IPAdapterUnifiedLoader",
              "inputs": {"model": ["1", 0], "preset": "PLUS (high strength)"}},
        "6": {"class_type": "IPAdapter",
              "inputs": {"model": ["5", 0], "ipadapter": ["5", 1],
                         "image": ["4", 0], "weight": weight,
                         "start_at": 0.0, "end_at": 1.0,
                         "weight_type": "standard"}},
        "7": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["6", 0], "positive": ["2", 0], "negative": ["3", 0],
            "latent_image": ["7", 0], "seed": seed or int(time.time() % 100000),
            "steps": steps, "cfg": cfg, "sampler_name": "euler",
            "scheduler": "normal", "denoise": denoise}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "anime_ipadapter"}},
    }
    b._set_filename_prefix(wf, node_id="10")
    pid = b.submit(wf)
    _wait_ok(b, pid, "IPAdapter锁角色")
    return _save_output(b, ".png", "anime_ipadapter")
