# DEPLOY.md — 新机器部署方案（AI 图像/视频生成剪辑工作室合集）

> 目标：把本仓库克隆到另一台 Windows 电脑，**下载引擎与模型并放回各 Studio 目录**
> 后，目录结构与原机一致，双击 `control_menu.bat` 即可使用。
>
> 仓库里只有代码/脚本/文档；引擎（ComfyUI 便携包）、模型权重、ffmpeg、素材都是外部下载。

---

## 0. 环境要求

| 项目 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 10/11 64 位 | 出图脚本为 .bat，Linux 需自行适配 |
| 显卡 | NVIDIA，**显存 ≥ 8GB**（推荐 16GB） | 出图类 Studio 需要；Camera_Studio 纯 ffmpeg 不需要 GPU |
| 驱动 | NVIDIA 显卡驱动（游戏驱动即可） | — |
| 内存 | ≥ 16GB（推荐 32GB） | FLUX.2 编码器走 CPU 推理吃内存 |
| 磁盘 | ≥ 60GB 空闲 | 引擎+模型按所选 Studio 计算（见各节） |
| Python | 见各 Studio | 出图类用 ComfyUI 便携包自带 python；Camera 需要系统 Python 3.8+ |
| 网络 | 仅首次下载需要 | 下载后可完全离线使用 |

> ⚠️ **显存规则**：一次只开一个出图引擎（二次元 8188 或 写实 8189），同时开会显存爆。
> Camera_Studio（8094）纯 ffmpeg 不吃显存，可随时开。

---

## 1. 总体步骤（快速版）

1. 克隆本仓库：
   ```bat
   git clone https://github.com/yishui111/aijianjishiping.git
   cd aijianjishiping
   ```
2. 按「第 2~6 节」把每个要用到的 Studio 补齐（下载引擎→放模型→装自定义节点）。
3. 双击根目录 `control_menu.bat`，按需选择 [1][2][3][5] 启动。
4. 需要关闭时按 [S] 全部停止（或运行根目录 `stop.bat`）。

---

## 2. Illustrious_ImageStudio（二次元出图：引擎 8188 + 工作台 8093）

代码/脚本已就位：`start_anime.bat`、`start_batch_anime.bat`、`stop_anime.bat`、`pipeline/`。

### 2.1 放回引擎（ComfyUI Windows 便携包）

- 官方获取：<https://github.com/comfyanonymous/ComfyUI>（Windows 便携包 Releases）。
- 解压后把 `ComfyUI_windows_portable` 文件夹放到本 Studio 根目录：
  ```
  Illustrious_ImageStudio\
  ├─ ComfyUI_windows_portable\        ← 新增（含 python_embeded + ComfyUI）
  ├─ start_anime.bat
  └─ pipeline\
  ```
- 启动脚本要求该引擎有 `ComfyUI_windows_portable\python_embeded\python.exe`。

### 2.2 自定义节点（ComfyUI_windows_portable\ComfyUI\custom_nodes\）

| 节点 | 用途 | 获取 |
|------|------|------|
| ComfyUI-GGUF | 加载 GGUF（如有） | ComfyUI Manager 或 GitHub `city96/ComfyUI-GGUF` |
| ComfyUI-KJNodes | 图生图/锁角色辅助节点 | ComfyUI Manager 或 GitHub `kijai/ComfyUI-KJNodes` |
| ComfyUI_IPAdapter_plus | IPAdapter「锁角色」 | GitHub `cubiq/ComfyUI_IPAdapter_plus` |

### 2.3 模型（放到 ComfyUI_windows_portable\ComfyUI\models\）

