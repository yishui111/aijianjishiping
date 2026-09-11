# AGENTS.md — AI 字幕剪辑工作台（aijianjishiping 仓库）项目档案

> ⚠️ 修改本仓库前先通读本文件。给「AI 助手 / 开发者」看的项目记忆：定位、结构、公开版边界、维护约定。

## 1. 定位
本地**字幕（台词时间戳）驱动**的离线视频剪辑系统。给一个视频目录 → 语音识别出台词与时间戳，同目录生成 `<视频名>.clip.json` → 按关键词/一句话/剧本精准剪出成片。

> 演进史（勿再回退）：AI 出图工作室系列（~58GB）已删除；`ai-video-studio/`（画面语义选段路线）已于 commit `dc3abee` **下线删除**——实测画面语义选段不可靠。当前仓库只含**一个系统**：`tools/subtitle-clip/`。

## 2. 结构与端口
| 组件 | 作用 |
| ---- | ---- |
| tools/subtitle-clip/api_service.py | 分析服务（FastAPI，**61812**）：目录批量分析 / 关键词剪辑 / 剧本剪辑 |
| tools/subtitle-clip/funclip/launch.py | 剪辑工作台（Gradio，**61810**）：识别 / 勾台词 / 时间线精修 / 烧字幕 |
| tools/subtitle-clip/funclip/videoclipper.py | 剪辑引擎（FunClip 二次开发，MIT） |
| tools/subtitle-clip/start.ps1 / stop.ps1 | 真正的启停脚本 |
| 根 `启动.bat` / `关闭.bat` | 包装脚本 → 调用上面的 ps1（纯 ASCII，防中文乱码） |
| tools/subtitle-clip/runtime/ | venv（含 torch + ffmpeg，~1.7GB，不入库） |
| tools/subtitle-clip/modelscope-cache/ | 语音模型缓存（~3.3GB，不入库） |
| tools/funclip/ | 上游 FunClip 参考副本（不入库，仅本机对照） |

接口清单：`POST /api/analyze_folder`、`/api/list_jsons`、`/api/keyword_cut`、`/api/script_cut`、`GET /api/outputs`、`GET /media`。

## 3. 公开版边界（刻意不入库）
- `tools/subtitle-clip/runtime/`(~1.7GB)、`modelscope-cache/`(~3.3GB)、`logs/`、`test-media/`、`outputs.json`、`__pycache__/` —— 重件不入库（subtitle-clip/.gitignore 屏蔽）
- `tools/funclip/`（上游第三方克隆）不入库
- `素材/`（真人视频等）仅本机使用
- 无 `__pycache__`/pyc/日志

