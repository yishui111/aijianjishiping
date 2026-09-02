# -*- coding: utf-8 -*-
r"""
二次元出图工作台（Illustrious XL / Hassaku）— 独立项目，自包含
================================================================
- 文生图：prompt → anime_t2i（二次元高画质，角色质量强）
- 锁角色：上传参考图 + prompt → anime_img2img（denoise 0.55 保持外观）
- 端口 8093；打开 http://127.0.0.1:8093
用法：python_embeded\python.exe pipeline\scripts\anime_workbench.py
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

from workbench_tools_anime import anime_t2i, anime_img2img, anime_ipadapter

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

PORT = 8093
WEB_DIR = _PIPELINE_DIR / "web"
OUT_DIR = _PIPELINE_DIR / "output" / "anime"
LOG_DIR = _PIPELINE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "anime_wb.log"), level=logging.INFO,
                    encoding="utf-8", format="%(asctime)s [%(levelname)s] %(message)s")


def _log(m):
    logging.info(m)
    print(f"[AnimeWB] {m}", flush=True)


def _log_err(m):
    logging.error(m)
    print(f"[AnimeWB] 错误: {m}", flush=True)


TASKS = {}
TASK_LOCK = threading.Lock()
REF_IMAGES = {}


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
    try:
        p = t["payload"]
        if t["kind"] == "t2i":
            img = anime_t2i(p["prompt"], width=p.get("width", 832),
                            height=p.get("height", 480), steps=p.get("steps", 25),
                            cfg=p.get("cfg", 7.0), seed=p.get("seed"))
        elif t["kind"] == "edit":
            ref = REF_IMAGES.get(p.get("ref_id"))
            if not ref:
                raise RuntimeError("参考图未上传或已过期，请重新上传")
            if p.get("mode") == "ipadapter":
                img = anime_ipadapter(ref, p["prompt"],
                                      width=p.get("width", 832), height=p.get("height", 480),
                                      steps=p.get("steps", 25), cfg=p.get("cfg", 7.0),
                                      weight=p.get("weight", 0.85), seed=p.get("seed"))
            else:
                img = anime_img2img(ref, p["prompt"], denoise=p.get("denoise", 0.55),
                                    width=p.get("width", 832), height=p.get("height", 480),
                                    steps=p.get("steps", 25), cfg=p.get("cfg", 7.0),
                                    seed=p.get("seed"))
        else:
            raise RuntimeError(f"未知任务类型 {t['kind']}")
        with TASK_LOCK:
            t["status"] = "done"
            t["message"] = "完成"
            if img is not None:
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                dest = OUT_DIR / f"{t['kind']}_{uuid.uuid4().hex[:8]}.png"
                dest.write_bytes(Path(img).read_bytes())
                t["result"] = str(dest)
    except Exception as e:
        _log_err(f"任务 {tid} 失败: {e}")
        with TASK_LOCK:
            t["status"] = "error"
            t["message"] = str(e)


def _to_url(p):
    if not p:
        return None
    path = Path(p)
    try:
        rel = path.relative_to(OUT_DIR)
        return "/files/" + str(rel).replace("\\", "/")
    except ValueError:
        return p


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
            html = WEB_DIR / "anime_test.html"
            if not html.exists():
                self._send(404, "anime_test.html 不存在", "text/plain; charset=utf-8")
                return
            self._send(200, html.read_bytes(), "text/html; charset=utf-8")
        elif p == "/api/tasks":
            with TASK_LOCK:
                out = []
                for v in TASKS.values():
                    item = {k: vv for k, vv in v.items() if k != "payload"}
                    item["result"] = _to_url(v.get("result"))
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
                out["result"] = _to_url(t.get("result"))
            self._send(200, out)
        elif p.startswith("/files/"):
            rel = p[len("/files/"):]
            f = (OUT_DIR / rel).resolve()
            if OUT_DIR not in f.parents or not f.exists():
                self._send(404, "not found", "text/plain")
                return
            ext = f.suffix.lower()
            ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
            self._send(200, f.read_bytes(), ctype)
        else:
            self._send(404, {"error": "no such api"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/t2i":
            body = json.loads(self._read_body().decode("utf-8") or "{}")
            tid = _new_task("t2i", body)
            self._send(200, {"id": tid})
        elif p == "/api/edit":
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                raw = self._read_body()
                fields = _parse_multipart(raw, ctype)
                ref_id = _save_ref(fields.get("image"))
                body = {"prompt": fields.get("prompt", ""),
                        "steps": int(fields.get("steps", 25) or 25),
                        "cfg": float(fields.get("cfg", 7.0) or 7.0),
                        "denoise": float(fields.get("denoise", 0.55) or 0.55),
                        "mode": fields.get("mode", ""),
                        "weight": float(fields.get("weight", 0.85) or 0.85),
                        "width": int(fields.get("width", 832) or 832),
                        "height": int(fields.get("height", 480) or 480),
                        "seed": fields.get("seed") or None,
                        "ref_id": ref_id}
            else:
                body = json.loads(self._read_body().decode("utf-8") or "{}")
                ref_id = body.get("ref_id") or list(REF_IMAGES.keys())[-1] if REF_IMAGES else None
                body["ref_id"] = ref_id
            if not body.get("ref_id"):
                self._send(400, {"error": "请先上传参考图"})
                return
            tid = _new_task("edit", body)
            self._send(200, {"id": tid})
        else:
            self._send(404, {"error": "no such api"})


def _save_ref(img_bytes):
    if not img_bytes:
        raise RuntimeError("未收到图片数据")
    rid = uuid.uuid4().hex[:8]
    d = OUT_DIR / "refs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"ref_{rid}.png"
    f.write_bytes(img_bytes)
    REF_IMAGES[rid] = str(f)
    return rid


def _parse_multipart(raw, ctype):
    import re
    m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
    boundary = (m.group(1) or m.group(2)).encode()
    parts = {}
    for block in raw.split(b"--" + boundary):
        if not block.strip() or block.strip() == b"--":
            continue
        head, _, content = block.partition(b"\r\n\r\n")
        hm = re.search(r'name="([^"]+)"', head.decode("utf-8", "ignore"))
        if not hm:
            continue
        name = hm.group(1)
        content = content.rstrip(b"\r\n")
        if name == "image":
            parts["image"] = content
        else:
            parts[name] = content.decode("utf-8", "ignore").strip()
    return parts


def main():
    _log(f"二次元出图工作台启动: http://127.0.0.1:{PORT}")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
