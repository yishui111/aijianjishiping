# -*- coding: utf-8 -*-
"""字幕剪辑功能全面测试（API 级）。"""
import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:57803"
P01 = "3分钟/青海卫视《西游记 》全25集 p01 西游记 (1)石猴初问世_BV1iv411j7QE_p1_P01.mp4"
F59 = "1用户视频/43de3419f1be4e0789eaa2d2410269b0.mp4"
IMG = "1用户视频/微信图片_20260819232724_21_1728.jpg"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + str(detail) if detail else ""))


def ts(sec):
    ms = max(0, int(round(sec * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ---- 0. 三服务健康 ----
for port, svc in [(8001, "analyzer"), (8002, "executor"), (8003, "planner")]:
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=8)
        check(f"0.health:{svc}", r.status_code == 200)
    except Exception as e:
        check(f"0.health:{svc}", False, str(e))

# ---- 1. 转写：缓存命中 ----
t0 = time.time()
r = httpx.post(f"{BASE}/api/transcribe", json={"file": P01, "force": False}, timeout=60).json()
check("1.transcribe缓存命中", r.get("cached") is True and len(r.get("speech") or []) == 3,
      f"{time.time()-t0:.1f}s {len(r.get('speech') or [])}句")

# ---- 2. 转写：全新素材（59s，medium 模型）----
t0 = time.time()
r = httpx.post(f"{BASE}/api/transcribe", json={"file": F59, "force": False}, timeout=900).json()
dt = time.time() - t0
lines59 = r.get("speech") or []
check("2.transcribe全新转写", not r.get("cached") and len(lines59) > 0, f"{dt:.0f}s {len(lines59)}句")
for l in lines59[:5]:
    print("      ", f"[{l['start']:.1f}-{l['end']:.1f}]", l["text"][:36])

# ---- 3. 转写：无音轨/图片素材 → 优雅处理 ----
r = httpx.post(f"{BASE}/api/transcribe", json={"file": IMG, "force": False}, timeout=120)
ok = r.status_code == 200
d = r.json() if ok else {}
check("3.transcribe图片素材不崩", ok, "speech=" + str(len(d.get("speech") or [])))

# ---- 4. 转写：不存在素材 → 报错 ----
r = httpx.post(f"{BASE}/api/transcribe", json={"file": "不存在/xx.mp4"}, timeout=30)
check("4.transcribe不存在素材报错", r.status_code in (200, 404) and (r.json().get("error") or r.status_code == 404),
      r.text[:60])

# ---- 5. 智能勾选：正常 query ----
lines = (lines59 or (httpx.post(f"{BASE}/api/transcribe", json={"file": P01}, timeout=30).json()["speech"]))
payload = {"query": "里面说了什么", "lines": [{"i": i, "start": l["start"], "end": l["end"], "text": l["text"]} for i, l in enumerate(lines)]}
r = httpx.post(f"{BASE}/api/match_lines", json=payload, timeout=180).json()
picked = r.get("picked") or []
valid_i = {p["i"] for p in payload["lines"] if isinstance(p["i"], int)}
check("5.match_lines正常", isinstance(r.get("picked"), list) and all(p["i"] in valid_i for p in picked),
      f"{len(picked)}/{len(lines)}")

# ---- 6. 智能勾选：空清单 → 报错 ----
r = httpx.post(f"{BASE}/api/match_lines", json={"query": "x", "lines": []}, timeout=30)
check("6.match_lines空清单", bool(r.json().get("error")))

# ---- 7. 按台词剪：时长精确校验 ----
# 勾前 3 句（不足 3 句则全部）
n = min(3, len(lines))
sels = [{"file": F59 if lines59 else P01, "start": lines[i]["start"], "end": lines[i]["end"]} for i in range(n)]
expect = sum(s["end"] - s["start"] for s in sels)
r = httpx.post(f"{BASE}/api/clip_selected", json={"selections": sels}, timeout=900).json()
out = r.get("output")
check("7.clip_selected出片", bool(out) and r.get("steps_ok"), f"steps={r.get('steps_ok')}")
if out:
    import sys
    sys.path.insert(0, "services")
    from common import media
    p = Path(out)
    if not p.is_absolute():
        p = Path("output") / out
    got = media.probe(p).get("duration_sec") or 0
    check("7b.成片时长精确", abs(got - expect) <= 0.25 * n + 0.3, f"期望{expect:.2f}s 实际{got:.2f}s")

    # ---- 8. 烧硬字幕 ----
    srt = ""
    tl, k = 0.0, 0
    for s in sels:
        k += 1
        en = tl + (s["end"] - s["start"])
        srt += f"{k}\n{ts(tl)} --> {ts(en)}\n字幕测试第{k}句\n\n"
        tl = en
    r = httpx.post(f"{BASE}/api/burn_srt", json={"output": out, "srt": srt}, timeout=1200).json()
    sub_ok = bool(r.get("output"))
    check("8.burn_srt硬字幕", sub_ok, r.get("error", "")[:60])
    if sub_ok:
        sp = Path("output") / r["output"]
        got2 = media.probe(sp).get("duration_sec") or 0
        check("8b.硬字幕时长一致", abs(got2 - got) <= 0.5, f"{got2:.2f}s vs {got:.2f}s")
        # 抽中段一帧看字幕是否真的烧上（保存后人工/视觉检查）
        import subprocess
        sys.path.insert(0, "services")
        from common import media as M
        frame = sp.parent / "_test_frame.jpg"
        subprocess.run([M.ffmpeg(), "-y", "-v", "error", "-ss", str(got2 / 2), "-i", str(sp),
                        "-frames:v", "1", str(frame)], timeout=60)
        check("8c.抽帧成功", frame.exists() and frame.stat().st_size > 1000, str(frame))

# ---- 9. burn_srt 错误路径 ----
r = httpx.post(f"{BASE}/api/burn_srt", json={"output": "nofolder/nofile.mp4", "srt": "x"}, timeout=30)
check("9.burn_srt错误路径", bool(r.json().get("error")))

# ---- 10. 剪映草稿导出（字幕选区）----
r = httpx.post(f"{BASE}/api/export_jianying", json={"name": "字幕剪辑测试", "selections": sels}, timeout=120).json()
check("10.export_jianying", bool(r.get("draft_name")) and Path(r["folder"], "draft_content.json").exists(),
      f"{r.get('segments')}段 {r.get('duration_sec')}s zip={bool(r.get('zip_url'))}")

# ---- 11. SRT 导出（字幕选区）----
r = httpx.post(f"{BASE}/api/export_srt", json={"name": "字幕剪辑测试", "selections": sels}, timeout=120).json()
check("11.export_srt", r.get("cues", 0) >= 1, f"{r.get('cues')}条")

# ---- 12. 批量剪（免分析）----
r = httpx.post(f"{BASE}/api/batch_clip", json={"folder": "1用户视频", "start": 2.0, "end": 6.0, "resolution": "原样"}, timeout=30).json()
check("12.batch_clip启动", bool(r.get("batch_id")), str(r.get("total", ""))[:20])

print("\n===== 汇总 =====")
fails = [x for x in results if not x[1]]
print(f"{len(results) - len(fails)}/{len(results)} 通过")
for name, ok, detail in fails:
    print("FAIL:", name, detail)
