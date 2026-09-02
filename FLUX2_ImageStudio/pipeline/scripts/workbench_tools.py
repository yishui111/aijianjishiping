# -*- coding: utf-8 -*-
r"""
工作台能力工具模块 — 5 类模型解耦封装
======================================
每个能力一个独立函数，互不依赖，可单独调用（CLI 或 import）：

  1. remove_bg(图片)     抠图        → ComfyUI BiRefNet
  2. generate_image(描述) 文生图      → animagine（动漫高质量）
  3. img2img(图,描述)     图生图      → SDXL 重绘/换风格
  4. generate_video(描述,图?) 图生视频 → Wan2.2
  5. tts(文本)           文字转语音   → edge-tts（免费在线）

CLI 用法：
  python workbench_tools.py remove_bg <图片路径>
  python workbench_tools.py generate_image <提示词>
  python workbench_tools.py img2img <图片路径> <提示词> [denoise]
  python workbench_tools.py generate_video <提示词> [图片路径]
  python workbench_tools.py tts <文本> [输出.mp3]
"""

import json
import logging
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

# 本机服务（ComfyUI/Ollama）直连，禁用系统代理（否则 127.0.0.1 被代理拦截）
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPTS_DIR.parent
for _p in (_PIPELINE_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backends.base import get_backend

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
OUT_DIR = _PIPELINE_DIR / "output" / "tools"
NEG = ("lowres, bad anatomy, bad hands, text, error, missing fingers, cropped, "
       "worst quality, low quality, jpeg artifacts, signature, watermark, blurry")

# ---- 工具日志（与工作台日志同文件） ----
LOG_DIR = _PIPELINE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "chat_workbench.log"), level=logging.INFO,
                    encoding="utf-8", format="%(asctime)s [%(levelname)s] %(message)s")


def _log(msg):
    logging.info(msg)


def _log_err(msg):
    logging.error(msg)


def _save_output(b, suffix, tag):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        out = b.fetch_output(suffix=suffix)
    except TypeError:
        out = b.fetch_output()  # 部分后端（wan22）覆写了无 suffix 参数版本
    dest = OUT_DIR / f"{tag}_{uuid.uuid4().hex[:8]}{out.suffix}"
    dest.write_bytes(out.read_bytes())
    return dest


# ---------- 1. 抠图 ----------
def remove_bg(image_path, ckpt="birefnet-general.safetensors"):
    """抠图：返回透明背景 PNG"""
    b = get_backend("sdxl", server_url=COMFY_URL)  # 复用基类 submit/wait/fetch
    wf = {
        "1": {"class_type": "LoadImage",
              "inputs": {"image": _upload_image(b, image_path)}},
        "2": {"class_type": "LoadBackgroundRemovalModel",
              "inputs": {"bg_removal_name": ckpt}},
        "3": {"class_type": "RemoveBackground",
              "inputs": {"image": ["1", 0], "bg_removal_model": ["2", 0]}},  # 输出 MASK
        "4": {"class_type": "JoinImageWithAlpha",
              "inputs": {"image": ["1", 0], "alpha": ["3", 0]}},  # 合成透明图
        "5": {"class_type": "SaveImageWithAlpha",
              "inputs": {"images": ["4", 0], "mask": ["3", 0],
                         "filename_prefix": "rmbg"}},
    }
    b._set_filename_prefix(wf, node_id="5")
    pid = b.submit(wf)
    _wait_ok(b, pid, "抠图")
    return _save_output(b, ".png", "rmbg")


# ---------- 2. 文生图 ----------
def generate_image(prompt, ckpt="animagine-xl-3.1.safetensors", seed=None,
                   width=832, height=480, steps=30, cfg=7.0):
    """文生图：返回 PNG 路径"""
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": NEG}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
            "latent_image": ["4", 0], "seed": seed or int(time.time() % 100000),
            "steps": steps, "cfg": cfg, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": "t2i"}},
    }
    b._set_filename_prefix(wf, node_id="7")
    pid = b.submit(wf)
    _wait_ok(b, pid, "文生图")
    return _save_output(b, ".png", "t2i")


