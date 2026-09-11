<div align="center">

# 🎬 AI 字幕剪辑工作台 — 本地离线字幕驱动剪辑系统

> ⭐ **喜欢这个项目？请先点个 Star ⭐ 支持一下，让更多人看到！**

![GitHub stars](https://img.shields.io/github/stars/yishui111/aijianjishiping.svg?style=flat-square&color=orange)
![GitHub forks](https://img.shields.io/github/forks/yishui111/aijianjishiping.svg?style=flat-square)

**把视频里的声音转成带时间戳的字幕 → 按一句话/关键词/剧本精准剪出成片。全程本地离线运行，素材不出门。**

</div>

---

## ✨ 项目简介

给系统一个**视频文件夹**，它会用本地语音大模型（Paraformer + VAD + 标点 + 说话人区分）把每个视频的台词识别出来，在**同目录生成一份台词 JSON**（谁在何时说了什么）。之后：

- 输入**一句话或关键词** → 自动找出字幕里对应的时间区间并剪出成片
- 给一段**小剧本** → 按剧本每一句去已分析的台词 JSON 里找最相近的话，按剧本顺序剪出来
- 也可以直接在**剪映式时间线**里手动精修、烧录硬字幕、导出 SRT

> 仓库名 `aijianjishiping` 为历史命名。早前的「AI 视频智能剪辑系统 / 画面语义选段」路线已废弃下线（画面语义选段实测不可靠），现全线转向**字幕（台词时间戳）驱动**方案。

## 🎯 主要功能

- **字幕识别**：本地语音大模型（Paraformer-large + FSMN-VAD + 标点恢复 + CAM++ 说话人区分），CPU/GPU 自适应
- **目录批量分析**：给一个目录，逐个视频识别 → 每个视频旁生成 `<视频名>.clip.json`
- **一句话 / 关键词剪辑**：关键词包含匹配，确定性时间戳，不幻觉
- **剧本自动匹配剪辑**：剧本逐条 ↔ 台词做文本相似度匹配（阈值 0.45），按剧本顺序剪接
- **说话人区分**：标注每句话是谁说的，可按说话人筛选
- **自动加字幕**：识别结果直接烧录进画面，或导出 SRT
- **手动精修**：内置剪映式时间线（分割 / 删除 / 字幕烧录）

## 🗂️ 目录结构

```
aijianjishiping/
├── 启动.bat / 关闭.bat        # 根目录一键启停（包装脚本）
├── tools/
│   └── subtitle-clip/         # 系统本体
│       ├── start.ps1          # 启停脚本（真正干活的那个）
│       ├── stop.ps1
│       ├── api_service.py     # 分析服务（FastAPI，61812）
│       ├── funclip/           # 剪辑工作台（Gradio，61810）+ 剪辑引擎
│       ├── runtime/           # venv（含 torch/ffmpeg，约 1.7GB，不入库）
│       ├── modelscope-cache/  # 语音模型缓存（约 3.3GB，不入库）
│       ├── test-media/        # 测试素材
│       └── logs/              # 运行日志
├── README.md / DEPLOY.md / AGENTS.md
```

## 🚀 快速开始（换电脑部署）

| 方式 | 操作 | 说明 |
| ---- | ---- | ---- |
| **A（推荐，100%）** | U 盘/网盘把**原项目整份文件夹**（含 `tools\subtitle-clip\runtime` + `modelscope-cache` 约 5GB）复制到新电脑 | 双击 `启动.bat` 即用 |
| **B（代码装配）** | `git clone` 本仓库 → 按 [DEPLOY.md](DEPLOY.md) 补齐大件 | 需重建 venv 与模型缓存 |

启动后会自动打开两个页面：

- <http://127.0.0.1:61810> —— 剪辑工作台（上传/选择视频 → 识别 → 勾台词 → 裁剪）
- <http://127.0.0.1:61812> —— 分析服务（整个文件夹批量分析 → 台词 JSON → 关键词/剧本剪辑）

关闭：双击 `关闭.bat`。

## 📄 台词 JSON 格式

分析完成后，每个视频**同目录**生成 `视频名.clip.json`：

```json
{
  "video": "第1集.mp4",
  "duration_sec": 2200.5,
  "lines": [
    {"start": 1.2, "end": 4.5, "text": "你当初是不是看到我爸的钱了", "speaker": "女人"}
  ]
}
```

后续的关键词剪辑与剧本剪辑都读这份 JSON，按时间区间精确剪出。

## 📥 大件资源（不入库，部署时获取）

| 资源 | 大小 | 获取 |
| ---- | ---- | ---- |
| `tools\subtitle-clip\runtime\`（venv，含 torch + ffmpeg） | ~1.7GB | 方式 A 母版复制；或 `python -m venv runtime` + `pip install -r requirements.txt` |
| `tools\subtitle-clip\modelscope-cache\`（Paraformer/VAD/标点/说话人模型） | ~3.3GB | 母版复制；或首次启动自动下载 |

## ❓ 常见问题

- **Q：双击 `启动.bat` 没反应？** A：确认 `tools\subtitle-clip\runtime\` 与 `modelscope-cache\` 已就位（见 DEPLOY.md）；若窗口一闪而过，说明脚本报错，查看 `tools\subtitle-clip\logs\` 下的日志。
- **Q：8G 显存能跑吗？** A：可以。无 GPU 自动回退 CPU（较慢）。
- **Q：分析很慢？** A：首次会加载语音模型（约 10-20 秒），之后常驻内存；单条视频识别速度约数倍实时。

## ⚠️ 注意事项

- `runtime/`、`modelscope-cache/`、`logs/`、`test-media/` 等**不入库**（见 `.gitignore`）；真人素材请勿上传
- 对他人内容剪辑请注意版权与肖像权

## 📄 许可证

MIT License。基于开源项目 [FunClip](https://github.com/modelscope/FunClip)（MIT）二次开发，依赖 FunASR / ModelScope 生态，第三方组件遵循其各自协议。

---

## 🙏 支持与致谢

如果这个项目帮到了你，**请点亮右上角的 ⭐ Star**，你的支持是我持续更新的最大动力！
