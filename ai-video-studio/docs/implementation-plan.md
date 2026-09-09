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

## 15. 2026-09 架构调整：视觉大模型可选 + 免 VLM 的 AI 自动剪辑

### 15.1 背景（为什么改）

1. 本机（8G 档）跑不动 `qwen2.5vl:3b`：VLM 分析要么 OOM 要么 CPU 上慢到不可用；
2. 旧实现里 VLM 调用失败后每个场景仍要白等 180s 超时，一个 72 场景的视频分析卡死数小时；
3. 实测发现**流剪切（-c copy）在关键帧稀疏的源（老剧 AV1/HEVC 转码）上切点漂移 5~10 秒**，
   相邻片段内容重复，成片时长膨胀近一倍——"剪出来必然对"的承诺在执行层就是假的；
4. 结论：AI 剪辑的准确性不能押在 VLM 上，改为**确定性信号为主、VLM 为可选增强**。

### 15.2 改动清单（全部已实测）

| 层 | 改动 |
|---|---|
| analyzer | `VLM_ENABLED`（auto/false/true）+ 连续失败 2 次自动熔断；VLM 缺席时 **Chinese-CLIP 零样本场景标注**兜底（16 个中文标签 ↔ 关键帧向量比相似度，生成 content/labels/confidence）；能量由**帧间差异动量+切点密度**确定性估计；`ASR_DEVICE` 配置 + 转写层 CPU 回退（cublas DLL 缺失不再卡死） |
| planner | 新增 **`POST /api/auto_edit`**：需求+目标时长 → 场景候选池 → bge-m3 文字 0.45 / CLIP 画面 0.55 双通道打分（无需求则时长适配+台词占比启发式）→ 贪心选段凑目标时长 → trim+concat+export 直接无损出片；`OLLAMA_BASE` 与对话模型地址解耦（切线上 DeepSeek 后 bge-m3/模型状态仍走本地 Ollama）；`_llm_chat` 不再向线上 API 发 Ollama 专属 `num_ctx` |
| executor | **trim 精准化**：先流切 → 回读成品时长校验（±0.25s）→ 漂移则自动改帧级精准重编码（`-ss` 前置 + libx264）；超 180s 的长切接受关键帧级精度；`_same_spec` 增加 fps 比对 |
| 规范化 | `_normalize_model_edl` 按 plan 内 trim 真实顺序重写 concat.files 的派生 key（模型常输出重复原名导致首段重复拼接）；`/api/clip_selected` 修同样的交叉勾选顺序 bug；`_match_shot_scenes` 不再污染场记索引缓存 |
| 部署 | **start_local.ps1 加载 `.env`**（覆盖脚本默认值）——线上 DeepSeek/`VLM_ENABLED`/`ASR_DEVICE` 原生模式即配即用；注意：含中文的 .ps1 必须带 UTF-8 BOM，否则 PowerShell 5.1 按 GBK 误解直接语法错误 |

### 15.3 实测结果（本机，2026-09-05）

- AI 自动剪辑「猴子 猴王 / 目标 30s」：9.9s 出片，9 段共 29.9s（修复前同请求得 53s 重复内容）；
- 免 VLM 分析 8s 测试片：15s 完成（场景/标签/能量/向量齐全），标签命中（海边戏→"水边海浪"）；
- DeepSeek 对话剪辑：6.3s 出方案（本地 7B 需数分钟且常格式错）；场记双通道检索正常（text 0.62 / clip 0.39）。

### 15.4 取舍

- CLIP 零样本标签是**弱语义**（能分清"打斗/风景/特写"，认不出"孙悟空"）；VLM 可用的机器设
  `VLM_ENABLED=true/auto` 即恢复自由文本描述，数据契约不变（labels 为新增可选字段）；
- 免 VLM 模式下"按人物剪"依赖对话模型读场记，人物实体信息弱——后续可加人脸聚类补齐（P1b 原计划）；
- trim 漂移回退会重编码部分片段，混合规格 concat 会触发整体归一化（多一次转码）——正确性优先。

