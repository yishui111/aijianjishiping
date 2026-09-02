# docs/workflows — 各 Studio 自研工作流代码说明

本仓库只含自研代码/脚本/文档；引擎与模型按根目录 [DEPLOY.md](../DEPLOY.md) 下载后放回各自目录。
下列目录与原始运行目录一一对应，**不要移动文件位置**（启动脚本用相对路径调用）。

---

## Illustrious_ImageStudio（二次元出图：引擎 8188 + 工作台 8093）

路径：`Illustrious_ImageStudio\`

| 文件 | 用途 |
|------|------|
| `start_anime.bat` | 一键启动：ComfyUI 引擎(8188) + 二次元工作台(8093)，等待引擎就绪后开浏览器 |
| `stop_anime.bat` | 只停止本项目的引擎/工作台进程（按命令行精确匹配，避免误杀） |
| `start_batch_anime.bat` | 剧本批量出图：`剧本.json 角色图目录` → 每段图 + manifest.json |
| `pipeline\scripts\anime_workbench.py` | 二次元工作台 HTTP 服务（端口 8093）：文生图/锁角色/任务队列，页面在 `pipeline\web\anime_test.html` |
| `pipeline\scripts\batch_gen_anime.py` | 批量出图 CLI：剧本 JSON → 分段图 + manifest（自动：有角色基准图→img2img 锁角色；无→文生图） |
| `pipeline\scripts\workbench_tools_anime.py` | 工具封装：`anime_t2i` / `anime_img2img` / `anime_ipadapter`（构造 ComfyUI workflow JSON，直连 8188 API） |
| `pipeline\scripts\prompt_translator.py` | 中文提示词 → Danbooru 英文标签翻译器（本地 Ollama qwen3:8b，可降级原样返回） |
| `pipeline\backends\base.py` | ComfyUI HTTP 提交/等待/取回基类（上传图片、查任务、拿输出） |
| `pipeline\backends\registry.json` | 后端注册表（本 Studio 实际用 sdxl 后端） |
| `pipeline\backends\sdxl\adapter.py` | SDXL 工作流适配器（本 Studio 出图以该架构为准） |
| `pipeline\web\anime_test.html` | 工作台网页（文生图/锁角色表单，调 `/api/*`） |

依赖模型（放 `ComfyUI_windows_portable\ComfyUI\models\`）：checkpoints `Illustrious-XL-v1.0(.safetensors)`、
ipadapter `ip-adapter-plus_sdxl_vit-h.safetensors`、clip_vision `CLIP-ViT-bigG-14-...`。详见根 DEPLOY.md。

---

## FLUX2_ImageStudio（写实/场景出图：引擎 8189 + 工作台 8092）

路径：`FLUX2_ImageStudio\`

| 文件 | 用途 |
|------|------|
| `start_flux2.bat` | 一键启动：ComfyUI 引擎(8189) + 写实工作台(8092)；支持 `FLUX2_QUANT`（Q8_0/Q5_K_M） |
| `stop_flux2.bat` | 停止本项目的引擎/工作台进程 |
| `start_batch.bat` | 剧本批量出图：`剧本.json 角色图目录 [--width/--height/--steps]` |
| `pipeline\scripts\flux2_workbench.py` | 写实工作台 HTTP 服务（端口 8092）：文生图 + 锁角色编辑（ReferenceLatent），任务队列 |
| `pipeline\scripts\batch_gen.py` | 批量出图 CLI：剧本 JSON → seg 图 + manifest.json |
| `pipeline\scripts\workbench_tools.py` | 工具封装：`flux2_t2i` / `flux2_image_edit`（FLUX.2 Klein GGUF，UnetLoaderGGUF + Qwen3-8B 编码器 CPU 推理） |
| `pipeline\backends\base.py` | ComfyUI 提交/等待/取回基类 |
| `pipeline\backends\registry.json` | 后端注册表（flux/sdxl/wan/h3 等为可扩展声明，实际出图走 `workbench_tools.py` 内置 workflow） |
| `pipeline\backends\flux\adapter.py` 等 | 各后端适配器示例（FLUX/sdxl/Wan21/Wan22/restore/h3）——扩展用，未装载模型的后端不可用 |
| `pipeline\backends\wan22\templates\*.json` | Wan2.2 工作流模板（API/UI 两套）——接入 Wan 视频后端时用 |
| `pipeline\config\tool_schemas.json` | LLM 工具协议 schema 示例（图生图/文生图/抠图/文生视频等）——对接对话工作台用 |
| `pipeline\config\workbench_config.json` | 配置示例（LLM/Comfy 地址/端口 8090）——未被出图主流程引用，作为通用配置模板 |
| `pipeline\web\flux2_test.html` | 工作台网页 |

依赖模型（`ComfyUI_windows_portable\ComfyUI\models\`）：diffusion_models `flux-2-klein-base-9b-Q8_0/Q5_K_M.gguf`、
text_encoders `qwen_3_8b_fp8mixed.safetensors`、vae `flux2-vae.safetensors`。详见根 DEPLOY.md。

---

## Camera_Studio（镜头工作台：8094，纯 ffmpeg）

路径：`Camera_Studio\`

| 文件 | 用途 |
|------|------|
| `start_camera.bat` | 启动镜头工作台(8094)，检测内置 ffmpeg |
| `stop_camera.bat` | 停止工作台进程 |
| `start_batch_camera.bat` | 命令行批量：`清单.json [--width 832] [--height 480]` → 各段镜头视频 + 拼接成片 |
| `pipeline\scripts\camera_engine.py` | 镜头引擎：图片 + 7 种运镜（zoom/pan/rotate）×2 构图（face/full_body）→ Ken Burns 视频；ffmpeg 调用封装 |
| `pipeline\scripts\batch_camera.py` | JSON 清单 → 逐段生成镜头 → 拼接 + 输出 manifest |
| `pipeline\scripts\camera_workbench.py` | 镜头工作台 HTTP 服务（端口 8094），上传 JSON+图 → 预览/重试/拼接 |
| `pipeline\web\camera_test.html` | 工作台网页 |

依赖：ffmpeg（`tools\ffmpeg\bin\ffmpeg.exe` 或系统 PATH）；无 GPU、无 AI 模型。

---

## illustrious_ui（官方风格界面：7860，Gradio）

路径：`illustrious_ui\`

| 文件 | 用途 |
|------|------|
| `app.py` | Gradio 官方风格界面：文生图 + 锁角色编辑，走二次元引擎(8188)；自动定位同级 `Illustrious_ImageStudio\pipeline\scripts`（可 `ILL_SCRIPTS_DIR` 覆盖） |
| `start_ui.bat` / `stop_ui.bat` | 启动/停止界面（需要 venv，安装见 DEPLOY.md） |

---

## 说明

- 本仓库各 Studio 的 `pipeline\scripts\*.py` 都是**标准库 HTTP 客户端**直接驱动本机 ComfyUI/ffmpeg，
  不依赖额外 pip 包（出图引擎自带 python 即可运行）。
- `pipeline\backends\*` 是“后端抽象层”示例，展示如何扩展模型（SDXL/FLUX/Wan/H3…），
  未下载对应模型的后端不会被主流程使用。
- 日志与生成物写到各 `pipeline\logs\`、`pipeline\output\`，均不入库（见 .gitignore）。
