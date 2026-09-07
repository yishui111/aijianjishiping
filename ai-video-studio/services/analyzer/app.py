"""L1 理解服务：素材（视频/图片）→ analysis.json + manifest.json。"""
from __future__ import annotations

import base64
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import clip_client, media, util  # noqa: E402

MATERIALS = Path(os.environ.get("MATERIALS_DIR", "/materials"))
ANALYSIS = Path(os.environ.get("ANALYSIS_DIR", "/analysis"))
VLM_BASE_URL = os.environ.get("VLM_BASE_URL", "http://ollama:11434/v1").rstrip("/")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:3b")
# 视觉大模型开关：true=总是用 / false=禁用（用 CLIP 零样本标注兜底） / auto=先试，连续失败自动熔断转 CLIP
# 本机跑不动 VLM 时在 .env 里设 VLM_ENABLED=false，分析照常出场景/标签/向量，只是没有 VLM 的自由文本描述
VLM_ENABLED = os.environ.get("VLM_ENABLED", "auto").strip().lower()
NUM_CTX = int(os.environ.get("NUM_CTX", "8192"))
ASR_MODEL = os.environ.get("ASR_MODEL", "faster-whisper-small")
WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "")
# 转写 CPU 线程上限：限制为 4（8G 电脑/笔记本安全值），防止吃满所有核导致过热
ASR_CPU_THREADS = int(os.environ.get("ASR_CPU_THREADS", "4"))
# 转写设备：auto=自动（GPU 库缺失时自动回退 CPU）/ cpu=强制 CPU（本机缺 cublas 等运行库时在 .env 设 cpu）
ASR_DEVICE = os.environ.get("ASR_DEVICE", "auto").strip().lower()
FACE_ENABLED = os.environ.get("FACE_ENABLED", "false").lower() == "true"
MAX_FRAMES_PER_SCENE = int(os.environ.get("MAX_FRAMES_PER_SCENE", "2"))
MAX_SCENES = int(os.environ.get("MAX_SCENES", "16"))
FRAME_MAX_W = int(os.environ.get("FRAME_MAX_W", "384"))
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".ts"}

app = FastAPI(title="AI Video Studio Analyzer")

# 字段值给出简短枚举而非示例文本，避免小模型把示例原样输出
SCENE_PROMPT = (
    "你是视频素材分析器。下面这张图是同一段视频【开头、中间、结尾】三个时刻截图的横向拼接（从左到右依次为开头、中间、结尾），请综合这张图分析这一整段视频。"
    "描述 content 时请写一句完整通顺的话，必须包含：【画面主体是谁 + 在做什么 + 什么场景 + 关键视觉元素（人物穿着颜色/道具/环境特征）】，"
    "例如『身穿蓝色官袍的官员在室内向皇后行礼，皇后端坐，周围有宫女侍立』，不要写『一段对话场景』这种泛化描述。"
    "只输出严格 JSON（不要 markdown、不要任何解释、不要复述字段说明）："
    '{"content":"这句话描述画面",'
    '"subjects":["画面里出现的所有人物或主体，同框的都要列出"],'
    '"type":"wide或closeup或interview或aerial或medium",'
    '"camera":"固定或推或拉或摇或移或跟或俯拍",'
    '"lighting":"自然光或室内光或暗光或逆光或舞台光",'
    '"energy":"low或medium或high",'
    '"editing_use":"broll或空镜或定场或采访或动作或对白",'
    '"confidence":0到1之间的小数}'
)


class AnalyzeRequest(BaseModel):
    file: str
    force: bool = False


class ScanRequest(BaseModel):
    folder: str = "."
    force: bool = False  # True = 已分析也重跑（旧分析补台词/刷新 CLIP 标注）
    mode: str = "full"   # full=完整分析；asr=只跑语音转写（提取字幕，秒级/分钟级，不跑画面理解）


class QuerySegmentRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float = 10.0
    num_frames: int = 4


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


# ---------- 工具函数 ----------

# VLM 熔断器：连续失败 2 次即认为本机跑不动 VLM，剩余调用直接跳过（不再白等超时）
_vlm_fail_streak = 0
_VLM_FAIL_LIMIT = 2


def _vlm_active() -> bool:
    """VLM 是否应参与分析（结合开关与熔断状态）。"""
    if VLM_ENABLED in ("false", "0", "no", "off"):
        return False
    return _vlm_fail_streak < _VLM_FAIL_LIMIT


def _vlm_describe(images: list) -> dict | None:
    """调 Ollama（OpenAI 兼容接口）分析一组帧，返回语义 JSON。

    每帧单独编码进 messages（多图）；实测 3B 模型 1~2 帧多图识别人物最稳，
    3 帧多图/拼图会泛化成"角色A/皇帝大臣"式抽象标签，故保持 2 帧上限。
    VLM 被禁用/熔断时立即返回 None（走 CLIP 零样本标注兜底）。
    """
    if not _vlm_active() or not images:
        return None
    global _vlm_fail_streak
    parts = [{"type": "text", "text": SCENE_PROMPT}]
    for img in images:
        ok, buf = cv2.imencode(".jpg", img)
        if not ok:
            continue
        b64 = base64.b64encode(buf.tobytes()).decode()
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    if len(parts) == 1:
        return None
    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": parts}],
        "temperature": 0.2,
        "max_tokens": 512,
    }
    try:
        resp = httpx.post(f"{VLM_BASE_URL}/chat/completions", json=payload, timeout=120)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        _vlm_fail_streak = 0
        return util.parse_llm_json(content)
    except Exception as exc:
        _vlm_fail_streak += 1
        if _vlm_fail_streak == _VLM_FAIL_LIMIT:
            print(f"[vlm] 连续 {_vlm_fail_streak} 次失败，本次扫描剩余场景改用 CLIP 零样本标注（最后一次错误: {exc}）")
        else:
            print(f"[vlm] 失败: {exc}")
        return None


