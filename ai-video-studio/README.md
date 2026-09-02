# AI Video Studio — 单文件夹便携部署包

本地视频素材 AI 剪辑系统。全部代码、依赖缓存、模型都在这一个文件夹里，换电脑只需整体复制。

## 目录结构

```
ai-video-studio/
├── docker-compose.yml        # 默认编排（CPU 安全）
├── docker-compose.gpu.yml    # GPU 透传（与默认 compose 叠加使用）
├── .env.example              # 复制为 .env 后配置对话模型
├── config/
│   └── operation-catalog.json
├── services/
│   ├── common/               # 共享库（ffmpeg 封装、JSON 工具）
│   ├── analyzer/             # L1 理解服务（Ollama VLM + 场景检测 + ASR）
│   ├── executor/             # L3 执行服务（EDL → ffmpeg 出片）
│   └── planner/              # L2 方案服务（对话 + 工具调用 + EDL 生成）
├── models/
│   ├── ollama/               # Ollama 模型权重（构建机预拉取，随文件夹走）
│   └── whisper/              # faster-whisper 模型（构建机预下载）
├── offline/
│   ├── wheels/               # pip 依赖离线包（构建机 pip download）
│   └── images/               # docker save 出的镜像 tar（离线加载）
├── materials/                # 素材目录（挂载给容器）
├── analysis/                 # 分析结果 JSON + 关键帧
├── output/                   # 成片输出
└── scripts/
    ├── build-offline.ps1/.sh # 【构建机】准备离线包 + 构建 + 导出镜像
    └── load-and-start.ps1/.sh# 【目标机】加载镜像并启动
```

## 两种机器

- **构建机**：有 Docker + 能联网（必要时可科学上网）。运行 `scripts/build-offline.ps1`（或 `.sh`），一次性把依赖、模型、镜像全部缓存进本文件夹。
- **目标机**：只要装了 Docker（建议开启 WSL2），把整个文件夹复制过去，运行 `scripts/load-and-start.ps1` 即可，全程离线。

## 快速开始

### 一键启动 / 关闭（推荐）

根目录提供四个脚本，日常使用直接运行即可：

| 操作 | Windows | Linux / Git Bash |
|---|---|---|
| 启动（自动停掉其他项目容器，释放主机资源） | `start.ps1`（右键"使用 PowerShell 运行"） | `./start.sh` |
| 关闭本项目服务 | `stop.ps1`（右键"使用 PowerShell 运行"） | `./stop.sh` |

> 启动脚本会先 `docker stop` 掉所有**非 `avs-*` 前缀**的容器（其他项目），再启动本项目 4 个服务，确保 AI 剪辑时主机资源充足。

### 构建机（有网络）

```powershell
cd ai-video-studio
.\scripts\build-offline.ps1 -VlmModel qwen2.5vl:3b
```

可选参数：`-Include7B`（同时拉 7B 模型）、`-SkipWhisper`（跳过转写模型）。

### 目标机（离线）

```powershell
cd ai-video-studio
.\scripts\load-and-start.ps1
```

然后：

1. 把素材放进 `materials/`；
2. 理解素材：`curl -X POST http://localhost:8001/scan -H "Content-Type: application/json" -d '{"folder":"."}'`（或等空闲调度）；
3. 剪辑：浏览器打开 `http://localhost:8003`。
   - **对话剪辑**：勾选素材 → 对话（如"保留 3-10 秒"）→ 预览 → 执行出片；
   - **手动精剪**：点素材卡片上的「👁 预览」→ 播放视频、用「⏺ 设为起点 / ⏹ 设为终点」或直接输入秒数（支持 `12` 或 `1:30`）→ 「▶ 试播区间」确认 → 「✂️ 剪辑此区间并出片」，成片直接内嵌预览并可下载。

## GPU 配置

有 NVIDIA 显卡时叠加 GPU 文件：

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

- Linux：需安装 `nvidia-container-toolkit`；
- Windows：Docker Desktop + WSL2（默认支持 CUDA）；
- 无 GPU 也能跑：Ollama 回退 CPU（慢），理解服务自动适配。

## 对话模型配置（.env）

复制 `.env.example` 为 `.env`。两种模式：

- **线上 DeepSeek**（规划更强）：填 `PLANNER_BASE_URL=https://api.deepseek.com/v1`、`PLANNER_MODEL=deepseek-chat`、`PLANNER_API_KEY`；
- **全本地**（隐私最好）：`PLANNER_BASE_URL=http://localhost:11434/v1`、`PLANNER_MODEL=qwen2.5vl:3b`、API Key 留空。

## 当前实现范围

- ✅ 理解：ffprobe 探测、场景切分、关键帧、VLM 逐场景语义、ASR 转写、静音检测 → analysis.json + manifest.json
- ✅ 执行：trim / remove_silence / concat / speed / export，concat 前自动归一化，dry-run 预览 + 审计
- ✅ 方案：对话接口 + 工具调用（query_segment 精读、search_semantic 语义检索、preview / execute）
- ✅ 网页预览与手动精剪：素材卡片「👁 预览」→ 浏览器直接播放素材（Range 流式，可拖进度条）、手动起止时间（秒 / 分:秒）、试播区间、一键出片（`/api/clip`，不经过 LLM）
- ⏳ 人脸嵌入与按人像检索（P1b，默认关闭，`FACE_ENABLED=true` 后启用）
- ⏳ 空闲时段自动调度（P1b，当前提供手动 /scan 与任务计划触发）

## 部署手册（完整版）

见包内 `docs/implementation-plan.md` 第 14 节。

## 人工精剪工具（Shotcut）

项目内置 **Shotcut**（免费开源视频剪辑软件，便携版，界面中文，操作类似剪映）——用于**人工精细裁剪**（刀片分割、点选删除、导出），
与网页端 AI 场记/批量剪辑互补。启动方式、使用流程、注意事项详见：

👉 **`docs/SHOTCUT-剪辑软件.md`**

- 启动：双击桌面「Shotcut 视频剪辑」快捷方式，或 `Shotcut\Shotcut\shotcut.exe`
- 核心流程：打开 → 播放 → 刀片切 → 点选删 → 导出（H.264/MP4）

