# -*- coding: utf-8 -*-
r"""
FLUX2 批量出图 — 剧本JSON → 图片 + 时长 + 清单JSON
====================================================
输入：漫画剧本 JSON（title/characters/timeline，与漫画工作台同格式）
输出到 output/batch/<任务名>/：
  - seg_001.png, seg_002.png ...  每段一张图（16:9 832x480）
  - manifest.json                  清单：段号/图片/显示时长/相机/台词/角色 → 供工作台做镜头

出图规则：
  - 片段 characters 有角色基准图  → 锁角色编辑（image_edit，保持外观）
  - 片段无角色基准图             → 文生图（t2i）
  - 战斗/动作片段强制文生图（多角色同框，编辑模式易崩脸）

用法（8G 机长跑）：
  python_embeded\python.exe pipeline\scripts\batch_gen.py <剧本.json> [角色基准图目录]
  例：python_embeded\python.exe pipeline\scripts\batch_gen.py script.json chars/
      （角色基准图目录里放 <角色id>.png，如 c1.png c2.png）

参数（可选，追加在命令后）：
  --width 640 --height 384   分辨率（8G 机建议 640x384）
  --steps 20 --cfg 3.5       采样参数（8G 机 steps 可降到 16）
  --edit-steps 24            锁角色编辑步数
  --name 任务名               输出目录名（默认取剧本文件名）
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

from workbench_tools import flux2_t2i, flux2_image_edit

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

OUT_ROOT = _PIPELINE_DIR / "output" / "batch"
LOG_DIR = _PIPELINE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / "batch_gen.log"), level=logging.INFO,
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
    """角色基准图：<角色id>.png / <角色id>.jpg"""
    if not chars_dir:
        return None
    for ext in ("png", "jpg", "jpeg", "webp"):
        f = Path(chars_dir) / f"{cid}.{ext}"
        if f.exists():
            return str(f)
    # 兼容 24_角色基准_红发剑客.png 这类命名：取含 id 或中文名的
    for f in Path(chars_dir).iterdir():
        if f.suffix.lower() in (".png", ".jpg", ".jpeg") and cid in f.stem:
            return str(f)
    return None


def _gen_segment(seg, char_bases, args, job_dir):
    """单段出图，返回 (图片路径, 段信息)"""
    prompt = seg.get("image_prompt") or seg.get("scene") or ""
    seg_type = seg.get("type", "dialogue")
    chars = seg.get("characters") or []
    # 找基准图：战斗/动作段强制文生图（多角色同框编辑易崩）
    base = None
    if seg_type != "fight":
        for cid in chars:
            b = _find_char_base(char_bases, cid)
            if b:
                base = b
                break
    t0 = time.time()
    if base:
        img = flux2_image_edit(base, f"{prompt}，保持角色外观一致",
                               steps=args.edit_steps, cfg=args.edit_cfg)
        method = f"锁角色(编辑{args.edit_steps}步)"
    else:
        img = flux2_t2i(prompt, width=args.width, height=args.height,
                        steps=args.steps, cfg=args.cfg)
        method = f"文生图({args.steps}步)"
    # 复制进任务目录，规范命名
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
    ap = argparse.ArgumentParser(description="FLUX2 批量出图")
    ap.add_argument("script", help="剧本 JSON 路径")
    ap.add_argument("chars_dir", nargs="?", default=None, help="角色基准图目录（可选）")
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--cfg", type=float, default=3.5)
    ap.add_argument("--edit-steps", type=int, default=24)
    ap.add_argument("--edit-cfg", type=float, default=5.0)
    ap.add_argument("--name", default=None, help="输出任务名（默认用剧本文件名）")
    args = ap.parse_args()

    script = _load_script(args.script)
    name = args.name or Path(args.script).stem
    job_dir = OUT_ROOT / name
    job_dir.mkdir(parents=True, exist_ok=True)

    _log(f"任务[{name}] 共 {len(script['timeline'])} 段，"
         f"分辨率 {args.width}x{args.height}，"
         f"文生图{args.steps}步 / 编辑{args.edit_steps}步")
    if args.chars_dir:
        _log(f"角色基准图目录: {args.chars_dir}")

    manifest = {"title": script.get("title", name), "characters": script.get("characters", []),
                "segments": [], "params": {"width": args.width, "height": args.height,
                                           "steps": args.steps, "edit_steps": args.edit_steps}}
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