# ---------- CLIP 零样本场景标注（VLM 不可用时的语义兜底） ----------
# 原理：Chinese-CLIP 把"标签文本"和"关键帧画面"编码到同一向量空间，
# 余弦相似度即"画面像不像这个标签"。完全本地、确定性、毫秒级，不需要任何大模型。
_CLIP_LABELS = [
    ("人物特写", "一个人面部的特写镜头"),
    ("人物对话", "两个人物面对面说话交谈"),
    ("打斗动作", "两个人激烈打斗搏斗的动作场面"),
    ("武打兵器", "手持刀剑兵器的武打场面"),
    ("奔跑追逐", "人物在奔跑追逐"),
    ("跪拜礼仪", "古代礼仪跪拜祭祀典礼场面"),
    ("悲伤情绪", "人物悲伤哭泣的情绪戏"),
    ("户外风景", "户外大自然山水风景"),
    ("室内场景", "室内房间宫殿里的场景"),
    ("夜晚场景", "夜晚黑暗的夜景"),
    ("人群场面", "很多人聚集的热闹场面"),
    ("动物画面", "一只动物在画面里"),
    ("水边海浪", "海边水边有波浪"),
    ("宫殿建筑", "古代宫殿楼阁建筑"),
    ("骑马出行", "骑马或坐马车的画面"),
    ("吃饭喝酒", "吃饭喝酒宴席的画面"),
]
_clip_label_vecs: list = None  # 惰性缓存：每个标签的 CLIP 文本向量


def _label_vecs() -> list:
    """标签文本向量（一次性计算并缓存；CLIP 不可用时返回空表）。"""
    global _clip_label_vecs
    if _clip_label_vecs is not None:
        return _clip_label_vecs
    _clip_label_vecs = []
    try:
        vecs = clip_client.text_embed([t for _, t in _CLIP_LABELS])
        if vecs and len(vecs) == len(_CLIP_LABELS):
            _clip_label_vecs = [v for v in vecs]
    except Exception as exc:
        print(f"[clip] 标签向量编码失败: {exc}")
    return _clip_label_vecs


def _clip_label_scene(clip_vec: list) -> tuple[list[dict], str, str, float]:
    """单场景零样本标注：返回 (labels, content, energy_hint, confidence)。

    - labels: [{name,score}] 相似度达阈值的前 3 个
    - content: 拼好的可读描述（"画面:打斗动作/人物特写 · 场景:户外风景"）
    - energy_hint: 暂为空（能量由动量统计决定）
    - confidence: 最高标签分（映射到 0~0.6，明确低于 VLM 描述的可信度）
    """
    lvecs = _label_vecs()
    if not lvecs or not clip_vec:
        return [], "", "", 0.0
    import numpy as np

    v = np.asarray(clip_vec, dtype=np.float32)
    scored = []
    for (name, _txt), lv in zip(_CLIP_LABELS, lvecs):
        score = float(np.dot(v, np.asarray(lv, dtype=np.float32)))
        scored.append((name, score))
    scored.sort(key=lambda x: -x[1])
    top = [{"name": n, "score": round(s, 3)} for n, s in scored[:3] if s >= 0.16]
    if not top:
        top = [{"name": scored[0][0], "score": round(scored[0][1], 3)}]
    names = [t["name"] for t in top]
    content = "画面:" + "/".join(names[:2]) + (" · 场景:" + names[2] if len(names) > 2 else "")
    return top, content, "", min(0.6, round(top[0]["score"] * 1.5, 2))


def _motion_energy(motion: float, cut_density: float) -> str:
    """动量+切点密度 → 能量档位（无 VLM 时的确定性能量估计）。"""
    m = motion + cut_density * 0.5
    if m >= 0.12:
        return "high"
    if m >= 0.05:
        return "medium"
    return "low"


_TYPE_OK = {"wide", "closeup", "interview", "aerial", "medium"}
_CAMERA_OK = ("固定", "推", "拉", "摇", "移", "跟", "俯拍")
_ENERGY_OK = {"low", "medium", "high"}
_USE_OK = {"broll", "空镜", "定场", "采访", "动作", "对白"}


