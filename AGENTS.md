# AGENTS.md — AI Video Studio（aijianjishiping 仓库）项目档案

> ⚠️ 修改本仓库前先通读本文件。给「AI 助手 / 开发者」看的项目记忆：定位、结构、公开版边界、维护约定。

## 1. 定位
本地 AI 视频智能剪辑系统（目录历史名 aijianjishiping；早前同目录的 AI 出图工作室系列已**全部废弃删除**，2026-09 清理）。仓库只含**一个自研系统**：`ai-video-studio/`。

## 2. 结构与端口
| 组件 | 作用 |
| ---- | ---- |
| ai-video-studio/services/analyzer | L1 理解：场景切分/ASR/VLM 总结/向量（端口 61801） |
| ai-video-studio/services/planner | L2 方案：对话/剧本/场记单 API + 前端（61803） |
| ai-video-studio/services/executor | L3 执行：ffmpeg 剪辑（61802） |
| ai-video-studio/services/common | 共享库：clip_client(Chinese-CLIP)/media/util |
| ai-video-studio/scripts, config, docs | 构建脚本、操作目录、方案手册 |
| ai-video-studio/start_local.bat | 原生模式一键启动（推荐，无 Docker） |

入口 `ai-video-studio\README.md`、`ai-video-studio\docs\implementation-plan.md`（含部署手册第 14 节）。

## 3. 公开版边界（刻意不入库）
- `ai-video-studio/runtime/`(venv+Ollama ~4.5GB)、`models/`(~9.8GB)、`offline/`、`materials/`、`output/`、`Shotcut/`、`analysis/`、`*.zip`、`*.mp4` —— 重件不入库（ai-video-studio/.gitignore 屏蔽）
- `.env`（**含真实 sk- API Key 曾出现过**）绝不提交，只传 `.env.example`（占位 sk-xxxxxxxx）
- `素材/`（真人视频等）仅本机使用；本机保留的根《部署方案.md》已在 .gitignore（/部署方案.md）
- 无 `__pycache__`/pyc/日志

## 4. 特殊约定
- 代码中 `PLANNER_API_KEY = os.environ.get(...)` 等为环境变量读取，不是真实密钥（扫描勿误报）
- services 代码无本机盘符硬编码（均 env/相对）；修改时保持
- bat/ps1：根目录与 avs 内启停脚本保持纯 ASCII/CRLF/无 BOM（avs 的 .ps1 可含中文提示，UTF-8）
- clone 后跑：先按 DEPLOY.md 就位 runtime/models（方式 A 母版复制或方式 B 装配）

