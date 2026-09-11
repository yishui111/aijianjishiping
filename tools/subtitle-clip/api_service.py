# -*- coding: utf-8 -*-
"""② 分析服务：目录批量分析 → 台词阶层 JSON；关键词/剧本 匹配剪辑。

与 Gradio 工作台（61810）共用同一套本地语音模型与本 venv，端口 61812，完全离线。
JSON 阶层格式（每个视频一个，存在视频同目录，文件名 = 视频名 + .clip.json）：
{
  "video": "xxx.mp4", "duration_sec": 59.0,
  "lines": [ {"start": 0.1, "end": 3.2, "text": "...", "speaker": "女人"}, ... ]
}
"""
import difflib
import json
import os
import sys
import threading
import time
from pathlib import Path

# 离线 + 本地模型缓存（必须在 import funasr 前设置）
ROOT = Path(__file__).resolve().parent
os.environ["MODELSCOPE_CACHE"] = str(ROOT / "modelscope-cache")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
CODE_DIR = ROOT / "funclip"
sys.path.insert(0, str(CODE_DIR))

import socket
socket.setdefaulttimeout(15)   # 离线环境：模型库在线检查 15 秒内失败即回退本地缓存，避免永久挂起

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import moviepy.editor as mpy

MODEL_SPECS = {
    "model": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "vad_model": "damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "punc_model": "damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
    "spk_model": "damo/speech_campplus_sv_zh-cn_16k-common",
    "disable_update": True,
}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".m4v", ".ts"}
OUT_REGISTRY = ROOT / "outputs.json"

_lock = threading.Lock()
_clipper = None


def get_clipper():
    """懒加载本地语音模型（首次调用时载入，之后常驻）。"""
    global _clipper
    with _lock:
        if _clipper is None:
            from funasr import AutoModel
            from videoclipper import VideoClipper
            m = AutoModel(**MODEL_SPECS)
            c = VideoClipper(m)
            c.lang = "zh"
            _clipper = c
    return _clipper


app = FastAPI(title="AI 字幕剪辑 - 分析服务")