## 16. 2026-09 专业剪辑衔接：剪映草稿/SRT 导出 + 时间线剪辑升级

### 16.1 背景

AI 粗剪的定位是"选段"，精修交给专业工具。两条路：
1. **导出剪映草稿**：选区直接变成剪映首页可见的草稿（素材为本机绝对路径），打开剪映即可继续精修；
2. **导出 SRT**：选段内的 ASR 台词平移到成片时间轴，随粗剪一起交付（后期配音/字幕直接用）。

同时把网页时间线从"只能分割/删除/撤销"升级到常规剪辑软件的操作面。

### 16.2 改动清单（全部已实测）

| 层 | 改动 |
|---|---|
| common | 新增 `jianying_draft.py`：选区列表 → `draft_content.json` + `draft_meta_info.json`（微秒时间轴；materials.videos 绝对路径；segments 的 target/source_timerange + speeds/canvases 引用；剪映打开时自动补全其余字段，兼容 6/7/10.x 明文草稿） |
| planner | 新增 `POST /api/export_jianying`（选区 → 草稿文件夹 + zip；探测到剪映草稿库 `%LOCALAPPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft` 或 `JIANYING_DRAFT_DIR` 环境变量时自动复制进去）、`POST /api/export_srt`（选区 → SRT，台词按选区裁剪并平移到时间轴，UTF-8 with BOM）、`GET /exports/{path}`（zip/srt 下载，防目录穿越）；`OUTPUT_DIR` 环境变量进 start_local.ps1 planner 分支 |
| 时间线 | **拖左右黄边裁剪**（0.1s 步进，限制在源素材时长内、最短 0.2s）、**拖片段本体调顺序**、**恢复（redo）栈**、**复制选中片段**、快捷键（空格播放 / S 分割 / Del 删除 / Ctrl+D 复制 / Ctrl+Z 撤销 / Ctrl+Y 恢复 / ←→ 移动播放头）；`tlFileDur` 记录各素材总时长供裁剪钳制 |
| 入口 | 时间线剪辑面板、场记单勾选栏、AI 自动剪辑结果三处都有「🪄 导出剪映草稿 / 💬 导出SRT」；AI 结果另加「进时间线手动调」 |

### 16.3 实测（2026-09-05）

- `/api/export_jianying` 两段选区（10-25s / 100-130s）：草稿 JSON 校验通过——target_timerange 顺序无缝（0s/15s）、source 对应 10s/100s、素材绝对路径存在、画布自动取素材分辨率 960x540、speed/canvas 引用齐全、meta 的 tm_duration 与 content 一致；zip 下载 200；
- `/api/export_srt` 映射单测（注入 4 条台词）：跨选区起止的台词正确裁剪（99-105s → 15-20s；128-135s → 43-45s），无台词素材返回空 SRT 不报错；注意 **SRT 依赖分析文档里的 speech，ASR 修复前生成的旧分析需重新点「分析」补台词**；
- 浏览器实测：拖右缘 -120px → 10-60s 变 10-50s；拖左缘 +60px → 15-50s；Ctrl+Z/Ctrl+Y/S/Del/Ctrl+D/空格 全部生效；手柄/按钮渲染正常；
- 路径穿越（`/exports/../app.py`、URL 编码 `..%2F`）均 404。

### 16.4 一致性清理（2026-09-05 第二轮）

