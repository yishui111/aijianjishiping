"""Chinese-CLIP 封装：图像/文本 → 512 维向量（中文语义对齐）。

用途：
- 镜头画面向量化（analyzer 分析时每镜头编码关键帧）
- 中文查询文字向量化（planner 检索时编码查询词，与画面向量比对）
- 重复镜头检测（画面向量余弦相似度）

依赖：torch(cpu) + transformers，模型权重离线放置于 models/chinese-clip/。
模型：OFA-Sys/chinese-clip-vit-base-patch16（中文 CLIP，对中文文字→画面对齐有效）。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np

_MODEL_DIR = os.environ.get(
    "CHINESE_CLIP_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "models" / "chinese-clip"),
)
_lock = threading.Lock()
_clip = None  # (model, processor) 懒加载单例
_VEC_DIM = 512


def _load():
    global _clip
    if _clip is not None:
        return _clip
    with _lock:
        if _clip is not None:
            return _clip
        import torch
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

        model = ChineseCLIPModel.from_pretrained(_MODEL_DIR, local_files_only=True)
        model.eval()
        # CPU 推理：半精度加速且省内存（x86 不支持 fp16 时自动退回 fp32）
        try:
            model = model.half()
        except Exception:
            pass
        processor = ChineseCLIPProcessor.from_pretrained(_MODEL_DIR, local_files_only=True)
        _clip = (model, processor)
        print(f"[clip] Chinese-CLIP 已加载（{_MODEL_DIR}），设备 CPU，维度 {_VEC_DIM}")
        return _clip


def _norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / (n + 1e-9)


def image_embed(images: list[np.ndarray]) -> list[np.ndarray]:
    """编码图像（BGR ndarray 列表，OpenCV 帧）→ 512 维归一化向量列表。"""
    if not images:
        return []
    try:
        import torch

        model, processor = _load()
        rgb = [cv2_bgr2rgb(im) for im in images]
        inputs = processor(images=rgb, return_tensors="pt")
        with torch.no_grad():
            feats = model.get_image_features(**inputs)
        feats = _pool(feats).float().numpy()
        return [vec for vec in _norm(feats)]
    except Exception as exc:
        print(f"[clip] 图像编码失败: {exc}")
        return []


def text_embed(texts: list[str]) -> list[np.ndarray]:
    """编码中文文本 → 512 维归一化向量列表。"""
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return []
    try:
        import torch

        model, processor = _load()
        inputs = processor(text=texts, return_tensors="pt", padding=True)
        with torch.no_grad():
            feats = model.get_text_features(**inputs)
        feats = _pool(feats).float().numpy()
        return [vec for vec in _norm(feats)]
    except Exception as exc:
        print(f"[clip] 文本编码失败: {exc}")
        return []


def _pool(feats):
    """transformers 5.x 的 get_*_features 返回 ModelOutput 对象，兼容取池化向量。"""
    if hasattr(feats, "pooler_output"):
        return feats.pooler_output
    if hasattr(feats, "last_hidden_state"):
        return feats.last_hidden_state[:, 0, :]
    if isinstance(feats, tuple) and feats:
        return feats[0]
    return feats


def text_embed_single(text: str) -> np.ndarray | None:
    """单句文本编码，失败返回 None。"""
    vs = text_embed([text])
    return vs[0] if vs else None


def cv2_bgr2rgb(img: np.ndarray) -> np.ndarray:
    if img is None:
        return np.zeros((224, 224, 3), dtype=np.uint8)
    if img.ndim == 2:  # 灰度图转 3 通道
        import cv2

        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img[:, :, ::-1].copy()  # BGR → RGB


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or len(a) != len(b):
        return 0.0
    return float(np.dot(a, b))  # 已归一化，点积即余弦


def is_ready() -> bool:
    """CLIP 模型是否就绪（懒加载前返回 False）。"""
    return _clip is not None
