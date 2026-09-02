# -*- coding: utf-8 -*-
r"""
镜头引擎（Ken Burns 锁定镜头）— 纯 ffmpeg，无 AI 依赖，独立自包含
====================================================================
功能：静止图片 + 镜头推拉摇移 → 镜头视频（画面内容不变，不会穿模）。
不依赖 ComfyUI / 出图系统 / 任何 AI 模型，拷到哪都能跑。

镜头类型（7 种）：
  zoom_in / zoom_out / pan_left / pan_right / pan_up / pan_down / rotate

构图目标（camera_target）：
  face      → 聚焦画面上部中央（人物面部区）
  full_body → 全画面（默认）

用法：
  python camera_engine.py <图片> <镜头> [时长] [--target face] [--width 832] [--height 480] [--fps 24]
  例：python camera_engine.py seg_001.png zoom_in 4 --target face
"""

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

# ---- 镜头运动模板（d=帧数, w/h=输出尺寸, fps=帧率）----
CAMERA_MOTIONS = {
    "zoom_in":  {"name": "推近", "vf": "scale=8000:-1,zoompan=z='min(zoom+0.0010,1.30)':d={d}:s={w}x{h}:fps={fps}"},
    "zoom_out": {"name": "拉远", "vf": "scale=8000:-1,zoompan=z='if(lte(zoom,1.0),1.30,max(1.001,zoom-0.0010))':d={d}:s={w}x{h}:fps={fps}"},
    "pan_left": {"name": "左移", "vf": "scale=8000:-1,zoompan=z='1.15':x='(iw-iw/zoom)*on/({d}-1)':d={d}:s={w}x{h}:fps={fps}"},
    "pan_right":{"name": "右移", "vf": "scale=8000:-1,zoompan=z='1.15':x='(iw-iw/zoom)*(1-on/({d}-1))':d={d}:s={w}x{h}:fps={fps}"},
    "pan_up":   {"name": "上移", "vf": "scale=8000:-1,zoompan=z='1.15':y='(ih-ih/zoom)*on/({d}-1)':d={d}:s={w}x{h}:fps={fps}"},
    "pan_down": {"name": "下移", "vf": "scale=8000:-1,zoompan=z='1.15':y='(ih-ih/zoom)*(1-on/({d}-1))':d={d}:s={w}x{h}:fps={fps}"},
    "rotate":   {"name": "微旋转", "vf": "scale=4000:-1,rotate=a='0.02*min(t,4)':ow=iw:oh=ih:c=black@0,zoompan=z='1.06':d={d}:s={w}x{h}:fps={fps}"},
}

# ---- 构图目标：face 聚焦上部（人物面部通常在画面上 1/3 区域）----
TARGET_VF = {
    "face":      "crop=iw*0.75:ih*0.60:iw*0.125:ih*0.05,scale=8000:-1",
    "full_body": "scale=8000:-1",
}

_FFMPEG = Path(__file__).resolve().parent.parent.parent / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
OUT_DIR = Path(__file__).resolve().parent.parent / "output" / "clips"


def camera_shot(image_path, motion="zoom_in", duration=4.0, target="full_body",
                width=832, height=480, fps=24, out_dir=None):
    """生成一个镜头视频。返回输出 mp4 路径。

    motion: zoom_in/zoom_out/pan_left/pan_right/pan_up/pan_down/rotate
    target: face（聚焦面部）/ full_body（全画面）
    """
    import time
    if motion not in CAMERA_MOTIONS:
        raise RuntimeError(f"未知镜头: {motion}，可选 {list(CAMERA_MOTIONS.keys())}")
    if target not in TARGET_VF:
        raise RuntimeError(f"未知构图: {target}，可选 {list(TARGET_VF.keys())}")
    ffmpeg = str(_FFMPEG) if _FFMPEG.exists() else "ffmpeg"
    od = Path(out_dir) if out_dir else OUT_DIR
    od.mkdir(parents=True, exist_ok=True)
    dest = od / f"cam_{motion}_{int(time.time()%100000)}_{uuid.uuid4().hex[:4]}.mp4"
    frames = int(duration * fps)
    # face 聚焦：先裁剪上半部再套镜头；full_body：直接套
    base = TARGET_VF[target]
    vf_tpl = CAMERA_MOTIONS[motion]["vf"].format(d=frames, w=width, h=height, fps=fps)
    vf = f"{base},{vf_tpl}"
    cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(image_path),
           "-vf", vf, "-t", f"{duration:.2f}", "-r", str(fps),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError(f"镜头[{motion}]失败: {r.stderr[-250:]}")
    return dest


def concat_clips(clip_paths, out_path, out_dir=None):
    """按顺序拼接多个镜头视频（画面相同编码，直接 copy 快速无损）"""
    ffmpeg = str(_FFMPEG) if _FFMPEG.exists() else "ffmpeg"
    od = Path(out_dir) if out_dir else Path(out_path).parent
    od.mkdir(parents=True, exist_ok=True)
    list_file = od / f"concat_{uuid.uuid4().hex[:6]}.txt"
    list_file.write_text("".join(f"file '{p}'\n" for p in clip_paths), encoding="utf-8")
    r = subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0",
                        "-i", str(list_file), "-c", "copy", str(out_path)],
                       capture_output=True, text=True)
    list_file.unlink(missing_ok=True)
    if r.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"拼接失败: {r.stderr[-250:]}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="镜头引擎（Ken Burns）")
    ap.add_argument("image", help="图片路径")
    ap.add_argument("motion", nargs="?", default="zoom_in", help="镜头类型")
    ap.add_argument("duration", nargs="?", type=float, default=4.0, help="时长秒")
    ap.add_argument("--target", default="full_body", choices=list(TARGET_VF.keys()))
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--out", default=None, help="输出路径")
    args = ap.parse_args()

    if not Path(args.image).exists():
        print(f"[错误] 图片不存在: {args.image}")
        return
    out = camera_shot(args.image, motion=args.motion, duration=args.duration,
                      target=args.target, width=args.width, height=args.height,
                      fps=args.fps, out_dir=Path(args.out).parent if args.out else None)
    print(f"[OK] 镜头[{CAMERA_MOTIONS[args.motion]['name']}] {args.duration}s "
          f"{args.width}x{args.height} target={args.target}")
    print(f"输出: {out}")


if __name__ == "__main__":
    main()
