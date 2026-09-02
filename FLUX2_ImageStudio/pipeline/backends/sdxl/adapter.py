# -*- coding: utf-8 -*-
"""
SDXL 文生图后端适配器
======================
- 剧本提示词 → 高质量关键帧（T2I），供 wan22 I2V 使用
- 8G 显卡可流畅运行（约 3.5GB 权重）
- 模型文件：ComfyUI/models/checkpoints/sdxl_base_1.0.safetensors
  （或把模板 config/sdxl_t2i_api.json 里的 ckpt_name 改成你实际的文件名）

节点约定（与模板 config/sdxl_t2i_api.json 对应）：
  4  CheckpointLoaderSimple
  6/7 CLIPTextEncode 正/负向
  5  EmptyLatentImage（width/height）
  3  KSampler
  9  SaveImage（filename_prefix 由基类设为本次 run_id，输出 .png）
"""

from backends.base import ComfyBackend


class Adapter(ComfyBackend):

    backend_id = "sdxl"

    def build_workflow(self, *, prompt: str, negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       seconds: float = None, steps: int = None,
                       cfg: float = None, **kwargs) -> dict:
        if image is not None:
            raise NotImplementedError("sdxl 后端仅支持文生图（T2I），不支持图生图")
        wf = self._load_workflow()

        # 提示词：第一个 CLIPTextEncode = 正向，其余 = 负向（模板惯例）
        pos_done = False
        for nid, node in wf.items():
            if node.get("class_type") == "CLIPTextEncode":
                if not pos_done:
                    node["inputs"]["text"] = prompt
                    pos_done = True
                elif negative_prompt:
                    node["inputs"]["text"] = negative_prompt

        # 尺寸
        for nid, node in wf.items():
            if node.get("class_type") == "EmptyLatentImage":
                node["inputs"]["width"] = width or self.defaults.get("width", 832)
                node["inputs"]["height"] = height or self.defaults.get("height", 480)

        # KSampler
        for nid, node in wf.items():
            if node.get("class_type") == "KSampler":
                node["inputs"]["seed"] = seed
                if steps:
                    node["inputs"]["steps"] = steps
                elif "steps" in self.defaults:
                    node["inputs"]["steps"] = self.defaults["steps"]
                if cfg:
                    node["inputs"]["cfg"] = cfg
                elif "cfg" in self.defaults:
                    node["inputs"]["cfg"] = self.defaults["cfg"]

        self._set_filename_prefix(wf)
        return wf
