# -*- coding: utf-8 -*-
"""补齐缺口：重新提取继承说话人 + /api/folder_lines 全目录台词接口。"""
from pathlib import Path

APP = Path("services/planner/app.py")
t = APP.read_text(encoding="utf-8")

# ============ 1) _transcribe_only：force 重跑时按时间重叠继承旧说话人 ============
old = '''    info = media.probe(src)
    speech = _asr(src)
    if old:
        old["speech"] = speech
        old.setdefault("analysis_meta", {})["asr_at"] = util.now_iso()
        util.save_json(analysis_path, old)
        a = old'''
new = '''    info = media.probe(src)
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
        a = old'''
assert old in t, "transcribe inherit"
t = t.replace(old, new)

# ============ 2) 台词读取抽公共函数 + 新增 /api/folder_lines ============
old = '''@app.post("/api/export_subtitles_json")
def api_export_subtitles_json(req: ExportSubtitlesRequest):
    """导出目录级台词 JSON：所有已提取字幕的视频的对白+时间点，合成一个文件（喂给剧本剪辑用）。"""
    base = (MATERIALS / req.folder).resolve() if req.folder and req.folder != "." else MATERIALS.resolve()
    if not base.exists() or not base.is_relative_to(MATERIALS.resolve()):
        return {"error": f"目录不存在: {req.folder}"}
    videos = []
    for md in sorted(base.rglob("_analysis")):
        if not md.is_dir():
            continue
        for jf in sorted(md.glob("*.analysis.json")):
            doc = util.load_json(jf)
            if not doc or not (doc.get("speech") or []):
                continue
            videos.append({
                "file": doc.get("file") or jf.name.replace(".analysis.json", ""),
                "duration_sec": doc.get("duration_sec"),
                "lines": [{"start": sp.get("start"), "end": sp.get("end"),
                           "text": sp.get("text"), "speaker": sp.get("speaker")}
                          for sp in doc["speech"]],
            })
    if not videos:
        return {"error": "该目录（含子目录）还没有任何台词——先提取字幕"}
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)'''
new = '''def _folder_speech_videos(folder: str) -> tuple[list[dict], Path | None]:
    """收集目录（含子目录）下所有已提取字幕素材的台词。返回 (videos, 错误)。"""
    base = (MATERIALS / folder).resolve() if folder and folder != "." else MATERIALS.resolve()
    if not base.exists() or not base.is_relative_to(MATERIALS.resolve()):
        return [], f"目录不存在: {folder}"
    videos = []
    for md in sorted(base.rglob("_analysis")):
        if not md.is_dir():
            continue
        for jf in sorted(md.glob("*.analysis.json")):
            doc = util.load_json(jf)
            if not doc or not (doc.get("speech") or []):
                continue
            videos.append({
                "file": doc.get("file") or jf.name.replace(".analysis.json", ""),
                "duration_sec": doc.get("duration_sec"),
                "lines": [{"start": sp.get("start"), "end": sp.get("end"),
                           "text": sp.get("text"), "speaker": sp.get("speaker")}
                          for sp in doc["speech"]],
            })
    return videos, None


class FolderLinesRequest(BaseModel):
    folder: str = "."


@app.post("/api/folder_lines")
def api_folder_lines(req: FolderLinesRequest):
    """全目录台词池（跨视频剧本匹配用）：所有已提取字幕素材的对白，统一编号返回。"""
    videos, err = _folder_speech_videos(req.folder)
    if err:
        return {"error": err}
    lines = []
    for v in videos:
        for sp in v["lines"]:
            lines.append({"i": len(lines), "file": v["file"], "start": sp["start"],
                          "end": sp["end"], "text": sp["text"], "speaker": sp.get("speaker")})
    return {"video_count": len(videos), "lines": lines}


@app.post("/api/export_subtitles_json")
def api_export_subtitles_json(req: ExportSubtitlesRequest):
    """导出目录级台词 JSON：所有已提取字幕的视频的对白+时间点，合成一个文件（喂给剧本剪辑用）。"""
    videos, err = _folder_speech_videos(req.folder)
    if err:
        return {"error": err}
    if not videos:
        return {"error": "该目录（含子目录）还没有任何台词——先提取字幕"}
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)'''
assert old in t, "export refactor"
t = t.replace(old, new)

APP.write_text(t, encoding="utf-8")
print("planner ok")