- 前后端对账：所有 onclick/JS 函数/fetch 路由三方核对，无坏按钮、无失效请求；planner 调 analyzer/executor 的端点全部存在；
- 删除 6 个已被新工作流取代且 UI/内部/文档三处零引用的死端点：`/api/execute`（chat 工具直连 executor）、`/api/clip_match` + `/api/snippets` + `/api/snippet_delete` + `/api/merge_snippets`（被 场记勾选→clip_selected 取代的旧"片段库"流）、`/api/script_clip`（被 script_match/script_text_clip 取代），连带 `_match_and_clip`/`_llm_filter_batch`/`_script_clip_run` 等死助手；保留 `_plog`/`/api/process_log`（/chat 流水线配套）与 `_llm_filter_scenes`（chat 在用）；
- 删除与 start_local.bat/stop_local.bat 完全重复的 one_click_start.bat / one_click_stop.bat（全仓零引用）；素材页标题「素材选择与 AI 对话」改为「素材选择」（对话 UI 早已不在该页）；
- 启停脚本实测：stop→start→三服务 200 + Ollama 检测→重复 start 幂等跳过→stop 释放端口且保留 Ollama，全链路通过；.env 11 项正常加载（VLM_ENABLED=false 生效）。

### 16.5 体验优化（2026-09-05 第三轮）

- **重新分析入口**：`/scan` 全链路（analyzer `ScanRequest.force` → planner `/api/scan` → 前端「🔁 重新分析（覆盖）」按钮，带确认弹窗）。用途：给 ASR 修复前生成的旧分析补台词（SRT/检索依赖 speech）、VLM 配置变化后刷新标注。实测：force 重析 p01 后 speech 0→3 条；
- **波形磁盘缓存**：`/api/waveform` 结果按 `mtime+points` 存 `_analysis/<素材名>.wave.json`，重复打开时间线不再重算 ffmpeg PCM 提取；
- **启动脚本健康检查改轮询**：start_local.ps1 [3/3] 由"等 6 秒查一次"改为逐服务轮询最长 90 秒（analyzer 首次导入 torch/cv2 慢时不再误报"未就绪"）。

### 16.6 前台重构：三个 Tab 对应后台服务（2026-09-05 第四轮）

问题：页面是一整条长滚动，7 个功能区块无分组、两条重复的剪辑入口（目录页「时间线剪辑」下拉勾选 vs 素材页「合并到时间线」）、
过时的 4 步骤条还写着已移除的「对话」，用户反映"看不懂、没法用"。

重构（功能与 ID 全保留，只动布局与入口）：
- 顶部三个 Tab，一一对应后台服务：**① 素材分析 = analyzer :8001**（目录/分析/重新分析/进度 + 免分析批量剪）；**② 剪辑出片 = planner :8003 + executor :8002**（素材勾选 → AI 自动剪 / 手动精剪 / 场记单 → 成片 / 剪映草稿 / SRT）；**③ 进阶：剧本剪辑 = planner :8003**（文本剧本/JSON/线上生成 + 剧本序列）；
- Tab 栏下方加「前后台对应」说明条（每个 Tab 标题也带服务徽标）；
- 删除目录页重复的"时间线剪辑"选文件夹入口及其专属 JS（loadEditFiles/openEditFromSel/editSelFiles），手动精剪统一走 ② 的勾选入口；删除过时步骤条与 markStep；
- ② 内加"三种剪法选一个"引导文案；③ 头部有「← 去 ② 勾选素材」跳转。

实测：node --check 通过；浏览器验证 Tab 切换/选目录→下一步→素材卡渲染/勾选→手动精剪进时间线（片段+裁剪手柄正常）/剧本区完整。

## 17. 2026-09-06 定型：字幕驱动的视频剪辑（按用户最终定位重构）

定位确认：画面语义选段不可靠（用户实测"分析出来的结果剪不了"），改为**字幕（台词时间戳）驱动**——
借鉴 FunClip（modelscope，选台词剪片段）/ autocut（mli，文本即时间轴）的交互，用仓库已内置的开源件
（faster-whisper 本地语音模型 + ffmpeg）实现，未引入新依赖。

### 17.1 改动清单

