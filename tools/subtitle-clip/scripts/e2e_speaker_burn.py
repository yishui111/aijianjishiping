# -*- coding: utf-8 -*-
"""端到端实测：识别+区分说话人 → 按说话人分开剪 / 整片烧字幕。

用法（用项目 venv 跑，沙箱环境先 `env -u PYTHONPATH`）:
  runtime/Scripts/python.exe scripts/e2e_speaker_burn.py <视频路径> <输出目录> <stage>

stage:
  repro  复现旧路径：按 spk0 裁剪（改代码前后各跑一次可对比格式）
  new    新功能：按说话人分开剪 + 整片烧字幕
  recog  只做识别并缓存 state（调试用）

输出统一写到指定输出目录；模型走本地 modelscope-cache，全程离线。
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MODELSCOPE_CACHE", str(ROOT / "modelscope-cache"))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, str(ROOT / "funclip"))

MODEL_SPECS = {
    "model": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "vad_model": "damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "punc_model": "damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
    "spk_model": "damo/speech_campplus_sv_zh-cn_16k-common",
    "disable_update": True,
}


def get_clipper():
    from funasr import AutoModel
    from videoclipper import VideoClipper
    m = AutoModel(**MODEL_SPECS)
    c = VideoClipper(m)
    c.lang = "zh"
    return c


def probe(path):
    """打印成片关键格式信息，用于人眼核对 h264/aac/时长。"""
    import subprocess
    ffprobe = None
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    ffprobe = Path(ffmpeg).parent / "ffprobe.exe"
    if not ffprobe.exists():
        # imageio-ffmpeg 不带 ffprobe，退回直接读流信息
        print(f"[probe] {path} size={Path(path).stat().st_size}")
        return
    r = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "format=format_name,duration,size:stream=index,codec_name,codec_type,pix_fmt,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    info = json.loads(r.stdout or "{}")
    streams = {s.get("codec_type"): s.get("codec_name") for s in info.get("streams", [])}
    fmt = info.get("format", {})
    print(f"[probe] {Path(path).name}: video={streams.get('video')} audio={streams.get('audio')} "
          f"dur={fmt.get('duration')}s size={fmt.get('size')}")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    video = Path(sys.argv[1])
    outdir = Path(sys.argv[2])
    stage = sys.argv[3]
    outdir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    clipper = get_clipper()
    print(f"[load] model ready in {time.time()-t0:.1f}s")

    t0 = time.time()
    res_text, res_srt, state = clipper.video_recog(str(video), "Yes", "", output_dir=str(outdir))
    print(f"[recog] {time.time()-t0:.1f}s, sentences={len(state['sentences'])}, "
          f"sd={len(state.get('sd_sentences') or [])}")
    spk_ids = []
    for d in state.get("sd_sentences") or []:
        if d.get("spk") not in spk_ids:
            spk_ids.append(d.get("spk"))
    print(f"[recog] speakers={spk_ids}")

    if stage == "recog":
        return

    if stage == "repro":
        f, msg, srt = clipper.video_clip("", 0, 0, state, dest_spk="spk0", output_dir=str(outdir))
        print(f"[repro] {msg[:120]}")
        print(f"[repro] file={f}")
        probe(f)
        return

    if stage == "new":
        files, msg = clipper.video_clip_per_speaker(state, output_dir=str(outdir))
        print(f"[per-spk] {msg}")
        for f in files:
            probe(f)
        f, msg, srt = clipper.video_burn_subtitles(state, output_dir=str(outdir))
        print(f"[burn] file={f}")
        probe(f)
        return

    if stage == "dub":
        # 真服务：TTS_API_BASE 未设时走默认 127.0.0.1:18062；角色用 TTS_VOICE 指定
        voice = os.environ.get("TTS_VOICE", "azhong")
        f, msg = clipper.video_dub_subtitles(
            state, voice=voice, speed=1.0, output_dir=str(outdir),
            tts_base=os.environ.get("TTS_API_BASE"))
        print(f"[dub] {msg}")
        if f:
            probe(f)
        return

    if stage == "dubstub":
        # 离线链路验证：进程内起一个假 TTS 服务（固定 1 秒正弦 mp3），打满全部台词
        import threading
        import subprocess
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import imageio_ffmpeg
        import json as _json

        tone = os.path.join(outdir, "_stub_tone.mp3")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=1", "-b:a", "64k",
                        "-y", tone], check=True)
        tone_bytes = open(tone, "rb").read()

        class StubTTS(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                body = _json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    self._json({"status": "ok", "ready_roles": ["stub"]})
                elif self.path == "/models":
                    self._json({"models": [{"name": "stub", "ready": True}]})
                else:
                    self._json({"detail": "not found"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(tone_bytes)))
                self.end_headers()
                self.wfile.write(tone_bytes)

        srv = HTTPServer(("127.0.0.1", 18099), StubTTS)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        f, msg = clipper.video_dub_subtitles(
            state, voice="stub", speed=1.0, output_dir=str(outdir),
            tts_base="http://127.0.0.1:18099")
        print(f"[dubstub] {msg}")
        if f:
            probe(f)
        return

    print(f"unknown stage: {stage}")
    sys.exit(1)


if __name__ == "__main__":
    main()
