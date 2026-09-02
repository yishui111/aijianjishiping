# 后端抽象层使用与扩展指南

> 位置：`pipeline/backends/` ｜ 作用：把"用什么模型/工作流出片"和"流水线调度"解耦。
> 加一个新模型 = 新增一个目录 + 注册表加一行，**不动任何核心代码**。

---

## 一、当前已注册后端

| backend_id | 模型 | 类型 | 能力 | 机器 |
|------------|------|------|------|------|
| `h3` | MiniMax H3 | 文生视频 | t2v, audio | 16G 主力 |
| `wan22` | Wan2.2 TI2V 5B | 文生视频+图生视频 | t2v, i2v | **8G 主力** |
| `wan21_t2v` | Wan2.1 T2V 1.3B | 文生视频 | t2v | 8G 保底 |
| `sdxl` | SDXL | 文生图 | t2i | 出关键帧 |

## 二、目录结构

```
pipeline/
├── backends/
│   ├── __init__.py
│   ├── registry.json      # 注册表：集中描述所有后端
│   ├── base.py            # 基类：ComfyUI 提交/等待/取回 + 图片上传
│   ├── h3/adapter.py      # 各后端 adapter：只做"工作流节点改写"
│   ├── wan22/adapter.py
│   ├── wan21_t2v/adapter.py
│   └── sdxl/adapter.py
├── stages/                # 镜头处理管线（stage 组合）
└── config/                # 工作流模板（*_api.json）+ 全局/机器配置
```

## 三、怎么添加一个新后端（如未来的 Wan3 / 新 I2V 模型）

**三步，约 10 分钟：**

### 第 1 步：注册表加一行
`backends/registry.json`：
```json
"wan3": {
  "kind": "ti2v",
  "name": "Wan3 XXX",
  "workflow": "wan3_api.json",
  "capabilities": ["t2v", "i2v"],
  "defaults": {
    "width": 832, "height": 480, "steps": 20,
    "cfg": 6.0, "fps": 16, "max_seconds": 5,
    "output_suffix": ".mp4"
  }
}
```

### 第 2 步：准备工作流模板
在 ComfyUI 界面加载官方模板 → 手动跑通一条 → 菜单「导出 API 格式」→ 保存到 `pipeline/config/wan3_api.json`。

### 第 3 步：写 adapter
新建 `pipeline/backends/wan3/adapter.py`，继承基类，**只需实现一个方法**：

```python
from backends.base import ComfyBackend

class Adapter(ComfyBackend):
    backend_id = "wan3"

    def build_workflow(self, *, prompt, negative_prompt="", image=None,
                       seed=42, width=None, height=None, length=None,
                       seconds=None, steps=None, cfg=None, **kwargs):
        wf = self._load_workflow()          # 读 config/wan3_api.json
        # ... 按节点 class_type 改写提示词/尺寸/帧数/seed ...
        # I2V 时：self._upload_image(image) → 加 LoadImage 节点
        self._set_filename_prefix(wf)       # 输出文件名 = run_id（取回用）
        return wf
```

然后跑 `python pipeline/tests/test_backends.py` 确认没破坏其他后端。

**核心代码（video_gen.py / scheduler.py / stages/）零改动。**

## 四、怎么用

```python
from video_gen import generate_single_shot

# 纯文生视频（8G 机器用 wan22 或 wan21_t2v）
generate_single_shot("http://127.0.0.1:8188", "提示词", seed=42,
                     backend_id="wan22", duration_s=5)

# 图生视频（先出图再动）
generate_single_shot("http://127.0.0.1:8188", "提示词", seed=42,
                     backend_id="wan22", image="关键帧.png", duration_s=5)

# 批量出片（stage 管线，自动按机器配置组合 t2i→i2v）
from stages import generate_chapter, load_machine_config
generate_chapter(screenplay, load_machine_config())
```

## 五、机器配置

`pipeline/config/machines/<机器名>.json`（机器名 = hostname 或环境变量 `PIPELINE_MACHINE`）：

```json
{
  "backend": "wan22",      // 视频后端
  "t2i_backend": "sdxl",   // 文生图后端
  "mode": "t2i_i2v",       // t2v | t2i_i2v | i2v
  "resolution": {"width": 832, "height": 480},
  "shot_seconds": 5
}
```

没有机器专属文件时用 `default.json`；都没有时回退旧 `config.json`（向后兼容）。

## 六、常见问题

- **GGUF 模型加载失败**：确认已装 ComfyUI-GGUF 节点（ComfyUI Manager → Install Custom Nodes → ComfyUI-GGUF）。
- **8G 显存 OOM**：`machines/<机器>.json` 里降分辨率（如 768x448），或改用 GGUF q4 主模型，或 `config/global.json` 调整 `shot_seconds` 缩短时长。
- **ComfyUI 便携包 import 报错**：所有核心脚本已内置 sys.path 引导（paths.ensure_sys_path），正常情况下无需处理；若自己写新脚本，顶部加：
  ```python
  import sys
  from pathlib import Path
  _p = Path(__file__).resolve().parent.parent
  if str(_p) not in sys.path: sys.path.insert(0, str(_p))
  ```
- **取回不到输出文件**：确认工作流输出节点是 SaveVideo/SaveImage/SaveWEBM（带 filename_prefix），基类按 run_id 前缀精确取回；旧模板没有该字段时会退回"最新文件"兜底。
