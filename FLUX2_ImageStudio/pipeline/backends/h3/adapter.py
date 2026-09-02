# -*- coding: utf-8 -*-
"""
MiniMax H3 后端适配器
======================
- 文生视频 T2V，输出自带音轨（项目 16G 机器主力）
- 逻辑从原 scripts/video_gen.py 迁移：保持 H3 工作流的节点改写行为不变

节点约定（与模板 config/h3_t2v_api.json 对应）：
  104 MiniMaxH3ImageToVideo（prompt / width / height / length）
  15  RandomNoise（noise_seed）
  9   BasicScheduler（steps）
  92  SaveVideo（filename_prefix 由基类设为本次 run_id）
"""

from backends.base import ComfyBackend


class Adapter(ComfyBackend):

    backend_id = "h3"

    # ---- 帧数换算：H3 24fps，length 对齐 17 网格（原 video_gen 逻辑） ----

    @staticmethod
    def calc_length(duration_s: float, fps: int = 24) -> int:
        length = round(duration_s * fps)
        length = length + (17 - (length % 17)) % 17
        return length

    def build_workflow(self, *, prompt: str, negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       seconds: float = None, steps: int = None,
                       cfg: float = None, **kwargs) -> dict:
        if image is not None:
            raise NotImplementedError(
                "h3 后端当前仅支持 T2V；I2V 请用 wan22 后端（8G 机器路线）"
            )
        wf = self._load_workflow()
        fps = self.defaults.get("fps", 24)

        # 1) 提示词：第一个文本节点（prompt 或 text 字段）为正向
        pos_set = False
        for nid, node in wf.items():
            inputs = node.get("inputs", {})
            for field in ("prompt", "text"):
                if field in inputs and isinstance(inputs[field], str):
                    inputs[field] = prompt
                    pos_set = True
                    break
            if pos_set:
                break
        if not pos_set:
            raise RuntimeError("h3 工作流里找不到文本输入节点")

        # 2) seed（noise_seed / seed 字段都设置）
        for nid, node in wf.items():
            inputs = node.get("inputs", {})
            if "noise_seed" in inputs:
                inputs["noise_seed"] = seed
            if "seed" in inputs:
                inputs["seed"] = seed

        # 3) 分辨率 / 帧数（MiniMaxH3ImageToVideo）
        target_length = length
        if target_length is None and seconds:
            target_length = self.calc_length(seconds, fps)
        if target_length is None and "max_seconds" in self.defaults:
            target_length = self.calc_length(self.defaults["max_seconds"], fps)
        for nid, node in wf.items():
            inputs = node.get("inputs", {})
            if "width" in inputs and isinstance(inputs["width"], int):
                inputs["width"] = width or self.defaults.get("width", 1344)
            if "height" in inputs and isinstance(inputs["height"], int):
                inputs["height"] = height or self.defaults.get("height", 768)
            if "length" in inputs and isinstance(inputs["length"], int) and target_length:
                inputs["length"] = target_length

        # 4) 步数（BasicScheduler）
        if steps or "steps" in self.defaults:
            for nid, node in wf.items():
                if node.get("class_type") == "BasicScheduler":
                    node["inputs"]["steps"] = steps or self.defaults.get("steps", 12)

        # 5) 输出 filename_prefix = run_id
        self._set_filename_prefix(wf)
        return wf
