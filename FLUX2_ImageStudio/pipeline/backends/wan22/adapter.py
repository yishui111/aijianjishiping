# -*- coding: utf-8 -*-
"""
Wan2.2 TI2V 5B 后端适配器
============================
- 一个模型同时支持：文生视频（T2V） / 图生视频（I2V）
- 判定规则：build_workflow 传入 image 参数即为 I2V，否则 T2V
- 工作流模板：config/wan22_ti2v_api.json（基于 ComfyUI 官方 5B 模板转换）

节点约定（与模板 wan22_ti2v_api.json 对应）：
  37 UNETLoader（模型名可用 GGUF / fp16，通过 kwargs.unet_name 覆盖）
  6/7 CLIPTextEncode 正/负向
  55 Wan22ImageToVideoLatent（width/height/length，可选 start_image）
  3  KSampler（seed/steps/cfg）
  47 SaveWEBM（filename_prefix 由基类设为本次 run_id，用于取回）
"""

from pathlib import Path

from backends.base import ComfyBackend


class Adapter(ComfyBackend):

    backend_id = "wan22"

    # ---- 帧数对齐：Wan2.2 5B 视频 VAE 时间压缩 4 倍，(length-1) 需为 4 的倍数 ----

    @staticmethod
    def calc_length(seconds: float, fps: int = 16) -> int:
        length = round(seconds * fps)
        return 1 + 4 * ((length - 1 + 3) // 4)

    # ---- 主入口 ----

    def build_workflow(self, *, prompt: str, negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       seconds: float = None, steps: int = None,
                       cfg: float = None, **kwargs) -> dict:
        wf = self._load_workflow()
        w = width or self.defaults.get("width", 832)
        h = height or self.defaults.get("height", 480)
        fps = self.defaults.get("fps", 16)

        # 1) UNETLoader：模型名可覆盖（fp16 / fp8 / GGUF q8）
        #    支持 UNETLoader / UnetLoaderGGUF 两种节点（按工作流模板决定）
        unet_name = kwargs.get("unet_name") or self.defaults.get("unet_name")
        if unet_name:
            for nid, node in wf.items():
                if node.get("class_type") in ("UNETLoader", "UnetLoaderGGUF"):
                    node["inputs"]["unet_name"] = unet_name

        # 2) 提示词：第一个 CLIPTextEncode = 正向，其余 = 负向（模板惯例）
        pos_done = False
        for nid, node in wf.items():
            if node.get("class_type") != "CLIPTextEncode":
                continue
            if not pos_done:
                node["inputs"]["text"] = prompt
                pos_done = True
            elif negative_prompt:
                node["inputs"]["text"] = negative_prompt

        # 3) latent 尺寸/长度
        #    优先级：length 显式 > seconds 换算 > max_seconds 兜底 > 模板默认
        target_length = length
        if target_length is None and seconds:
            target_length = self.calc_length(seconds, fps)
        if target_length is None and "max_seconds" in self.defaults:
            target_length = self.calc_length(self.defaults["max_seconds"], fps)
        for nid, node in wf.items():
            if node.get("class_type") == "Wan22ImageToVideoLatent":
                node["inputs"]["width"] = w
                node["inputs"]["height"] = h
                if target_length:
                    node["inputs"]["length"] = target_length

        # 4) KSampler
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

        # 5) I2V：上传图片 → 添加 LoadImage 节点 → 接到 Wan22ImageToVideoLatent.start_image
        if image is not None:
            uploaded = self._upload_image(image)
            load_id = "9999"
            wf[load_id] = {
                "class_type": "LoadImage",
                "inputs": {"image": uploaded, "upload": "image"},
            }
            for nid, node in wf.items():
                if node.get("class_type") == "Wan22ImageToVideoLatent":
                    node["inputs"]["start_image"] = [load_id, 0]

        # 6) 输出节点 filename_prefix = run_id（取回用）
        self._set_filename_prefix(wf)
        return wf

    # ---- 取回 ----

    def fetch_output(self):
        """SaveVideo 输出 .mp4（h264 硬件编码，比 vp9 快）；兜底尝试 .webm"""
        try:
            return super().fetch_output(suffix=".mp4")
        except RuntimeError:
            return super().fetch_output(suffix=".webm")
