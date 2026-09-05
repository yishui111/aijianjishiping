"""L3 执行服务：EDL → ffmpeg 出片（dry-run 预览 + 审计）。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import media, util  # noqa: E402

MATERIALS = Path(os.environ.get("MATERIALS_DIR", "/materials"))
ANALYSIS = Path(os.environ.get("ANALYSIS_DIR", "/analysis"))
OUTPUT = Path(os.environ.get("OUTPUT_DIR", "/output"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
CATALOG = util.load_json(CONFIG_DIR / "operation-catalog.json") if (CONFIG_DIR / "operation-catalog.json").exists() else {"operations": []}
NORMALIZE_W = int(os.environ.get("NORMALIZE_W", "1280"))
NORMALIZE_H = int(os.environ.get("NORMALIZE_H", "720"))
NORMALIZE_FPS = int(os.environ.get("NORMALIZE_FPS", "30"))

app = FastAPI(title="AI Video Studio Executor")


class EdlRequest(BaseModel):
    edl: dict


class MergeRequest(BaseModel):
    files: list[str]  # 容器内成片路径（/output/job-x/xxx.mp4）


class CutCopyRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float = 0.0  # <=0 表示到结尾
    out_name: str = "cut.mp4"


@app.post("/api/cut_copy")
def cut_copy(req: CutCopyRequest):
    """无损剪切（stream copy，不重新编码）：适合去片头片尾等纯时间裁剪，秒级完成。

    大视频（40 分钟/1GB+）用 EDL+转码会非常慢；这里直接切流，几秒出片。
    """
    src = (MATERIALS / req.file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"素材不存在: {req.file}")
    out_dir = OUTPUT / f"job-{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / (req.out_name or "cut.mp4")
    args = [media.ffmpeg(), "-y"]
    if req.start and req.start > 0:
        args += ["-ss", f"{req.start}"]  # -ss 在 -i 前 = fast seek（关键帧对齐）
    args += ["-i", str(src)]
    if req.end and req.end > 0 and req.end > req.start:
        args += ["-t", f"{req.end - req.start}"]
    args += ["-c", "copy", "-avoid_negative_ts", "make_zero", str(dst)]
    media.run(args)
    return {"job": out_dir.name, "output": dst.as_posix()}


# ---------- 剪辑操作 ----------

_COPY_VERIFY_TOLERANCE = 0.25  # 流切时长与请求时长的最大允许偏差（秒）
_COPY_VERIFY_MAX_DUR = 180.0   # 超长剪切不回读校验（重编码代价太大），接受关键帧级精度


def _trim(src: Path, dst: Path, start: float, end: float):
    """剪切 [start, end)：先试无损流切，切点因关键帧稀疏漂移时自动改帧级精准重编码。

    背景：-c copy 只能从关键帧开始切。老剧/低码率转码源（如 AV1/HEVC 老片）关键帧
    间隔可达 5~10 秒，剪 1.5 秒实际得 5 秒，相邻片段还会内容重复。因此流切后回读
    成品时长校验：偏差超过阈值就重切（-ss 在 -i 前配转码 = 帧级精准）。
    """
    dur = max(0.1, end - start)
    args = [media.ffmpeg(), "-y"]
    if start and start > 0:
        args += ["-ss", f"{start}"]  # fast seek（关键帧对齐）
    args += ["-i", str(src), "-t", f"{dur}", "-c", "copy", "-avoid_negative_ts", "make_zero", str(dst)]
    media.run(args)
    if dur > _COPY_VERIFY_MAX_DUR:
        return
    try:
        got = media.probe(dst).get("duration_sec") or 0
    except Exception:
        return
    if abs(got - dur) <= _COPY_VERIFY_TOLERANCE:
        return
    # 关键帧稀疏导致漂移：就近解码后精准重编码（同分辨率，无 pad/缩放）
    media.run(
        [
            media.ffmpeg(), "-y", "-ss", f"{start}", "-i", str(src), "-t", f"{dur}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-ar", "44100",
            "-avoid_negative_ts", "make_zero", str(dst),
        ]
    )


def _speed(src: Path, dst: Path, factor: float):
    if not (0.25 <= factor <= 4.0):
        raise ValueError("factor 需在 0.25–4.0")
    media.run(
        [
            media.ffmpeg(), "-y", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a:0?",
            "-filter:v", f"setpts={1 / factor}*PTS",
            "-filter:a", f"atempo={factor}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-ar", "44100",
            str(dst),
        ]
    )


def _normalize(src: Path, dst: Path):
    media.run(
        [
            media.ffmpeg(), "-y", "-i", str(src),
            "-vf",
            f"scale={NORMALIZE_W}:{NORMALIZE_H}:force_original_aspect_ratio=decrease,"
            f"pad={NORMALIZE_W}:{NORMALIZE_H}:(ow-iw)/2:(oh-ih)/2,fps={NORMALIZE_FPS}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            str(dst),
        ]
    )


def _same_spec(paths: list[Path]) -> bool:
    """所有输入是否同编码/同分辨率/同帧率（同规格才能无损拼接）。"""
    specs = set()
    for p in paths:
        info = media.probe(p)
        specs.add((info.get("video_codec"), info.get("resolution"), info.get("fps"), bool(info.get("has_audio"))))
    return len(specs) <= 1


def _concat(files: list[Path], dst: Path, work: Path):
    if len(files) == 1:
        shutil.copy(files[0], dst)
        return
    if _same_spec(files):
        # 同规格：concat demuxer + copy，无损秒级拼接
        lst = work / "concat.txt"
        lst.write_text("\n".join(f"file '{f.as_posix()}'" for f in files), encoding="utf-8")
        media.run(
            [
                media.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                "-c", "copy", "-avoid_negative_ts", "make_zero", str(dst),
            ]
        )
        return
    # 不同规格：归一化后拼接（转码，仅必要场景）
    norm = []
    for i, f in enumerate(files):
        nf = work / f"norm_{i}.mp4"
        _normalize(f, nf)
        norm.append(nf)
    lst = work / "concat.txt"
    lst.write_text("\n".join(f"file '{nf.as_posix()}'" for nf in norm), encoding="utf-8")
    media.run(
        [
            media.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-ar", "44100",
            str(dst),
        ]
    )


def _remove_silence(src: Path, dst: Path, work: Path, threshold_db: float = -35, margin_sec: float = 0.3):
    if not media.probe(src).get("has_audio"):
        shutil.copy(src, dst)
        return
    segs = media.silencedetect(src, threshold_db=threshold_db)
    dur = media.probe(src).get("duration_sec") or 0
    cursor = 0.0
    loud = []
    for s in segs:
        start = s["start"]
        end = s.get("end") or dur
        if start > cursor + 0.1:
            loud.append((max(0.0, cursor - margin_sec), min(start + margin_sec, dur)))
        cursor = max(cursor, end)
    if cursor < dur - 0.1:
        loud.append((max(0.0, cursor - margin_sec), dur))
    if not loud:
        shutil.copy(src, dst)
        return
    parts = []
    for i, (a, b) in enumerate(loud):
        p = work / f"part_{i}.mp4"
        _trim(src, p, a, b)
        parts.append(p)
    _concat(parts, dst, work)


def _export(src: Path, dst: Path, resolution=None, crf: int = 20):
    res = str(resolution or "")
    if not res or res == "原样":
        # 原样输出：无损复制（不转码）
        shutil.copy(src, dst)
        return
    info = media.probe(src)
    if info.get("resolution") == res:
        # 目标分辨率与源一致：也无损复制
        shutil.copy(src, dst)
        return
    # 只有明确改分辨率才转码
    args = [media.ffmpeg(), "-y", "-i", str(src)]
    res_map = {"1080p": "1920x1080", "720p": "1280x720", "4k": "3840x2160"}
    if res in res_map:
        res = res_map[res]
    if "x" in res:
        w, h = res.split("x")
        args += ["-vf", f"scale={w}:{h}"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-c:a", "aac", str(dst)]
    media.run(args)


# ---------- 计划处理 ----------

def _order_plan(plan: list[dict]) -> list[dict]:
    rank = {"trim": 0, "remove_silence": 0, "speed": 0, "normalize": 1, "concat": 2, "export": 3}
    return sorted(plan, key=lambda x: rank.get(x.get("op"), 0))


def _resolve(src: str, registry: dict, work: Path) -> Path:
    if src in registry:
        return registry[src]
    base = src.split("#", 1)[0]  # 兼容 "file#1" 形式的派生 key
    p = (MATERIALS / base).resolve()
    if not p.exists() or not p.is_relative_to(MATERIALS.resolve()):
        raise HTTPException(404, f"素材不存在: {src}")
    return p


def _registry_key(file: str, registry: dict) -> str:
    """同素材多段裁剪时生成不冲突的派生 key：file、file#1、file#2..."""
    if file not in registry:
        return file
    n = 1
    while f"{file}#{n}" in registry:
        n += 1
    return f"{file}#{n}"


