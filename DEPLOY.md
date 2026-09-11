# AI 字幕剪辑工作台 · 换电脑部署方案（DEPLOY）

## 🚀 换电脑部署（保证可用）

> **方式 A（推荐 · 100% 保证）**：用 U 盘 / 网盘把「原项目整份文件夹」（含 `tools\subtitle-clip\runtime`、`modelscope-cache` 大件约 5GB）复制到新电脑 → 双击根目录 `启动.bat`。
>
> **方式 B（代码装配）**：`git clone https://github.com/yishui111/aijianjishiping.git` → 补齐下列大件 → 启动。

> 说明：运行时与模型体积超过 GitHub 单文件 100MB 上限，不随仓库分发；本仓库承载全部自研代码与装配指引。**方式 A 是最稳路径。**

---

## 1. 环境要求

- Windows 10/11 64 位
- 磁盘空闲 ≥ 10GB；内存 ≥ 8GB（推荐 16GB）
- NVIDIA 显卡可选（无 GPU 自动回退 CPU，较慢）
- **无需单独安装 Python、ffmpeg**（`runtime\` 已含 venv 与 ffmpeg）
- 无需 Docker

## 2. 方式 A：整目录复制（最快最稳）

1. 把完整原项目文件夹拷到新电脑任意位置（保持内部结构不变）
2. 双击根目录 `启动.bat`
3. 等待服务就绪（首次加载语音模型约 10-20 秒），浏览器自动打开 <http://127.0.0.1:61810>

## 3. 方式 B：git clone + 装配

```bash
git clone https://github.com/yishui111/aijianjishiping.git
cd aijianjishiping/tools/subtitle-clip
```

补齐（任选其一）：

- 从已部署机器**复制**整个 `runtime\`（venv，约 1.7GB）与 `modelscope-cache\`（语音模型，约 3.3GB）→ 最省事；
- 或全新重建：
  ```bash
  python -m venv runtime
  runtime\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```
  首次启动会自动把 Paraformer / VAD / 标点 / 说话人模型下载到 `modelscope-cache\`（需联网，约 3.3GB）。

## 4. 启动 / 停止 / 验证

| 动作 | 操作 |
| ---- | ---- |
| 启动 | 双击根目录 `启动.bat`（内部调用 `tools\subtitle-clip\start.ps1`） |
| 停止 | 双击根目录 `关闭.bat` |
| 验证 | 剪辑工作台 <http://127.0.0.1:61810>；分析服务 <http://127.0.0.1:61812> |

验证用例（已实测通过）：

1. 分析服务填视频文件夹路径 → 点「分析」→ 该目录每个视频旁生成 `<视频名>.clip.json`（含台词 + 时间戳）
2. 关键词填「钱」→ 点关键词剪辑 → 该目录 `剪辑成片\` 出现命中台词的成片
3. 剧本框逐行填台词 → 点剧本剪辑 → 按剧本顺序剪接成片

## 5. 端口一览

| 端口 | 服务 |
| ---- | ---- |
| 61810 | 剪辑工作台（Gradio：识别 / 勾台词 / 时间线精修 / 烧字幕） |
| 61812 | 分析服务（FastAPI：目录批量分析 / 关键词剪辑 / 剧本剪辑） |

## 6. 常见问题排查

- **双击 `启动.bat` 一闪而过 / 没反应**：说明脚本报错退出。先确认 `tools\subtitle-clip\runtime\` 与 `modelscope-cache\` 在位，再看 `tools\subtitle-clip\logs\` 下的 `studio.log.err` / `api.log.err`。
- **61810 或 61812 打不开**：看 `logs\` 日志；确认端口没被其它程序占用。
- **分析报模型找不到**：确认 `modelscope-cache\models\` 下四个模型目录齐全（Paraformer / VAD / 标点 / 说话人）。
- **无 GPU 很慢**：属预期；改用 GPU 机器可显著加速。
- **转写报 CUDA / DLL 错误**：走 CPU 模式即可（无 GPU 自动回退）。

## 7. 脚本维护约定

- `启动.bat` / `关闭.bat`：**纯 ASCII 内容 + CRLF + 无 BOM**（避免中文在 GBK 代码页下乱码）；中文只出现在文件名里
- `tools\subtitle-clip\start.ps1`：**含中文，必须带 UTF-8 BOM + CRLF**，否则 PowerShell 5.1 会按 GBK 解析成语法错误
- 日志统一落 `tools\subtitle-clip\logs\`