| 层 | 改动 |
|---|---|
| 模型 | 新增 **faster-whisper-medium**（约1.5GB，HF 镜像 hf-mirror.com 下载到 `models/whisper-medium`）；start_local.ps1 自动优先用 medium（存在即用），否则回退内置 small。转写加 `initial_prompt="以下是普通话简体中文的句子。"` 偏置简体（老剧素材偶尔出繁体） |
| analyzer | 新增 `POST /transcribe`（**只跑语音转写**，不跑画面理解；无分析文档则生成精简文档，有则只更新 speech 字段）；`/scan` 支持 `mode="asr"`（整目录批量提取字幕） |
| planner | 新增 `POST /api/transcribe`（代理）、`POST /api/match_lines`（**一句话智能勾选**：台词清单交对话模型做文本相关性挑选，输出行号+理由；线上 DeepSeek / 本地 qwen 均可）、`POST /api/burn_srt`（**烧录硬字幕**：libass + Microsoft YaHei，切目录相对路径规避 Windows 转义；输出 `*_sub.mp4`） |
| 前端 | **Tab① 素材库·提取字幕**（分析按钮→「🔊 提取字幕（全目录）」，走 asr 模式；按时间规则批量剪保留）；**Tab② 字幕剪辑·手动精剪**（勾素材→提取字幕→台词列表勾选/一句话智能勾选→剪拼成片，可选烧硬字幕；导出剪映草稿/SRT/进时间线手动调）。**下线**：AI 自动剪辑、场记单剪辑、剧本剪辑 Tab（对应后端接口保留未删，前端无入口） |

### 17.2 实测（本机，medium 模型 CPU int8 4线程）

- 3 分钟《西游记》p01 转写 25 秒出 3 句台词（此段本就台词少；BGM 大时 VAD 会滤掉部分语音，属正常取舍）；
- 「石猴出世的段落」智能勾选：DeepSeek 命中 2/3 句并给出理由（"石猴自称尸猴，相关"）；
- 勾选台词 → 剪出 4.1s 成片（无损拼接 4 步全过）→ 烧硬字幕成功输出 `*_sub.mp4`（960x540，libass 中文字幕）；
- 浏览器全流程：提取字幕（缓存命中秒回）→ 台词列表勾选 → 剪辑按钮联动正常。

### 17.3 全面测试（2026-09-06，scripts/test_subtitle_suite.py，18/18 通过）

- 健康×3、缓存转写 1.6s、**全新转写 59s 素材 84s 出 18 句**（medium CPU，台词准确且密度高）、图片素材不崩、不存在素材报错；
- match_lines 正常/空清单边界、**按台词剪时长精确**（期望 9.00s vs 实际 9.15s，容差内）、烧硬字幕成功且时长一致（抽帧目视确认"字幕测试第2句"压在画面底部）、burn_srt 错误路径、剪映草稿/SRT 导出、批量剪启动；
- 浏览器全流程：未分析素材可直接勾选 → 提取字幕 → 一句话智能勾选（"提到钱的句子"命中 3 句）→ 剪出成片。

### 17.4 测试中发现并修复的 bug

- **新素材无法勾选**（鸡生蛋）：卡片勾选被"未分析"拦截，而 `analyzed` 标记来自完整分析的 manifest，只提取字幕不写 manifest → 永远选不中。修复：`_transcribe_only` 两条路径都调 `_update_manifest`；前端取消"未分析不让选"拦截（字幕工作流本身不需要先分析）。
- Ollama 冷启动超 60 秒导致 start_local 误报失败：等待放宽到 120 秒。

## 18. 2026-09-07 声音分析定型 + 说话人/剧本/台词JSON（用户定位最终版）

用户最终定位：核心功能 = **声音分析**（不再有视频画面分析）——每个视频生成分析文档（JSON：谁在何时说了什么）；
剪辑诉求 =「把某人说的话全部剪出来」「给一个剧本，按剧本把对应的话全剪出来」。

### 18.1 改动清单

