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
NUM_CTX = int(os.environ.get("NUM_CTX", "8192"))
ASR_MODEL = os.environ.get("ASR_MODEL", "faster-whisper-small")
WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "")
# 转写 CPU 线程上限：限制为 4（8G 电脑/笔记本安全值），防止吃满所有核导致过热
ASR_CPU_THREADS = int(os.environ.get("ASR_CPU_THREADS", "4"))
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


class QuerySegmentRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float = 10.0
    num_frames: int = 4


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


# ---------- 工具函数 ----------

def _vlm_describe(images: list) -> dict | None:
    """调 Ollama（OpenAI 兼容接口）分析一组帧，返回语义 JSON。

    每帧单独编码进 messages（多图）；实测 3B 模型 1~2 帧多图识别人物最稳，
    3 帧多图/拼图会泛化成"角色A/皇帝大臣"式抽象标签，故保持 2 帧上限。
    """
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
        resp = httpx.post(f"{VLM_BASE_URL}/chat/completions", json=payload, timeout=180)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return util.parse_llm_json(content)
    except Exception as exc:
        print(f"[vlm] 失败: {exc}")
        return None


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


def _find_cuts(path: Path) -> tuple[float, list[tuple[float, float]]]:
    """检测所有镜头切点：返回 (时长, [(切点秒, 差异分数)...])。"""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return 0.0, []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps if total else 0.0
    stride = max(1, int(fps * 0.5))
    prev_hist, prev_edge = None, None
    cuts: list[tuple[float, float]] = []
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
                # 阈值放宽：旧剧/低对比度视频转场柔和，0.35/0.12 会漏切成长镜头
                if hist_diff > 0.25 or edge_diff > 0.08:
                    t = idx / fps
                    if not cuts or t - cuts[-1][0] > 0.8:
                        cuts.append((t, max(hist_diff, edge_diff * 3)))
            prev_hist, prev_edge = hist, edge
        idx += 1
    cap.release()
    return duration, cuts


def _detect_cuts(path: Path, min_sec: float = 1.5, max_sec: float = 15.0) -> list[dict]:
    """细镜头切分：返回所有镜头边界（最小 1.5s，最长 15s），不合并——供场记精确时间戳。

    2 分钟视频能得到几十个镜头级分段，而不是几个大段。
    """
    duration, cuts = _find_cuts(path)
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
    return final


def _detect_scenes(path: Path, max_scenes: int | None = None) -> list[dict]:
    """场景切分（合并版）：供 VLM 描述分组，数量受 max_scenes 控制。"""
    max_scenes = max_scenes or MAX_SCENES
    duration, cuts = _find_cuts(path)
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


def _asr(path: Path) -> list[dict]:
    global _whisper
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        print(f"[asr] faster-whisper 未安装: {exc}")
        return []
    if _whisper is None:
        try:
            local = Path(WHISPER_MODEL_DIR) if WHISPER_MODEL_DIR else None
            if local and local.exists() and any(local.iterdir()):
                model_path = str(local)
            else:
                model_path = ASR_MODEL
            # cpu_threads 限制转写占用的核数（默认 4），防止 CPU 满载过热
            _whisper = WhisperModel(model_path, device="auto", compute_type="int8", cpu_threads=ASR_CPU_THREADS)
        except Exception as exc:
            print(f"[asr] 模型加载失败（离线需预缓存）: {exc}")
            return []
    try:
        segments, _ = _whisper.transcribe(str(path), vad_filter=True)
        return [
            {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "speaker": None}
            for s in segments
            if s.text and s.text.strip()
        ]
    except Exception as exc:
        print(f"[asr] 转写失败: {exc}")
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
        frames_per_scene = MAX_FRAMES_PER_SCENE
        # 两段式：
        # ① 细镜头（_detect_cuts）：所有镜头边界（1.5~15s），场记时间戳精确到镜头
        # ② 描述组（_detect_scenes 合并版）：VLM 只描述合并组，控制调用次数
        fine_scenes = _detect_cuts(src)
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
        frames = _extract_frames(src, desc_scenes, frames_dir, frames_per_scene=frames_per_scene)
        keyframes = [{"scene_index": f["scene_index"], "t": f["t"], "path": f["path"]} for f in frames]
        # 描述组 VLM
        desc_results = []
        for scene in desc_scenes:
            imgs = [f["image"] for f in frames if f["scene_index"] == scene["index"]]
            semantic = _norm_semantic(_vlm_describe(imgs)) if imgs else {}
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
            scene_results.append(
                {
                    "start": round(fs_start, 1),
                    "end": round(fs_end, 1),
                    "content": desc.get("content", ""),
                    "subjects": desc.get("subjects") or [],
                    "type": desc.get("type", ""),
                    "camera": desc.get("camera", ""),
                    "lighting": desc.get("lighting", ""),
                    "energy": desc.get("energy", ""),
                    "editing_use": desc.get("editing_use", ""),
                    "confidence": desc.get("confidence", 0.0),
                    "keyframes": [fine_thumbs[fi]] if fi < len(fine_thumbs) else (desc.get("keyframes") or []),
                    "clip_vec": (fine_vecs[fi] if fi < len(fine_vecs) else []),  # 画面向量（CLIP 512d）
                }
            )
        speech = _asr(src)
        silent = media.silencedetect(src, min_duration=0.8) if info.get("has_audio") else []
        faces = _faces(src, frames_dir)
        summary = None
        tags = sorted({t for s in scene_results for t in (s.get("subjects") or [])})[:10]
        # 摘要优先用画面语义（VLM 场景描述），转写只作兜底，避免摘要变成台词
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
                "vlm": VLM_MODEL,
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
    return {"ok": True, "vlm": VLM_MODEL, "face_enabled": FACE_ENABLED}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    return analyze_one(req.file, req.force)


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
                    a = analyze_one(rel)
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
