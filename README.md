<div align="center">

# 🎬 AI 图像/视频生成剪辑工作室合集（AI 出片系统）

> ⭐ **喜欢这个项目？请先点个 Star ⭐ 支持一下，让更多人看到！**

![GitHub stars](https://img.shields.io/github/stars/yishui111/aijianjishiping.svg?style=flat-square&color=orange)
![GitHub forks](https://img.shields.io/github/forks/yishui111/aijianjishiping.svg?style=flat-square)
![GitHub repo size](https://img.shields.io/github/repo-size/yishui111/aijianjishiping.svg?style=flat-square)

**一个多引擎 AI 图像 / 视频生成剪辑工作室合集：二次元出图、写实出图、镜头成片、官方风格界面…… 用 `control_menu.bat` 统一管理启动与停止。**

</div>

---

## ✨ 项目简介

本项目把多个**本地 AI 生成 / 剪辑工作台**整合到一个目录、用统一菜单管理，一条龙完成
「剧本 JSON → 出图（二次元 / 写实）→ manifest 清单 → 镜头视频 → 成片」的完整流程：

| Studio 目录 | 干什么 | 引擎 | 工作台端口 |
|---|---|---|---|
| `Illustrious_ImageStudio` | **二次元动漫出图**（Illustrious XL，锁角色） | ComfyUI(8188) | 8093 |
| `FLUX2_ImageStudio` | **写实 / 场景出图**（FLUX.2 Klein 9B，锁角色） | ComfyUI(8189) | 8092 |
| `illustrious_ui` | **官方风格网页界面**（Gradio 风格对话框出图） | 依赖二次元引擎 | 7860 |
| `Camera_Studio` | **镜头工作台**：图片 → Ken Burns 运镜视频 → 拼接成片 | 纯 ffmpeg，无需显卡 | 8094 |
| `control_menu.bat` | 统一启动 / 停止 / 状态检查 | — | — |

> ℹ️ `model_pad`（8095 极简模型调试台）与 `ai-video-studio`（AI 视频素材剪辑系统）是
> 同一文件夹里并列的本机项目，**未随本仓库分发**（见 FAQ）。

> 💡 本仓库只包含**自研代码 / 启动脚本 / 工作流 / 文档**。
> ComfyUI 引擎、模型权重、ffmpeg、素材等**大文件需自行下载**，见下方「大件资源」与 [DEPLOY.md](DEPLOY.md)。

## 🎯 主要功能

- 🕹️ **统一控制菜单** `control_menu.bat`：一键起停所有出图引擎与工作台，带端口状态检查
- 🎌 **二次元出图**：Illustrious XL 文生图 + IPAdapter「锁角色」图生图，中文提示词自动翻译
- 🌅 **写实出图**：FLUX.2 Klein 9B（GGUF），文生图 + Reference 锁角色，8G/16G 显存可切换档位
- 🖼️ **官方风格界面**：HuggingFace 官方 demo 风格对话框（Gradio），会锁角色、可连续出图
- 🎥 **剧本批量出图**：剧本 JSON → 逐段出图 → 输出 `manifest.json`（图片/时长/相机/台词）
- 🎬 **镜头工作台**：JSON 清单控镜头（推近/拉远/平移/微旋转），纯 ffmpeg Ken Burns，无需 GPU
- 🖥️ **纯本地运行**：全部服务 `127.0.0.1` 直连，模型不离开你的电脑

## 🗂️ 目录结构

```
aijianjishiping/
├── control_menu.bat        # 统一控制菜单（启动/停止所有 Studio）
├── start.bat / stop.bat    # 总入口 / 总停止（等价菜单操作）
├── README.md               # 本文件
├── DEPLOY.md               # 新机器部署方案（下载引擎/模型、恢复目录结构）
├── AI_Image_Studio_README.md  # 原版使用说明（菜单 [H] 帮助）
├── docs/workflows/         # 各 Studio 自研工作流代码说明（每文件用途）
├── Illustrious_ImageStudio/   # 二次元出图：启动脚本 + pipeline 工作流代码
├── FLUX2_ImageStudio/         # 写实出图：启动脚本 + pipeline 工作流代码
├── Camera_Studio/             # 镜头工作台：启动脚本 + pipeline 工作流代码
└── illustrious_ui/            # 官方风格界面（Gradio app.py + 启动脚本）
```

## 🚀 快速开始（拉到新电脑即可部署）

### 环境要求

- 操作系统：Windows 10/11 64 位
- 显卡：NVIDIA（出图类 Studio 需要，8GB 可跑、16GB 流畅；Camera_Studio 纯 ffmpeg 不需要）
- 运行时：ComfyUI Windows 便携包自带 Python（出图）；Camera_Studio 需要系统 Python 3.8+ 与 ffmpeg
- 磁盘：出图模型单个 1~10GB，请预留足够空间

### 1. 克隆

```bash
git clone https://github.com/yishui111/aijianjishiping.git
cd aijianjishiping
```

### 2. 安装引擎与模型

引擎与模型**不随仓库分发**（体积大），请严格按 [DEPLOY.md](DEPLOY.md) 操作：
下载 ComfyUI Windows 便携包与对应模型 → 放入各 Studio 目录中约定位置 → 目录结构即恢复为可运行状态。

### 3. 启动

```bat
:: 方式一：总入口
start.bat

:: 方式二：直接双击控制菜单
control_menu.bat
```

控制菜单按键：
`[1]` 二次元出图（引擎 8188 + 工作台 8093） · `[2]` 写实出图（引擎 8189 + 工作台 8092）
`[3]` 官方风格界面 7860 · `[5]` 镜头工作台 8094 · `[S]` 全部停止 · `[H]` 帮助

> 菜单里还有 `[4]` model_pad（调试台）与 `[6]` AI Video Studio：它们是原机并列的本地项目，
> **未随本仓库分发**，克隆仓库后这两项不可用（FAQ 有说明）。

### 4. 验证

浏览器打开 <http://127.0.0.1:8093>（二次元）或 <http://127.0.0.1:8092>（写实），
输入提示词能出图即部署成功。端口速查见下方。

## 📥 大件资源下载（模型 / 引擎 / 运行时）

| 资源 | 用途 | 下载地址 / 获取方式 | 大小 |
| ---- | ---- | ---- | ---- |
| ComfyUI Windows 便携包 | 两个出图 Studio 的引擎 | ComfyUI 官方 GitHub（`comfyanonymous/ComfyUI` releases / 官方便携包），放入 `Illustrious_ImageStudio\ComfyUI_windows_portable` 与 `FLUX2_ImageStudio\ComfyUI_windows_portable` | ~2-5GB/份 |
| Illustrious-XL-v1.0（或 v0.1） | 二次元主力模型 | 模型站（如 Hugging Face / Civitai）下载 `Illustrious-XL-v1.0.safetensors` 等 → `models\checkpoints\` | 6.94GB |
| IPAdapter Plus 模型 + CLIP Vision | 二次元「锁角色」 | IPAdapter_plus（GitHub `cubiq/ComfyUI_IPAdapter_plus`）配套模型 → `models\ipadapter\`、`models\clip_vision\` | ~2-7GB |
| FLUX.2 Klein 9B GGUF | 写实主力模型 | 社区量化版：`flux-2-klein-base-9b-Q8_0.gguf`(16G) / `Q5_K_M`(8G) → `models\diffusion_models\` | 7-10GB |
| Qwen3-8B fp8 编码器 + flux2 VAE | FLUX.2 文本编码 / VAE | → `models\text_encoders\`、`models\vae\` | ~9GB |
| ComfyUI 自定义节点 | GGUF/KJNodes/IPAdapter Plus | ComfyUI Manager 内安装或 Git 克隆到 `custom_nodes\` | 数 MB |
| ffmpeg | Camera_Studio 镜头引擎 | ffmpeg 官方构建（gyan.dev 等）→ 放入 `Camera_Studio\tools\ffmpeg\bin\` 或加入 PATH | ~100MB |
| Ollama + qwen3:8b（可选） | 二次元中文提示词翻译 / 对话工作台 | <https://ollama.com> 安装后 `ollama pull qwen3:8b` | ~5.6GB |

> 文件名/放置目录务必与各 Studio `pipeline` 代码中的常量一致（见 `docs/workflows/` 各说明），
> 更细的逐 Studio 清单与显卡档位见 [DEPLOY.md](DEPLOY.md)。

## 🛠️ 本地开发 & 提交

```bash
git add .
git commit -m "feat: xxx"
git push origin main
```

## ❓ 常见问题（FAQ）

- **Q：点菜单没反应 / 引擎没起来？** A：先按 `control_menu.bat` 顶部看端口状态；引擎未运行看各 Studio 目录下 `comfy_*.log`；确认显卡驱动与模型文件齐全。
- **Q：为什么仓库里没有 ComfyUI / 模型 / 素材？** A：它们体积巨大（几十 GB），按 [DEPLOY.md](DEPLOY.md) 下载放回对应目录即可，代码相对路径不变。
- **Q：菜单里的 model_pad（8095）和 ai-video-studio 呢？** A：它们是本机并列的独立小项目/大项目（后者约 15GB，含模型与 Docker 运行时），不适合随仓库分发；需要时从原机目录拷贝或按其自身说明部署。
- **Q：8G 显存能跑吗？** A：二次元 Illustrious 轻松跑（模型仅占 3-4GB）；FLUX2 出图前 `set FLUX2_QUANT=Q5_K_M` 用 8G 档（640x384/16步）。**一次只开一个出图引擎**，否则显存会爆。
- **Q：锁角色不像？** A：denoise 调到 0.45~0.6（保外观）或 0.7+（重绘）；检查 IPAdapter 模型与 clip_vision 是否齐全、参考图是否清晰。

## ⚠️ 注意事项

- 引擎（ComfyUI）与其模型版权归各自作者，请按各自许可在官网/官方渠道获取；
- 素材（`素材/` 等）为个人使用内容，不在仓库内；不要上传他人版权素材；
- 敏感信息（密钥、token）请放 `.env` / 环境变量，禁止提交到仓库；
- 本仓库仅供学习交流使用。

## 📄 许可证

MIT License

---

## 🙏 支持与致谢

如果这个项目帮到了你，**请点亮右上角的 ⭐ Star**，你的支持是我持续更新的最大动力！