# ---------- 3. 图生图 ----------
def img2img(image_path, prompt, denoise=0.6, ckpt="animagine-xl-3.1.safetensors",
            seed=None, width=832, height=480):
    """图生图：以图为基础重绘/换风格，denoise 越大变化越大"""
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": NEG}},
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
            "steps": 25, "cfg": 7.0, "sampler_name": "euler",
            "scheduler": "normal", "denoise": denoise}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"images": ["8", 0], "filename_prefix": "i2i"}},
    }
    b._set_filename_prefix(wf, node_id="9")
    pid = b.submit(wf)
    _wait_ok(b, pid, "图生图")
    return _save_output(b, ".png", "i2i")


# ---------- 4. 图生视频 ----------
def generate_video(prompt, image_path=None, seconds=2.0, width=832, height=480,
                   steps=30, cfg=6.0, seed=None):
    """图生视频（有参考图）或文生视频（无图）
    质量优化：steps 30（画质更稳）+ cfg 6.0（Wan2.2 官方范围）
    提示词应由调用方保证'轻微自然运动'（剧烈运动易闪烁）"""
    b = get_backend("wan22", server_url=COMFY_URL)
    wf = b.build_workflow(prompt=prompt, seed=seed or int(time.time() % 100000),
                          image=str(image_path) if image_path else None,
                          width=width, height=height,
                          length=1 + 4 * ((round(seconds * 24) - 1 + 3) // 4),
                          steps=steps, cfg=cfg)
    for nid, node in wf.items():
        if node.get("class_type") == "CreateVideo":
            node["inputs"]["fps"] = 24
    pid = b.submit(wf)
    _wait_ok(b, pid, "视频")
    return _save_output(b, ".mp4", "video")


# ---------- 5. 文字转语音 ----------
def tts(text, out_path=None, voice="zh-CN-XiaoxiaoNeural"):
    """文字转语音（edge-tts，免费在线）"""
    import asyncio
    import edge_tts
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(out_path) if out_path else OUT_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"
    async def _run():
        c = edge_tts.Communicate(text, voice)
        await c.save(str(dest))
    asyncio.run(_run())
    return dest


# ---------- 6. 文生视频（纯文字，无参考图） ----------
def t2v(prompt, seconds=2.0, width=832, height=480, steps=20, seed=None):
    """文生视频：不依赖图片，直接由文字生成"""
    return generate_video(prompt, image_path=None, seconds=seconds,
                          width=width, height=height, steps=steps, seed=seed)


# ---------- 7. 图片超分（4x） ----------
def upscale(image_path, model="4x-UltraSharp.pth"):
    """图片 4 倍超分辨率放大"""
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "LoadImage",
              "inputs": {"image": _upload_image(b, image_path)}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel",
              "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage",
              "inputs": {"images": ["3", 0], "filename_prefix": "upscale"}},
    }
    b._set_filename_prefix(wf, node_id="4")
    pid = b.submit(wf)
    _wait_ok(b, pid, "超分")
    return _save_output(b, ".png", "upscale")


# ---------- 8. 视频合成（ffmpeg 拼接） ----------
def concat_videos(video_paths, out_path=None):
    """把多个 mp4 拼接成一个视频（顺序拼接）"""
    if not video_paths:
        raise RuntimeError("没有可拼接的视频")
    import subprocess
    ffmpeg = _PIPELINE_DIR.parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not ffmpeg.exists():
        ffmpeg = Path("ffmpeg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(out_path) if out_path else OUT_DIR / f"concat_{uuid.uuid4().hex[:8]}.mp4"
    list_file = OUT_DIR / f"concat_{uuid.uuid4().hex[:8]}.txt"
    list_file.write_text("".join(f"file '{p}'\n" for p in video_paths), encoding="utf-8")
    r = subprocess.run([str(ffmpeg), "-y", "-f", "concat", "-safe", "0",
                        "-i", str(list_file), "-c", "copy", str(dest)],
                       capture_output=True, text=True)
    list_file.unlink(missing_ok=True)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"拼接失败: {r.stderr[-300:]}")
    return dest


