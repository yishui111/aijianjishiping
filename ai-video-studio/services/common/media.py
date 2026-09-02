"""ffmpeg/ffprobe 定位、媒体探测、静音检测。"""
from __future__ import annotations

import json as _json
import os
import re
import shutil
import subprocess
from pathlib import Path

try:
    import imageio_ffmpeg

    _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    _FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", _FFMPEG)


def ffmpeg() -> str:
    return FFMPEG_BIN


def ffprobe() -> str | None:
    return os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe")


def run(args: list, timeout: int = 3600) -> subprocess.CompletedProcess:
    cmd = [str(a) for a in args]
    # errors="replace"：Windows 下 ffmpeg 的中文输出无法用 GBK 解码，防止 UnicodeDecodeError 导致 stderr=None
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(cmd)}\n{proc.stderr[-3000:]}")
    return proc


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_VID_RE = re.compile(r"Video:\s*([^,]+)")
_RES_RE = re.compile(r"(\d{2,5})x(\d{2,5})")  # 分辨率形如 3840x2160（编码行含括号逗号时原正则失效）
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_AUD_RE = re.compile(r"Audio:\s*([^,]+)")


def _parse_ffmpeg_info(text: str) -> dict:
    info = {
        "duration_sec": None,
        "resolution": None,
        "fps": None,
        "video_codec": None,
        "audio_codec": None,
        "has_audio": False,
    }
    m = _DUR_RE.search(text)
    if m:
        h, mm, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info["duration_sec"] = h * 3600 + mm * 60 + s
    m = _VID_RE.search(text)
    if m:
        info["video_codec"] = m.group(1).strip()
    rm = _RES_RE.search(text)
    if rm:
        info["resolution"] = f"{rm.group(1)}x{rm.group(2)}"
    m = _FPS_RE.search(text)
    if m:
        info["fps"] = float(m.group(1))
    m = _AUD_RE.search(text)
    if m:
        info["audio_codec"] = m.group(1).strip()
        info["has_audio"] = True
    return info


def probe(path: str | Path) -> dict:
    fp = ffprobe()
    if fp:
        proc = subprocess.run(
            [fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if proc.returncode == 0:
            try:
                data = _json.loads(proc.stdout)
                streams = data.get("streams", [])
                video = next((s for s in streams if s.get("codec_type") == "video"), None)
                audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
                info = {
                    "duration_sec": float(data.get("format", {}).get("duration") or 0) or None,
                    "resolution": f"{video['width']}x{video['height']}" if video else None,
                    "fps": None,
                    "video_codec": (video or {}).get("codec_name"),
                    "audio_codec": (audio or {}).get("codec_name"),
                    "has_audio": audio is not None,
                }
                if video and video.get("avg_frame_rate") and video["avg_frame_rate"] != "0/0":
                    num, _, den = video["avg_frame_rate"].partition("/")
                    try:
                        if float(den):
                            info["fps"] = round(float(num) / float(den), 3)
                    except (ValueError, ZeroDivisionError):
                        pass
                return info
            except Exception:
                pass
    proc = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True, errors="replace")
    return _parse_ffmpeg_info(proc.stderr)


def silencedetect(path: str | Path, threshold_db: float = -35, min_duration: float = 0.5) -> list[dict]:
    proc = run(
        [
            ffmpeg(), "-i", str(path), "-af",
            f"silencedetect=noise={threshold_db}dB:d={min_duration}",
            "-f", "null", "-",
        ]
    )
    out = proc.stderr
    starts = re.findall(r"silence_start:\s*([\d.]+)", out)
    ends = re.findall(r"silence_end:\s*([\d.]+)", out)
    segments = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        segments.append({"start": float(s), "end": float(e) if e else None})
    return segments
