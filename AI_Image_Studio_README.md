# AI 出片系统 · 使用说明（统一管理）

> 所有项目都在这一个文件夹（`本项目根目录`）里，各自独立、自包含。
> **统一入口：双击 `control_menu.bat`** —— 一个菜单管所有项目的启动/停止。
>
> 📦 **GitHub 仓库范围**：本仓库收录 二次元/写实出图、官方界面、镜头工作台 的自研代码与文档；
> ComfyUI 引擎、模型权重、ffmpeg 需按根目录 [DEPLOY.md](DEPLOY.md) 下载后放回；
> `model_pad`（调试台）与 `ai-video-studio`（独立子项目）**未随仓库分发**（详见根 README FAQ）。

---

## 一、项目清单与启动/关闭方式

| # | 项目（子文件夹） | 干什么 | 启动 | 关闭 |
|---|----------------|--------|------|------|
| 1 | `Illustrious_ImageStudio` | 二次元出图（Illustrious v1.0 原版 + IPAdapter 锁角色） | 管理菜单[1] 或 `start_anime.bat` | 管理菜单[S] 或 `stop_anime.bat` |
| 2 | `FLUX2_ImageStudio` | 写实/场景出图（FLUX2 Klein Q8_0） | 管理菜单[2] 或 `start_flux2.bat` | 管理菜单[S] 或 `stop_flux2.bat` |
| 3 | `illustrious_ui` | 官方风格界面（Gradio，调二次元） | 管理菜单[3] 或 `start_ui.bat` | 管理菜单[S] 或关窗口 |
| 4 | `model_pad` | 极简模型调试台（对话框+模型） | 管理菜单[4] 或 `start_model_pad.bat` | 管理菜单[S] 或关窗口 |
| 5 | `Camera_Studio` | 镜头工作台（JSON控镜头→成片） | 管理菜单[5] 或 `start_camera.bat` | 管理菜单[S] 或 `stop_camera.bat` |

### 每个项目的独立启动脚本（都在各自文件夹里）
```
Illustrious_ImageStudio\start_anime.bat     → 引擎8188 + 工作台8093
FLUX2_ImageStudio\start_flux2.bat           → 引擎8189 + 工作台8092
illustrious_ui\start_ui.bat                 → 官方界面7860
model_pad\start_model_pad.bat               → 调试台8095
Camera_Studio\start_camera.bat              → 镜头工作台8094
```

### 停止
- **全部停止**：管理菜单按 [S]（停所有引擎+界面）
- **单个停止**：各项目自己的 `stop_*.bat`，或直接关掉启动窗口

---

## 二、端口速查

| 端口 | 是谁 | 网址 |
|------|------|------|
| 8188 | 二次元引擎 | — |
| 8189 | 写实引擎 | — |
| 8093 | 二次元工作台 | http://127.0.0.1:8093 |
| 8092 | 写实工作台 | http://127.0.0.1:8092 |
| 7860 | 官方风格界面 | http://127.0.0.1:7860 |
| 8095 | 模型调试台 | http://127.0.0.1:8095 |
| 8094 | 镜头工作台 | http://127.0.0.1:8094 |

---

## 三、完整流程（出图 → 镜头 → 成片）

```
① 剧本 JSON（漫画剧本格式：title/characters/timeline，每段含 image_prompt/duration/camera）
   ↓
② 出图系统批量出图（二选一）：
   二次元:  Illustrious_ImageStudio\start_batch_anime.bat 剧本.json 角色图目录
   写实:    FLUX2_ImageStudio\start_batch.bat 剧本.json 角色图目录
   → 输出图片 + manifest.json（含每段时长/相机/台词）
   ↓
③ 镜头工作台：
   Camera_Studio\start_batch_camera.bat 清单.json
   → 每段镜头视频 + 拼接成片
   ↓
④ （手动）配音/最终合成
```

---

## 四、显存规则（重要）

- **一次只开一个出图引擎**（二次元 8188 或 写实 8189），同时开会显存爆（16G 也会）
- 镜头工作台（8094）纯 ffmpeg 不吃显存，可随时开
- 官方界面（7860）/调试台（8095）只是前端，依赖对应引擎在跑

---

## 五、模型精度切换

**FLUX2（写实）**：默认 Q8_0（16G 显卡最佳）。8G 显卡机：
```
set FLUX2_QUANT=Q5_K_M
start_flux2.bat
```

---

## 六、常见问题

1. **打开网址没反应**：先看管理菜单顶部的引擎状态（运行中/未运行）
2. **出图报错"引擎未启动"**：先启动对应引擎（管理菜单[1]或[2]）
3. **显存不足**：一次只跑一个引擎；FLUX2 用 Q5 档
4. **锁角色不像**：官方界面/工作台里把强度调高（weight 0.85+）或换更清晰的参考图