def _find_keyframes(file: str, start: float, end: float) -> list[str]:
    """从素材目录 _analysis/xxx.analysis.json 找该时间范围内的关键帧，用于预览。"""
    src = (MATERIALS / file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return []
    analysis_path = src.parent / "_analysis" / (src.name + ".analysis.json")
    a = util.load_json(analysis_path)
    if not a:
        return []
    hits = []
    for s in a.get("scenes") or []:
        if s.get("end") is None:
            continue
        if s["end"] >= start and s["start"] <= end:
            hits.extend(s.get("keyframes") or [])
    return hits[:6]


def _describe(op: dict, plan: list[dict]) -> dict:
    oid = op.get("op")
    if oid == "trim":
        return {"text": f"裁剪 {op['file']} {op['start']}–{op['end']} 秒", "est_sec": max(0.0, op["end"] - op["start"]), "keyframes": _find_keyframes(op["file"], op["start"], op["end"])}
    if oid == "remove_silence":
        return {"text": f"去除 {op['file']} 静音（阈值 {op.get('threshold_db', -35)}dB，边距 {op.get('margin_sec', 0.3)}s）", "est_sec": None, "keyframes": []}
    if oid == "speed":
        return {"text": f"变速 {op['file']} ×{op['factor']}", "est_sec": None, "keyframes": []}
    if oid == "concat":
        return {"text": f"拼接 {len(op.get('files', []))} 段（自动归一化 {NORMALIZE_W}x{NORMALIZE_H}@{NORMALIZE_FPS}fps）", "est_sec": None, "keyframes": []}
    if oid == "export":
        return {"text": f"导出 {op.get('format', 'mp4')} {op.get('resolution', '原样')} crf={op.get('crf', 20)}", "est_sec": None, "keyframes": []}
    return {"text": f"操作 {oid}", "est_sec": None, "keyframes": []}


# ---------- 接口 ----------

@app.get("/health")
def health():
    return {"ok": True}


@app.get("/operations")
def operations():
    return CATALOG


@app.get("/output/{job}/{name}")
def output_file(job: str, name: str):
    p = (OUTPUT / job / name).resolve()
    if not p.exists() or not p.is_relative_to(OUTPUT.resolve()):
        raise HTTPException(404, "成片不存在")
    return FileResponse(p, media_type="video/mp4")


@app.post("/merge_files")
def merge_files(req: MergeRequest):
    """把多个已出片的片段按顺序合并成一个成片（自动归一化）。"""
    if not req.files or len(req.files) < 2:
        raise HTTPException(400, "至少需要 2 个片段文件")
    out_dir = OUTPUT / f"job-{int(time.time())}"
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, f in enumerate(req.files):
        p = Path(f).resolve()
        if not p.exists() or not p.is_relative_to(OUTPUT.resolve()):
            raise HTTPException(404, f"片段文件不存在: {f}")
        nf = work / f"norm_{i:03d}.mp4"
        _normalize(p, nf)
        parts.append(nf)
    lst = work / "concat.txt"
    lst.write_text("\n".join(f"file '{nf.as_posix()}'" for nf in parts), encoding="utf-8")
    name = f"merged_{int(time.time())}.mp4"
    dst = out_dir / name
    media.run(
        [
            media.ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-ar", "44100",
            str(dst),
        ]
    )
    return {"job": out_dir.name, "output": dst.as_posix(), "parts": len(parts)}


@app.post("/preview")
def preview(req: EdlRequest):
    ok, errors = util.validate_edl(req.edl, CATALOG)
    if not ok:
        raise HTTPException(400, f"EDL 校验失败: {'; '.join(errors)}")
    plan = _order_plan(req.edl.get("plan", []))
    lines = []
    total = 0.0
    all_frames = []
    for op in plan:
        d = _describe(op, plan)
        lines.append(d["text"])
        if d.get("est_sec"):
            total += d["est_sec"]
        all_frames.extend(d.get("keyframes") or [])
    return {
        "request_id": req.edl.get("request_id", "unknown"),
        "plan": lines,
        "estimated_sec": round(total, 2) if total else None,
        "keyframes": all_frames[:20],
        "confirm_required": True,
    }


@app.post("/execute")
def execute(req: EdlRequest):
    ok, errors = util.validate_edl(req.edl, CATALOG)
    if not ok:
        raise HTTPException(400, f"EDL 校验失败: {'; '.join(errors)}")
    job = f"job-{int(time.time())}"
    out_dir = OUTPUT / job
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    registry = {}
    audit = {"request_id": req.edl.get("request_id"), "started_at": util.now_iso(), "steps": []}
    final = None
    try:
        for idx, op in enumerate(_order_plan(req.edl.get("plan", []))):
            oid = op.get("op")
            step = {"op": oid, "params": op, "command": None, "ok": False}
            if oid == "trim":
                # 输入始终用原始素材：同素材多段从原片不同区间裁剪，避免链式裁剪偏差
                raw = (MATERIALS / op["file"]).resolve()
                if not raw.exists() or not raw.is_relative_to(MATERIALS.resolve()):
                    raise HTTPException(404, f"素材不存在: {op['file']}")
                key = _registry_key(op["file"], registry)
                dst = work / f"f{idx:03d}.mp4"
                _trim(raw, dst, op["start"], op["end"])
                registry[key] = dst
                final = dst
                step["command"] = f"trim {raw.name} -> {dst.name}"
            elif oid == "remove_silence":
                src = _resolve(op["file"], registry, work)
                dst = work / f"f{idx:03d}.mp4"
                _remove_silence(src, dst, work, op.get("threshold_db", -35), op.get("margin_sec", 0.3))
                registry[op["file"]] = dst
                final = dst
                step["command"] = f"remove_silence {src.name} -> {dst.name}"
            elif oid == "speed":
                src = _resolve(op["file"], registry, work)
                dst = work / f"f{idx:03d}.mp4"
                _speed(src, dst, op["factor"])
                registry[op["file"]] = dst
                final = dst
                step["command"] = f"speed {src.name} x{op['factor']}"
            elif oid == "concat":
                files = [_resolve(f, registry, work) for f in op["files"]]
                dst = out_dir / f"concat_{idx:03d}.mp4"
                _concat(files, dst, work)
                final = dst
                step["command"] = f"concat {len(files)} 段"
            elif oid == "export":
                src = _resolve(op["file"], registry, work) if op.get("file") else final
                if src is None:
                    raise HTTPException(400, "export 之前没有可导出的中间产物")
                name = req.edl.get("request_id", "output") + ".mp4"
                dst = out_dir / name
                _export(src, dst, op.get("resolution"), op.get("crf", 20))
                final = dst
                step["command"] = f"export -> {name}"
            else:
                raise HTTPException(400, f"不支持的操作: {oid}")
            step["ok"] = True
            audit["steps"].append(step)
    except Exception as exc:
        audit["error"] = str(exc)
        audit["finished_at"] = util.now_iso()
        util.save_json(out_dir / "audit.json", audit)
        raise HTTPException(500, f"执行失败: {exc}")
    audit["output"] = final.as_posix() if final else None
    audit["finished_at"] = util.now_iso()
    util.save_json(out_dir / "audit.json", audit)
    return {"job": job, "output": final.as_posix() if final else None, "audit": (out_dir / "audit.json").as_posix(), "steps_ok": sum(1 for s in audit["steps"] if s["ok"])}