## 4. 特殊约定
- **bat/ps1 编码（踩过坑）**：根目录 `启动.bat`/`关闭.bat` 必须**纯 ASCII 内容 + CRLF + 无 BOM**（中文只放文件名）；`start.ps1`/`stop.ps1` **含中文，必须带 UTF-8 BOM + CRLF**，否则 PowerShell 5.1 按 GBK 解析成语法错误
- 根 bat 不要写中文内容——GBK 代码页下会乱码；需要中文提示就放 `.ps1`（带 BOM）
- `start.ps1` 会把 `MODELSCOPE_CACHE` 指向本地 `modelscope-cache\`（保证离线可整目录复制）；**传 Windows 风格路径**，POSIX 风格 `/d/...` 会被解释成 `D:\d\...` 并触发重新下载
- `runtime/` 内已含 imageio-ffmpeg 提供的 ffmpeg，无需系统安装
- 无本机盘符硬编码；修改时保持
- clone 后跑：先按 DEPLOY.md 就位 `runtime/` 与 `modelscope-cache/`（方式 A 母版复制或方式 B 装配）

## 5. 维护约定
- 改动代码后同步更新：README.md（用户向）、DEPLOY.md、本文件
- 提交：`git add <具体文件>` → `git commit -m "..."` → `git push origin main`（默认只有仓库主人可 push）
- 勿用 `git add -A`（本机有大量被 ignore/exclude 的大件与个人文件，防止误提交）
- **改动启停脚本后，务必核对根 `启动.bat` 指向的目标目录仍存在**——2026-09-11 就是因为 `ai-video-studio/` 被下线删除后根脚本没跟着改，导致双击"没反应"

---
### 关键点（2026-09-11 修复根启停脚本 + 字幕剪辑链路实测）
- **故障**：双击根 `启动.bat` 没反应。原因：脚本仍 `call "%~dp0ai-video-studio\start_local.bat"`，而该目录已在 `dc3abee` 被删除 → `call` 失败、`echo off` 下无输出、窗口秒关
- **修复**：根 `启动.bat`/`关闭.bat` 改为委托 `tools\subtitle-clip\start.ps1`/`stop.ps1`，并加目标缺失时的显式报错 + `pause`（不再静默退出）；内容保持纯 ASCII/CRLF/无 BOM
- **实测（2026-09-11）**：`test-media\e2e\test_video.mp4`（59s）走完整链路——
  目录分析 21.0s → 生成 27 条台词的 `test_video.clip.json`（`cached:false`）；关键词「钱」→ 命中 4 句 → 成片 8.75s；剧本 3 行 → 逐行匹配命中（相似度阈值 0.45）→ 成片 4.58s
- 服务端口：61810（Gradio 工作台）/ 61812（FastAPI 分析服务）；日志在 `tools/subtitle-clip/logs/`
- 本机测试时注意：WorkBuddy 沙箱会注入 `PYTHONPATH=<...>\cli\vendor\shim`，其中 `sitecustomize.py` 把 `os.remove` 重定向到回收站，会让 `videoclipper.video_recog` 在删临时音频时抛 `OSError: SHFileOperationW 失败: 0x2`（HTTP 500）。**这是沙箱副作用，不是项目 bug**；用 `env -u PYTHONPATH` 启动服务即可复现真实行为

### 关键点（2026-09-11 修 _split_long_sentence 两个 bug + 剧本剪辑实测）
- **bug 1（影响匹配精度）**：`funclip/videoclipper.py::_split_long_sentence` 切分长句时
  `chunk["text"] = tokens[start:idx+1]` 直接存了 `str2list` 的**字符列表**，下游
  `api_service` 用 `str()` 写 JSON → 产出 `"['王', '呃', '几', '个', '妖', '怪', '占']"`
  这种 Python 列表字面量。后果：关键词「妖怪」命中 **0**（字符被引号逗号隔开）、
  剧本相似度匹配失效。**改为 `"".join(tokens[...])`**。
  另在 `api_service` 写 JSON 处加兜底：text 若为 list/tuple 先 join。
- **bug 2（影响剪片精度）**：切分片段用 `dict(normalized)` 继承了**父句的 start/end**，
  同一父句切出的多片段时间区间完全相同（实测两段都是 `89.45-103.20`），
  按台词剪辑会把同一段素材**重复剪进去**。**改为各片段用自己 timestamp 的首尾毫秒值**。
- 触发条件：句子 duration ≥ `MAX_SUBTITLE_DURATION_MS`(8000) 或 tokens ≥ `MAX_SUBTITLE_TOKENS`(30)
  **且** `len(tokens) == len(timestamp)`（不等则整句返回，不切分）。
- **实测（素材\3分钟 西游记 p01-p03）**：修复前 4 条异常台词 / 2 组区间重复 / 关键词「妖怪」命中 0；
  修复后异常 0 / 重复 0 / 「妖怪」命中 1 并成功出片。提交 `6ffcc12`。
- **剧本自动匹配剪辑实测通过**：跨 p02/p03 两集取真实台词组 5 行剧本，
  逐行命中各自集数（`difflib` 相似度阈值 0.45），改写句「我要打上凌霄宝殿」也模糊命中
  p03「不然我就打上凌」；按集输出 2 个成片（p02 3.0s / p03 22.44s）。
- ⚠️ **改完代码必须重新分析**：`/api/analyze_folder` 见 `.clip.json` 已存在就跳过（cached），
  旧的错误 JSON 不会自动更新——需先删掉目标目录的 `*.clip.json` 再分析。

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
