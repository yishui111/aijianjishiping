<div align="center">

# 🎬 AI Video Studio — 本地 AI 视频智能剪辑系统

> ⭐ **喜欢这个项目？请先点个 Star ⭐ 支持一下，让更多人看到！**

![GitHub stars](https://img.shields.io/github/stars/yishui111/aijianjishiping.svg?style=flat-square&color=orange)
![GitHub forks](https://img.shields.io/github/forks/yishui111/aijianjishiping.svg?style=flat-square)

**本地视频素材的 AI 剪辑系统：导入素材 → AI 理解 → 对话/场记单/剧本 → 自动剪片 → 时间线精修 → 导出成片。全程本地运行，数据不出门，可选全离线。**

> 仓库名 `aijianjishiping` 为历史命名（该目录早前还包含已废弃的 AI 出图工作室，现已清理删除，仅保留本系统）。

</div>

---

## ✨ 项目简介

把一个（或多个）视频素材文件夹交给本系统：它会先用视觉语言模型 + 语音转写 + 向量检索把素材**理解成"场记单"**（场景/人物/台词/关键帧），然后你既可以像聊天一样说"把孙悟空打斗的片段剪成 2 分钟"，也可以基于场记单勾选片段、或让它按剧本逐镜头剪辑，最后用**剪映式时间线**精修后无损导出成片。

- **自研实现**：三级流水线（理解→方案→执行）+ 时间线编辑器均为本项目自有代码
- **本地优先**：默认用本地 Ollama 模型，素材不出本机；可选接入线上 DeepSeek 提升剧本质量
- **原生模式**：无需 Docker（Windows 双击 `start_local.bat` 即跑），Docker 版保留可选
- 8G 显存/CPU 均可运行（模型按需加载，分析后自动卸载释放显存）

## 🎯 主要功能

- 🔬 **L1 理解**：场景自动切分、语音转写、VLM 场景总结、逐镜头关键帧 + 中文 CLIP 画面向量
- 📋 **场记单 + 双通道检索**：bge-m3 文字通道 + Chinese-CLIP 画面通道加权融合，毫秒级、确定性、不幻觉
- 💬 **对话剪辑**：本地 7B 模型听懂自然语言指令（"保留 2-4 分钟""去静音""精彩集锦"等），规则引擎 + 工具调用双保险
- 📝 **剧本批量剪辑**：喂场记生成镜头剧本 → 自动逐镜头匹配素材片段 → 勾选合并出片
- ✂️ **剪映式时间线**：大预览窗 + 轨道缩略图/波形 + 播放头 + 分割/删除/撤销 + 跨素材合并 + 无损导出
- 🎛️ **L3 执行**：ffmpeg 精确裁剪、拼接自动归一化、变速、去静音、1080p 导出
- 🧵 长视频分段分析、CPU 线程/显存资源限制防烧机

## 🗂️ 目录结构

```
aijianjishiping/
├── ai-video-studio/          # 系统本体（见其 README.md）
│   ├── services/
│   │   ├── analyzer/         # L1 理解：场景/ASR/VLM/向量
│   │   ├── planner/          # L2 方案：对话/剧本/场记单 API + Web 前端
│   │   ├── executor/         # L3 执行：ffmpeg 剪辑
│   │   └── common/           # 共享库（clip/media/util）
│   ├── scripts/              # 构建/启动脚本
│   ├── config/               # 操作目录（operation-catalog.json）
│   ├── docs/                 # 实施方案与部署手册
│   ├── start_local.bat       # 原生模式一键启动（推荐）
│   ├── stop_local.bat
│   └── .env.example          # 配置模板（复制为 .env 填写）
├── README.md / DEPLOY.md / AGENTS.md
```

## 🚀 快速开始（换电脑部署）

| 方式 | 操作 | 说明 |
| ---- | ---- | ---- |
| **A（推荐，100%）** | U 盘/网盘把**原项目整份文件夹**（含 `ai-video-studio\runtime` + `models` 约 14GB）复制到新电脑 | 双击 `ai-video-studio\start_local.bat` 即用 |
| **B（代码装配）** | `git clone` 本仓库 → 按 [DEPLOY.md](DEPLOY.md) 补齐大件 | 需下载/复制 runtime 与模型 |

详情见 [DEPLOY.md](DEPLOY.md) 与 `ai-video-studio\README.md`、`ai-video-studio\docs\implementation-plan.md`。

## 📥 大件资源（不入库，部署时获取）

| 资源 | 大小 | 获取 |
| ---- | ---- | ---- |
| `ai-video-studio\runtime\`（venv + Ollama 便携版） | ~4.5GB | 方式 A 母版复制；或按 implementation-plan 重建 venv + 下载 Ollama |
| `ai-video-studio\models\`（qwen2.5vl:3b / qwen2.5:7b / bge-m3 / whisper / chinese-clip） | ~9.8GB | 母版复制或按部署文档下载 |
| ffmpeg | — | runtime 内含或系统 PATH |

## ❓ 常见问题

- **Q：双击 start_local.bat 没反应？** A：确认 `runtime\` 与 `models\` 已就位（见 DEPLOY.md 方式 A/B）。
- **Q：想用线上模型提升效果？** A：复制 `.env.example` 为 `.env`，填 `PLANNER_API_KEY`/`GEN_SCRIPT_API_KEY`（**不要**把 .env 提交到仓库）。
- **Q：8G 显存能跑吗？** A：可以。模型按需加载，分析完自动卸载；也可 CPU 运行（慢）。

## ⚠️ 注意事项

- `.env`、`runtime/`、`models/`、素材等**不入库**（见 `.gitignore`）；真人素材请勿上传
- 对他人内容剪辑请注意版权与肖像权

## 📄 许可证

MIT License（第三方组件 Ollama/Shotcut 等遵循其各自协议，均不随仓库分发）

---

## 🙏 支持与致谢

如果这个项目帮到了你，**请点亮右上角的 ⭐ Star**，你的支持是我持续更新的最大动力！