# ---------- 9. 骨架跳舞（Wan2.2 Fun Control，GGUF 量化版） ----------
FUN_CONTROL_MODEL = "Wan2.2-Fun-5B-Control-Q5_K_M.gguf"


# ---------- 10. 视频转 GIF（ffmpeg） ----------
def video_to_gif(video_path, fps=12, width=None):
    """把视频转成 GIF 动图"""
    import subprocess
    ffmpeg = _PIPELINE_DIR.parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not ffmpeg.exists():
        ffmpeg = Path("ffmpeg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"gif_{uuid.uuid4().hex[:8]}.gif"
    cmd = [str(ffmpeg), "-y", "-i", str(video_path), "-vf",
           f"fps={fps}" + (f",scale={width}:-1" if width else ""),
           str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"GIF 转换失败: {r.stderr[-200:]}")
    return dest


# ---------- 11. 本地配音（Piper，离线） ----------
def piper_tts(text, out_path=None, voice="zh_CN-huayan-medium"):
    """本地离线 TTS（Piper）。未安装时抛错提示下载方法。"""
    try:
        import piper
    except ImportError:
        raise RuntimeError(
            "本地配音需要安装 Piper：\n"
            "1. pip install piper-tts（在有网环境执行）\n"
            "2. 下载中文模型 zh_CN-huayan-medium.onnx（约60MB），"
            "放到 pipeline\\models\\piper\\ 目录\n"
            "装好后重启工作台即可用")
    model_dir = _PIPELINE_DIR / "models" / "piper"
    onnx = model_dir / f"{voice}.onnx"
    if not onnx.exists():
        raise RuntimeError(
            f"缺少 Piper 中文模型：{onnx.name}（约60MB）\n"
            f"下载后放到 pipeline\\models\\piper\\ 即可")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(out_path) if out_path else OUT_DIR / f"tts_local_{uuid.uuid4().hex[:8]}.wav"
    import wave
    import piper
    voice = piper.PiperVoice.load(str(onnx))
    with wave.open(str(dest), "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return dest


# ---------- 12. 音视频合成（ffmpeg） ----------
def merge_av(video_path, audio_path, out_path=None):
    """把音频合成进视频（视频+配音 = 成片）"""
    import subprocess
    ffmpeg = _PIPELINE_DIR.parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not ffmpeg.exists():
        ffmpeg = Path("ffmpeg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(out_path) if out_path else OUT_DIR / f"final_{uuid.uuid4().hex[:8]}.mp4"
    cmd = [str(ffmpeg), "-y", "-i", str(video_path), "-i", str(audio_path),
           "-c:v", "copy", "-c:a", "aac", "-shortest", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"合成失败: {r.stderr[-200:]}")
    return dest


# ---------- 13. 镜头运动（Ken Burns：静止图 + 镜头推拉摇移，纯 ffmpeg 不生成内容） ----------
CAMERA_MOTIONS = {
    "zoom_in":  {"name": "推近", "vf": "scale=8000:-1,zoompan=z='min(zoom+0.0010,1.30)':d={d}:s={w}x{h}:fps={fps}"},
    "zoom_out": {"name": "拉远", "vf": "scale=8000:-1,zoompan=z='if(lte(zoom,1.0),1.30,max(1.001,zoom-0.0010))':d={d}:s={w}x{h}:fps={fps}"},
    "pan_left": {"name": "左移", "vf": "scale=8000:-1,zoompan=z='1.15':x='(iw-iw/zoom)*on/({d}-1)':d={d}:s={w}x{h}:fps={fps}"},
    "pan_right":{"name": "右移", "vf": "scale=8000:-1,zoompan=z='1.15':x='(iw-iw/zoom)*(1-on/({d}-1))':d={d}:s={w}x{h}:fps={fps}"},
    "pan_up":   {"name": "上移", "vf": "scale=8000:-1,zoompan=z='1.15':y='(ih-ih/zoom)*on/({d}-1)':d={d}:s={w}x{h}:fps={fps}"},
    "pan_down": {"name": "下移", "vf": "scale=8000:-1,zoompan=z='1.15':y='(ih-ih/zoom)*(1-on/({d}-1))':d={d}:s={w}x{h}:fps={fps}"},
    "rotate":   {"name": "微旋转", "vf": "scale=4000:-1,rotate=a='0.02*min(t,4)':ow=iw:oh=ih:c=black@0,zoompan=z='1.06':d={d}:s={w}x{h}:fps={fps}"},
}


# ---------- 14. FLUX.2 Klein 文生图（2026 轻量旗舰，画质代差级） ----------
# 模型精度可选：Q8_0（画质最高，16G 显卡用）/ Q5_K_M（8G 显卡用）
# 通过环境变量 FLUX2_QUANT 切换，默认 Q8_0（本机 16G 最佳）
_FLUX2_QUANT = os.environ.get("FLUX2_QUANT", "Q8_0")
FLUX2_UNET = f"flux-2-klein-base-9b-{_FLUX2_QUANT}.gguf"
FLUX2_CLIP = "qwen_3_8b_fp8mixed.safetensors"  # FLUX.2 Klein 9B 专用 Qwen3-8B 编码器（12288 维，CPU 推理省显存）
FLUX2_VAE = "flux2-vae.safetensors"


def flux2_t2i(prompt, width=832, height=480, steps=20, cfg=3.5, seed=None):
    """FLUX.2 Klein 文生图（高画质；后续可加 image_edit 锁角色）"""
    b = get_backend("sdxl", server_url=COMFY_URL)
    wf = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": FLUX2_UNET}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": FLUX2_CLIP, "type": "flux2", "device": "cpu"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "4": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["3", 0]}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX2_VAE}},
        "6": {"class_type": "EmptyFlux2LatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "Flux2Scheduler",
              "inputs": {"steps": steps, "width": width, "height": height}},
        "8": {"class_type": "RandomNoise",
              "inputs": {"noise_seed": seed or int(time.time() % 100000)}},
        "9": {"class_type": "CFGGuider", "inputs": {
            "model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "cfg": cfg}},
        "10": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["8", 0], "guider": ["9", 0], "sampler": ["10", 0],
            "sigmas": ["7", 0], "latent_image": ["6", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["5", 0]}},
        "13": {"class_type": "SaveImage",
               "inputs": {"images": ["12", 0], "filename_prefix": "flux2"}},
    }
    b._set_filename_prefix(wf, node_id="13")
    pid = b.submit(wf)
    _wait_ok(b, pid, "FLUX2")
    return _save_output(b, ".png", "flux2")