| 项 | 内容 |
|---|---|
| 启动 | start_local.ps1 健康检查全过后**自动打开浏览器** http://localhost:8003 |
| 说话人 | `POST /api/attribute_speakers`：全量台词一次对话模型调用，按称呼/上下文推断每句说话人，写回分析文档 speech[].speaker；提示词明确"没名字用角色称呼（女人/男人/师父/旁白），尽量不写未知"；前端「🗣 标记说话人」按钮 + 台词列表橙色说话人徽标 |
| match_lines | 提示词强化：指定说话人时**只挑标记一致的行**（别人说的绝不选）。实测"把男人说的话剪出来"精确命中 9/18（男人 9 句 0 混入；修过一版 18/18 全选的宽松提示词） |
| 按剧本剪 | `POST /api/script_match_lines`：剧本逐条 ↔ 台词清单一次批量匹配（时间戳确定性，模型只做文本匹配）；前端 📜 按剧本剪区（每行一条剧本 → 匹配结果预览 → 🎬 按剧本顺序剪成一条 / 转手动勾选微调 / 导出剪映草稿） |
| 台词 JSON | `POST /api/export_subtitles_json`：目录级汇总（所有视频的对白+时间戳+说话人合成一个 JSON），output/exports/ 下可下载——给剧本剪辑/外部工具当数据源 |
| 前端 | subLines 透传 speaker；文案全面改为"声音分析"定位；修复上一轮结构手术吃掉的两个闭合 </div> |

### 18.2 实测（59s 对话素材，medium + DeepSeek）

- 说话人标注 4s：女人 9 句 / 男人 9 句，对话交替全部正确；
- "把男人说的话剪出来"：9/18 命中，0 混入；
- 剧本（质问钱→反驳→感叹人命）→ 命中 4 句，按剧本顺序剪出 11.2s 成片（期望 11.0s）；
- 台词 JSON：目录 1 视频导出 18 句含说话人；
- 启动脚本重启后浏览器自动打开。

## 19. 2026-09-07 专用端口段：57800~57803（与其他项目彻底隔离）

用户机器上项目多（TTS/ComfyUI/训练服务等），8001-8003/11434 这类通用端口冲突频发，迁移到专用段：

| 服务 | 旧端口 | 新端口 |
|---|---|---|
| Ollama（本项目独立实例） | 11434 | **57800** |
| analyzer | 8001 | **57801** |
| executor | 8002 | **57802** |
| planner（网页） | 8003 | **57803** |

- start_local.ps1：`OLLAMA_HOST=127.0.0.1:57800` 启动本项目专属 Ollama（模型目录独立，与其他项目的 Ollama 互不干扰）；所有探活/健康检查改用 **127.0.0.1**（localhost 会先解析 IPv6 ::1，服务只绑 IPv4 时误判）+ `[System.Net.WebRequest]::DefaultWebProxy = $null`（PS 5.1 不认 NO_PROXY 环境变量，Clash 系统代理会劫持探活请求）；Ollama 输出落 runtime/logs/ollama.log 便于排障
- docker-compose.yml 只改宿主机映射（容器内端口不变）；.env 无端口键不受影响
- 排障记录：迁移后首次启动误报"Ollama 启动失败"，实际已监听 57800——就是探活走了 localhost/系统代理；另外发现两个旧 ollama 实例（11434/57800）共享同一模型目录互相干扰，已全部清掉重启

### 19.1 按用户愿景补全的功能缺口（2026-09-07，代码审查后）

- **📄 导出台词JSON（全目录）**：Tab① 新增按钮——目录内所有视频的对白+时间戳+说话人汇总成一个 JSON 下载（"结算文件"有了界面入口）；
- **🗣 说话人（全目录）**：Tab① 新增批量标注（逐素材调 /api/attribute_speakers，已标的自动跳过，进度条可见）；
- **🌐 跨视频剧本剪**：按剧本剪新增「用整个目录的台词」开关 + `POST /api/folder_lines`（全目录台词统一编号）——剧本跨多个视频也能一次匹配、按剧本顺序剪成一条（对应"给剧本→去台词JSON里找→全剪出来"）；
- **重新提取不丢说话人**：重跑转写后按时间重叠自动继承旧 speaker 标注（重叠>50% 才继承）。

