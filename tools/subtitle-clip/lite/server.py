# -*- coding: utf-8 -*-
"""最简工作台（lite）：一个静态页 + 反向代理，把 61812 分析 API 的能力包成单端口页面。

约定（仓库铁律）：这是独立新服务，不修改 api_service.py / funclip/launch.py / videoclipper.py。
61812 没开 CORS，本服务让页面与接口同源（61814），页面才能直接调用。

启动：tools/subtitle-clip/lite/启动简易台.bat（或 runtime/Scripts/python.exe server.py）
前提：分析 API 61812 已启动（双击仓库根 启动.bat）；本服务只做转发，不加载模型。
"""
import os
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
UPSTREAM = os.environ.get("LITE_UPSTREAM", "http://127.0.0.1:61812")
HOST = os.environ.get("LITE_HOST", "127.0.0.1")
PORT = int(os.environ.get("LITE_PORT", "61814"))

app = FastAPI(title="subtitle-clip-lite", docs_url=None, redoc_url=None)


@app.get("/")
def index():
    return FileResponse(HERE / "index.html", media_type="text/html; charset=utf-8")


@app.get("/health")
def health():
    """自身存活 + 上游 61812 探测（不打扰模型加载）。"""
    upstream_ok = False
    try:
        r = requests.get(f"{UPSTREAM}/health", timeout=3)
        upstream_ok = r.ok and bool(r.json().get("ok"))
    except Exception:
        pass
    return {
        "ok": True,
        "service": "subtitle-clip-lite",
        "port": PORT,
        "upstream": UPSTREAM,
        "upstream_ok": upstream_ok,
    }


# ---- 以下全部原样转发给 61812（识别/台词/剪辑/成片/取片）----

PASS_METHODS = {"GET", "POST"}


@app.api_route("/api/{path:path}", methods=sorted(PASS_METHODS))
@app.api_route("/media", methods=["GET"])
async def proxy(request: Request, path: str = ""):
    url = UPSTREAM + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    headers = {}
    if request.headers.get("content-type"):
        headers["Content-Type"] = request.headers["content-type"]
    body = await request.body()
    # 分析接口首次要加载模型（数分钟），读超时必须放宽
    try:
        r = requests.request(
            request.method, url, data=body, headers=headers,
            timeout=(10, 3600),
        )
    except requests.RequestException as exc:
        return JSONResponse(
            {"error": f"分析 API（61812）连不上：请先双击仓库根 启动.bat（{type(exc).__name__}）"},
            status_code=502,
        )
    resp = Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/octet-stream"),
    )
    if r.headers.get("content-disposition"):
        resp.headers["Content-Disposition"] = r.headers["content-disposition"]
    return resp


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
