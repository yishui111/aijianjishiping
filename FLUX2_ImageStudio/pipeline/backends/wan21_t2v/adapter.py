# -*- coding: utf-8 -*-
"""
Wan2.1 T2V 1.3B 后端适配器（8G 机器保底）
==========================================
- 纯文生视频（1.3B 官方无 I2V）
- 工作流模板：config/wan21_t2v_api.json

节点约定：
  5  UNETLoader
  4  CLIPLoader
  10 VAELoader
  40 EmptyHunyuanLatentVideo（width/height/length）
  48 ModelSamplingSD3
  3  KSampler
  50 SaveVideo（filename_prefix 由基类设为 run_id）
"""

from backends.base import ComfyBackend


class Adapter(ComfyBackend):

    backend_id = "wan21_t2v"

    @staticmethod
    def calc_length(seconds: float, fps: int = 16) -> int:
        """Wan2.1 视频 VAE 时间压缩 4 倍，(length-1) 为 4 的倍数"""
        length = round(seconds * fps)
        return 1 + 4 * ((length - 1 + 3) // 4)

    def build_workflow(self, *, prompt: str, negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       seconds: float = None, steps: int = None,
                       cfg: float = None, **kwargs) -> dict:
        if image is not None:
            raise NotImplementedError("wan21_t2v 仅支持 T2V；I2V 请用 wan22 后端")
        wf = self._load_workflow()
        w = width or self.defaults.get("width", 832)
        h = height or self.defaults.get("height", 480)
        fps = self.defaults.get("fps", 16)

        # 提示词：第一个 CLIPTextEncode = 正向，其余 = 负向（模板惯例）
        pos_done = False
        for nid, node in wf.items():
            if node.get("class_type") == "CLIPTextEncode":
                if not pos_done:
                    node["inputs"]["text"] = prompt
                    pos_done = True
                elif negative_prompt:
                    node["inputs"]["text"] = negative_prompt

        # latent 尺寸/长度
        target_length = length
        if target_length is None and seconds:
            target_length = self.calc_length(seconds, fps)
        if target_length is None and "max_seconds" in self.defaults:
            target_length = self.calc_length(self.defaults["max_seconds"], fps)
        for nid, node in wf.items():
            if node.get("class_type") == "EmptyHunyuanLatentVideo":
                node["inputs"]["width"] = w
                node["inputs"]["height"] = h
                if target_length:
                    node["inputs"]["length"] = target_length

        # seed / steps / cfg
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
