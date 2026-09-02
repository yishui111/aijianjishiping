# 视频素材 AI 剪辑系统 — 实施方案

> 目标：本地理解素材 → 对话出剪辑方案（本地 Qwen3-VL 单模型，或线上 DeepSeek，可配置）→ 本地执行出片。
> 原则：模块解耦、契约先行、组件可替换、流水线可扩展。
> 硬件约束：8GB 显存，慢可接受，以"吃透素材"为最高优先级。

## 1. 总体架构

```
┌────────────────────────── 本地（自有硬件） ──────────────────────────┐
│                                                                      │
│  素材文件夹                                                           │
│     │                                                                 │
│     ▼                                                                 │
│  L1 理解层（GPU，Docker）                                              │
│   ├─ ffprobe          读取时长/分辨率/帧率/码流                        │
│   ├─ VLM（Ollama Qwen2.5-VL / Qwen3-VL）看懂画面 → 场景语义            │
│   ├─ faster-whisper / FunASR  转写人声 + 时间戳 + 说话人               │
│   ├─ PySceneDetect    帧级精确切点                                     │
│   └─ ffmpeg silencedetect  静音区间                                    │
│     │                                                                 │
│     ▼                                                                 │
│  每个视频 → xxx.analysis.json；整个目录 → manifest.json（仅元数据）    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ 只上传 JSON，素材不出本地
                               ▼
┌────────────── L2 方案层（可配置：本地 Qwen3-VL / 线上 DeepSeek） ───────┐
│  对话 + 工具调用（function calling / JSON mode）                        │
│  输入：manifest.json + 操作目录 operation-catalog.json + 用户需求       │
│  输出：EDL 剪辑方案（经 schema 校验）                                   │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌────────────────────────── 本地（Docker） ─────────────────────────────┐
│  L3 执行层：EDL 校验 → 计划排序 → 逐条转 ffmpeg / Auto-Editor 命令     │
│  输出目录：成片 + 审计日志（每条剪辑决策可追溯）                        │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. 分层职责与扩展点

| 层 | 职责 | 扩展点 |
|---|---|---|
| L0 素材接入 | 监控文件夹、按文件哈希去重、增量扫描 | 加网盘/NAS 同步、多素材源 |
| L1 理解层 | 产出每段素材（视频/图片）的结构化 JSON | 换 VLM、加 OCR/人脸/音乐节拍等新信号 |
| L2 方案层 | 对话 + 把用户需求翻译成 EDL | 对话模型可配置：本地 Qwen3-VL（单模型）或线上 DeepSeek（分离式）；可加 Dify 编排 UI |
| L3 执行层 | 按 EDL 确定性出片 | 加新剪辑动作、导出剪映/Premiere 工程 |
| L4 交付 | 输出成片与审计记录 | 加封面、字幕、多格式导出 |

### 2.5 模型角色：单模型 vs 分离式

视频理解模型（Qwen2.5-VL / Qwen3-VL）本身就是**支持对话的多模态模型**，可以完成"看懂素材 + 和用户聊天 + 调用剪辑工具"三件事。因此 L2 有两种组织方式：

| 方式 | 组成 | 优点 | 缺点 |
|---|---|---|---|
| A. 单模型（本地） | 一个本地 VLM 既理解素材又对话，通过 function calling 直接调剪辑工具 | 全本地、架构最简、隐私最好 | 规划/指令遵循能力取决于该 VLM；大素材库对话受上下文限制 |
| B. 分离式 | 本地 VLM 只做理解；线上 DeepSeek 做对话与方案 | 各自用最擅长的模型；规划质量高、对话成本低 | 两套模型；素材元数据需传给线上 |

**结论**：采用 A 为主、B 可选。无论哪种方式，"理解阶段先批量预处理成 JSON 缓存"都保留（见 7.5）。对话层做成配置项：

- `PLANNER_PROVIDER=local-vlm`：本地 Qwen3-VL 直接对话 + 工具调用，素材完全不出本地；
- `PLANNER_PROVIDER=deepseek`：同一套 JSON 契约接线上 DeepSeek。

架构、数据契约、工具注册表、执行器完全复用，只换 L2 的"大脑"。

## 3. 数据契约（系统接口，最重要）

所有跨层数据都带 `schema_version`，未来字段变更时做版本迁移而不是破坏性修改。

### 3.1 每段素材：`xxx.analysis.json`

```json
{
  "schema_version": 1,
  "file": "C0117.MP4",
  "file_hash": "sha256:...",
  "duration_sec": 187.4,
  "resolution": "3840x2160",
  "fps": 30,
  "has_audio": true,
  "language": "zh",
  "summary": "雪山湖泊航拍，两人在水中游泳",
  "tags": ["航拍", "风景", "人物"],
  "scenes": [
    {
      "start": 0.0, "end": 2.55,
      "type": "wide", "camera": "static",
      "subjects": ["雪山"], "energy": "low",
      "transition_out": "cut"
    }
  ],
  "faces": [
    {
      "face_id": "p001",
      "scene_index": 3,
      "start": 55.2, "end": 58.7,
      "thumbnail": "analysis/C0117.MP4/face_p001.jpg",
      "embedding_ref": "analysis/C0117.MP4/embeddings.npz#0",
      "label": null
    }
  ],
  "speech": [
    { "start": 10.2, "end": 15.8, "text": "大家看这个湖", "speaker": "spk0" }
  ],
  "silent_segments": [
    { "start": 15.8, "end": 20.3 }
  ],
  "audio_stats": { "mean_volume_db": -18.5, "peak_db": -2.1 },
  "analysis_meta": {
    "vlm": "qwen2.5vl:7b-q8_0",
    "asr": "faster-whisper large-v3",
    "scene_detector": "pyscenedetect 0.6.4",
    "analyzed_at": "2026-08-07T10:00:00+08:00",
    "elapsed_sec": 84
  }
}
```

### 3.2 目录索引：`manifest.json`

```json
{
  "schema_version": 1,
  "folder": "/materials",
  "scanned_at": "2026-08-07T10:30:00+08:00",
  "video_count": 12,
  "total_duration_sec": 3612,
  "videos": [
    {
      "file": "C0117.MP4",
      "duration_sec": 187.4,
      "resolution": "3840x2160",
      "has_audio": true,
      "language": "zh",
      "summary": "雪山湖泊航拍，两人在水中游泳",
      "tags": ["航拍", "风景", "人物"],
      "scene_count": 6,
      "person_count": 2,
      "speech_ratio": 0.42,
      "silent_ratio": 0.18
    }
  ]
}
```

manifest 刻意只放精简字段，控制 DeepSeek 上下文成本；需要细节时按文件引用 analysis.json。人脸聚类信息（每个身份出现几段、共多少秒）也可汇总进 manifest，让方案模型直接按人物出剪辑方案。

### 3.3 操作目录：`operation-catalog.json`

执行层把"会干什么"暴露给方案层。新增剪辑动作 = 写一个执行函数 + 加一份 schema，系统自动扩展：

```json
{
  "schema_version": 1,
  "operations": [
    {
      "id": "trim",
      "description": "截取某段素材的指定时间范围",
      "params": { "file": "string", "start": "number", "end": "number" }
    },
    {
      "id": "remove_silence",
      "description": "去掉静音区间，可保留边距",
      "params": { "file": "string", "threshold_db": -35, "margin_sec": 0.3 }
    },
    {
      "id": "concat",
      "description": "按顺序拼接多个素材",
      "params": { "files": ["string"] }
    },
    {
      "id": "speed",
      "description": "变速",
      "params": { "file": "string", "factor": 1.5 }
    },
    {
      "id": "select_by_face",
      "description": "按人像检索所有含此人的片段并生成裁剪序列",
      "params": { "reference_photo": "string", "margin_sec": 0.5, "min_similarity": 0.6 }
    },
    {
      "id": "query_segment",
      "description": "精读指定时间段：返回该段关键帧、转写、画面描述，供方案模型在复杂需求下补查",
      "params": { "file": "string", "start": 0.0, "end": 10.0 }
    },
    {
      "id": "search_semantic",
      "description": "语义检索：按画面描述（如\"日落空镜\"）在所有素材的关键帧向量中检索，返回命中片段",
      "params": { "query": "string", "top_k": 10 }
    },
    {
      "id": "export",
      "description": "导出成片",
      "params": { "format": "mp4", "resolution": "1080p", "crf": 20 }
    }
  ]
}
```

### 3.4 剪辑方案：`edl.json`（DeepSeek 输出）

```json
{
  "schema_version": 1,
  "request_id": "req-20260807-001",
  "user_request": "把素材里的视频拼接，去掉没有声音的部分",
  "plan": [
    { "op": "trim", "file": "C0117.MP4", "start": 2.55, "end": 59.73 },
    { "op": "remove_silence", "file": "C0117.MP4", "threshold_db": -35, "margin_sec": 0.3 },
    { "op": "trim", "file": "C0048.MP4", "start": 0.0, "end": 30.0 },
    { "op": "concat", "files": ["C0117.MP4", "C0048.MP4"] },
    { "op": "export", "format": "mp4", "resolution": "1080p" }
  ]
}
```

执行层职责：schema 校验 → 检查文件引用都存在 → 排序（trim/remove_silence 先于 concat；**concat 前自动插入 normalize，统一编码/分辨率/帧率/采样率**）→ **dry-run 输出预览报告**（关键帧拼图 + 裁剪清单 + 预计时长）→ 用户确认或命令行确认 → 逐条执行 → 写审计日志。

### 3.5 示例需求："把含这个人的所有素材剪出来"

1. 理解阶段已在 analysis.json 里存好人脸嵌入（见 7.7 第 4 步）；
2. 用户上传一张人像 → 系统对其做人脸嵌入；
3. 与全部素材的人脸嵌入做余弦相似度匹配（阈值如 0.6）；
4. 命中所有场景/时间段 → 自动生成 EDL（trim + 可选 concat）→ 执行层出片。

这属于**确定性的向量检索**，不用大模型"猜"哪段有人；VLM 只负责补充"这个人出现在哪、在做什么"的上下文描述。

## 4. 组件选型

| 组件 | 选择 | 理由 / 可替换性 |
|---|---|---|
| 理解模型 | 8G 档：Ollama + Qwen2.5-VL-3B（默认）/ 7B-Q4（可试）；以后可升 Qwen3-VL | OpenAI 兼容 API，可无缝换成 vLLM、LM Studio 或云端 VLM |
| 语音转写 | 默认 FunASR（Paraformer + CAM++ 说话人分离，中文最稳）；faster-whisper 作英文/备选 | 只产 JSON，替换不影响其他层 |
| 切点检测 | PySceneDetect | 确定性工具，帧级精度 |
| 静音检测 | ffmpeg silencedetect / Auto-Editor | 纯命令，无模型成本 |
| 人脸识别 | insightface / ArcFace 嵌入 + 余弦相似度检索 | 确定性向量检索，不占大模型资源 |
| 语义检索 | CLIP 每关键帧向量（CPU 可跑） | 支撑"搜日落空镜"类画面语义检索，替代/补充 VLM 标签 |
| 方案模型 | 默认本地 Qwen3-VL（单模型）；可选线上 DeepSeek | 均为 OpenAI 兼容接口，`PLANNER_PROVIDER` 配置切换 |
| 执行 | ffmpeg + Auto-Editor，封装为操作注册表 | 新操作=新函数+新 schema |
| 服务框架 | FastAPI（理解服务/执行服务）+ Celery 或简单队列 | MVP 用同步任务，扩容时换 Redis/RabbitMQ 队列 |
| 批量调度 | 空闲检测 + 增量扫描 + 场景级断点续跑 | 可换任意调度器（Windows 任务计划 / cron / 自研常驻） |
| 对话编排 | MVP 直接调 DeepSeek；工具层可暴露 MCP，兼容 Claude/Codex 等成熟 agent | Dify 也支持 Ollama 和工具调用 |

## 5. 部署方案（Docker Compose）

```
services:
  ollama:
    image: ollama/ollama
    runtime: nvidia          # 需要 nvidia-container-toolkit
    environment:
      - OLLAMA_MAX_LOADED_MODELS=1     # 8G 显存：同一时刻只载一个模型
      - OLLAMA_KV_CACHE_TYPE=q8_0      # 省显存
    volumes:
      - ./models/ollama:/root/.ollama
    ports: ["11434:11434"]

  analyzer:
    build: ./services/analyzer
    depends_on: [ollama]
    environment:
      - VLM_BASE_URL=http://ollama:11434/v1   # OpenAI 兼容接口，可换成 vLLM
      - VLM_MODEL=qwen2.5vl:3b                # 8G 默认；7B-Q4 需试跑
      - ASR_MODEL=faster-whisper-small        # large-v3 可切 CPU 跑
      - NUM_CTX=8192
    volumes:
      - ./materials:/materials        # 素材只读挂载
      - ./analysis:/analysis          # JSON 输出

  executor:
    build: ./services/executor
    volumes:
      - ./materials:/materials
      - ./analysis:/analysis
      - ./output:/output