def flux2_image_edit(image_path, prompt, steps=24, cfg=5.0, seed=None):
    """FLUX.2 Klein 图像编辑/锁角色：
    参考图（角色设定图）作为 ReferenceLatent 条件绑定，
    在保持角色外观的前提下按 prompt 修改/生成新画面。
    官方模板 image_flux2_klein_image_edit_9b_base 结构：
    参考图 → ImageScaleToTotalPixels → (GetImageSize 定尺寸 + VAEEncode 取 latent)
    → 正/负条件各接一个 ReferenceLatent(conditioning, latent) → CFGGuider → 采样。
    """
    b = get_backend("sdxl", server_url=COMFY_URL)
    ref = _upload_image(b, image_path)
    wf = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": FLUX2_UNET}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": FLUX2_CLIP, "type": "flux2", "device": "cpu"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX2_VAE}},
        "6": {"class_type": "LoadImage", "inputs": {"image": ref}},
        "7": {"class_type": "ImageScaleToTotalPixels",
              "inputs": {"image": ["6", 0], "upscale_method": "lanczos",
                         "megapixels": 1.0, "resolution_steps": 1}},
        "8": {"class_type": "GetImageSize", "inputs": {"image": ["7", 0]}},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["7", 0], "vae": ["5", 0]}},
        "10": {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": ["3", 0], "latent": ["9", 0]}},
        "11": {"class_type": "ReferenceLatent", "inputs": {
            "conditioning": ["4", 0], "latent": ["9", 0]}},
        "12": {"class_type": "Flux2Scheduler", "inputs": {
            "steps": steps, "width": ["8", 0], "height": ["8", 1]}},
        "13": {"class_type": "EmptyFlux2LatentImage", "inputs": {
            "width": ["8", 0], "height": ["8", 1], "batch_size": 1}},
        "14": {"class_type": "CFGGuider", "inputs": {
            "model": ["1", 0], "positive": ["10", 0], "negative": ["11", 0], "cfg": cfg}},
        "15": {"class_type": "RandomNoise",
               "inputs": {"noise_seed": seed or int(time.time() % 100000)}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "17": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["15", 0], "guider": ["14", 0], "sampler": ["16", 0],
            "sigmas": ["12", 0], "latent_image": ["13", 0]}},
        "18": {"class_type": "VAEDecode", "inputs": {"samples": ["17", 0], "vae": ["5", 0]}},
        "19": {"class_type": "SaveImage",
               "inputs": {"images": ["18", 0], "filename_prefix": "flux2_edit"}},
    }
    b._set_filename_prefix(wf, node_id="19")
    pid = b.submit(wf)
    _wait_ok(b, pid, "FLUX2编辑")
    return _save_output(b, ".png", "flux2_edit")


