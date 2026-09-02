# AI Video Studio · 换电脑部署方案（DEPLOY）

## 🚀 换电脑部署（保证可用）

> **方式 A（推荐 · 100% 保证）**：用 U 盘 / 网盘把「原项目整份文件夹」（含 `ai-video-studio\runtime`、`models` 大件约 14GB）复制到新电脑 → 双击 `ai-video-studio\start_local.bat`。
>
> **方式 B（代码装配）**：`git clone https://github.com/yishui111/aijianjishiping.git` → 补齐下列大件 → 启动。

> 说明：模型/运行时体积超过 GitHub 单文件 100MB 上限，不随仓库分发；本仓库承载全部自研代码与装配指引。**方式 A 是最稳路径。**

---

## 1. 环境要求

- Windows 10/11 64 位（原生模式）
- 磁盘空闲 ≥ 20GB；内存 ≥ 8GB（推荐 16GB）；NVIDIA 显卡可选（无 GPU 自动回退 CPU，较慢）
- 可选：Docker Desktop（仅 Docker 部署方式需要）

## 2. 方式 A：整目录复制（最快最稳）

1. 把含大件的完整原项目文件夹拷到新电脑任意位置（保持内部结构不变）
2. 双击 `ai-video-studio\start_local.bat`
3. 等待服务就绪，浏览器会自动打开 <http://localhost:8003>

## 3. 方式 B：git clone + 装配

```bash
git clone https://github.com/yishui111/aijianjishiping.git
cd aijianjishiping
```

补齐（任选其一）：
- 从已部署机器**复制** `ai-video-studio\runtime\`（Ollama 便携 + venv）与 `ai-video-studio\models\`（qwen2.5vl:3b、qwen2.5:7b、bge-m3、whisper、chinese-clip）→ 最省事；
- 或全新下载/重建：见 `ai-video-studio\docs\implementation-plan.md`（Ollama 便携下载、`pip install` 依赖清单、各模型来源与落点、Chinese-CLIP 权重等）。

## 4. 配置

```bash
cd ai-video-studio
copy .env.example .env     # Windows
# 编辑 .env：不填任何 key 即全本地离线；填 PLANNER_API_KEY / GEN_SCRIPT_API_KEY 走线上 DeepSeek
```

⚠️ `.env` 已被 .gitignore 屏蔽，**不要提交**（内含密钥）。

## 5. 启动 / 停止 / 验证

| 动作 | 操作 |
| ---- | ---- |
| 启动 | 双击 `ai-video-studio\start_local.bat`（自动拉起 Ollama + analyzer/executor/planner） |
| 停止 | 双击 `ai-video-studio\stop_local.bat` |
| 验证 | 打开 <http://localhost:8003>；analyzer <http://localhost:8001> |

验证用例：分析一段视频 → 场记单出现场景/台词 → 勾选片段剪辑 → `ai-video-studio\output\` 出现成片。

## 6. 端口一览

| 端口 | 服务 |
| ---- | ---- |
| 8001 | analyzer（理解/分析 API） |
| 8002 | executor（剪辑执行） |
| 8003 | planner（对话/剧本/场记单 Web） |
| 11434 | Ollama（本地模型） |

## 7. 常见问题排查

- **启动后 8003 打不开**：看控制台日志；确认 Ollama 已起（11434 通）。
- **分析报模型未找到**：确认 `models\ollama\models` 与 whisper/clip 权重在位。
- **无 GPU 很慢**：属预期；调大 `ASR_CPU_THREADS` 或改用 GPU 机器。

详细设计与修复记录见 `ai-video-studio\docs\implementation-plan.md`。