## 5. 维护约定
- 改动代码后同步更新：README.md（用户向）、DEPLOY.md、本文件、avs/docs
- 提交：`git add <具体文件>` → `git commit -m "..."` → `git push origin main`（默认只有仓库主人可 push）
- 勿用 `git add -A`（本机有大量被 ignore/exclude 的大件与个人文件，防止误提交）
---
### 关键点（2026-09-02 上传整理补充）
- 本仓库 = AI Video Studio（AI 视频智能剪辑系统），目录历史名 aijianjishiping；同目录早前的 AI 出图工作室系列（Illustrious/FLUX2/illustrious_ui/Camera/model_pad 等 ~58GB）已判定烂尾并于 2026-09 删除（仓库与磁盘同步精简）
- 三服务 + Ollama：analyzer 61801 / executor 61802 / planner(Web) 61803 / Ollama 61800；原生模式 start_local.bat（无 Docker，推荐）
- 大件不入库（ai-video-studio/.gitignore 屏蔽）：runtime/(venv+Ollama ~4.5GB)、models/(qwen2.5vl:3b/qwen2.5:7b/bge-m3/whisper/chinese-clip ~9.8GB)、offline/materials/output/Shotcut/analysis、*.zip/*.mp4
- .env 曾含真实 sk- DeepSeek Key → 绝不提交，只传 .env.example(占位 sk-xxxxxxxx)；PLANNER_API_KEY 等均为 os.environ 读取勿误报
- 根 /部署方案.md 与 素材/ 仅本机保留（已 gitignore/exclude）；换机部署按 DEPLOY.md 方式A(整目录复制)或方式B(装配)
- avs 内更细文档：ai-video-studio/README.md、docs/implementation-plan.md(含部署手册与修复史)

### 关键点（2026-09-05 免 VLM 化改造补充）
- 定位转向：VLM 是可选增强非必需——`VLM_ENABLED=false` 时分析用 Chinese-CLIP 零样本场景标注兜底（content 变标签式短语），检索/auto_edit 不受影响；本机 .env 已设 false + ASR_DEVICE=cpu（缺 cublas64_12.dll）
- 新增 `POST /api/auto_edit`（AI 自动剪辑：需求+目标时长→bge-m3/CLIP 双通道打分→贪心选段→出片），前端「🤖 AI 自动剪辑」卡片
- executor trim 已精准化：流切后回读校验 ±0.25s，漂移自动转帧级重编码——老剧 AV1/HEVC 源关键帧稀疏，纯 -c copy 会切点漂移、片段重复（踩过坑，勿回退）
- planner 的 OLLAMA_BASE 与 PLANNER_BASE_URL 已解耦：对话走线上 DeepSeek 时 bge-m3/模型状态仍走本地 Ollama；_llm_chat 对线上 API 不发 num_ctx
- start_local.ps1 会加载 .env（覆盖脚本默认值）；**含中文的 .ps1 必须带 UTF-8 BOM**，无 BOM 会被 PowerShell 5.1 按 GBK 误解成语法错误（此文件已带 BOM，勿删）
- 实测数据与改动全表见 avs/docs/implementation-plan.md 第 15 节

### 关键点（2026-09-05 剪映衔接补充）
- services/common/jianying_draft.py 生成剪映明文草稿（draft_content.json/draft_meta_info.json，微秒时间轴，素材绝对路径，剪映打开自动补全字段）；planner `/api/export_jianying` 探测 `%LOCALAPPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft`（或 JIANYING_DRAFT_DIR）自动复制草稿，找不到给 zip 下载（`/exports/{path}`，注意防穿越已测）；`/api/export_srt` 把选区内 ASR 台词平移到成片时间轴——**依赖分析文档 speech 字段，旧分析（ASR 修复前）在目录页点「🔁 重新分析（覆盖）」补台词**（/scan 已透传 force；/api/waveform 结果有磁盘缓存 `_analysis/*.wave.json`，mtime 失效自动重算）
- planner 新增 OUTPUT_DIR 环境变量（start_local.ps1 planner 分支注入），导出产物在 output/exports/
- 时间线升级（planner/static/index.html）：拖左右黄边裁剪（钳制在源时长内、最短 0.2s）/拖片段排序/redo 栈/复制片段/快捷键（空格/S/Del/Ctrl+D/Ctrl+Z/Ctrl+Y/←→）；导出剪映草稿+SRT 入口有三处：时间线面板、场记勾选栏、AI 自动剪辑结果
- 前台已重构为三 Tab 对应后台服务：①素材分析=analyzer:61801（含免分析批量剪）②剪辑出片=planner:61803+executor:61802（素材勾选→AI自动剪/手动精剪/场记单→成片/剪映草稿/SRT）③进阶剧本=planner:61803；原目录页"时间线剪辑"下拉入口与步骤条已删（loadEditFiles/openEditFromSel/markStep 已移除），手动精剪统一走②勾选入口，showTab() 切换

### 关键点（2026-09-06 定型：字幕驱动剪辑）
- **用户最终定位**：画面语义选段不可靠（实测剪不了），全线转向**字幕（台词时间戳）驱动**——借鉴 FunClip/autocut 交互，用内置开源件（faster-whisper+ffmpeg）实现，无新依赖
- 新增 `models/whisper-medium`（约1.5GB，hf-mirror.com 下载；faster-whisper-medium int8）；start_local.ps1 自动优先用 medium，否则回退 models/whisper（small）；_asr 加 initial_prompt 偏置简体（老剧会出繁体）
- analyzer `POST /transcribe` 只跑 ASR（精简分析文档/只更新 speech）；`/scan` 支持 `mode:"asr"`；planner `/api/transcribe`（代理，timeout 3600）、`/api/match_lines`（一句话智能勾选：LLM 做台词文本相关性挑选，确定性时间戳）、`/api/burn_srt`（libass 烧硬字幕，切到成片目录用相对文件名规避 Windows subtitles 滤镜转义；planner 与 executor 原生模式共用 output 目录）
- 前端两 Tab：①素材库·提取字幕（scan asr 模式+批量剪）②字幕剪辑·手动精剪（台词列表勾选/智能勾选→剪拼成片→烧硬字幕/剪映草稿/SRT）；AI 自动剪/场记单/剧本 Tab 已从 UI 下线（后端接口保留休眠）
- 实测数据见 avs/docs/implementation-plan.md 第 17 节

### 关键点（2026-09-07 声音分析定型：说话人/剧本/台词JSON）
- 最终定位：核心 = **声音分析**（画面分析已全面下线）——分析文档 JSON = 谁在何时说了什么（speech[].{start,end,text,speaker}）
- `POST /api/attribute_speakers`：LLM 按称呼/上下文推断说话人写回 speech[].speaker（提示词要求没名字用角色称呼；实测女人/男人交替全对）；match_lines 提示词：指定说话人时只挑标记一致的行（曾出过 18/18 全选的宽松版，已修）
- `POST /api/script_match_lines`：剧本逐条↔台词批量匹配（确定性时间戳，模型只做文本匹配）；前端 📜 按剧本剪按剧本顺序剪接
- `POST /api/export_subtitles_json`：目录级台词汇总 JSON（output/exports/），给剧本剪辑/外部工具当数据源
- start_local.ps1 三服务就绪后自动打开浏览器；前端 subLines 必须透传 speaker 字段（曾漏掉导致界面说话人不显示）
- 实测数据见 avs/docs/implementation-plan.md 第 18 节
- 实测数据与改动全表见 avs/docs/implementation-plan.md 第 16 节

### 关键点（2026-09-07 最终形态+端口+GPU 补充）
- 端口已迁专用段：Ollama **61800** / analyzer **61801** / executor **61802** / planner **61803**（61804/61805 预留）；根目录新增 启动.bat/关闭.bat 包装；start_local.ps1 探活全部改 127.0.0.1 + DefaultWebProxy=$null（PS5.1 不认 NO_PROXY，Clash 会劫持探活）+ Ollama 日志落 runtime/logs/ollama.log；启动成功自动打开浏览器
- 界面最终形态（planner/static/index.html）：**①字幕分析**（文件夹多选/移出→分析生成台词JSON，manifest 持久记录已分析状态；🗣说话人(全目录)、📄台词JSON）→ **②一句话剪辑**（选已分析文件夹→📂加载台词阶层→🎤语音输入/文字→🎯匹配→默认全选→剪辑/烧硬字幕/剪映草稿/SRT；底部视频清单进③）→ **③剪辑工具页**（edit-modal 全屏化）。下线 UI：VLM 状态条/按时间批量剪/素材卡片勾选/剧本剪（后端接口休眠保留）
- 语音对话：analyzer `POST /voice_transcribe`（音频上传→faster-whisper→文本，临时文件即删）+ planner `/api/voice_input` 代理；前端 MediaRecorder 录音自动匹配
- **GPU 加速已启用**：.env ASR_DEVICE=auto + pip 安装 nvidia-cublas-cu12/nvidia-cudnn-cu12（清华源）+ analyzer `_cuda_dll_dirs()` 自动注册 DLL；实测 RTX4080 上 medium 转 8s 音频 1.2s（~6x 实时）；失败自动回退 CPU
- 剪切工艺（/api/clip_selected）：同素材连续/重叠选区自动合并（间隙≤0.5s）+ 呼吸余量（头-0.15s/尾+0.25s 钳制素材时长）
- 一句话剪辑核心接口：/api/keyword_lines（关键词包含匹配，确定性）、/api/match_lines（LLM 模糊匹配，说话人感知）、/api/folder_lines（目录台词池）、/api/script_match_lines（剧本跨视频匹配）