def camera_shot(image_path, motion="zoom_in", duration=4.0, width=832, height=480, fps=24):
    """Ken Burns 镜头运动：静止图片 + 镜头推拉摇移（纯 ffmpeg，画面内容不变，不会穿模）
    motion: zoom_in/zoom_out/pan_left/pan_right/pan_up/pan_down/rotate
    """
    import subprocess
    if motion not in CAMERA_MOTIONS:
        raise RuntimeError(f"未知镜头: {motion}，可选 {list(CAMERA_MOTIONS.keys())}")
    ffmpeg = _PIPELINE_DIR.parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not ffmpeg.exists():
        ffmpeg = Path("ffmpeg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"cam_{motion}_{uuid.uuid4().hex[:6]}.mp4"
    frames = int(duration * fps)
    vf = CAMERA_MOTIONS[motion]["vf"].format(d=frames, w=width, h=height, fps=fps)
    cmd = [str(ffmpeg), "-y", "-loop", "1", "-i", str(image_path),
           "-vf", vf, "-t", f"{duration:.2f}", "-r", str(fps),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        _log_err(f"镜头[{motion}]失败: {r.stderr[-250:]}")
        raise RuntimeError(f"镜头生成失败: {r.stderr[-200:]}")
    _log(f"镜头[{motion}] 完成 耗时{duration:.1f}s {width}x{height}")
    return dest


def fun_control(person_image, control_video, prompt, width=640, height=384,
                length=49, steps=20, seed=None):
    """骨架/动作控制：人物按指定动作视频跳舞。
    person_image: 人物图；control_video: 动作参考视频（骨架/边缘）。
    需要模型 wan2.2_fun_control_5B_bf16.safetensors（未下载会提示）。"""
    model_dir = (_PIPELINE_DIR.parent / "ComfyUI_windows_portable" / "ComfyUI"
                 / "models" / "diffusion_models" / FUN_CONTROL_MODEL)
    if not model_dir.exists():
        raise RuntimeError(
            f"骨架跳舞需要模型 {FUN_CONTROL_MODEL}（GGUF 量化版，约4.6GB），"
            f"下载后放入 ComfyUI\\models\\diffusion_models\\ 即可。"
            f"下载地址见《骨架跳舞_FunControl_部署说明.md》")
    b = get_backend("wan22", server_url=COMFY_URL)
    up = _upload_image(b, person_image)
    # 控制视频截取到生成所需帧数（防止 Canny 处理整个长视频 OOM）
    clip = _clip_control_video(control_video, length)
    cv = _upload_video(b, clip)
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": up}},
        "2": {"class_type": "LoadVideo", "inputs": {"file": cv}},
        "3": {"class_type": "GetVideoComponents", "inputs": {"video": ["2", 0]}},
        "4": {"class_type": "Canny", "inputs": {"image": ["3", 0], "low_threshold": 0.1, "high_threshold": 0.6}},
        "5": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": FUN_CONTROL_MODEL}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["5", 0], "shift": 8.0}},
        "7": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                         "type": "wan", "device": "cpu"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": prompt}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["7", 0], "text": "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，丑陋的，畸形的，多余的手指，静止不动的画面"}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "11": {"class_type": "Wan22FunControlToVideo",
               "inputs": {"positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0],
                          "ref_image": ["1", 0], "control_video": ["4", 0],
                          "width": width, "height": height, "length": length, "batch_size": 1}},
        "12": {"class_type": "KSampler", "inputs": {
            "model": ["6", 0], "positive": ["11", 0], "negative": ["11", 1],
            "latent_image": ["11", 2], "seed": seed or int(time.time() % 100000),
            "steps": steps, "cfg": 6.0, "sampler_name": "uni_pc",
            "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["10", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": 24}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0],
               "filename_prefix": "dance", "format": "mp4", "codec": "auto"}},
    }
    b._set_filename_prefix(wf, node_id="15")
    pid = b.submit(wf)
    _wait_ok(b, pid, "骨架跳舞")
    return _save_output(b, ".mp4", "dance")


