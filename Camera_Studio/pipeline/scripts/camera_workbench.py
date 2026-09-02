# -*- coding: utf-8 -*-
r"""
镜头工作台 — JSON 清单 → 逐段镜头视频 → 预览/拼接（独立项目，自包含）
======================================================================
- 上传镜头清单 JSON + 图片目录 → 逐段生成 Ken Burns 镜头 → 页面预览
- 支持单段重试 / 全部生成 / 拼接成片
- 纯 ffmpeg，无需 ComfyUI / 出图系统
- 端口 8094；打开 http://127.0.0.1:8094
用法：python_embeded\python.exe pipeline\scripts\camera_workbench.py
"""

import json
import logging
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPTS_DIR.parent
for _p in (_PIPELINE_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from camera_engine import camera_shot, concat_clips, CAMERA_MOTIONS

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

PORT = 8094
WEB_DIR = _PIPELINE_DIR / "web"
DATA_DIR = _PIPELINE_DIR / "data"          # 上传的 JSON + 图片
OUT_CLIPS = _PIPELINE_DIR / "output" / "clips"
OUT_FINAL = _PIPELINE_DIR / "output" / "final"
LOG_DIR = _PIPELINE_DIR / "logs"
for d in (DATA_DIR, OUT_CLIPS, OUT_FINAL, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "camera_wb.log"), level=logging.INFO,
                    encoding="utf-8", format="%(asctime)s [%(levelname)s] %(message)s")


def _log(m):
    logging.info(m)
    print(f"[CameraWB] {m}", flush=True)


def _log_err(m):
    logging.error(m)
    print(f"[CameraWB] 错误: {m}", flush=True)


# ---- 会话状态 ----
SESSION = {"name": "", "manifest": None, "job_dir": None, "width": 832,
           "height": 480, "fps": 24}
TASKS = {}
TASK_LOCK = threading.Lock()


