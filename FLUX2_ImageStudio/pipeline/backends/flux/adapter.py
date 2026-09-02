# -*- coding: utf-8 -*-
"""
FLUX.1-schnell 文生图后端适配器（关键帧升级）
==============================================
- 单文件 checkpoint（flux1-schnell-fp8.safetensors，含 unet+clip+vae）
- 4 步出图（蒸馏版），画质远超 SDXL，8G 显卡可跑（fp8 单文件 ~7.2GB，8G 边缘可 offload）
- 工作流模板：config/flux_t2i_api.json

节点约定：
  4  CheckpointLoaderSimple（单文件 checkpoint）
  6  CLIPTextEncode（正向；FLUX cfg=1 无负面，负向复用同节点）
  5  EmptySD3LatentImage
  3  KSampler（steps=4, cfg=1.0, euler+simple）
  9  SaveImage（filename_prefix=run_id）
"""

from backends.base import ComfyBackend


class Adapter(ComfyBackend):

    backend_id = "flux"

    def build_workflow(self, *, prompt: str, negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       seconds: float = None, steps: int = None,
                       cfg: float = None, **kwargs) -> dict:
        if image is not None:
            raise NotImplementedError("flux 后端仅支持文生图（T2I）")
        wf = self._load_workflow()

        # 提示词：第一个 CLIPTextEncode = 正向
        pos_done = False
        for nid, node in wf.items():
            if node.get("class_type") == "CLIPTextEncode":
                if not pos_done:
                    node["inputs"]["text"] = prompt
                    pos_done = True

        # 尺寸
        for nid, node in wf.items():
            if node.get("class_type") == "EmptySD3LatentImage":
                node["inputs"]["width"] = width or self.defaults.get("width", 768)
                node["inputs"]["height"] = height or self.defaults.get("height", 448)

        # KSampler（FLUX schnell: 4 步, cfg 1.0）
        for nid, node in wf.items():
            if node.get("class_type") == "KSampler":
                node["inputs"]["seed"] = seed
                node["inputs"]["steps"] = steps or self.defaults.get("steps", 4)
                node["inputs"]["cfg"] = cfg or self.defaults.get("cfg", 1.0)

        self._set_filename_prefix(wf)
        return wf