| 模型文件 | 目录 | 说明/来源 |
|----------|------|-----------|
| `Illustrious-XL-v1.0.safetensors`（或 `illustriousXL_v01.safetensors`） | `models\checkpoints\` | 二次元主力，6.94GB，SDXL 架构完整 checkpoint（内置 CLIP+VAE）；模型站 / Hugging Face Illustrious 官方仓库 |
| `ip-adapter-plus_sdxl_vit-h.safetensors` | `models\ipadapter\` | 锁角色用 IPAdapter（IPAdapter_plus 项目页提供） |
| `CLIP-ViT-bigG-14-laion2B-39B-b160k.safetensors` | `models\clip_vision\` | IPAdapter 需要的 CLIP Vision |
| （可选）`ip-adapter_sdxl.safetensors` | `models\ipadapter\` | 备用 |

> 代码中模型常量见 `pipeline/scripts/workbench_tools_anime.py`，请保持一致。

### 2.4 验证

```
start_anime.bat      → 引擎 8188 + 工作台 http://127.0.0.1:8093
```

---

## 3. FLUX2_ImageStudio（写实/场景出图：引擎 8189 + 工作台 8092）

脚本已就位：`start_flux2.bat`、`start_batch.bat`、`stop_flux2.bat`、`pipeline/`。

### 3.1 放回引擎

- 同样使用 ComfyUI Windows 便携包，解压到本 Studio 根目录：
  ```
  FLUX2_ImageStudio\
  ├─ ComfyUI_windows_portable\        ← 新增
  ├─ start_flux2.bat
  └─ pipeline\
  ```
- ⚠️ 注意：二次元与写实各自维护一套便携引擎（端口不同：8188 / 8189），不要共用。

### 3.2 自定义节点

| 节点 | 获取 |
|------|------|
| ComfyUI-GGUF（必需，加载 .gguf 主模型） | ComfyUI Manager / GitHub `city96/ComfyUI-GGUF` |
| ComfyUI-KJNodes | ComfyUI Manager / GitHub `kijai/ComfyUI-KJNodes` |

### 3.3 模型（ComfyUI_windows_portable\ComfyUI\models\）

| 模型文件 | 目录 | 说明/来源 |
|----------|------|-----------|
| `flux-2-klein-base-9b-Q8_0.gguf`（16G 显卡默认） | `models\diffusion_models\` | FLUX.2 Klein 主模型 Q8_0 ≈9.98GB |
| `flux-2-klein-base-9b-Q5_K_M.gguf`（8G 显卡用） | `models\diffusion_models\` | Q5 量化 ≈7.02GB |
| `qwen_3_8b_fp8mixed.safetensors` | `models\text_encoders\` | Klein 专用文本编码器（Qwen3-8B fp8，≈8.66GB，CPU 推理） |
| `flux2-vae.safetensors` | `models\vae\` | FLUX.2 VAE ≈0.34GB |

- **精度切换**：启动前 `set FLUX2_QUANT=Q8_0`（16G）或 `set FLUX2_QUANT=Q5_K_M`（8G）。
- 编码器与主模型是配套的（12288 维 ↔ 9B），不要混用 Klein 4B 那套（7680 维）。
- 来源：模型文件以 Black Forest Labs 官方 / 社区量化（Hugging Face、Comfy-Org 等）发布的对应文件名为准。

### 3.4 验证

```
start_flux2.bat      → 引擎 8189 + 工作台 http://127.0.0.1:8092
```

---

## 4. illustrious_ui（官方风格界面 7860）

- 依赖二次元引擎已启动（8188，见第 2 节）。
- 本目录代码：`app.py`、`start_ui.bat`、`stop_ui.bat`。原机运行在 `venv` 中，仓库不含 venv，需自行创建：

```bat
cd illustrious_ui
python -m venv venv
venv\Scripts\pip install gradio
start_ui.bat          :: 打开 http://127.0.0.1:7860
```

- `app.py` 会自动从仓库布局中找到 `..\Illustrious_ImageStudio\pipeline\scripts`；
  也可以设置环境变量 `ILL_SCRIPTS_DIR` 指向实际位置。

---

## 5. Camera_Studio（镜头工作台 8094，纯 ffmpeg）

- 无 GPU / 无 AI 引擎要求；需要**系统 Python 3.8+** 和 **ffmpeg**。
- ffmpeg 获取：<https://www.gyan.dev/ffmpeg/builds/>（或任意 ffmpeg 构建）。
  两种方式任选：
  1. 把 `ffmpeg.exe` 放到 `Camera_Studio\tools\ffmpeg\bin\ffmpeg.exe`（脚本优先用这个）；
  2. 或把 ffmpeg 加入系统 PATH（`camera_engine.py` 找不到内置 exe 时自动回退 PATH 中的 ffmpeg）。
- 启动：`start_camera.bat` → 打开 http://127.0.0.1:8094
- 命令行批量：`start_batch_camera.bat 清单.json`
- 说明：JSON 清单格式与出图系统 `manifest.json` 兼容（segments: image/camera/camera_target/duration/line…）。

---

## 6.（可选/本地）model_pad 与 ai-video-studio

控制菜单 [4]（model_pad 调试台 8095）与 [6]（AI Video Studio）是**原机并列的本机项目**，
**未随本仓库分发**：

- `model_pad`：极简模型调试台（极小型自用工具），仓库刻意不收录；需要时从原机目录拷贝。
- `ai-video-studio`：AI 视频素材剪辑系统（planner 8003 / analyzer 8001 / executor 8002，
  本地 Ollama 模型 qwen2.5vl:3b 等，整套约 15GB，含 Docker/运行时/模型，代码为独立子项目）。
  如需该子系统的源码与部署文档，请从其原始项目目录另行发布（本仓库为图像/镜头方向合集，
  避免把一个独立 15GB 项目塞进集合仓库）。

> 若在本机沿用原 `aijianjishiping` 目录（含上述两项目），控制菜单 [4]/[6] 可直接使用；
> 仅克隆本仓库时这两项暂不可用。

---

## 7. 端口速查

| 端口 | 是谁 | 网址 |
|------|------|------|
| 8188 | 二次元引擎（Illustrious） | — |
| 8189 | 写实引擎（FLUX2） | — |
| 8093 | 二次元工作台 | http://127.0.0.1:8093 |
| 8092 | 写实工作台 | http://127.0.0.1:8092 |
| 7860 | 官方风格界面 | http://127.0.0.1:7860 |
| 8095 | model_pad 调试台（本地，未分发） | http://127.0.0.1:8095 |
| 8094 | 镜头工作台 | http://127.0.0.1:8094 |

---

## 8. 日常使用 / 停止

- 启动：根目录 `control_menu.bat`，或单独 `Illustrious_ImageStudio\start_anime.bat` 等。
- 全部停止：控制菜单按 [S]，或运行根目录 `stop.bat`。
- 单个停止：各 Studio 自带 `stop_*.bat`。
- 批量出图（可选）：`start_batch_anime.bat 剧本.json 角色图目录` / `start_batch.bat 剧本.json 角色图目录`
  → 输出 `pipeline\output\batch\<任务名>\`（每段图 + `manifest.json`），可直接交给 Camera_Studio 做镜头。

---

## 9. 常见问题排查

| 现象 | 处理 |
|------|------|
| 菜单顶部显示 stopped | 按对应数字启动；引擎首次加载模型 1-2 分钟属正常 |
| 引擎起不来 / 出图报错 | 看 `comfy_*.log`；确认显卡驱动；确认模型文件名与代码常量一致 |
| `mat1 and mat2 shapes`（FLUX2） | text_encoder 用错了版本：必须 `qwen_3_8b_fp8mixed.safetensors`（配 9B 主模型） |
| 显存不足 OOM | 一次只开一个引擎；FLUX2 用 Q5_K_M 档 + 640x384/16 步；Illustrious 可 640x384 |
| 锁角色不像 | denoise 0.45~0.6 保外观；参考图清晰；IPAdapter/clip_vision 模型齐全 |
| 中文乱码 | 文件本身 UTF-8 无 BOM；PowerShell 读取用 `Get-Content -Encoding UTF8` |
| 想换更省显存模型 | FLUX2 Klein 4B 版需同时换 4B 编码器（两套不通用），见原版说明 |

---

## 10. 更新约定

每次优化/修复后同步更新本文件与根 README，保证新机器可复现。
