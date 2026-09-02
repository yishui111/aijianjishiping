# -*- coding: utf-8 -*-
r"""
二次元批量出图 — 剧本JSON → 图片 + 时长 + 清单JSON（Illustrious XL）
======================================================================
输入：漫画剧本 JSON（与漫画工作台同格式）
输出到 output/batch/<任务名>/：
  - seg_001.png ...         每段一张图
  - manifest.json           清单：段号/图片/显示时长/相机/台词/角色 → 供工作台做镜头

出图规则：
  - 有角色基准图 → img2img 锁角色（denoise 0.55 保持外观）
  - 无基准图/战斗段 → 文生图
用法（8G 机长跑）：
  python_embeded\python.exe pipeline\scripts\batch_gen_anime.py <剧本.json> [角色基准图目录]
  例：python_embeded\python.exe pipeline\scripts\batch_gen_anime.py script.json chars/
      （chars 里放 <角色id>.png，如 c1.png c2.png）
参数：--width 832 --height 480 --steps 25 --denoise 0.55 --name 任务名
"""

import argparse
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPTS_DIR.parent
for _p in (_PIPELINE_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from workbench_tools_anime import anime_t2i, anime_img2img

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

OUT_ROOT = _PIPELINE_DIR / "output" / "batch"
LOG_DIR = _PIPELINE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "batch_anime.log"), level=logging.INFO,
                    encoding="utf-8", format="%(asctime)s [%(levelname)s] %(message)s")


def _log(m):
    logging.info(m)
    print(f"[批量] {m}", flush=True)


def _load_script(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if "timeline" not in d:
        raise RuntimeError("剧本缺少 timeline 字段（不是漫画工作台格式）")
    return d


def _find_char_base(chars_dir, cid):
    if not chars_dir:
        return None
    chars_dir = Path(chars_dir)
    for ext in ("png", "jpg", "jpeg", "webp"):
        f = chars_dir / f"{cid}.{ext}"
        if f.exists():
            return str(f)
    for f in chars_dir.iterdir():
        if f.suffix.lower() in (".png", ".jpg", ".jpeg") and cid in f.stem:
            return str(f)
    return None


def _gen_segment(seg, char_bases, args, job_dir):
    prompt = seg.get("image_prompt") or seg.get("scene") or ""
    seg_type = seg.get("type", "dialogue")
    chars = seg.get("characters") or []
    base = None
    if seg_type != "fight":
        for cid in chars:
            b = _find_char_base(char_bases, cid)
            if b:
                base = b
                break
    t0 = time.time()
    if base:
        img = anime_img2img(base, f"{prompt}，保持角色外观一致", denoise=args.denoise,
                            width=args.width, height=args.height,
                            steps=args.steps, cfg=args.cfg)
        method = f"锁角色(denoise {args.denoise})"
    else:
        img = anime_t2i(prompt, width=args.width, height=args.height,
                        steps=args.steps, cfg=args.cfg)
        method = f"文生图({args.steps}步)"
    n = int(seg.get("id", 0))
    dest = job_dir / f"seg_{n:03d}.png"
    dest.write_bytes(Path(img).read_bytes())
    _log(f"  段{n} [{method}] 用时{time.time()-t0:.0f}s")
    info = {
        "id": n, "type": seg_type, "start": seg.get("start", 0),
        "duration": seg.get("duration", 4), "scene": seg.get("scene", ""),
        "characters": chars, "line": seg.get("line", ""),
        "camera": seg.get("camera", "zoom_in"),
        "camera_target": seg.get("camera_target", "full_body"),
        "image": dest.name, "method": method,
    }
    return dest, info


def main():
    ap = argparse.ArgumentParser(description="二次元批量出图（Illustrious）")
    ap.add_argument("script", help="剧本 JSON 路径")
    ap.add_argument("chars_dir", nargs="?", default=None, help="角色基准图目录（可选）")
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=7.0)
    ap.add_argument("--denoise", type=float, default=0.55)
    ap.add_argument("--name", default=None, help="输出任务名（默认用剧本文件名）")
    args = ap.parse_args()

    script = _load_script(args.script)
    name = args.name or Path(args.script).stem
    job_dir = OUT_ROOT / name
    job_dir.mkdir(parents=True, exist_ok=True)

    _log(f"任务[{name}] 共 {len(script['timeline'])} 段，"
         f"分辨率 {args.width}x{args.height}，{args.steps}步")
    if args.chars_dir:
        _log(f"角色基准图目录: {args.chars_dir}")

    manifest = {"title": script.get("title", name), "characters": script.get("characters", []),
                "segments": [], "params": {"width": args.width, "height": args.height,
                                           "steps": args.steps, "denoise": args.denoise}}
    t_all = time.time()
    for i, seg in enumerate(script["timeline"]):
        _log(f"[{i+1}/{len(script['timeline'])}] 段{seg.get('id')}: "
             f"{seg.get('scene','')} {seg.get('line','')[:20]}")
        try:
            _, info = _gen_segment(seg, args.chars_dir, args, job_dir)
            manifest["segments"].append(info)
        except Exception as e:
            _log(f"  段{seg.get('id')} 失败: {e}")
            manifest["segments"].append({"id": seg.get("id", i), "error": str(e)})

    mf = job_dir / "manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for s in manifest["segments"] if "error" not in s)
    _log(f"完成：成功 {ok}/{len(manifest['segments'])} 段，总耗时 {time.time()-t_all:.0f}s")
    _log(f"输出目录: {job_dir}")
    _log(f"清单: {mf}")


if __name__ == "__main__":
    main()