## 20. 2026-09-07 最终形态：AI 字幕剪辑（按用户思路整体收敛）

用户最终思路：整个项目 = **① 字幕分析**（声音→字幕阶层 JSON：哪段时间出现什么字幕）+ **② 一句话剪辑**（对阶层下命令剪出来）+ **③ 剪辑工具页**（剪映式手动精修，工具非核心）。之前的视频画面分析类功能全部下架。

### 20.1 改动

| 项 | 内容 |
|---|---|
| 后端 | 新增 `POST /api/keyword_lines`：关键词包含匹配（"字幕里含'孙悟空'的区间全剪出来"，确定性子串、OR 语义、不走模型） |
| 前端 | Tab 更名：**① 字幕分析**（提取字幕/说话人/台词JSON）、**② 一句话剪辑**（🎯 关键词剪辑 + 🪄 一句话智能勾选 + 🗣 说话人 + 📜 按剧本剪 + 剪/烧字幕/剪映草稿/SRT）；**下线**：VLM 状态条及轮询、按时间规则批量剪（UI 层，后端接口保留休眠）、素材卡片上的场景数/摘要/标签等旧分析字段（状态改为 已有台词/未提取字幕） |
| 剪辑工具页 | edit-modal 全屏化为独立页面（占满视口），标题「🎞️ 剪辑工具」，返回按钮退出——对应"再写一个页面，原理和剪映差不多，是工具不是核心" |

### 20.2 页面最终结构

- ① 字幕分析：目录列表 → 提取字幕/重新提取/说话人(全目录)/台词JSON(全目录) → 下一步
- ② 一句话剪辑：素材选择（勾选已提取字幕的素材）→ 字幕阶层列表 → 🎯 关键词全选 / 🪄 一句话智能勾选 / 📜 按剧本剪（🌐 跨视频）→ ✂️ 剪勾选的台词 → 成片/烧硬字幕/剪映草稿/SRT
- ③ 剪辑工具页：素材卡片「🎬 剪辑」或 ✂️ 手动精剪 进入，全屏时间线

### 19.3 端口再次迁移：5780x → 6180x（2026-09-07 晚）

迁移到 5780x 后发现机器上仍有**外部启动脚本**（%TEMP%\start_analyzer.bat 等，非本仓库文件）按旧端口 8001-8003/11434 反复拉起本项目服务，与用户其他项目冲突。按用户要求迁到更冷门的 **61800(Ollama)/61801(analyzer)/61802(executor)/61803(planner)** 段（已验证不在 Windows 保留区间、无占用，61804/61805 留作扩展），并把 %TEMP% 里那三个外部 bat 也改写到新端口。全部服务已按用户要求停止，明天用户自行测试。

## 21. 2026-09-07 界面按用户流程最终定型

用户流程：**选文件夹（可多选/移出）→ 分析（生成台词 JSON，"已分析"状态按目录持久记录）→ 进入剪片（提示选"已分析的文件夹"）→ 语音/文字对话（如"我要剪出所有视频里的孙悟空"）→ 模糊匹配 → 勾选确认（默认全选）→ 剪辑**。其他功能收敛到后端休眠或独立工具页。

| 改动 | 内容 |
|---|---|
| ① 字幕分析 | 文件夹**多选**（点击切换选中/移出，提示条显示已选列表）；「🔊 分析」对所选目录逐个提取字幕（进度逐目录可见）；已分析状态由 manifest 持久记录，重选即显示 ✅；保留 🗣 说话人（全目录）/ 📄 台词JSON |
| ② 一句话剪辑 | 不再按素材逐个操作：选**已分析的文件夹** → 「📂 加载台词阶层」（全目录台词统一编号）→ **🎤 语音输入**（MediaRecorder 录音 → `POST /api/voice_input` → 本地语音模型转文字 → 自动匹配）或打字 → 「🎯 匹配」（模糊匹配，命中句默认全选，可增删）→ ✂️ 剪辑 → 成片/烧硬字幕/剪映草稿/SRT；底部「视频清单」可进剪辑工具页 |
| 后端新增 | analyzer `POST /voice_transcribe`（音频上传→faster-whisper→文本，webm 不佳时 ffmpeg 转 16k wav 重试，临时文件用完即删）；planner `POST /api/voice_input`（代理） |
| 剪辑工具页 | 保持全屏独立页不变（工具属性，非核心） |

