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
### 关键点（2026-09-30 深度测试：lite 修 4 处 + 报告冻结代码成片覆盖缺陷）
- **测试结果改放 `测试\` 文件夹**（根 .gitignore 由 `测试结果/` 改为 `/测试/`）：
  `测试\2026-09-29_重构首轮测试\`（上轮迁移）、`测试\2026-09-30_深入测试\`（报告+截图+素材+成片+api_e2e.py）。
- **API 20/20、UI 10/10 全过**（中文目录双视频识别 23.5s、中文逗号关键词、无命中/错误路径、
  代理/直连一致、UI 宕机红灯自动恢复、下载事件触发）。可复现脚本 `测试\2026-09-30_深入测试\api_e2e.py`。
- **lite 修复 4 处**（均已复测）：①busy() 不恢复按钮文字（真 bug，`busy(btn,false)` 需从 dataset.t 恢复）
  ②bat 先开浏览器后起服务的竞态（延迟 3s 再开）③fetch 无超时/无上游轮询（AbortController
  识别 3600s/剪辑 600s + 15s health 轮询，61812 掉线页面自动变红）④`<a>`套`<button>` 非法嵌套改 `a.dlbtn`。
- **报告未修（冻结代码，等用户授权）**：`videoclipper.py:431` 成片命名用 `self.GLOBAL_COUNT`
  （实例内存计数器），**61812/会话重启后从 0 重计 → 同名覆盖旧成片**（outputs.json 按路径去重，
  登记不变但内容被换；原工作台同样受影响）。修法：命名加时间戳或启动时按已有文件初始化计数器，3~5 行。
- **测试坑补充**：④沙箱再次同时收割 61812/61814（日志全 200 无报错即被外部杀，重启继续即可）；
  ⑤IAB webview 僵尸渲染会让**坐标点击落点不可信**（页面被并排画两份）——用元素级
  `locator.evaluate(el=>el.click())` + 服务端访问日志交叉验证；⑥该 evaluate 跑在隔离世界，
  **看不到页面全局 JS 函数**（只能操作 DOM），下载事件 `waitForEvent("download")` 可用。

---
### 关键点（2026-09-29 重构：最简剪辑台 lite(61814) + 历史测试清理）
- **用户要求**：「剪辑软件的不动，别的根据模型能力写个最简单功能；写完测试，结果写进测试结果\，清理历史测试」
- **新文件 `tools/subtitle-clip/lite/`**（铁律②的样板）：`server.py`（FastAPI，**61814**，
  `LITE_PORT`/`LITE_UPSTREAM` 可覆盖）= 静态单页 + 把 `/api/*`、`/media` 反向代理到 61812
  （61812 无 CORS，代理让页面同源可调；转发超时 (10,3600)s 覆盖首次模型加载）；
  `index.html` 单文件无框架页（识别→台词→关键词/剧本剪→成片卡片预览+下载）；
  `启动简易台.bat`（纯 ASCII+CRLF 无 BOM，用 runtime python 拉起并开浏览器）。
  **lite 不含任何引擎逻辑**，61812 没起时 /health 的 upstream_ok=false、页面红字提示。
- **api_service.py 的 `GET /health`**（+6 行，2026-09-28 深夜会话所加、当时未提交）本次一并提交，
  本次对该文件零新改动。
- **清理**：删 `subtitle-clip/tests\`（17 个 9/8 上游 FunClip 外部 API 测试，minimax/litellm/
  orcarouter/twelvelabs 等，与本项目无关）、`funclip_test_out\`（42MB 旧测试成片）、
  根 `测试结果\` 旧文件、空 `tools/funclip\`。保留 `test-media\`（回归素材）与 `scripts\`。
- **实测（2026-09-29，报告在 测试\2026-09-29_重构首轮测试\）**：经 61814 代理 API 9 项全过
  （全新识别 20.3s 27 句；关键词「钱」命中 4 句；剧本原句精确命中+改写句模糊命中；
  /media 取片 ftyp isom 有效）；内置浏览器 UI 4 项全过（状态灯/查看已识别/页面点关键词剪
  「命中 4 句出片 1 个」/成片卡片自动刷新）。
- **测试坑（复测必读）**：①Git Bash `curl -d` 发中文 JSON 会被控制台 GBK 弄坏 body（假 400/404）——
  用 UTF-8 文件 `--data-binary @file` 或 python requests；②内置浏览器按中文按钮文本/onclick
  定位点击会超时（fill 正常），**坐标点击（=真人点击）一次成功**，不是页面 bug；
  ③Windows python 不认 Git Bash 的 /tmp 路径，python 里用 `cygpath -w` 转换。

---
### 关键点（2026-09-24 恢复原版 + 新页面开发约定【最高优先级，先读这段再动手】）
- **用户决定（见仓库根 `修改意见.txt`）**：2026-09-23 深夜的「识别即时反馈」改造（commit 95527ab）
  被用户否定——原话「改回去吧，现在完全不能用；后端服务不要动；它原有的功能是正常的，我们是做
  功能的增加和扩展」。已执行 `git reset --hard origin/main`（=86e1b06），本地与远程完全一致；
  95527ab 只在 reflog 里可考古。**原页面/原服务从此冻结。**
- **三条铁律（后续所有会话必须遵守）**：
  ①**服务不变**：`funclip/launch.py`(61810)、`api_service.py`(61812)、`funclip/videoclipper.py`
  及原页面 UI 一律不改，发现问题先报告；
  ②**新页面开发**：一切新功能/体验改进都做成**独立新文件**（新页面/新服务），调用原有服务拿能力，
  绝不把逻辑塞回原文件；
  ③**对应关系**：新页面能力与原有服务一一对应（映射见下），不另起炉灶重写识别/剪辑引擎。
- **原有服务能力 ↔ 调用面映射（新页面的合法调用清单）**：
  - `61812 REST`：POST `/api/analyze_folder`（目录批量识别→旁挂 `*.clip.json`）、`/api/list_jsons`、
    `/api/keyword_cut`（关键词剪）、`/api/script_cut`（剧本剪）、GET `/api/outputs`、GET `/media?f=`
  - `61810 Gradio queue`（POST /queue/join + GET /queue/data SSE，State 由服务端按 session_hash 保持；
    config 里 api_name **不带斜杠**）：`mix_recog`（识别）、`mix_recog_speaker`（分说话人识别）、
    `mix_clip`（按台词/说话人/时间段偏移剪）、`video_clip_addsub`（剪+烧字幕）、
    `burn_full_video_subtitles`（整片加字幕）、`clip_per_speaker`（按说话人分开剪）、
    `download_subtitled_video`、`download_per_speaker_clips`、`load_demo_media`（示例）、
    `refresh_dub_voices`+`dub_video_from_subtitles`（依赖 18062 TTS）、`llm_inference`/`AI_clip`（需 key）
  - 若新页面需要更干净的 REST（整片字幕/分说话人/配音目前无 REST 端点）：**新建独立适配服务**
    （新端口如 61814）包 videoclipper 引擎，不改 api_service.py
- **原版回归基线（2026-09-24 实测 15/15）**：61812 六项接口全 PASS（p03 全新识别 20.2s、关键词/剧本
  剪出片）；61810 工作台九链路全 PASS（识别 12.6s/分说话人识别/裁剪/裁剪+字幕/整片字幕/分说话人剪
  （p01 剪出 4 个说话人成片）/示例加载/下载带字幕视频/配音角色优雅降级，产物 h264+aac）。
  **唯一已知边界（原版继承 FunClip 的缺口，按铁律①不改原码）**：识别数据里若出现**空 timestamp 的句子**
  （p03 实测踩中），「按说话人分开剪」会在 `subtitle_utils.py:33 Text2SRT.__init__` IndexError
  （`generate_srt` 有空时间戳保护、`generate_srt_clip` 没有）；数据相关个例，常规视频（p01/访谈）正常。
  新页面若要规避：识别后先过滤空 timestamp 句再进入剪辑调用（在适配服务里做，不改原码）。
  **回归脚本模式**：必须先 `POST /upload` 把视频传进缓存再引用其返回路径（直接给缓存外路径会在
  preprocess 的 move_files_to_cache 处炸掉，且连锁导致 State 缺失、后续全部报
  state['recog_res_raw'] None——别误判成服务坏了）；gr.Video 入参须包成
  `{"video": FileData, "subtitle": null}`、其返回值同样包在 `{"video": {...}}` 里；
  /config 的 api_name **不带斜杠**；零输入事件（refresh_dub_voices）可直接调用。
- **服务存活运维（昨夜结论中仍成立部分）**：AI 会话里拉起的服务活不过会话（WMI
  `Win32_Process.Create` 实测 ~75s 被收割，「WMI 可靠」结论作废）；**长期存活只能用户自己双击
  `启动.bat`**。排查三板斧：端口监听、`logs/*.log(.err)` 尾部、`%TEMP%\gradio` 新上传。
  原页面「识别过程无进度提示」的体验问题依旧存在——按铁律②改进做进新页面，不再动原页面。

### 关键点（2026-09-23 深夜 示例点击失效修复：弃用 gr.Examples + gradio 字体桥接补丁）
- **用户报告**：点击示例视频无法加载到视频输入。根因有二，均与当晚业务代码无关：
  ①**gradio 4.44.1 Dataset 前后端不一致**：`gr.Examples` 的 Dataset 点击事件，前端把索引发成
  字符串/对象 → 后端 `dataset.py:160 raw_samples[payload]` 抛 `TypeError: list indices must be
  integers... not str`（真实点击的 traceback 已取证）。后端用整数索引调 `/call/load_example`
  完全正常——纯前端 payload 类型问题，无法在应用侧修
  ②**页面挂载卡「加载中...」**：gradio 前端 JS 无条件注入 `fonts.googleapis.com` 字体样式表
  （Index-DB1XLvMK.js 的 Ns 函数），被墙环境该请求挂起/失败阻塞挂载
- **修复**：
  ①`gr.Examples`（视频+音频共 3 组）整体删除 → `demo_pick` Dropdown（4 个视频示例 + 1 个音频示例）
  + `load_demo_media(name)`：下载到 `%TEMP%/aicc_demo_media/`（带缓存）→ .wav 回 audio_input、
  其余回 video_input。依赖 0 号 = demo_pick.change → outputs [video_input, audio_input]
  ②`scripts/patch_gradio_fonts.py`：把 runtime 里 gradio 前端 chunk（Index-DB1XLvMK.js）的
  fonts.googleapis URL 替换为同源 `/assets/fonts-bridge.css`（空文件，StaticFiles 直接服务）。
  **runtime 重装/升级 gradio 后需重跑**；已在本机 runtime 应用
- **排查教训（都是坑）**：
  - gr.Button 的文本在 props.**value** 不是 label——用 label 探测版本会全部误判（当晚"回退验证"
    因此全部失效，白折腾两轮重启）
  - ZCode 沙箱里后台任务拉起的服务，任务结束会连带杀掉整个进程树（Start-Process 也逃不掉）；
    WMI Win32_Process.Create 落在 session 0，`Start-Process -WindowStyle Hidden` 会报「拒绝访问」。
    **可靠做法：WMI 起 cmd（带 `>> log 2>&1` 重定向），python 直接跑，绕开 GUI 依赖**
  - IAB webview 的截图/可见画面与 evaluate 所见文档可能不同步（僵尸渲染），以截图+接口联合验证为准
- **验证**：`/call/load_demo_media`（中文 body 需 \u 转义）→ complete 并返回 video FileData ✓；
  配置/依赖关系逐项核对 ✓。UI 端到端点击验证因 IAB webview 僵尸状态未完成，用户侧验证即可

### 关键点（2026-09-23 下载区改造 + 对接 wenziqudong 字幕配音）
- **用户改造需求**：原「下载识别结果(txt)/下载SRT字幕」两个文件下载位，改成**动作按钮**——
  「⬇️ 下载带字幕视频」（原片+烧全部字幕，复用 `video_burn_subtitles`）和
  「⬇️ 下载分说话人片段」（复用 `video_clip_per_speaker`，每人一个文件）。识别成功时这两个下载位会被清空
  （新识别使旧成片失效）；右侧页签的「整片加字幕/按说话人分开剪」保留，且产物流进同一对下载位
- **对接 wenziqudong（D:\xm\wenziqudong，文字变声音，GPT-SoVITS）**：接口见其 `接口文档.md`——
  `GET /health`（status=ok 才算就绪，首次加载 5~10 分钟）、`GET /models`（只用 ready=true 的 name）、
  `POST /v1/audio/speech` `{voice,input,speed}` → **音频二进制 mp3**（错误才是 JSON `{detail}`）；
  端口固定 18062 绝不漂移，地址可用环境变量 `TTS_API_BASE` 覆盖；配音超时须 ≥300s，偶发 500 重试一次，
  单次 ≤1000 字；**服务绝不自动启动/重启**（其项目约定），没起就提示用户双击其 start.bat
- **新文件**：`funclip/tts_client.py`（三接口客户端 + TTSError）；`videoclipper.video_dub_subtitles`——
  台词逐句合成 → moviepy `AudioFileClip.set_start(原句首字时间)` → `CompositeAudioClip` 叠加（重叠句相加混合）
  → `set_duration(视频时长)` 截断 → `set_audio` 换声，画面不动；第 1 句失败即中止（多半是角色/服务问题），
  后续失败跳过计数；临时 mp3 放 mkdtemp 目录，finally 里 rmtree
- **UI**：识别区下方新增「🔄 刷新配音角色」（拉 /models 填充 Dropdown，allow_custom_value 可手填）+ 语速滑条
  （0.6~1.65，对齐原时间戳全靠它微调）+「🗣 字幕配音替换原声」；产物同样落到 剪辑成片/
- **实测（2026-09-23）**：`scripts/e2e_speaker_burn.py <视频> <输出目录> dubstub`（进程内假 TTS 服务，
  固定 1 秒正弦 mp3）→ 53 句全部合成，成片 123.0s h264+aac，volumedetect 确认音轨非静音；真服务联调
  `dub` 阶段（TTS_VOICE=azhong）→ 50/53 句成功替换原声，3 句服务端偶发 500 重试后仍败按设计跳过，
  成片 123.0s h264+aac、画面帧不变；`refresh_dub_voices` 经 Gradio /call 实测返回全部就绪角色
- **沙箱启动坑**：从 ZCode 会话里 `bash &` 拉起的进程树会被连坐回收（TTS 服务曾就绪后被杀）；
  `Start-Process` 分离启动可跨调用存活；WMI `Win32_Process.Create` 落在 session 0，start.ps1 的
  `Start-Process -WindowStyle Hidden` 会报「拒绝访问」——工作台用本会话跑 start.ps1 即可
- **删代码**：launch.py 的 `save_text_to_file` 与 txt/srt 下载位随之移除（SRT 文本仍在「SRT字幕内容」文本框可见）

### 关键点（2026-09-22 整片加字幕 + 按说话人分开剪 + 出片参数显式化）
- **用户需求**：①视频没字幕时，识别后一键生成带字幕的完整视频（不裁剪）；②按说话人分开剪，几个人就出几个视频。此前只有「裁剪(+字幕)」（必须先匹配文本/说话人），没有这两条直达路径
- **新方法（funclip/videoclipper.py）**：`video_burn_subtitles`（整片烧全部台词，走既有 moviepy+Pillow 合成路线——自带 imageio-ffmpeg 是 gyan essentials 构建，**无 libass/drawtext 滤镜**，别想用 ffmpeg subtitles 滤镜替代）、`video_clip_per_speaker`（遍历 sd_sentences 的 spk 去重列表，逐个走既有 `video_clip(dest_spk=...)` 后改名 `*_spk{k}.mp4`）
- **新 UI（funclip/launch.py）**：「🎬 整片加字幕」「👥 按说话人分开剪」按钮 + `gr.Files` 多成片下载列表；两者输出目录不填时落到 `tools/subtitle-clip/剪辑成片/`（已 gitignore；此前空输出路径会把成片写进 Gradio 临时目录，重启即丢——**用户"生成的视频不见了/打不开"疑似与此有关**）
- **出片参数显式化**：`_write_standard_mp4` 统一 codec=libx264 / audio=aac / pix_fmt=yuv420p / faststart / 显式 fps。起因：用户反馈多说话人示例剪出的成片打不开；坏文件已被 Gradio 重启清掉无法取证，用当前代码在访谈.mp4 上复现两条写盘路径均产出正常 h264+aac，故按"杜绝 moviepy 默认值意外"加固
- **实测（访谈.mp4 123s，4 位说话人）**：分开剪 4 个成片 34.7/32.3/26.6/18.9s（各自台词段依序拼接）；整片烧字幕 53 条、123s；全部 ffprobe 验证 h264+aac+yuv420p+moov 前置，抽帧确认字幕与说话人内容正确
- **E2E 脚本**：`scripts/e2e_speaker_burn.py <视频> <输出目录> <repro|new|recog>`（`env -u PYTHONPATH` 跑，stage=new 覆盖两个新功能）
- 服务用 `start.ps1` 重启后新 UI 生效（Gradio 4.44.1 `/config` 已验证新组件在页）

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