# ---------- 页面 ----------
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>分析服务 - 台词阶层 JSON</title>
<style>
 body{background:#0f1420;color:#e8ecf4;font-family:system-ui;margin:0;padding:24px;}
 .wrap{max-width:960px;margin:0 auto;}
 .card{background:#171e2e;border:1px solid #2c3a55;border-radius:12px;padding:18px;margin-bottom:16px;}
 h1{font-size:20px} h2{font-size:16px;margin:0 0 10px}
 .hint{color:#9fb3d1;font-size:12px}
 input{background:#1d2739;color:#e8ecf4;border:1px solid #2c3a55;border-radius:8px;padding:7px 10px;font-size:13px}
 button{background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
 button:disabled{opacity:.5}
 button.secondary{background:#1d2739;border:1px solid #2c3a55}
 textarea{width:100%;min-height:120px;background:#1d2739;color:#e8ecf4;border:1px solid #2c3a55;border-radius:8px;padding:10px;font-size:13px;box-sizing:border-box}
 .msg{background:#1d2739;border-radius:8px;padding:8px 12px;font-size:13px;margin:8px 0}
 .ok{border-left:3px solid #22c55e}.err{border-left:3px solid #ef4444}
 table{width:100%;border-collapse:collapse;font-size:13px}
 td,th{padding:6px 8px;border-bottom:1px solid #2c3a55;text-align:left}
 a{color:#86efac}
</style></head>
<body><div class="wrap">
 <h1>📊 分析服务 <span class="hint">目录视频 → 台词阶层 JSON（含说话人）</span></h1>
 <div class="card">
  <h2>① 目录分析</h2>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
   <input id="folder" placeholder="输入视频文件夹完整路径，如 D:\\素材\\西游记" style="flex:1;min-width:280px" value="__LAST__">
   <button id="btn-an" onclick="analyze()">🔍 分析该目录（提取全部台词）</button>
   <button class="secondary" onclick="listJsons()">📋 查看已生成的 JSON</button>
  </div>
  <div id="an-msg"></div>
  <div id="json-list"></div>
 </div>
 <div class="card">
  <h2>② 关键句裁剪</h2>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
   <input id="kw-folder" placeholder="视频所在的文件夹路径" style="flex:1;min-width:240px">
   <input id="kw" placeholder="关键词，空格分隔。如：孙悟空 金箍棒" style="flex:1;min-width:240px">
   <button id="btn-kw" onclick="kwCut()">✂️ 剪出包含关键词的句子</button>
  </div>
  <div class="hint">会扫描该目录下所有 *.clip.json，命中句子的区间自动剪出并拼接成一条成片。</div>
  <div id="kw-msg"></div>
 </div>
 <div class="card">
  <h2>③ 剧本匹配剪辑</h2>
  <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
   <input id="sc-folder" placeholder="视频所在的文件夹路径" style="flex:1;min-width:240px">
  </div>
  <textarea id="sc" placeholder="剧本逐行写，每行一句：&#10;你当初是不是看到我爸的钱了&#10;他没有人家有钱来的"></textarea>
  <div style="margin:10px 0"><button id="btn-sc" onclick="scriptCut()">🎬 按剧本匹配并剪辑</button>
  <span class="hint">每一行剧本去台词 JSON 里做相近匹配，命中的区间全部剪出、按剧本顺序拼接。</span></div>
  <div id="sc-msg"></div>
 </div>
 <div class="card">
  <h2>④ 成片下载</h2>
  <div id="outs">点「刷新列表」查看已生成的成片</div>
  <button class="secondary" onclick="loadOuts()">🔄 刷新列表</button>
 </div>
</div>
<script>
function msg(id, html) { document.getElementById(id).innerHTML = html; }
const RE = /[\\\\/]/;
async function analyze() {
  const f = document.getElementById("folder").value.trim();
  if (!f) { alert("输入文件夹路径"); return; }
  localStorage.setItem("last_folder", f);
  const b = document.getElementById("btn-an"); b.disabled = true;
  msg("an-msg", '<div class="msg">⏳ 分析中（本地语音模型，速度约为视频时长的 0.05~1 倍），请勿关闭...</div>');
  try {
    const r = await fetch("/api/analyze_folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder: f }) });
    const d = await r.json();
    if (d.error) { msg("an-msg", '<div class="msg err">❌ ' + d.error + '</div>'); b.disabled = false; return; }
    const rows = d.results.map(x => `<tr><td>${x.video.replace(RE, "/").split("/").pop()}</td><td>${x.lines} 句</td><td>${x.cached ? "已有（跳过）" : "新分析"}</td></tr>`).join("");
    msg("an-msg", `<div class="msg ok">✅ 分析完成：${d.total} 个视频</div><table><tr><th>视频</th><th>台词</th><th>状态</th></tr>${rows}</table>`);
  } catch (e) { msg("an-msg", '<div class="msg err">❌ ' + e.message + '</div>'); }
  b.disabled = false;
}
async function listJsons() {
  const f = document.getElementById("folder").value.trim();
  if (!f) { alert("输入文件夹路径"); return; }
  const r = await fetch("/api/list_jsons", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder: f }) });
  const d = await r.json();
  if (d.error) { msg("json-list", '<div class="msg err">❌ ' + d.error + '</div>'); return; }
  msg("json-list", d.jsons.length ? '<div class="msg ok">已有台词 JSON：' + d.jsons.length + ' 个</div>' : '<div class="msg">还没有 JSON</div>');
}
async function kwCut() {
  const folder = document.getElementById("kw-folder").value.trim();
  const kw = document.getElementById("kw").value.trim();
  if (!folder || !kw) { alert("填文件夹路径和关键词"); return; }
  const b = document.getElementById("btn-kw"); b.disabled = true;
  msg("kw-msg", '<div class="msg">⏳ 匹配并剪辑中...</div>');
  try {
    const r = await fetch("/api/keyword_cut", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, keywords: kw }) });
    const d = await r.json();
    if (d.error) { msg("kw-msg", '<div class="msg err">❌ ' + d.error + '</div>'); b.disabled = false; return; }
    const rows = d.outputs.map(o => `<tr><td>${o.video.replace(RE, "/").split("/").pop()}</td><td><a href="/media?f=${encodeURIComponent(o.file)}&dl=1" download>⬇ 下载成片</a></td></tr>`).join("");
    msg("kw-msg", `<div class="msg ok">✅ 命中 ${d.matched} 句，剪出 ${d.outputs.length} 段</div><table>${rows}</table>`);
  } catch (e) { msg("kw-msg", '<div class="msg err">❌ ' + e.message + '</div>'); }
  b.disabled = false;
}
async function scriptCut() {
  const folder = document.getElementById("sc-folder").value.trim();
  const script = document.getElementById("sc").value.trim();
  if (!folder || !script) { alert("填文件夹路径和剧本"); return; }
  const b = document.getElementById("btn-sc"); b.disabled = true;
  msg("sc-msg", '<div class="msg">⏳ 剧本逐句匹配并剪辑中...</div>');
  try {
    const r = await fetch("/api/script_cut", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ folder, script }) });
    const d = await r.json();
    if (d.error) { msg("sc-msg", '<div class="msg err">❌ ' + d.error + '</div>'); b.disabled = false; return; }
    const rows = d.matches.map(m => `<tr><td>剧本第 ${m.line_no + 1} 句</td><td>命中 ${m.count} 句</td><td>${m.video.replace(RE, "/").split("/").pop()}</td></tr>`).join("");
    const files = d.outputs.map(o => `<a href="/media?f=${encodeURIComponent(o.file)}&dl=1" download>⬇ ${o.file.replace(RE, "/").split("/").pop()}</a>`).join(" ");
    msg("sc-msg", `<div class="msg ok">✅ 剧本匹配完成</div><table>${rows}</table><div class="msg">成片：${files}</div>`);
  } catch (e) { msg("sc-msg", '<div class="msg err">❌ ' + e.message + '</div>'); }
  b.disabled = false;
}
async function loadOuts() {
  const r = await fetch("/api/outputs");
  const d = await r.json();
  document.getElementById("outs").innerHTML = d.outputs.length
    ? d.outputs.map(o => `<a href="/media?f=${encodeURIComponent(o.file)}&dl=1" download>⬇ ${o.file.replace(RE, "/").split("/").pop()}</a>`).join("<br>")
    : "还没有成片";
}
loadOuts();
document.getElementById("folder").value = localStorage.getItem("last_folder") || "";
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE.replace("__LAST__", "")


@app.post("/api/analyze_folder")
def api_analyze_folder(body: dict):
    folder = str(body.get("folder") or "").strip()
    base = Path(folder) if folder else None
    if not base or not base.is_dir():
        return JSONResponse({"error": f"文件夹不存在: {folder}"}, status_code=400)
    videos = sorted(p for p in base.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        return JSONResponse({"error": "该目录没有视频文件"}, status_code=400)
    clipper = get_clipper()
    results = []
    for v in videos:
        out_json = v.with_suffix(".clip.json")
        n_lines = 0
        cached = False
        if out_json.exists():
            try:
                n_lines = len(json.loads(out_json.read_text(encoding="utf-8")).get("lines") or [])
                cached = True
                results.append({"video": v.name, "lines": n_lines, "cached": True})
                continue
            except Exception:
                pass
        res_text, res_srt, state = clipper.video_recog(str(v), sd_switch="yes", output_dir=str(v.parent))
        sents = state.get("sentences") or []
        sd = state.get("sd_sentences") or []
        lines = []
        for s in sents:
            # FunClip 句子的 start/end 单位是毫秒，统一转成秒存 JSON
            s_start = float(s.get("start") or 0) / 1000.0
            s_end = float(s.get("end") or 0) / 1000.0
            spk = None
            for sd_s in sd:
                sd_start = float(sd_s.get("start") or 0) / 1000.0
                if abs(sd_start - s_start) < 0.3:
                    spk = sd_s.get("speaker") or sd_s.get("spk")
                    break
            # text 兜底：FunASR 少数分支会把 text 给成 token 列表，
            # 必须拼成字符串再存，否则 JSON 里出现 "['王', '呃', ...]" 这种
            # Python 列表字面量，关键词/剧本匹配都会失效
            raw_text = s.get("text")
            if isinstance(raw_text, (list, tuple)):
                raw_text = "".join(str(x) for x in raw_text)
            lines.append({
                "start": round(s_start, 2),
                "end": round(s_end, 2),
                "text": str(raw_text or "").strip(),
                "speaker": spk,
                # 字级时间戳（毫秒对），供剪辑引擎精确切分
                "timestamp": s.get("timestamp") or [],
            })
        doc = {
            "video": v.name,
            "duration_sec": round(float(state["video"].duration or 0), 2),
            "lines": lines,
        }
        out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append({"video": v.name, "lines": len(lines), "cached": False})
    return {"total": len(results), "results": results}


@app.post("/api/list_jsons")
def api_list_jsons(body: dict):
    folder = str(body.get("folder") or "").strip()
    base = Path(folder) if folder else None
    if not base or not base.is_dir():
        return JSONResponse({"error": f"文件夹不存在: {folder}"}, status_code=400)
    js = sorted(base.glob("*.clip.json"))
    return {"jsons": [p.name for p in js]}


def _load_folder_videos(folder: str):
    """读取目录下全部 *.clip.json → [{video, path, vpath, lines}]（按台词时间排序）"""
    base = Path(folder)
    out = []
    for jf in sorted(base.glob("*.clip.json")):
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        vname = doc.get("video") or jf.name.replace(".clip.json", "")
        vpath = base / vname
        if not vpath.exists():
            continue
        out.append({"video": vname, "path": str(vpath),
                    "lines": doc.get("lines") or []})
    return out


def _make_state(vpath: str, lines: list):
    import moviepy.editor as mpy
    video = mpy.VideoFileClip(vpath)
    sents = []
    flat_ts = []
    for l in lines:
        ts = l.get("timestamp") or []
        # 兼容无字级时间戳的旧 JSON：按句长均匀生成
        if not ts:
            n = max(4, len(l["text"]))
            dur = (l["end"] - l["start"]) * 1000
            step = dur / n
            ts = [[int(round(i * step)), int(round((i + 1) * step))] for i in range(n)]
        sents.append({"text": l["text"], "timestamp": ts,
                      "start": l["start"], "end": l["end"]})
        flat_ts.extend(ts)
    return {"video_filename": vpath, "clip_video_file": vpath, "video": video,
            "recog_res_raw": "".join(l["text"] for l in lines),
            "timestamp": flat_ts,
            "sentences": sents}


def _run_clip_and_track(state, out_dir: str, ts_list_ms: list, registry: list):
    clip_file, message, clip_srt = get_clipper().video_clip(
        "", 0, 0, state, output_dir=out_dir, timestamp_list=ts_list_ms)
    registry.append({"file": clip_file})
    return clip_file


@app.post("/api/keyword_cut")
def api_keyword_cut(body: dict):
    folder = str(body.get("folder") or "").strip()
    kws = [k.strip() for k in str(body.get("keywords") or "").replace("，", " ").replace(",", " ").split() if k.strip()]
    if not kws:
        return JSONResponse({"error": "关键词为空"}, status_code=400)
    videos = _load_folder_videos(folder)
    if not videos:
        return JSONResponse({"error": "该目录还没有台词 JSON——先做目录分析"}, status_code=400)
    clipper = get_clipper()
    registry, outputs, matched = [], [], 0
    for v in videos:
        hits = [l for l in v["lines"] if any(k in l["text"] for k in kws)]
        if not hits:
            continue
        matched += len(hits)
        out_dir = str(Path(v["path"]).parent / "剪辑成片")
        ts_list = [[int(round(l["start"] * 1000)), int(round(l["end"] * 1000))] for l in hits]
        state = _make_state(v["path"], v["lines"])
        clip_file, message, _ = clipper.video_clip(
            "", 0, 0, state, output_dir=out_dir, timestamp_list=ts_list)
        registry.append({"file": clip_file})
        outputs.append({"video": v["video"], "file": clip_file})
    _save_registry(registry)
    return {"matched": matched, "outputs": outputs}


@app.post("/api/script_cut")
def api_script_cut(body: dict):
    folder = str(body.get("folder") or "").strip()
    script = str(body.get("script") or "").strip()
    if not script:
        return JSONResponse({"error": "剧本为空"}, status_code=400)
    queries = [q for q in (l.strip() for l in script.splitlines()) if q]
    videos = _load_folder_videos(folder)
    if not videos:
        return JSONResponse({"error": "该目录还没有台词 JSON——先做目录分析"}, status_code=400)
    clipper = get_clipper()
    matches, outputs, registry = [], [], []
    for v in videos:
        per_video = []   # [(line_no, line)]
        for qi, q in enumerate(queries):
            scored = sorted(((difflib.SequenceMatcher(None, q, l["text"]).ratio(), l)
                             for l in v["lines"]), key=lambda x: -x[0])
            if scored and scored[0][0] >= 0.45:
                per_video.append((qi, scored[0][1]))
        if not per_video:
            continue
        seen, ts_list = set(), []
        for qi, l in sorted(per_video, key=lambda x: x[0]):
            key = (l["start"], l["end"])
            if key in seen:
                continue
            seen.add(key)
            ts_list.append([int(round(l["start"] * 1000)), int(round(l["end"] * 1000))])
        if not ts_list:
            continue
        out_dir = str(Path(v["path"]).parent / "剪辑成片")
        state = _make_state(v["path"], v["lines"])
        clip_file, message, _ = clipper.video_clip(
            "", 0, 0, state, output_dir=out_dir, timestamp_list=ts_list)
        registry.append({"file": clip_file})
        outputs.append({"file": clip_file})
        for qi, l in sorted(per_video, key=lambda x: x[0]):
            matches.append({"line_no": qi, "count": 1, "video": v["video"], "text": l["text"]})
    _save_registry(registry)
    return {"matches": matches, "outputs": outputs}


def _save_registry(items: list):
    reg = []
    if OUT_REGISTRY.exists():
        try:
            reg = json.loads(OUT_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            reg = []
    for it in items:
        if it not in reg:
            reg.append(it)
    OUT_REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


@app.get("/api/outputs")
def api_outputs():
    reg = []
    if OUT_REGISTRY.exists():
        try:
            reg = json.loads(OUT_REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            reg = []
    reg = [r for r in reg if Path(r["file"]).is_file()]
    return {"outputs": reg}


@app.get("/media")
def media_file(f: str, dl: int = 1):
    p = Path(f)
    if not p.is_file():
        return JSONResponse({"error": "不存在"}, status_code=404)
    return FileResponse(p, filename=p.name)