def _rel_url(p):
    """磁盘路径 -> 浏览器 URL"""
    p = Path(p)
    if OUT_CLIPS in p.parents or p.parent == OUT_CLIPS:
        rel = p.relative_to(OUT_CLIPS)
        return "/clips/" + str(rel).replace("\\", "/")
    if OUT_FINAL in p.parents or p.parent == OUT_FINAL:
        rel = p.relative_to(OUT_FINAL)
        return "/final/" + str(rel).replace("\\", "/")
    return "/files/" + str(p.name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            html = WEB_DIR / "camera_test.html"
            if not html.exists():
                self._send(404, "camera_test.html 不存在", "text/plain; charset=utf-8")
                return
            self._send(200, html.read_bytes(), "text/html; charset=utf-8")
        elif p == "/api/session":
            with TASK_LOCK:
                s = dict(SESSION)
                s["manifest"] = SESSION.get("manifest")
            self._send(200, s)
        elif p == "/api/tasks":
            with TASK_LOCK:
                out = []
                for v in TASKS.values():
                    item = {k: vv for k, vv in v.items() if k != "payload"}
                    item["result"] = _rel_url(v.get("result")) if v.get("result") else None
                    out.append(item)
            self._send(200, out)
        elif p == "/api/task":
            from urllib.parse import parse_qs
            qs = parse_qs(self.path.split("?")[1] if "?" in self.path else "")
            tid = qs.get("id", [""])[0]
            with TASK_LOCK:
                t = TASKS.get(tid)
                if t is None:
                    self._send(404, {"error": "任务不存在"})
                    return
                out = {k: v for k, v in t.items() if k != "payload"}
                out["result"] = _rel_url(t.get("result")) if t.get("result") else None
            self._send(200, out)
        elif p.startswith("/clips/"):
            f = (OUT_CLIPS / p[len("/clips/"):]).resolve()
            if OUT_CLIPS not in f.parents or not f.exists():
                self._send(404, "not found", "text/plain")
                return
            self._send(200, f.read_bytes(), "video/mp4")
        elif p.startswith("/final/"):
            f = (OUT_FINAL / p[len("/final/"):]).resolve()
            if OUT_FINAL not in f.parents or not f.exists():
                self._send(404, "not found", "text/plain")
                return
            self._send(200, f.read_bytes(), "video/mp4")
        elif p.startswith("/files/"):
            f = (DATA_DIR / p[len("/files/"):]).resolve()
            if DATA_DIR not in f.parents or not f.exists():
                self._send(404, "not found", "text/plain")
                return
            ext = f.suffix.lower()
            ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "json": "application/json"}.get(ext, "application/octet-stream")
            self._send(200, f.read_bytes(), ctype)
        else:
            self._send(404, {"error": "no such api"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/load":
            # multipart: json 文件 + 图片们 + 参数
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                fields = _parse_multipart(self._read_body(), ctype)
                ok = _load_session(fields)
                if not ok:
                    self._send(400, {"error": "清单加载失败：缺 json 或格式不对"})
                    return
                self._send(200, {"ok": True, "name": SESSION["name"],
                                 "segments": len(SESSION["manifest"]["segments"])})
            else:
                self._send(400, {"error": "需要 multipart 上传"})
        elif p == "/api/gen_segment":
            body = json.loads(self._read_body().decode("utf-8") or "{}")
            idx = body.get("idx")
            tid = _new_task("segment", {"idx": idx,
                                        "width": body.get("width", SESSION["width"]),
                                        "height": body.get("height", SESSION["height"]),
                                        "fps": body.get("fps", SESSION["fps"])})
            self._send(200, {"id": tid})
        elif p == "/api/gen_all":
            body = json.loads(self._read_body().decode("utf-8") or "{}")
            SESSION["width"] = body.get("width", SESSION["width"])
            SESSION["height"] = body.get("height", SESSION["height"])
            SESSION["fps"] = body.get("fps", SESSION["fps"])
            tid = _new_task("all", {})
            self._send(200, {"id": tid})
        elif p == "/api/concat":
            tid = _new_task("concat", {})
            self._send(200, {"id": tid})
        else:
            self._send(404, {"error": "no such api"})


def _load_session(fields):
    """从上传的 multipart 字段恢复会话：json 文件 + 图片 + 参数"""
    global SESSION
    raw_json = fields.get("json")
    if not raw_json:
        return False
    try:
        data = json.loads(raw_json.decode("utf-8"))
    except Exception:
        return False
    segments = data.get("segments") or data
    if not isinstance(segments, list) or not segments:
        return False
    # 保存 json
    name = (fields.get("name") or data.get("title") or "task").strip() or "task"
    job = DATA_DIR / name
    job.mkdir(parents=True, exist_ok=True)
    mf = job / "manifest.json"
    mf.write_text(raw_json.decode("utf-8"), encoding="utf-8")
    # 保存图片（覆盖引用：图片名 -> 保存的文件名）
    for key in fields:
        if key.startswith("img_"):
            fname = key[4:]
            (job / fname).write_bytes(fields[key])
    # 把 segment 里的 image 引用改成绝对路径
    for seg in segments:
        img = seg.get("image", "")
        if img:
            p = Path(img)
            if not p.is_absolute():
                p = job / p
            seg["image"] = str(p)
    with TASK_LOCK:
        SESSION = {"name": name, "manifest": data, "job_dir": str(job),
                   "width": int(fields.get("width", 832) or 832),
                   "height": int(fields.get("height", 480) or 480),
                   "fps": int(fields.get("fps", 24) or 24)}
    _log(f"会话加载: {name}, {len(segments)} 段")
    return True


def _new_task(kind, payload):
    tid = uuid.uuid4().hex[:8]
    with TASK_LOCK:
        TASKS[tid] = {"kind": kind, "payload": payload,
                      "status": "queued", "message": "排队中", "result": None,
                      "created": time.time()}
    threading.Thread(target=_run_task, args=(tid,), daemon=True).start()
    return tid


def _run_task(tid):
    with TASK_LOCK:
        t = TASKS[tid]
        t["status"] = "running"
        t["message"] = "生成中…"
        payload = t["payload"]
        manifest = SESSION["manifest"]
        job_dir = Path(SESSION["job_dir"])
        width, height, fps = SESSION["width"], SESSION["height"], SESSION["fps"]
    try:
        segments = manifest.get("segments") or manifest
        clips_dir = OUT_CLIPS / SESSION["name"]
        clips_dir.mkdir(parents=True, exist_ok=True)
        if t["kind"] == "segment":
            idx = payload["idx"]
            seg = segments[idx]
            out = _gen_one(seg, job_dir, clips_dir, idx, width, height, fps)
            with TASK_LOCK:
                t["status"] = "done"
                t["message"] = "完成"
                t["result"] = str(out)
        elif t["kind"] == "all":
            done = 0
            for i, seg in enumerate(segments):
                try:
                    _gen_one(seg, job_dir, clips_dir, i, width, height, fps)
                    done += 1
                    with TASK_LOCK:
                        t["message"] = f"生成中… {done}/{len(segments)}"
                except Exception as e:
                    _log_err(f"段{i}失败: {e}")
            with TASK_LOCK:
                t["status"] = "done"
                t["message"] = f"完成 {done}/{len(segments)} 段"
        elif t["kind"] == "concat":
            # 收集已生成的 cam_*.mp4
            clips = sorted(clips_dir.glob("cam_*.mp4"))
            if not clips:
                raise RuntimeError("还没有生成的镜头，先点'全部生成'")
            final = OUT_FINAL / f"{SESSION['name']}_final.mp4"
            concat_clips([str(c) for c in clips], final)
            with TASK_LOCK:
                t["status"] = "done"
                t["message"] = "完成"
                t["result"] = str(final)
    except Exception as e:
        _log_err(f"任务 {tid} 失败: {e}")
        with TASK_LOCK:
            t["status"] = "error"
            t["message"] = str(e)


def _gen_one(seg, job_dir, clips_dir, idx, width, height, fps):
    img = Path(seg.get("image", ""))
    if not img.is_absolute():
        img = job_dir / img
    if not img.exists():
        raise RuntimeError(f"图片不存在: {img}")
    cam = seg.get("camera", "zoom_in")
    if cam not in CAMERA_MOTIONS:
        cam = "zoom_in"
    target = seg.get("camera_target", "full_body")
    dur = float(seg.get("duration", 4))
    n = int(seg.get("id", idx + 1))
    out = clips_dir / f"cam_{n:03d}.mp4"
    if out.exists():
        out.unlink()
    return camera_shot(str(img), motion=cam, duration=dur, target=target,
                       width=width, height=height, fps=fps, out_dir=clips_dir)


def _parse_multipart(raw, ctype):
    """极简 multipart 解析：name=json 的存 bytes，img_<文件名> 存 bytes，其余存 str"""
    import re
    m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
    boundary = (m.group(1) or m.group(2)).encode()
    parts = {}
    for block in raw.split(b"--" + boundary):
        if not block.strip() or block.strip() == b"--":
            continue
        head, _, content = block.partition(b"\r\n\r\n")
        hm = re.search(r'name="([^"]+)"', head.decode("utf-8", "ignore"))
        fm = re.search(r'filename="([^"]*)"', head.decode("utf-8", "ignore"))
        if not hm:
            continue
        name = hm.group(1)
        content = content.rstrip(b"\r\n")
        fname = fm.group(1) if fm else None
        if fname:
            parts[f"img_{fname}"] = content
        elif name in ("width", "height", "fps"):
            parts[name] = content.decode("utf-8", "ignore").strip()
        elif name == "name":
            parts[name] = content.decode("utf-8", "ignore").strip()
        elif name == "json":
            parts[name] = content
    return parts


def main():
    _log(f"镜头工作台启动: http://127.0.0.1:{PORT}")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