```

方案层不需要容器（走线上 API），可在主应用/CLI 中直接调用。GPU 透传：宿主机安装 `nvidia-container-toolkit`，容器加 `runtime: nvidia`。

空闲调度不依赖容器：Windows 用任务计划程序定时触发批量分析命令，Linux 用 cron；"GPU 空闲自动开工"由 analyzer 内的调度器实现（见 7.8）。

Windows 提示：Docker Desktop + GPU 透传（WSL2 + nvidia-container-toolkit）容易踩坑；Windows 上建议 Ollama 用**原生安装版**，分析/执行服务用 Python venv 或容器皆可，Compose 作为 Linux/服务器部署路径。

## 6. 硬件档位

| 档位 | 配置 | 能力 |
|---|---|---|
| 本机目标（8G） | RTX 3060 8G / 4060 8G，32G 内存，NVMe SSD | Qwen2.5-VL-3B（默认）或 7B-Q4；单并发串行，慢但能"吃透" |
| 舒适（推荐） | RTX 3090 / 4090 24G，64G 内存 | Qwen3-VL-8B 无压力，批量分析流畅 |
| 进阶 | 48G（A6000 / 双卡） | 32B 级模型，理解质量明显更好 |

磁盘：模型 5–20GB + 素材 + 中间产物，建议 NVMe SSD。CPU：4–8 核（ffmpeg 转码吃 CPU）。
8G 档位把"慢"交给空闲时段调度消化：白天对话用、夜里批量吃透素材，见 7.6–7.8。

## 7. 关键工程决策（防坑）

1. **语义与精度分离**：VLM 给语义和大致区间，PySceneDetect / silencedetect 给精确时间戳，合并时以检测器为准校正 VLM 时间。
2. **DeepSeek 输出必校验**：EDL 过 JSON Schema 校验，失败则把错误信息回喂重试一次；用 JSON mode / function calling 提高首过率。
3. **素材绝不上传**：只发 manifest 与 analysis JSON；上传前在本地做一次敏感字段过滤（如路径、文件名特殊字符）。
4. **命令注入防护**：EDL 字段全部白名单校验；ffmpeg 用参数数组调用，禁止拼接 shell 字符串。
5. **缓存与断点续跑**：分析结果按文件哈希缓存；批量分析可跳过已分析文件；执行层任务可重入。
6. **长视频处理**：VLM 抽帧上限 8–64 帧 + 分段分析；Qwen3-VL 支持 256K 上下文可整段喂入。
7. **4K 性能**：抽帧后先降采样再喂模型（如 192px–384px），可提速 10 倍以上。
8. **8G 显存策略**：模型量化 + 场景级分析 + 降采样 + 单并发，见 7.6。
9. **吃透素材**：多信号、分场景深分析 + 质量复核，见 7.7。
10. **空闲调度**：任务队列只在空闲时段跑，见 7.8。
11. **执行前预览**：渲染很贵（8G 机器一次成片可能几十分钟），EDL 必须先出 dry-run 预览报告，确认后再渲染，见 3.4。
12. **异构素材归一化**：不同编码/分辨率/帧率/采样率的素材不能直接 concat，concat 前自动转码归一化。
13. **质量基线（黄金样本）**：P0 建 5–10 段人工标注的黄金样本，每次改动跑回归，量化场景定位误差与标签命中率——这是"读素材必须准确"的度量标准。

### 7.5 为什么"先整理 JSON，再对话"这步不能省

单模型方案下，最容易犯的错是让模型在对话时"现看"视频。这会导致：

- 每次对话都要重新抽帧喂模型，又慢又贵（一段 70 秒素材分析要 1–4 分钟）；
- 整个素材库的视频帧塞不进一个对话上下文，模型"记不住"全部素材；
- 时间戳仍然不准，仍需检测器校正。

正确流程：**素材入库时批量分析一次 → 缓存成 JSON → 对话时只引用 JSON**。对话因此变成纯文本推理，快、便宜，而且模型一次就能"掌握"整个素材库。这步对所有方案（A 和 B）都成立。

### 7.6 8G 显存运行策略

- **模型档位**：默认 `qwen2.5vl:3b`（权重约 2GB，最稳）；可尝试 `qwen2.5vl:7b-q4_k_m`（约 5GB），配 `OLLAMA_KV_CACHE_TYPE=q8_0`、`NUM_CTX=4096–8192`，超显存部分自动溢出到 CPU 内存。
- **场景级分析而非整段抽帧**：先 PySceneDetect 切场景，每场景取 2–4 帧、降采样到 192–384px 再喂模型——显存占用小，理解还更深（见 7.7）。
- **单并发 + OOM 降档**：分析队列固定 1 个并发；Ollama 报显存不足时自动降档（7B→3B）只重试当前场景，不中断整个批任务。
- **ASR 分流**：faster-whisper `small` 跑 GPU，`large-v3` 放 CPU（慢但准）；中文可换 FunASR。
- **内存建议 32GB**：模型溢出层、ASR、ffmpeg 同时存在时更从容。
- **对话与后台错峰**：对话优先；后台批量分析只在空闲时段运行（7.8），两者天然错开。

### 7.7 吃透素材：深分析流水线

每段素材按五阶段走完，缺一不可：

1. **探测**：ffprobe 读时长/分辨率/帧率/码流/音轨。
2. **切场景**：PySceneDetect 先切出全部镜头，拿到精确时间戳。
3. **逐场景 VLM 深分析**：每个镜头取开头/中间/结尾代表帧（2–4 帧，192–384px），逐项提问：内容、主体、景别、运镜、光线、情绪、适合的剪辑用途（B-roll/空镜/定场/转场垫片），并存关键帧缩略图。整段视频只抽 8 帧 vs 逐镜头分析，信息量完全不同——这就是"吃透"的关键。
4. **人脸识别**：对每场景代表帧跑人脸检测 + 嵌入（insightface / ArcFace），产出 `faces` 列表（场景、时间、人脸缩略图、embedding 引用）。这是"按人像检索素材"的确定性基础，不依赖 VLM 猜。
5. **语音与音频**：ASR 转写（时间戳 + 说话人）、静音区间、响度统计；可选 OCR 画面文字。
6. **汇总与复核**：合成片级 summary + tags + 每场景 `confidence`；低于阈值的场景换提示词自动重试一次；全部完成后跑一轮"复核"（把各场景摘要再喂一次，检查遗漏和矛盾）。

输出仍是 analysis.json（场景数组更丰富），所有中间产物（关键帧、每场景 JSON）按文件哈希缓存，支持场景级断点续跑。

图片素材（jpg/png）同样支持：直接走单图分析（复用同一 VLM，比视频快一个量级），产出同构 JSON——无时间轴，视为单个"场景"。用户提供的人像照片走人脸向量匹配（insightface），参考图走 CLIP/VLM 语义检索，不占用理解层批处理。

### 7.8 空闲时段批量调度

- **触发方式**：Windows 任务计划程序 / Linux cron 定时启动批量分析；或 analyzer 常驻，检测"GPU 空闲"自动开工。
- **空闲判定**：`nvidia-smi` 显示 GPU 利用率 <10% 且持续 N 分钟才跑；也可手动指定时间窗（如 23:00–08:00）。
- **增量与续跑**：按文件哈希去重，已分析文件跳过；中断后从场景级断点继续，不重头来。
- **优先级**：手动触发的分析 > 空闲自动批量；对话请求 > 后台任务。
- **进度可见**：写 `analysis-status.json`（待处理/分析中/完成/失败/低置信度待复核），P2 用界面展示。
- **完成通知**：可选（邮件/钉钉 webhook），MVP 可不做。

### 7.9 粗读 + 精读：复杂需求怎么保证准确

JSON 是视频的**有损摘要**，纯文本模型不可能真正"画出"画面。复杂剪辑需求（如"把 A 和 B 谈话、B 提到合同的部分剪出来""剪出光线最美的日落镜头"）单靠一次预分析很难全对。解法是两级读取：

1. **粗读（入库时批量）**：五阶段预分析生成 JSON——覆盖面广，服务大多数常规需求。
2. **精读（对话时按需）**：方案模型发现 JSON 信息不足时，调用 `query_segment` 工具，对指定时间段现场抽帧 + 转写 + 重新问 VLM，拿到该段落的准确信息再出 EDL。模型"拿不准"时才去补查，而不是永远赌预分析。

准确性的四个工程保证：

- **时间戳以确定性工具为准**：场景切点、静音区间、ASR 逐词时间戳来自检测器，VLM 的时间估计只做参考；
- **多信号交叉验证**：人是谁由人脸向量定，说了什么由 ASR 定，画面内容由 VLM 定，三者独立产出再合并，互相纠错；
- **置信度与复核**：每个场景带 `confidence`，低置信度自动重试或标记待复核；
- **关键帧全缓存**：精读时直接复用缩略图，不用重新抽帧，秒级返回。

这条机制就是"读素材必须准确"的直接落点：**粗读保证广度，精读保证深度，复杂需求靠两者配合，而不是赌一次分析的质量**。

## 8. 分阶段交付路线

| 阶段 | 内容 | 验收标准 | 工期参考 |
|---|---|---|---|
| P0 技术验证 | 8G 机上确认 3B / 7B-Q4 跑通；单段素材完成五阶段深分析；Auto-Editor 去静音；建立 5–10 段黄金样本基线 | 1 段素材 → 完整吃透的 analysis.json + 去静音成片 + 基线分数 | 2–3 天 |
| P1a 单素材闭环 | 单段素材：理解 → JSON → manifest → 方案模型出 EDL（含 query_segment 精读）→ dry-run 预览 → 执行出片 | 1 段素材经对话完成一次复杂剪辑（含精读与预览确认） | 3–5 天 |
| P1b 批量与检索 | 批量分析 + 增量扫描 + 场景级断点续跑 + 空闲调度；人脸嵌入与按人像检索；search_semantic 语义检索 | 文件夹 10 段素材 → 空闲时段自动吃透 + 按人像/语义检索出片 | 1 周 |
| P2 工程化 | 复核轮、置信度面板、Gradio/Streamlit 界面、EDL 人工预览修改、审计 | 可日常使用，出错可追溯可恢复 | 2–3 周 |
| P3 扩展 | 新剪辑动作、OCR/节拍信号、剪映/Premiere 工程导出、多机扩容 | 按需扩展，不影响既有流程 | 持续 |

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 8G 显存 OOM | 3B 默认 + 场景级分析 + 降采样 + 单并发 + 自动降档重试 |
| VLM 时间戳不准 | 检测器校正 + 语义映射（CineSight 已验证此路线） |
| 方案模型生成非法 EDL | schema 校验 + 回喂重试 + 操作目录约束 |
| 素材量大、分析慢 | 空闲时段调度 + 增量扫描 + 场景级断点续跑，把慢变成"夜里跑" |
| 长视频上下文不足 | 分段分析，以场景为粒度，不整段喂 |
| 异构素材 concat 花屏/失败 | concat 前自动 normalize（统一编码/分辨率/帧率/采样率） |
| 说话人分不清 | ASR 默认 FunASR + CAM++ 说话人分离（faster-whisper 原生无此能力） |
| 渲染浪费（方案不对） | EDL dry-run 预览确认后再渲染 |
| 模型迭代快被锁定 | 全部走 OpenAI 兼容接口，配置即换模型 |

## 10. 待确认项

- [ ] GPU 已按 8G 显存规划（默认 Qwen2.5-VL-3B / 7B-Q4，待 P0 实测确认）
- [ ] 对话模型：本地单模型（Qwen3-VL）还是线上 DeepSeek，或两者可切换
- [ ] 是否要求素材完全不出本地（决定是否启用 DeepSeek 通道）
- [ ] 素材规模量级（单批几个视频还是数百 GB）
- [ ] 空闲时段偏好：自动检测 GPU 空闲，还是固定时间窗（如 23:00–08:00）
- [ ] 按人像检索是否 P1 必需（已规划为人脸嵌入信号，随时可加）
- [ ] 是否需要界面（命令行先跑通，P2 再加 UI）
- [ ] 成片是否需要导出剪映/Premiere 工程（影响 P3 排期）

## 11. 现成项目盘点（2026-08）

| 项目 | 能力 | 部署 | 与本方案的关系 |
|---|---|---|---|
| NarratoAI | 影视解说 + 自动剪辑流水线 | Docker / 整合包 | 完整流水线参照，可起步试用 |
| VibeClip | 对话式把长视频剪成竖屏短片 | Docker（BYO LLM） | 最接近"对话剪辑"的现成方案 |
| FunClip（阿里） | ASR 转写 + LLM 辅助按文本/说话人裁剪 | 本地 Gradio | 语音裁剪积木 |
| Tailor | **人脸剪辑：选一张人脸 → 自动裁出所有相关片段** | Windows 桌面（2024 年更新） | 现成的按人像裁剪成品，思路可借鉴 |
| VisualClipPicker | 按人脸存在/角度分段（**不能指定具体某个人**） | Windows，2023 年停滞 | 仅人脸存在性，不够用 |
| Immich | 素材库管理 + 人脸识别聚类（**整文件粒度**，不裁子片段） | Docker，成熟 | 素材管理积木 |
| CineSight | 素材文件夹 → 剪辑决策 JSON | macOS | 理解层参照（语义+检测器校正路线） |
| roughcut / paperedits / openscene | 本地优先的对话式/转录式剪辑 | 桌面端，较新 | 对话剪辑的另一种实现 |
| cleancut | 本地多层信号检测 + EDL + ffmpeg 执行 | Python | EDL + 执行层参照 |

**结论**：没有一个现成项目同时覆盖"逐镜头吃透素材 + 按人像裁出子片段 + 对话出方案 + 本地可扩展"。本方案的定位是拼装现成积木（ffmpeg / insightface / PySceneDetect / Whisper / Ollama-VLM），并补齐缺失的编排层。

## 12. 方案评审结论（2026-08-07）

### 12.1 总评

架构方向与行业成熟模式一致：信号分解 + 确定性工具校时 + LLM 规划 + 确定性执行，没有方向性错误。主要问题在工程细节与首版范围。

### 12.2 与成熟技术方案的对比

| 维度 | 本方案 | 商用成熟（剪映 / Descript / CapCut） | 开源成熟（VibeClip / NarratoAI / FunClip） |
|---|---|---|---|
| 素材理解 | 本地 VLM 逐镜头语义 + 多信号 | 云端模型为主 | 多为转写驱动，视觉理解浅 |
| 时间精度 | 检测器校正，帧级 | 依赖云模型 | 部分有（ASR 逐词时间戳） |
| 按人像检索 | 本地人脸向量，子片段粒度 | 有（闭源） | 基本没有 |
| 语义检索 | CLIP 向量（新增） | 有（闭源） | Immich/OpenCut 有，剪辑类没有 |
| 隐私 | 素材不出本地 | 素材上云 | 混合 |
| 可扩展性 | 契约化，模型/工具可换，可暴露 MCP | 黑盒 | 弱（个人项目） |
| 工程成本 | 需要自建（主成本） | 零 | 低（但不满足全部需求） |

### 12.3 优化点与优先级

1. **先做（影响准确性和可用性）**：ASR 换 FunASR 拿说话人分离；EDL dry-run 预览确认；concat 前自动归一化。
2. **低成本高价值**：CLIP 语义向量（画面语义检索）；工具层暴露 MCP，兼容成熟 agent 生态。
3. **质量保障**：黄金样本回归基线，让"读素材必须准确"可度量、可回归。
4. **范围控制**：P1 拆 P1a（单素材闭环）/ P1b（批量与检索），先跑通再铺开。

## 14. 便携部署：单文件夹 + 离线缓存 + Docker（2026-08-07）

部署目标为**另一台电脑**：本工作区只负责打包，不在本机启动服务。

### 14.1 部署包位置

`ai-video-studio/` 是完整部署包：全部代码、Dockerfile、依赖缓存、模型缓存、构建/部署脚本都在其中。复制该文件夹即得到完整系统。

```
ai-video-studio/
├── docker-compose.yml / docker-compose.gpu.yml
├── config/operation-catalog.json
├── services/  (common / analyzer / executor / planner)
├── models/    (ollama 模型、whisper 模型，构建机预缓存)
├── offline/   (wheels/ pip 离线包；images/ 镜像 tar)
├── materials/ analysis/ output/
└── scripts/   (build-offline.* 构建机；load-and-start.* 目标机)
```

### 14.2 两段式流程

**构建机（有网络，必要时可科学上网）**——只跑一次：

```powershell
.\scripts\build-offline.ps1 -VlmModel qwen2.5vl:3b
```

脚本依次完成：pip 依赖缓存（Linux wheel）→ 拉 Ollama 镜像并预拉模型 → 下载 whisper 模型 → 构建服务镜像 → `docker save` 导出镜像 tar。需要科学上网才能下载的东西全部在这一步缓存进文件夹。

**目标机（离线）**——复制整个 `ai-video-studio/` 文件夹过去后：

```powershell
.\scripts\load-and-start.ps1        # CPU 模式
.\scripts\load-and-start.ps1 -Gpu   # NVIDIA GPU 模式
```

### 14.3 离线覆盖清单

| 依赖 | 缓存位置 | 说明 |
|---|---|---|
| pip 依赖 | `offline/wheels/` | Docker 构建用 `pip install --no-index --find-links=/wheels`，无需联网 |
| Ollama 镜像 | `offline/images/ollama.tar` | 需科学上网时在构建机缓存 |
| VLM 模型（qwen2.5vl） | `models/ollama/` | 构建机 `ollama pull`，随文件夹走 |
| Whisper 模型 | `models/whisper/` | faster-whisper 离线转写 |
| 服务镜像 | `offline/images/analyzer|executor|planner.tar` | 目标机 `docker load` |

### 14.4 平台与 GPU

- Linux 目标机：安装 `nvidia-container-toolkit`，用 `docker-compose.gpu.yml` 叠加 GPU；
- Windows 目标机：Docker Desktop + WSL2（默认支持 CUDA）；若 Docker GPU 透传踩坑，Ollama 可原生安装，服务容器走 `host.docker.internal:11434`；
- 无 GPU：CPU 模式可运行（慢），理解层自动适配。

### 14.5 当前骨架范围（P1a 已实现 / P1b 待办）

已实现：理解（探测/场景切分/关键帧/VLM 语义/ASR 转写/静音检测 → analysis.json + manifest.json）；执行（trim / remove_silence / concat / speed / export，concat 前自动归一化，dry-run 预览 + 审计）；方案（对话 + 工具调用：query_segment 精读 / search_semantic 检索 / preview / execute）。

P1b 待办：人脸嵌入与按人像检索（`FACE_ENABLED=true`）、CLIP 语义向量、空闲时段自动调度器。

## 13. 理想状态端到端示例

场景：素材文件夹"西藏行"含 10 段视频 + 30 张照片，共约 100 分钟。系统空闲时段自动全部吃透。

### 13.1 入库后自动吃透的产物

- 每段素材 `xxx.analysis.json`（场景/人物/台词/静音/关键帧/向量）；
- `manifest.json`：人物聚类（p001=小王 12 段共 8.2 分钟、p002=小李 9 段共 6.5 分钟）、标签统计（航拍 5 段、篝火 2 段、采访 3 段）、时长/分辨率汇总；
- 关键帧缩略图 + CLIP 向量 + 人脸向量（供检索与精读）；
- `analysis-status.json`：全部完成，无低置信度待复核项。

`B_采访.mp4.analysis.json`（关键内容节选）：

```json
{
  "summary": "小王和小李在帐篷前聊明天去纳木错的计划",
  "scenes": [
    { "start": 0.0, "end": 58.3, "type": "interview",
      "subjects": ["小王", "小李", "帐篷"], "lighting": "golden_hour",
      "energy": "medium", "confidence": 0.94 }
  ],
  "speech": [
    { "start": 12.4, "end": 15.8, "speaker": "spk0", "text": "明天我们一早就去纳木错" },
    { "start": 16.1, "end": 21.3, "speaker": "spk1", "text": "那得提前起来，路上要四个小时" }
  ],
  "faces": [
    { "face_id": "p001", "start": 0.0, "end": 58.3, "embedding_ref": "analysis/B_采访.mp4/faces.npz#0" },
    { "face_id": "p002", "start": 0.0, "end": 58.3, "embedding_ref": "analysis/B_采访.mp4/faces.npz#1" }
  ],
  "silent_segments": [ { "start": 21.3, "end": 26.0 } ]
}
```

### 13.2 一次复杂对话

> 用户：把小王和小李聊到"纳木错"的片段剪出来，加采访字幕，再接一段日落空镜，整体 2 分钟内，去掉静音和废镜头。

处理链路：

1. 方案模型读 manifest：知道 p001=小王、p002=小李、B 素材含采访；
2. ASR 全文检索"纳木错" → 命中 B 素材 12.4–21.3 秒（精确到词）；
3. `query_segment` 精读候选段 → 确认画面是两人对话；
4. `search_semantic("日落 湖面 空镜")` → CLIP 命中 A 素材 2 个场景 + 3 张照片；
5. 生成 EDL：trim 采访段 → remove_silence → 加字幕 → concat 空镜 → export；
6. dry-run 预览：关键帧拼图 + 时间轴清单 + 预计时长 1:47；
7. 确认 → 渲染 → 出片 + 审计报告。

### 13.3 最终交付物

- **成片**：`西藏行-精华版.mp4`（1:47，1080p，含字幕）；
- **edl.json + audit.json**：每条剪切的来源、理由、信号依据（ASR 词命中 / 人脸向量 / CLIP 分数），可回溯；
- **可选**：剪映草稿、SRT 字幕、竖屏 9:16 版本、多语言字幕；
- **素材库侧**：人脸相册（上传小王照片 → 全库 12 段镜头合集一键出片）、语义搜索（"日落空镜"直接出片）、全部素材的检索式档案。