### 21.1 收尾三件套（2026-09-07 晚，全部完成）

1. **相邻命中句合并**：/api/clip_selected 预处理——同素材内间隙≤0.5s 的选区合并成一个区间（保持勾选顺序、不跨素材），消除"连续几句话剪成多段再拼"的接缝；
2. **切点呼吸余量**：合并后每区间头提前 0.15s、尾延后 0.25s（probe 素材时长钳制），防吃字/爆音；
3. **GPU 加速**：pip 装 nvidia-cublas-cu12 + nvidia-cudnn-cu12（清华源，配 ctranslate2 4.8.1），analyzer `_cuda_dll_dirs()` 自动发现并注册 DLL；.env ASR_DEVICE=auto。实测 RTX 4080：medium 转 8s 音频 **1.2s**（CPU 模式约 11s），失败自动回退 CPU。

## 22. 2026-09-09 部署 FunClip 与 AutoCut 两个开源声音剪辑工具（tools/ 目录）

按用户要求把两个"根据声音剪视频"的开源项目部署进本仓库 `tools/`，各自独立 venv、独立端口、互不影响主项目（61800-61805）。

### 22.1 FunClip（tools/funclip，端口 61810，阿里的"勾台词剪视频"）
- 代码：GitHub zip 经 ghproxy 下载（git 直连被代理挡）；独立 venv + requirements.txt（清华源）
- 模型：ModelScope 预下载 Paraformer 大模型 + FSMN-VAD + 标点 + CAM++ 说话人（约 1.4GB）
- **本地修复 2 个上游 bug**：① `--whisper-model` choices 无本地路径 + ② 剪切区间超视频末尾时 moviepy 崩溃（`video.duration` 钳制）
- 实测：59 秒素材 → **27 句字幕 / 转写 2.5 秒**（Paraformer rtf 0.043，比 Whisper 快得多）；关键词"钱"→ 成片 38MB
- 脚本：`启动.bat`（探测 61810 → 起服务 → 开浏览器）/ `关闭.bat`；日志在 logs/

### 22.2 AutoCut（tools/autocut，端口 61811，李沐的"删句子剪视频"）
- 代码：GitHub zip；pip 的 autocut 包构建失败 → 直接用仓库 + `python -m autocut`
- 依赖：torch/openai-whisper/srt/moviepy 等；**moviepy 必须 1.0.3**（2.x 移除了 editor，autocut 不兼容）；转写后端切 `--whisper-mode faster`，模型直接指向主项目的 `models/whisper`（small）/`models/whisper-medium`（medium）——零下载、全离线
- Web 包装页（app.py，autocut 本身无界面）：选视频 → 生成字幕 → 网页上删句子 → 剪切成片 → 下载；子进程用 **DETACHED_PROCESS**（继承服务句柄会莫名卡死）+ 产物文件轮询（faster 模式进程干完活不退出，靠产物出现就 terminate）
- `--vad 0`：VAD 依赖 torch.hub 拉 GitHub（被墙），关闭后功能可用
- 实测：转写 57s/59s 视频（medium CPU）；md → `test_video_cut.mp4` 成片
- 脚本：`启动.bat`（开浏览器）/ `关闭.bat`

### 22.3 端口总表
| 服务 | 端口 |
|---|---|
| 主项目 Ollama / analyzer / executor / planner | 61800 / 61801 / 61802 / 61803 |
| （预留） | 61804-61809 |
| FunClip | 61810 |
| AutoCut Web | 61811 |
