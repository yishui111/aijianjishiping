# AGENTS.md — AI Video Studio（aijianjishiping 仓库）项目档案

> ⚠️ 修改本仓库前先通读本文件。给「AI 助手 / 开发者」看的项目记忆：定位、结构、公开版边界、维护约定。

## 1. 定位
本地 AI 视频智能剪辑系统（目录历史名 aijianjishiping；早前同目录的 AI 出图工作室系列已**全部废弃删除**，2026-09 清理）。仓库只含**一个自研系统**：`ai-video-studio/`。

## 2. 结构与端口
| 组件 | 作用 |
| ---- | ---- |
| ai-video-studio/services/analyzer | L1 理解：场景切分/ASR/VLM 总结/向量（端口 8001） |
| ai-video-studio/services/planner | L2 方案：对话/剧本/场记单 API + 前端（8003） |
| ai-video-studio/services/executor | L3 执行：ffmpeg 剪辑（8002） |
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
