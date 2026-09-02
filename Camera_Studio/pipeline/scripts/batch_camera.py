# -*- coding: utf-8 -*-
r"""
镜头批量控制 — JSON 清单 → 逐段镜头视频 + 拼接成片
=====================================================
输入：镜头清单 JSON（与出图系统 manifest.json 兼容），格式：
{
  "title": "任务名",
  "segments": [
    {"id":1, "image":"seg_001.png", "camera":"zoom_in", "camera_target":"face",
     "duration":4, "line":"...", "scene":"...", "characters":["c1"]},
    ...
  ]
}
  - image：图片路径（必填，绝对路径或相对本 JSON 文件所在目录）
  - camera：镜头类型（zoom_in/zoom_out/pan_left/pan_right/pan_up/pan_down/rotate）
  - camera_target：face（聚焦面部）/ full_body（全画面，默认）
  - duration：该段秒数

输出：
  output/clips/<任务名>/     每段一个镜头视频（cam_<id>.mp4）
  output/final/<任务名>_final.mp4   拼接成片
  output/final/<任务名>_manifest.json  含每段视频路径的清单

用法：
  python batch_camera.py 清单.json [--width 832] [--height 480] [--fps 24] [--name 任务名]
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPTS_DIR.parent
for _p in (_PIPELINE_DIR, _SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from camera_engine import camera_shot, concat_clips, CAMERA_MOTIONS

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

OUT_CLIPS = _PIPELINE_DIR / "output" / "clips"
OUT_FINAL = _PIPELINE_DIR / "output" / "final"


def _resolve_image(img_ref, script_dir):
    """图片引用：绝对路径直接用；相对路径相对剧本 JSON 所在目录解析"""
    p = Path(img_ref)
    if p.is_absolute():
        return p
    return script_dir / p


def main():
    ap = argparse.ArgumentParser(description="镜头批量控制（JSON → 镜头视频）")
    ap.add_argument("manifest", help="镜头清单 JSON 路径")
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--name", default=None, help="输出任务名（默认用清单文件名）")
    ap.add_argument("--no-concat", action="store_true", help="只生成各段镜头，不拼接成片")
    args = ap.parse_args()

    mf_path = Path(args.manifest)
    if not mf_path.exists():
        print(f"[错误] 清单文件不存在: {mf_path}")
        return
    data = json.loads(mf_path.read_text(encoding="utf-8-sig"))
    segments = data.get("segments") or data  # 兼容 [{...}] 直接数组
    name = args.name or mf_path.stem

    clips_dir = OUT_CLIPS / name
    clips_dir.mkdir(parents=True, exist_ok=True)
    final_dir = OUT_FINAL
    final_dir.mkdir(parents=True, exist_ok=True)
    script_dir = mf_path.parent

    print(f"[镜头] 任务[{name}] 共 {len(segments)} 段, {args.width}x{args.height}@{args.fps}fps")
    clip_paths = []
    manifest_out = []
    t_all = time.time()

    for i, seg in enumerate(segments):
        img = _resolve_image(seg.get("image", ""), script_dir)
        if not img.exists():
            print(f"[跳过] 段{seg.get('id', i+1)} 图片不存在: {img}")
            continue
        cam = seg.get("camera", "zoom_in")
        if cam not in CAMERA_MOTIONS:
            cam = "zoom_in"
        target = seg.get("camera_target", "full_body")
        dur = float(seg.get("duration", 4))
        n = int(seg.get("id", i + 1))
        t0 = time.time()
        try:
            vid = camera_shot(str(img), motion=cam, duration=dur, target=target,
                              width=args.width, height=args.height, fps=args.fps,
                              out_dir=clips_dir)
            # 重命名成规范 cam_<id>.mp4
            final_clip = clips_dir / f"cam_{n:03d}.mp4"
            vid.replace(final_clip)
            clip_paths.append(str(final_clip))
            elapsed = time.time() - t0
            print(f"[{i+1}/{len(segments)}] 段{n} [{cam}/{target}] {dur}s 用时{elapsed:.1f}s")
            manifest_out.append({
                "id": n, "start": seg.get("start", 0), "duration": dur,
                "scene": seg.get("scene", ""), "line": seg.get("line", ""),
                "camera": cam, "camera_target": target,
                "clip": str(final_clip),
            })
        except Exception as e:
            print(f"[失败] 段{n}: {e}")

    if not clip_paths:
        print("[错误] 没有可生成的镜头")
        return

    # 输出清单
    out_manifest = final_dir / f"{name}_manifest.json"
    out_manifest.write_text(json.dumps(
        {"title": data.get("title", name), "segments": manifest_out,
         "params": {"width": args.width, "height": args.height, "fps": args.fps}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] 各段镜头清单: {out_manifest}")

    # 拼接成片
    if not args.no_concat:
        final = final_dir / f"{name}_final.mp4"
        concat_clips(clip_paths, final)
        total = sum(s.get("duration", 0) for s in manifest_out)
        print(f"[OK] 成片: {final} 总时长 {total:.1f}s")
    print(f"[完成] 总耗时 {time.time()-t_all:.0f}s")


if __name__ == "__main__":
    main()