def _norm_semantic(semantic: dict | None) -> dict:
    """清洗 VLM 输出：白名单校验枚举字段，防止小模型把 prompt 示例/废话原样输出。"""
    if not semantic:
        return {}
    out = {}
    if semantic.get("content"):
        out["content"] = str(semantic["content"])[:100]
    subs = [str(t).strip()[:30] for t in (semantic.get("subjects") or []) if str(t).strip()]
    if subs:
        out["subjects"] = subs[:6]
    t = str(semantic.get("type") or "").strip().lower()
    if t in _TYPE_OK:
        out["type"] = t
    c = str(semantic.get("camera") or "").strip()
    if c and any(k in c for k in _CAMERA_OK):
        out["camera"] = c
    l = str(semantic.get("lighting") or "").strip()
    if l:
        out["lighting"] = l[:20]
    e = str(semantic.get("energy") or "").strip().lower()
    if e in _ENERGY_OK:
        out["energy"] = e
    u = str(semantic.get("editing_use") or "").strip().lower()
    if u in _USE_OK:
        out["editing_use"] = u
    try:
        conf = float(semantic.get("confidence"))
        out["confidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    return out


def _find_cuts(path: Path) -> tuple[float, list[tuple[float, float]], list[tuple[float, float]]]:
    """检测所有镜头切点：返回 (时长, [(切点秒, 差异分数)...], [(样本秒, 帧间差异)...])。

    第三个返回值是逐样本差异时间线（画面变化剧烈程度），供无 VLM 时估计场景能量。
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return 0.0, [], []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps if total else 0.0
    stride = max(1, int(fps * 0.5))
    prev_hist, prev_edge = None, None
    cuts: list[tuple[float, float]] = []
    samples: list[tuple[float, float]] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            small = cv2.resize(frame, (160, 90))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            hist = hist.flatten()
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            edge = float((cv2.Canny(gray, 60, 120) > 0).mean())
            if prev_hist is not None:
                hist_diff = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                edge_diff = abs(edge - prev_edge)
                diff = max(hist_diff, edge_diff * 3)
                t = idx / fps
                samples.append((t, diff))
                # 阈值放宽：旧剧/低对比度视频转场柔和，0.35/0.12 会漏切成长镜头
                if hist_diff > 0.25 or edge_diff > 0.08:
                    if not cuts or t - cuts[-1][0] > 0.8:
                        cuts.append((t, diff))
            prev_hist, prev_edge = hist, edge
        idx += 1
    cap.release()
    return duration, cuts, samples


def _detect_cuts(path: Path, min_sec: float = 1.5, max_sec: float = 15.0) -> tuple[list[dict], list[tuple[float, float]], list[tuple[float, float]]]:
    """细镜头切分：返回 (所有镜头边界（最小 1.5s，最长 15s，不合并——供场记精确时间戳）, 差异样本时间线, 切点列表)。

    2 分钟视频能得到几十个镜头级分段，而不是几个大段。
    """
    duration, cuts, samples = _find_cuts(path)
    if not duration:
        return [{"index": 0, "start": 0.0, "end": 0.0}]
    boundaries = [0.0] + [t for t, _ in cuts] + [duration]
    segs = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        if e - s >= 0.3:
            segs.append({"start": s, "end": e})
    if not segs:
        segs = [{"start": 0.0, "end": duration}]
    # 合并过短段（< min_sec 并入前一段，避免碎镜头）
    out = []
    for seg in segs:
        if out and seg["end"] - out[-1]["start"] < min_sec:
            out[-1]["end"] = seg["end"]
        else:
            out.append(dict(seg))
    # 超长段强制切分（保证每段 ≤ max_sec）
    final = []
    for seg in out:
        s, e = seg["start"], seg["end"]
        while e - s > max_sec:
            final.append({"start": s, "end": s + max_sec})
            s += max_sec
        if e - s >= 0.3:
            final.append({"start": s, "end": e})
    for i, sc in enumerate(final):
        sc["index"] = i
    return final, samples, cuts


def _motion_stats(samples: list[tuple[float, float]], cuts: list[tuple[float, float]], start: float, end: float) -> tuple[float, float]:
    """某时间段的 (平均动量, 切点密度)——无 VLM 时估计场景能量的确定性信号。"""
    seg_len = max(0.1, end - start)
    mots = [d for t, d in samples if start < t <= end]
    motion = sum(mots) / len(mots) if mots else 0.0
    density = sum(1 for t, _ in cuts if start < t <= end) / seg_len
    return motion, density


def _detect_scenes(path: Path, max_scenes: int | None = None) -> list[dict]:
    """场景切分（合并版）：供 VLM 描述分组，数量受 max_scenes 控制。"""
    max_scenes = max_scenes or MAX_SCENES
    duration, cuts, _samples = _find_cuts(path)
    if not duration:
        return [{"index": 0, "start": 0.0, "end": 0.0}]
    cut_scores = {t: score for t, score in cuts}
    boundaries = [0.0] + [t for t, _ in cuts] + [duration]
    scenes = []
    for i in range(len(boundaries) - 1):
        s, e = boundaries[i], boundaries[i + 1]
        if e - s >= 0.3:
            scenes.append(
                {"index": len(scenes), "start": round(s, 3), "end": round(e, 3), "cut_score": cut_scores.get(e, 0.0)}
            )
    if not scenes:
        scenes = [{"index": 0, "start": 0.0, "end": round(duration, 3), "cut_score": 0.0}]
    # 超长场景强制切分（转场检测漏切时兜底，保证剪辑粒度，如 0-118s 的整场戏）
    max_scene_sec = 45.0
    if max_scene_sec > 0:
        split_scenes = []
        for sc in scenes:
            s, e = sc["start"], sc["end"]
            while e - s > max_scene_sec:
                split_scenes.append({"start": s, "end": s + max_scene_sec, "cut_score": 0.0})
                s += max_scene_sec
            if e - s >= 0.3:
                split_scenes.append({"start": s, "end": e, "cut_score": sc.get("cut_score", 0.0)})
        scenes = split_scenes
    # 场景过多时贪心合并：优先合并"最相似"的相邻场景（切点分数最低），
    # 且合并后单场景不超过 MAX_SCENE_SEC（防止低分切点扎堆导致超长场景）
    while len(scenes) > max_scenes:
        best_i = None
        best_score = None
        for i in range(len(scenes) - 1):
            merged_len = scenes[i + 1]["end"] - scenes[i]["start"]
            if merged_len > 60.0:
                continue  # 合并会超长，跳过该切点
            score = scenes[i].get("cut_score", 0.0)
            if best_score is None or score < best_score:
                best_score, best_i = score, i
        if best_i is None:
            # 所有相邻合并都超长（极端情况）：允许合并分数最低的，保证数量达标
            best_i = min(range(len(scenes) - 1), key=lambda i: scenes[i].get("cut_score", 0.0))
        a, b = scenes[best_i], scenes[best_i + 1]
        merged_scene = {"index": best_i, "start": a["start"], "end": b["end"], "cut_score": a.get("cut_score", 0.0)}
        scenes = scenes[:best_i] + [merged_scene] + scenes[best_i + 2 :]
        for i, sc in enumerate(scenes):
            sc["index"] = i
    # 统一编号（场景数未超上限时跳过合并，split_scenes 可能没 index）
    for i, sc in enumerate(scenes):
        sc["index"] = i
    return scenes


def _extract_frames(path: Path, scenes: list[dict], out_dir: Path, frames_per_scene: int | None = None) -> list[dict]:
    """每场景取 N 帧代表帧，降采样保存并返回（含内存图像供 VLM）。"""
    frames_per_scene = frames_per_scene or MAX_FRAMES_PER_SCENE
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    frames = []
    for scene in scenes:
        s, e = scene["start"], scene["end"]
        if e - s < 0.1:
            continue
        for i in range(frames_per_scene):
            # 均匀覆盖首/中/尾（3帧时取 1/4、2/4、3/4 位置），避免只看场景中间某一刻
            t = s + (e - s) * (i + 1) / (frames_per_scene + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            if w > FRAME_MAX_W:
                frame = cv2.resize(frame, (FRAME_MAX_W, int(h * FRAME_MAX_W / w)))
            name = f"scene{scene['index']:04d}_f{i}.jpg"
            util.save_image(frame, str(out_dir / name))
            frames.append({"scene_index": scene["index"], "t": round(t, 3), "path": str(out_dir / name), "image": frame})
    cap.release()
    return frames


def _extract_frames_range(path: Path, start: float, end: float, num_frames: int) -> list:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    out = []
    for i in range(num_frames):
        t = start + (end - start) * (i + 0.5) / num_frames
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        if w > FRAME_MAX_W:
            frame = cv2.resize(frame, (FRAME_MAX_W, int(h * FRAME_MAX_W / w)))
        out.append(frame)
    cap.release()
    return out


_whisper = None
_asr_cpu_only = False  # GPU 运行库缺失/显存不足被探测到后，本次进程固定走 CPU


def _asr_model(path: str):
    """加载 whisper 模型：按 ASR_DEVICE 配置，auto 失败自动回退 CPU。"""
    from faster_whisper import WhisperModel
    global _asr_cpu_only
    if ASR_DEVICE in ("cpu",) or _asr_cpu_only:
        _asr_cpu_only = True
        # cpu_threads 限制转写占用的核数（默认 4），防止 CPU 满载过热
        return WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=ASR_CPU_THREADS)
    try:
        return WhisperModel(path, device="auto", compute_type="int8", cpu_threads=ASR_CPU_THREADS)
    except Exception:
        _asr_cpu_only = True
        return WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=ASR_CPU_THREADS)


def _asr(path: Path) -> list[dict]:
    global _whisper
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        print(f"[asr] faster-whisper 未安装: {exc}")
        return []
    if _whisper is None:
        local = Path(WHISPER_MODEL_DIR) if WHISPER_MODEL_DIR else None
        if local and local.exists() and any(local.iterdir()):
            model_path = str(local)
        else:
            model_path = ASR_MODEL
        try:
            _whisper = _asr_model(model_path)
        except Exception as exc:
            print(f"[asr] 模型加载失败（离线需预缓存）: {exc}")
            return []

    def _do_transcribe(model):
        # initial_prompt 偏置简体中文（whisper 对港台/老剧素材偶尔输出繁体）
        segments, _ = model.transcribe(str(path), vad_filter=True, initial_prompt="以下是普通话简体中文的句子。")
        return [
            {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "speaker": None}
            for s in segments
            if s.text and s.text.strip()
        ]

    try:
        return _do_transcribe(_whisper)
    except Exception as exc:
        # GPU 运行库缺失（如 cublas DLL）/显存不足可能在转写时才爆：重建为 CPU 模型重试一次
        print(f"[asr] 转写失败（{exc}），回退 CPU 重试")
        local = Path(WHISPER_MODEL_DIR) if WHISPER_MODEL_DIR else None
        model_path = str(local) if local and local.exists() and any(local.iterdir()) else ASR_MODEL
        try:
            _whisper = _asr_model(model_path)
            return _do_transcribe(_whisper)
        except Exception as exc2:
            print(f"[asr] CPU 转写也失败: {exc2}")
            return []


def _faces(path: Path, out_dir: Path) -> list[dict]:
    """人脸嵌入（P1b）。骨架默认关闭；启用需安装 insightface 并实现。"""
    if not FACE_ENABLED:
        return []
    print("[face] FACE_ENABLED=true，但 insightface 集成在 P1b 提供，当前跳过")
    return []


def _analyze_image(src: Path, out_dir: Path, rel: str) -> dict:
    frame = cv2.imread(str(src))
    h, w = frame.shape[:2]
    if w > FRAME_MAX_W:
        frame = cv2.resize(frame, (FRAME_MAX_W, int(h * FRAME_MAX_W / w)))
    semantic = _norm_semantic(_vlm_describe([frame]))
    labels: list[dict] = []
    if not semantic.get("content"):
        # VLM 不可用：CLIP 零样本标注兜底（图片本身就有向量，直接对标签文本比相似度）
        vecs = clip_client.image_embed([frame])
        if vecs and vecs[0] is not None:
            labels, content, _e, conf = _clip_label_scene([round(float(x), 6) for x in vecs[0]])
            if content:
                semantic = {"content": content, "subjects": [], "confidence": conf}
                semantic["labels"] = labels
    out_dir.mkdir(parents=True, exist_ok=True)
    thumb = out_dir / "thumb.jpg"
    util.save_image(frame, str(thumb))
    analysis = {
        "schema_version": 1,
        "file": rel,
        "file_hash": f"sha256:{util.quick_hash(src)}",
        "duration_sec": None,
        "resolution": f"{w}x{h}",
        "fps": None,
        "has_audio": False,
        "language": None,
        "summary": semantic.get("content"),
        "tags": semantic.get("subjects") or [],
        "scenes": [
            {
                "start": 0.0, "end": None, "keyframes": [str(thumb)],
                **semantic,
            }
        ],
        "faces": [],
        "speech": [],
        "silent_segments": [],
        "audio_stats": None,
        "analysis_meta": {
            "vlm": VLM_MODEL, "asr": None, "scene_detector": "single_image",
            "analyzed_at": util.now_iso(), "analysis_dir": str(out_dir),
        },
    }
    return analysis


ANALYSIS_DIRNAME = "_analysis"  # 每个素材目录下的分析产物集中区


def _analysis_paths(src: Path) -> tuple[Path, Path]:
    """分析产物路径：素材目录下 _analysis/ 集中存放（xxx.mp4.analysis.json + xxx_frames/）。"""
    p = src.resolve()
    base = p.parent / ANALYSIS_DIRNAME
    return base / (p.name + ".analysis.json"), base / (p.stem + "_frames")


def _dir_manifest_path(src: Path) -> Path:
    """该素材所在目录 _analysis/manifest.json。"""
    return src.resolve().parent / ANALYSIS_DIRNAME / "manifest.json"


def analyze_one(file: str, force: bool = False) -> dict:
    src = (MATERIALS / file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"素材不存在: {file}")
    key = util.quick_hash(src)
    rel = src.relative_to(MATERIALS.resolve()).as_posix()  # 相对路径（含子目录），如 项目A/开场.mp4
    analysis_path, frames_dir = _analysis_paths(src)
    # 已分析判定：同目录存在 .analysis.json 即视为已分析（幂等跳过）
    if analysis_path.exists() and not force:
        return util.load_json(analysis_path)

    if src.suffix.lower() in IMAGE_EXTS:
        analysis = _analyze_image(src, frames_dir, rel)
    else:
        info = media.probe(src)
        dur = info.get("duration_sec") or 0
        # 动态描述组数：短视频少切（提速），长视频多切（粒度够）
        # 2 分钟 → 6 组；3~10 分钟 → 6~16 动态；10 分钟+ → 16 封顶
        max_scenes = min(MAX_SCENES, max(6, int(dur / 30))) if dur else MAX_SCENES
        # 每场景 2 帧（多图调用）：实测 1~2 帧识别人物最稳，3 帧会让 3B 模型泛化
        frames_per_scene = MAX_FRAMES_PER_SCENE if _vlm_active() else 0
        # 两段式：
        # ① 细镜头（_detect_cuts）：所有镜头边界（1.5~15s），场记时间戳精确到镜头
        # ② 描述组（_detect_scenes 合并版）：VLM 只描述合并组，控制调用次数
        #    VLM 不可用时描述留空，由 CLIP 零样本标注兜底（不抽描述帧，省一遍解码）
        fine_scenes, motion_samples, cut_list = _detect_cuts(src)
        desc_scenes = _detect_scenes(src, max_scenes=max_scenes)
        # 每个细镜头抽 1 张中帧缩略图（场记每行可见自己画面）+ CLIP 画面向量（供向量搜索/重复检测）
        fine_thumbs: list[str] = []
        fine_vecs: list[list[float]] = []  # 每细镜头 512 维归一化 CLIP 向量
        try:
            fcap = cv2.VideoCapture(str(src))
            fine_frames: list = []
            for fs in fine_scenes:
                t = (fs["start"] + fs["end"]) / 2
                fcap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, frame = fcap.read()
                if not ok:
                    continue
                h, w = frame.shape[:2]
                if w > FRAME_MAX_W:
                    frame = cv2.resize(frame, (FRAME_MAX_W, int(h * FRAME_MAX_W / w)))
                name = f"fine{fs['index']:04d}.jpg"
                util.save_image(frame, str(frames_dir / name))
                fine_thumbs.append(str(frames_dir / name))
                fine_frames.append(frame)
            fcap.release()
            # 批量 CLIP 编码（一次推理整批，快于逐帧调用）
            if fine_frames:
                vecs = clip_client.image_embed(fine_frames)
                for v in vecs:
                    fine_vecs.append([round(float(x), 6) for x in v] if v is not None else [])
        except Exception as exc:
            print(f"[analyze] 细镜头缩略图/CLIP 失败: {exc}")
        frames = _extract_frames(src, desc_scenes, frames_dir, frames_per_scene=frames_per_scene) if frames_per_scene else []
        keyframes = [{"scene_index": f["scene_index"], "t": f["t"], "path": f["path"]} for f in frames]
        # 描述组 VLM（VLM 不可用时该循环退化为空占位，语义由 CLIP 标注按细镜头补齐）
        desc_results = []
        for scene in desc_scenes:
            imgs = [f["image"] for f in frames if f["scene_index"] == scene["index"]]
            semantic = _norm_semantic(_vlm_describe(imgs)) if imgs else {}
            if not semantic.get("content"):
                semantic = {}  # VLM 禁用/熔断/输出无效：留空走 CLIP 兜底
            desc_results.append(
                {
                    "start": scene["start"], "end": scene["end"],
                    **semantic,
                    "keyframes": [k["path"] for k in keyframes if k["scene_index"] == scene["index"]],
                }
            )
        # 细镜头继承所属描述组的语义（时间戳精确到镜头，描述来自组，缩略图来自镜头自己）
        scene_results = []
        for fi, fs in enumerate(fine_scenes):
            fs_start, fs_end = fs["start"], fs["end"]
            desc = None
            for d in desc_results:
                if fs_start >= d["start"] - 0.1 and fs_end <= d["end"] + 0.1:
                    desc = d
                    break
            if desc is None and desc_results:
                # 跨组边界：归属覆盖它中点的组
                mid = (fs_start + fs_end) / 2
                desc = min(desc_results, key=lambda d: abs((d["start"] + d["end"]) / 2 - mid))
            if desc is None:
                desc = {}
            motion, density = _motion_stats(motion_samples, cut_list, fs_start, fs_end)
            scene_results.append(
                {
                    "start": round(fs_start, 1),
                    "end": round(fs_end, 1),
                    "content": desc.get("content", ""),
                    "subjects": desc.get("subjects") or [],
                    "type": desc.get("type", ""),
                    "camera": desc.get("camera", ""),
                    "lighting": desc.get("lighting", ""),
                    "energy": desc.get("energy") or _motion_energy(motion, density),
                    "editing_use": desc.get("editing_use", ""),
                    "confidence": desc.get("confidence", 0.0),
                    "keyframes": [fine_thumbs[fi]] if fi < len(fine_thumbs) else (desc.get("keyframes") or []),
                    "clip_vec": (fine_vecs[fi] if fi < len(fine_vecs) else []),  # 画面向量（CLIP 512d）
                }
            )
        # VLM 没给出描述的镜头：CLIP 零样本标注兜底（画面向量 ↔ 标签文本向量比相似度）
        labels_used = False
        for sr in scene_results:
            if not sr.get("content"):
                labels, content, _e, conf = _clip_label_scene(sr.get("clip_vec") or [])
                if content:
                    sr["content"] = content
                    sr["labels"] = labels
                    sr["confidence"] = conf
                    labels_used = True
        speech = _asr(src)
        silent = media.silencedetect(src, min_duration=0.8) if info.get("has_audio") else []
        faces = _faces(src, frames_dir)
        summary = None
        tag_pool = [t for s in scene_results for t in (s.get("subjects") or [])]
        tag_pool += [l["name"] for s in scene_results for l in (s.get("labels") or [])]
        tags = sorted(set(tag_pool))[:10]
        # 摘要优先用画面语义（VLM 场景描述 / CLIP 标注），转写只作兜底，避免摘要变成台词
        if scene_results:
            summary = "；".join([s.get("content", "") for s in scene_results if s.get("content")][:4])[:160] or None
        elif speech:
            summary = " ".join(s["text"] for s in speech[:3])[:160] or None
        analysis = {
            "schema_version": 1,
            "file": rel,
            "file_hash": f"sha256:{key}",
            "duration_sec": info.get("duration_sec"),
            "resolution": info.get("resolution"),
            "fps": info.get("fps"),
            "has_audio": bool(info.get("has_audio")),
            "language": None,
            "summary": summary,
            "tags": tags,
            "scenes": scene_results,
            "faces": faces,
            "speech": speech,
            "silent_segments": silent,
            "audio_stats": None,
            "analysis_meta": {
                "vlm": VLM_MODEL if frames_per_scene else "",
                "labeler": "chinese-clip-zeroshot" if labels_used else "",
                "asr": ASR_MODEL,
                "scene_detector": "hsv+edge",
                "analyzed_at": util.now_iso(),
                "analysis_dir": str(frames_dir),
            },
        }

    util.save_json(analysis_path, analysis)
    _update_manifest(src, analysis)
    return analysis


def _update_manifest(src: Path, analysis: dict):
    # manifest.json 写到素材所在目录（目录级索引）
    manifest_path = _dir_manifest_path(src)
    folder_rel = src.resolve().parent.relative_to(MATERIALS.resolve()).as_posix() or "."
    manifest = util.load_json(
        manifest_path,
        {"schema_version": 1, "folder": folder_rel, "scanned_at": util.now_iso(), "video_count": 0, "total_duration_sec": 0, "videos": []},
    )
    manifest["scanned_at"] = util.now_iso()
    rel = analysis.get("file") or src.name  # 相对路径（含子目录）
    videos = [v for v in manifest.get("videos", []) if v.get("file") != rel]
    dur = analysis.get("duration_sec") or 0
    speech_ratio = sum(s["end"] - s["start"] for s in (analysis.get("speech") or []) if s.get("end")) / dur if dur else 0
    silent_ratio = (
        sum(s.get("end", 0) - s["start"] for s in (analysis.get("silent_segments") or []) if s.get("end")) / dur if dur else 0
    )
    videos.append(
        {
            "file": rel,
            "duration_sec": dur,
            "resolution": analysis.get("resolution"),
            "has_audio": analysis.get("has_audio"),
            "language": analysis.get("language"),
            "summary": analysis.get("summary"),
            "tags": analysis.get("tags") or [],
            "scene_count": len(analysis.get("scenes") or []),
            "person_count": len({f.get("face_id") for f in (analysis.get("faces") or [])}),
            "speech_ratio": round(speech_ratio, 3),
            "silent_ratio": round(silent_ratio, 3),
            "analysis_dir": (analysis.get("analysis_meta") or {}).get("analysis_dir"),
        }
    )
    manifest["videos"] = videos
    manifest["video_count"] = len(videos)
    manifest["total_duration_sec"] = round(sum(v.get("duration_sec") or 0 for v in videos), 3)
    util.save_json(manifest_path, manifest)


def _iter_analysis():
    """遍历所有素材目录 _analysis/ 下的分析文件（xxx.mp4.analysis.json）。"""
    if not MATERIALS.exists():
        return
    for p in sorted(MATERIALS.rglob(f"{ANALYSIS_DIRNAME}/*.analysis.json")):
        a = util.load_json(p)
        if a:
            yield p, a


def _find_analysis(name: str) -> dict | None:
    src = (MATERIALS / name).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return None
    analysis_path, _ = _analysis_paths(src)
    return util.load_json(analysis_path)


# ---------- 接口 ----------

@app.get("/health")
def health():
    return {
        "ok": True,
        "vlm": VLM_MODEL,
        "vlm_enabled": VLM_ENABLED,
        "vlm_active": _vlm_active(),
        "face_enabled": FACE_ENABLED,
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    return analyze_one(req.file, req.force)


def _transcribe_only(file: str, force: bool = False) -> dict:
    """只跑语音转写（本地字幕模型），不跑画面理解——字幕剪辑的最小依赖。

    已有分析文档则只更新其 speech 字段；没有则生成一份只含字幕字段的精简文档。
    """
    src = (MATERIALS / file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"素材不存在: {file}")
    analysis_path, frames_dir = _analysis_paths(src)
    old = util.load_json(analysis_path) if analysis_path.exists() else None
    if old and old.get("speech") and not force:
        _update_manifest(src, old)  # 只提取字幕也要标记"已分析"，否则前端不让人勾选
        return {"file": file, "speech": old["speech"], "duration_sec": old.get("duration_sec"), "cached": True}
    info = media.probe(src)
    speech = _asr(src)
    if old:
        # 重跑转写会换新台词行：按时间重叠继承旧的说话人标注，避免重提取丢speaker
        old_speech = old.get("speech") or []
        for sp in speech:
            best, best_ov = None, 0.0
            for osp in old_speech:
                ov = min(float(sp["end"]), float(osp.get("end") or 0)) - max(float(sp["start"]), float(osp.get("start") or 0))
                if ov > best_ov:
                    best_ov, best = ov, osp
            if best is not None and best.get("speaker") and best_ov > 0.5 * max(0.1, float(sp["end"]) - float(sp["start"])):
                sp["speaker"] = best.get("speaker")
        old["speech"] = speech
        old.setdefault("analysis_meta", {})["asr_at"] = util.now_iso()
        util.save_json(analysis_path, old)
        a = old
    else:
        a = {
            "schema_version": 1,
            "file": src.relative_to(MATERIALS.resolve()).as_posix(),
            "duration_sec": info.get("duration_sec"),
            "resolution": info.get("resolution"),
            "fps": info.get("fps"),
            "has_audio": bool(info.get("has_audio")),
            "language": None,
            "summary": None,
            "tags": [],
            "scenes": [],
            "faces": [],
            "speech": speech,
            "silent_segments": [],
            "audio_stats": None,
            "analysis_meta": {"asr": ASR_MODEL, "mode": "asr-only", "analyzed_at": util.now_iso(),
                              "analysis_dir": str(frames_dir)},
        }
        util.save_json(analysis_path, a)
    _update_manifest(src, a)
    return {"file": file, "speech": speech, "duration_sec": a.get("duration_sec"), "cached": False}


@app.post("/transcribe")
def transcribe(req: AnalyzeRequest):
    """提取字幕：只跑本地语音转写，返回带时间戳的台词列表。"""
    return _transcribe_only(req.file, req.force)


@app.post("/scan")
def scan(req: ScanRequest):
    """启动异步扫描分析：立即返回 scan_id，进度通过 /scan_status 轮询。"""
    base = (MATERIALS / req.folder).resolve()
    if not base.exists() or not base.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"目录不存在: {req.folder}")
    exts = IMAGE_EXTS | VIDEO_EXTS

    def _is_derived(p: Path) -> bool:
        # 排除分析派生产物：_analysis/ 目录（含 xxx_frames/、xxx.analysis.json、manifest.json）
        for parent in p.parents:
            if parent.name == ANALYSIS_DIRNAME or parent.name.endswith("_frames"):
                return True
        return p.name.endswith(".analysis.json")

    files = sorted(p for p in base.rglob("*") if p.suffix.lower() in exts and not _is_derived(p))
    scan_id = f"scan-{int(time.time())}"
    state = _SCANS[scan_id] = {
        "folder": req.folder,
        "files": [p.relative_to(MATERIALS).as_posix() for p in files],
        "total": len(files),
        "done": 0,
        "ok": 0,
        "failed": 0,
        "current": "",
        "results": [],
        "finished": False,
        "error": None,
    }

    def _worker():
        try:
            for rel in list(state["files"]):
                state["current"] = rel
                try:
                    if req.mode == "asr":
                        a = _transcribe_only(rel, req.force)
                        state["results"].append({"file": rel, "ok": True, "scenes": 0, "lines": len(a.get("speech") or [])})
                    else:
                        a = analyze_one(rel, req.force)
                        state["results"].append({"file": rel, "ok": True, "scenes": len(a.get("scenes") or [])})
                    state["ok"] += 1
                except Exception as exc:
                    state["results"].append({"file": rel, "ok": False, "error": str(exc)})
                    state["failed"] += 1
                state["done"] += 1
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            state["finished"] = True
            state["current"] = ""

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"scan_id": scan_id, "total": len(files), "started": True}


_SCANS: dict[str, dict] = {}


@app.get("/scan_status")
def scan_status(scan_id: str):
    """轮询扫描进度。"""
    st = _SCANS.get(scan_id)
    if not st:
        raise HTTPException(404, f"扫描任务不存在: {scan_id}")
    return {
        "scan_id": scan_id,
        "total": st["total"],
        "done": st["done"],
        "ok": st["ok"],
        "failed": st["failed"],
        "current": st["current"],
        "finished": st["finished"],
        "error": st["error"],
        "results": st["results"] if st["finished"] else [],
    }


@app.get("/status")
def status():
    analyzed = sum(1 for _ in _iter_analysis())
    return {"analyzed": analyzed, "materials_dir": str(MATERIALS)}


@app.get("/material_duration")
def material_duration(file: str):
    """查询素材时长（优先读分析文件，其次 ffprobe）。"""
    src = (MATERIALS / file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"素材不存在: {file}")
    a = _find_analysis(file)
    dur = (a or {}).get("duration_sec")
    if not dur:
        info = media.probe(src)
        dur = info.get("duration_sec")
    return {"file": file, "duration_sec": dur}


@app.post("/query_segment")
def query_segment(req: QuerySegmentRequest):
    """精读：指定时间段现场抽帧 + VLM 描述 + 该段转写。"""
    src = (MATERIALS / req.file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"素材不存在: {req.file}")
    frames = _extract_frames_range(src, req.start, req.end, req.num_frames)
    semantic = _vlm_describe(frames) if frames else None
    analysis = _find_analysis(req.file)
    speech = [
        s for s in (analysis or {}).get("speech") or []
        if s.get("start", -1) >= req.start - 0.1 and s.get("end", 1e9) <= req.end + 0.1
    ]
    return {"file": req.file, "start": req.start, "end": req.end, "frames": len(frames), "semantic": semantic, "speech": speech}


@app.post("/search")
def search(req: SearchRequest):
    """语义/关键词检索（CLIP 向量在 P1b 增强，当前按文本匹配）。"""
    q = req.query.lower()
    hits = []
    for p, a in _iter_analysis():
        hay = " ".join(
            [
                a.get("summary") or "",
                " ".join(a.get("tags") or []),
                " ".join(s.get("content", "") for s in a.get("scenes") or []),
                " ".join(s.get("text", "") for s in a.get("speech") or []),
            ]
        ).lower()
        score = hay.count(q)
        if score:
            hits.append({"file": a.get("file"), "score": score, "summary": a.get("summary"), "analysis_dir": (a.get("analysis_meta") or {}).get("analysis_dir")})
    hits.sort(key=lambda x: -x["score"])
    return {"query": req.query, "hits": hits[: req.top_k]}
