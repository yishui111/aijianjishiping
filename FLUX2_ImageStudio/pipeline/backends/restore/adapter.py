# -*- coding: utf-8 -*-
"""
图片修复/动漫化后端适配器（超分 / 去水印 / 动漫化 img2img）
============================================================
- 通过 kwargs["mode"] 选择：
    upscale  超分（4x-UltraSharp）
    inpaint  去水印去物（SDXL + mask）
    anime    动漫化 img2img（Animagine XL，保留构图改风格）
- 工作流模板：restore_upscale_api.json / restore_inpaint_api.json / restore_anime_api.json
"""

from pathlib import Path
import json

from backends.base import ComfyBackend


class Adapter(ComfyBackend):

    backend_id = "restore"

    def build_workflow(self, *, prompt: str = "", negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       seconds: float = None, steps: int = None,
                       cfg: float = None, mode: str = "upscale",
                       mask: str = None, denoise: float = None,
                       **kwargs) -> dict:
        if image is None:
            raise ValueError("restore 需要 image 输入")
        if mode not in ("upscale", "inpaint", "anime"):
            raise ValueError(f"restore 未知模式: {mode}（upscale/inpaint/anime）")

        # anime 模式用专用工作流（Animagine img2img）
        if mode == "anime":
            wf = json.loads(
                (Path(__file__).resolve().parent.parent.parent / "config"
                 / "restore_anime_api.json").read_text(encoding="utf-8")
            )
            uploaded = self._upload_image(image)
            for nid, node in wf.items():
                if node.get("class_type") == "LoadImage":
                    node["inputs"]["image"] = uploaded
            # 正向提示词：动漫风格 + 保留内容
            pos_done = False
            for nid, node in wf.items():
                if node.get("class_type") == "CLIPTextEncode":
                    if not pos_done:
                        node["inputs"]["text"] = (
                            prompt or "masterpiece, best quality, very aesthetic, "
                            "Chinese donghua 2D animation style, detailed line art, "
                            "cel shading, vibrant colors, preserve composition and action"
                        )
                        pos_done = True
                elif node.get("class_type") == "KSampler":
                    node["inputs"]["seed"] = seed
                    if steps:
                        node["inputs"]["steps"] = steps
                    if cfg:
                        node["inputs"]["cfg"] = cfg
                    if denoise is not None:
                        node["inputs"]["denoise"] = denoise
            self._set_filename_prefix(wf)
            return wf

        wf = self._load_workflow()

        # 上传图片并挂到 LoadImage 节点
        uploaded = self._upload_image(image)
        for nid, node in wf.items():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = uploaded

        if mode == "upscale":
            self._set_filename_prefix(wf)
            return wf

        # inpaint：挂蒙版 + 提示词 + seed
        if mask:
            mask_uploaded = self._upload_image(mask)
            for nid, node in wf.items():
                if node.get("class_type") == "LoadMask":
                    node["inputs"]["mask"] = mask_uploaded
        pos_done = False
        for nid, node in wf.items():
            if node.get("class_type") == "CLIPTextEncode":
                if not pos_done:
                    node["inputs"]["text"] = prompt or "干净无瑕的自然修复"
                    pos_done = True
                elif negative_prompt:
                    node["inputs"]["text"] = negative_prompt
            elif node.get("class_type") == "KSampler":
                node["inputs"]["seed"] = seed
                if steps:
                    node["inputs"]["steps"] = steps
                if cfg:
                    node["inputs"]["cfg"] = cfg
        self._set_filename_prefix(wf)
        return wf

    def fetch_output(self):
        return super().fetch_output(suffix=".png")