def _clip_control_video(video_path, length, fps=24):
    """把控制视频截取到 length 帧（ffmpeg），防止 Canny 处理整段长视频 OOM"""
    import subprocess
    ffmpeg = _PIPELINE_DIR.parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if not ffmpeg.exists():
        ffmpeg = Path("ffmpeg")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUT_DIR / f"ctrl_{uuid.uuid4().hex[:8]}.mp4"
    secs = max(length / fps, 0.5)
    r = subprocess.run(
        [str(ffmpeg), "-y", "-i", str(video_path), "-t", f"{secs:.2f}",
         "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)],
        capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        _log_err(f"控制视频截取失败: {r.stderr[-200:]}")
        raise RuntimeError(f"控制视频截取失败: {r.stderr[-200:]}")
    _log(f"控制视频已截取到 {secs:.1f}s（{length} 帧）")
    return dst


def _upload_video(b, video_path):
    """把视频直接拷贝到 ComfyUI input 目录（同机文件拷贝，比 HTTP 上传稳定）"""
    import shutil
    p = Path(video_path)
    if not p.exists():
        raise FileNotFoundError(f"视频不存在: {p}")
    comfy_input = _PIPELINE_DIR.parent / "ComfyUI_windows_portable" / "ComfyUI" / "input"
    comfy_input.mkdir(parents=True, exist_ok=True)
    dst = comfy_input / p.name
    shutil.copy2(str(p), str(dst))
    _log(f"视频已放入 ComfyUI input: {dst.name}")
    return dst.name


# ---------- 内部工具 ----------
def _upload_image(b, image_path):
    """上传图片到 ComfyUI，返回服务器端文件名"""
    return b._upload_image(str(image_path))


def _wait_ok(b, pid, tag):
    res = b.wait(pid)
    if res["status"] != "success":
        _log_err(f"{tag} 失败: {res}")
        raise RuntimeError(f"{tag}失败: {res}")
    _log(f"工具[{tag}] OK prompt={pid[:8]}")


def _timed(tag, fn, *args, **kwargs):
    """带耗时与日志地执行工具函数"""
    t0 = time.time()
    _log(f"工具[{tag}] 开始 args={[str(a)[:40] for a in args]}")
    try:
        out = fn(*args, **kwargs)
        _log(f"工具[{tag}] 完成 耗时{time.time() - t0:.0f}s -> {out.name}")
        return out
    except Exception as e:
        _log_err(f"工具[{tag}] 异常: {e}")
        raise


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    if cmd == "remove_bg" and len(args) >= 2:
        print("抠图结果:", remove_bg(args[1]))
    elif cmd == "generate_image" and len(args) >= 2:
        print("文生图结果:", generate_image(args[1]))
    elif cmd == "img2img" and len(args) >= 3:
        d = float(args[3]) if len(args) > 3 else 0.6
        print("图生图结果:", img2img(args[1], args[2], denoise=d))
    elif cmd == "generate_video" and len(args) >= 2:
        img = args[2] if len(args) > 2 else None
        print("视频结果:", generate_video(args[1], img))
    elif cmd == "tts" and len(args) >= 2:
        out = args[2] if len(args) > 2 else None
        print("配音结果:", tts(args[1], out))
    else:
        print(__doc__)
