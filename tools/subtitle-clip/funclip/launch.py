#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# Copyright FunASR (https://github.com/alibaba-damo-academy/FunClip). All Rights Reserved.
#  MIT License  (https://opensource.org/licenses/MIT)

from http import server
import os
import logging
import argparse
import tempfile
import urllib.parse
import gradio as gr
from funasr import AutoModel
from videoclipper import VideoClipper
import tts_client
from model_selection import create_asr_model as _create_asr_model
from llm.openai_api import openai_call
from llm.qwen_api import call_qwen_model
from llm.g4f_openai_api import g4f_openai_call
from llm.litellm_api import litellm_call
from llm.twelvelabs_api import call_twelvelabs_pegasus
from utils.trans_utils import extract_timestamps
from introduction import top_md_1, top_md_3, top_md_4
from launch_config import build_launch_kwargs


def create_asr_model(model_name, lang, auto_model_cls=AutoModel, **kwargs):
    return _create_asr_model(
        model_name, lang, auto_model_cls=auto_model_cls, **kwargs
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='argparse testing')
    parser.add_argument('--lang', '-l', type=str, default = "zh", help="language mode; selects the Paraformer checkpoint but does not override --model")
    parser.add_argument('--model', '-m', type=str, default="paraformer", choices=["paraformer", "fun-asr-nano", "sensevoice", "moss"], help="ASR model: paraformer, fun-asr-nano, sensevoice, or moss (takes precedence over --lang)")
    parser.add_argument('--moss-backend', choices=["vllm"], default="vllm", help="MOSS runtime backed by an existing vLLM transcription service")
    parser.add_argument('--moss-base-url', default="http://127.0.0.1:8898/v1", help="OpenAI-compatible base URL for the MOSS vLLM service")
    parser.add_argument('--moss-api-key-env', default="MOSS_API_KEY", help="environment variable containing the optional MOSS service API key")
    parser.add_argument('--moss-max-tokens', type=int, default=8192, help="MOSS generation limit for long recordings")
    parser.add_argument('--share', '-s', action='store_true', help="if to establish gradio share link")
    parser.add_argument('--port', '-p', type=int, default=7860, help='port number')
    parser.add_argument('--listen', action='store_true', help="if to listen to all hosts")
    args = parser.parse_args()
    
    moss_api_key = os.environ.get(args.moss_api_key_env) if args.moss_api_key_env else None
    funasr_model = create_asr_model(
        args.model,
        args.lang,
        moss_backend=args.moss_backend,
        moss_base_url=args.moss_base_url,
        moss_api_key=moss_api_key,
        moss_max_tokens=args.moss_max_tokens,
    )
    audio_clipper = VideoClipper(funasr_model)
    audio_clipper.lang = args.lang
    
    def audio_recog(audio_input, sd_switch, hotwords, output_dir):
        return audio_clipper.recog(audio_input, sd_switch, None, hotwords, output_dir=output_dir)

    def video_recog(video_input, sd_switch, hotwords, output_dir):
        return audio_clipper.video_recog(video_input, sd_switch, hotwords, output_dir=output_dir)

    def video_clip(dest_text, video_spk_input, start_ost, end_ost, state, output_dir):
        return audio_clipper.video_clip(
            dest_text, start_ost, end_ost, state, dest_spk=video_spk_input, output_dir=output_dir
            )

    def mix_recog(video_input, audio_input, hotwords, output_dir):
        output_dir = output_dir.strip()
        if not len(output_dir):
            output_dir = None
        else:
            output_dir = os.path.abspath(output_dir)
        audio_state, video_state = None, None
        if video_input is not None:
            res_text, res_srt, video_state = video_recog(
                video_input, 'No', hotwords, output_dir=output_dir)
            return res_text, res_srt, video_state, None, None, None
        if audio_input is not None:
            res_text, res_srt, audio_state = audio_recog(
                audio_input, 'No', hotwords, output_dir=output_dir)
            return res_text, res_srt, None, audio_state, None, None

    def mix_recog_speaker(video_input, audio_input, hotwords, output_dir):
        output_dir = output_dir.strip()
        if not len(output_dir):
            output_dir = None
        else:
            output_dir = os.path.abspath(output_dir)
        audio_state, video_state = None, None
        if video_input is not None:
            res_text, res_srt, video_state = video_recog(
                video_input, 'Yes', hotwords, output_dir=output_dir)
            return res_text, res_srt, video_state, None, None, None
        if audio_input is not None:
            res_text, res_srt, audio_state = audio_recog(
                audio_input, 'Yes', hotwords, output_dir=output_dir)
            return res_text, res_srt, None, audio_state, None, None
    
    # 示例视频：不用 gr.Examples（其 Dataset 组件在 4.44 前后端间传索引类型不一致，
    # 点击会 500），改为下拉选择 + 自研加载函数，带本地缓存
    DEMO_MEDIA = [
        ("为什么要多读书（片段）", "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ClipVideo/%E4%B8%BA%E4%BB%80%E4%B9%88%E8%A6%81%E5%A4%9A%E8%AF%BB%E4%B9%A6%EF%BC%9F%E8%BF%99%E6%98%AF%E6%88%91%E5%90%AC%E8%BF%87%E6%9C%80%E5%A5%BD%E7%9A%84%E7%AD%94%E6%A1%88-%E7%89%87%E6%AE%B5.mp4"),
        ("2022云栖大会（片段2）", "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ClipVideo/2022%E4%BA%91%E6%A0%96%E5%A4%A7%E4%BC%9A_%E7%89%87%E6%AE%B52.mp4"),
        ("使用chatgpt（片段）", "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ClipVideo/%E4%BD%BF%E7%94%A8chatgpt_%E7%89%87%E6%AE%B5.mp4"),
        ("访谈（多说话人示例）", "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ClipVideo/%E8%AE%BF%E8%B0%88.mp4"),
        ("示例音频（鲁肃采访片段）", "https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ClipVideo/%E9%B2%81%E8%82%83%E9%87%87%E8%AE%BF%E7%89%87%E6%AE%B51.wav"),
    ]
    DEMO_LOOKUP = dict(DEMO_MEDIA)

    def load_demo_media(name):
        url = DEMO_LOOKUP.get(name)
        if not url:
            return None, None
        cache_dir = os.path.join(tempfile.gettempdir(), "aicc_demo_media")
        os.makedirs(cache_dir, exist_ok=True)
        local_path = os.path.join(cache_dir, urllib.parse.unquote(url.rsplit("/", 1)[-1]))
        if not os.path.exists(local_path):
            import requests
            logging.warning("下载示例媒体：%s", url)
            r = requests.get(url, timeout=600)
            r.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(r.content)
        if local_path.lower().endswith((".wav", ".mp3", ".flac", ".m4a")):
            return None, local_path
        return local_path, None

    def mix_clip(dest_text, video_spk_input, start_ost, end_ost, video_state, audio_state, output_dir):
        output_dir = output_dir.strip()
        if not len(output_dir):
            output_dir = None
        else:
            output_dir = os.path.abspath(output_dir)
        if video_state is not None:
            clip_video_file, message, clip_srt = audio_clipper.video_clip(
                dest_text, start_ost, end_ost, video_state, dest_spk=video_spk_input, output_dir=output_dir)
            return clip_video_file, None, message, clip_srt
        if audio_state is not None:
            (sr, res_audio), message, clip_srt = audio_clipper.clip(
                dest_text, start_ost, end_ost, audio_state, dest_spk=video_spk_input, output_dir=output_dir)
            return None, (sr, res_audio), message, clip_srt
    
    def video_clip_addsub(dest_text, video_spk_input, start_ost, end_ost, state, output_dir, font_size, font_color):
        output_dir = output_dir.strip()
        if not len(output_dir):
            output_dir = None
        else:
            output_dir = os.path.abspath(output_dir)
        return audio_clipper.video_clip(
            dest_text, start_ost, end_ost, state, 
            font_size=font_size, font_color=font_color, 
            add_sub=True, dest_spk=video_spk_input, output_dir=output_dir
            )
        
    def llm_inference(system_content, user_content, srt_text, model, apikey, video_input=None):
        SUPPORT_LLM_PREFIX = ['litellm', 'qwen', 'gpt', 'g4f', 'moonshot', 'deepseek', 'atlascloud', 'minimax', 'orcarouter', 'pegasus']
        if model.startswith('litellm/'):
            return litellm_call(apikey, model, user_content+'\n'+srt_text, system_content)
        if model.startswith('pegasus'):
            # TwelveLabs Pegasus reasons over the actual video (visuals + audio)
            # rather than the ASR transcript, so it needs the video source.
            if video_input is None:
                logging.error("Pegasus requires a video input; please upload a video first.")
                return "Please upload a video before running Pegasus inference."
            return call_twelvelabs_pegasus(apikey, video_input, model=model, prompt=system_content)
        if model.startswith('qwen'):
            return call_qwen_model(apikey, model, user_content+'\n'+srt_text, system_content)
        if model.startswith('gpt') or model.startswith('moonshot') or model.startswith('deepseek') or model.startswith('atlascloud/') or model.startswith('minimax/') or model.startswith('orcarouter/'):
            return openai_call(apikey, model, user_content+'\n'+srt_text, system_content)
        elif model.startswith('g4f'):
            model = "-".join(model.split('-')[1:])
            return g4f_openai_call(model, user_content+'\n'+srt_text, system_content)
        else:
            logging.error("LLM name error, only {} are supported as LLM name prefix."
                          .format(SUPPORT_LLM_PREFIX))
    
    def AI_clip(LLM_res, dest_text, video_spk_input, start_ost, end_ost, video_state, audio_state, output_dir):
        timestamp_list = extract_timestamps(LLM_res)
        output_dir = output_dir.strip()
        if not len(output_dir):
            output_dir = None
        else:
            output_dir = os.path.abspath(output_dir)
        if video_state is not None:
            clip_video_file, message, clip_srt = audio_clipper.video_clip(
                dest_text, start_ost, end_ost, video_state, 
                dest_spk=video_spk_input, output_dir=output_dir, timestamp_list=timestamp_list, add_sub=False)
            return clip_video_file, None, message, clip_srt
        if audio_state is not None:
            (sr, res_audio), message, clip_srt = audio_clipper.clip(
                dest_text, start_ost, end_ost, audio_state, 
                dest_spk=video_spk_input, output_dir=output_dir, timestamp_list=timestamp_list, add_sub=False)
            return None, (sr, res_audio), message, clip_srt
    
    def AI_clip_subti(LLM_res, dest_text, video_spk_input, start_ost, end_ost, video_state, audio_state, output_dir):
        timestamp_list = extract_timestamps(LLM_res)
        output_dir = output_dir.strip()
        if not len(output_dir):
            output_dir = None
        else:
            output_dir = os.path.abspath(output_dir)
        if video_state is not None:
            clip_video_file, message, clip_srt = audio_clipper.video_clip(
                dest_text, start_ost, end_ost, video_state,
                dest_spk=video_spk_input, output_dir=output_dir, timestamp_list=timestamp_list, add_sub=True)
            return clip_video_file, None, message, clip_srt
        if audio_state is not None:
            (sr, res_audio), message, clip_srt = audio_clipper.clip(
                dest_text, start_ost, end_ost, audio_state,
                dest_spk=video_spk_input, output_dir=output_dir, timestamp_list=timestamp_list, add_sub=True)
            return None, (sr, res_audio), message, clip_srt

    # 新功能输出目录：不填时落到 tools/subtitle-clip/剪辑成片（.gitignore 已屏蔽）
    WORKBENCH_OUT_DIR = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "剪辑成片"))

    def _resolve_output_dir(output_dir):
        output_dir = (output_dir or "").strip()
        if output_dir:
            return os.path.abspath(output_dir)
        return WORKBENCH_OUT_DIR

    def burn_full_video_subtitles(video_state, output_dir, font_size, font_color):
        clip_file, message, clip_srt = audio_clipper.video_burn_subtitles(
            video_state, font_size=font_size, font_color=font_color,
            output_dir=_resolve_output_dir(output_dir))
        return clip_file, message, clip_srt, clip_file

    def clip_per_speaker(video_state, output_dir):
        files, message = audio_clipper.video_clip_per_speaker(
            video_state, output_dir=_resolve_output_dir(output_dir))
        preview = files[0] if files else None
        return files, preview, message

    def download_subtitled_video(video_state, output_dir, font_size, font_color):
        clip_file, message, _ = audio_clipper.video_burn_subtitles(
            video_state, font_size=font_size, font_color=font_color,
            output_dir=_resolve_output_dir(output_dir))
        return clip_file, message

    def download_per_speaker_clips(video_state, output_dir):
        files, message = audio_clipper.video_clip_per_speaker(
            video_state, output_dir=_resolve_output_dir(output_dir))
        return files, message

    def refresh_dub_voices():
        roles, err = tts_client.list_ready_roles()
        if err:
            return gr.update(choices=[]), err
        if not roles:
            return gr.update(choices=[]), "语音服务没有就绪角色（把音色模型放入 wenziqudong 的 models 目录后重试）"
        return gr.update(choices=roles, value=roles[0]), "可用配音角色：" + "、".join(roles)

    def dub_video_from_subtitles(video_state, voice_id, dub_speed, output_dir):
        clip_file, message = audio_clipper.video_dub_subtitles(
            video_state, voice=voice_id, speed=float(dub_speed or 1.0),
            output_dir=_resolve_output_dir(output_dir))
        return clip_file, message
    
    # gradio interface
    theme = gr.Theme.load("funclip/utils/theme.json")
    with gr.Blocks(theme=theme, title='AI 字幕剪辑工作台') as funclip_service:
        gr.Markdown(top_md_1)
        # gr.Markdown(top_md_2)
        gr.Markdown(top_md_3)
        gr.Markdown(top_md_4)
        video_state, audio_state = gr.State(), gr.State()
        with gr.Row():
            with gr.Column():
                with gr.Row():
                    video_input = gr.Video(label="视频输入 | Video Input")
                    audio_input = gr.Audio(label="音频输入 | Audio Input")
                with gr.Column():
                    demo_pick = gr.Dropdown(
                        choices=[name for name, _ in DEMO_MEDIA],
                        label="🎬 示例视频/音频 | Demo（选中即自动加载到对应输入）",
                        info="多说话人示例请配合「识别+区分说话人」使用",
                        value=None)
                    with gr.Column():
                        # with gr.Row():
                            # video_sd_switch = gr.Radio(["No", "Yes"], label="👥区分说话人 Get Speakers", value='No')
                        hotwords_input = gr.Textbox(label="🚒 热词 | Hotwords(可以为空，多个热词使用空格分隔，仅支持中文热词)")
                        output_dir = gr.Textbox(label="📁 文件输出路径 | File Output Dir (可以为空，Linux, mac系统可以稳定使用)", value=" ")
                        with gr.Row():
                            recog_button = gr.Button("👂 识别 | ASR", variant="primary")
                            recog_button2 = gr.Button("👂👫 识别+区分说话人 | ASR+SD")
                video_text_output = gr.Textbox(label="✏️ 识别结果 | Recognition Result")
                video_srt_output = gr.Textbox(label="📖 SRT字幕内容 | RST Subtitles")
                with gr.Row():
                    dl_subtitled_button = gr.Button("⬇️ 下载带字幕视频 | Download Subtitled Video")
                    dl_spk_clips_button = gr.Button("⬇️ 下载分说话人片段 | Download Per-speaker Clips")
                with gr.Row():
                    dl_subtitled_file = gr.File(label="🎬 带字幕视频（原片+烧录字幕） | Subtitled Video", interactive=False)
                    dl_spk_files = gr.Files(label="👥 分说话人片段（每人一个） | Per-speaker Clips", interactive=False)
                with gr.Row():
                    voice_refresh_button = gr.Button("🔄 刷新配音角色 | Refresh Dub Voices")
                    voice_dropdown = gr.Dropdown(label="🗣 配音角色 | Dub Voice（文字驱动）", choices=[], allow_custom_value=True)
                    dub_speed = gr.Slider(minimum=0.6, maximum=1.65, value=1.0, step=0.05, label="🗣 配音语速 | Dub Speed")
                dub_button = gr.Button("🗣 字幕配音替换原声 | Dub Video from Subtitles", variant="primary")
            with gr.Column():
                with gr.Tab("🧠 LLM智能裁剪 | LLM Clipping"):
                    with gr.Column():
                        prompt_head = gr.Textbox(label="Prompt System (按需更改，最好不要变动主体和要求)", value=("你是一个视频srt字幕分析剪辑器，输入视频的srt字幕，"
                                "分析其中的精彩且尽可能连续的片段并裁剪出来，输出四条以内的片段，将片段中在时间上连续的多个句子及它们的时间戳合并为一条，"
                                "注意确保文字与时间戳的正确匹配。输出需严格按照如下格式：1. [开始时间-结束时间] 文本，注意其中的连接符是“-”"))
                        prompt_head2 = gr.Textbox(label="Prompt User（不需要修改，会自动拼接左下角的srt字幕）", value=("这是待裁剪的视频srt字幕："))
                        with gr.Column():
                            with gr.Row():
                                llm_model = gr.Dropdown(
                                    choices=[
                                        "deepseek-chat",
                                        "qwen-plus",
                                             "gpt-3.5-turbo",
                                             "gpt-3.5-turbo-0125",
                                             "gpt-4-turbo",
                                             "g4f-gpt-3.5-turbo",
                                             "litellm/openai/gpt-4o",
                                             "litellm/anthropic/claude-sonnet-4-6",
                                             "atlascloud/qwen/qwen3.5-flash",
                                             "atlascloud/deepseek-ai/deepseek-v4-pro",
                                             "minimax/MiniMax-M3",
                                             "minimax/MiniMax-M2.7",
                                             "minimax/MiniMax-M2.7-highspeed",
                                             "orcarouter/auto",
                                             "orcarouter/fusion",
                                             "orcarouter/fusion-flash",
                                             "orcarouter/fusion-mini",
                                             "pegasus1.5"],
                                    value="deepseek-chat",
                                    label="LLM Model Name",
                                    allow_custom_value=True)
                                apikey_input = gr.Textbox(label="APIKEY")
                            llm_button =  gr.Button("LLM推理 | LLM Inference（首先进行识别，非g4f需配置对应apikey）", variant="primary")
                        llm_result = gr.Textbox(label="LLM Clipper Result")
                        with gr.Row():
                            llm_clip_button = gr.Button("🧠 LLM智能裁剪 | AI Clip", variant="primary")
                            llm_clip_subti_button = gr.Button("🧠 LLM智能裁剪+字幕 | AI Clip+Subtitles")
                with gr.Tab("✂️ 根据文本/说话人裁剪 | Text/Speaker Clipping"):
                    video_text_input = gr.Textbox(label="✏️ 待裁剪文本 | Text to Clip (多段文本使用'#'连接)")
                    video_spk_input = gr.Textbox(label="✏️ 待裁剪说话人 | Speaker to Clip (多个说话人使用'#'连接)")
                    with gr.Row():
                        clip_button = gr.Button("✂️ 裁剪 | Clip", variant="primary")
                        clip_subti_button = gr.Button("✂️ 裁剪+字幕 | Clip+Subtitles")
                    with gr.Row():
                        video_start_ost = gr.Slider(minimum=-500, maximum=1000, value=0, step=50, label="⏪ 开始位置偏移 | Start Offset (ms)")
                        video_end_ost = gr.Slider(minimum=-500, maximum=1000, value=100, step=50, label="⏩ 结束位置偏移 | End Offset (ms)")
                    with gr.Row():
                        burn_sub_full_button = gr.Button("🎬 整片加字幕 | Burn Subtitles (Full Video)")
                        spk_clip_button = gr.Button("👥 按说话人分开剪 | Clip per Speaker")
                    with gr.Row():
                        font_size = gr.Slider(minimum=10, maximum=100, value=32, step=2, label="🔠 字幕字体大小 | Subtitle Font Size")
                        font_color = gr.Radio(["black", "white", "green", "red"], label="🌈 字幕颜色 | Subtitle Color", value='white')
                        # font = gr.Radio(["黑体", "Alibaba Sans"], label="字体 Font")
                video_output = gr.Video(label="裁剪结果 | Video Clipped")
                audio_output = gr.Audio(label="裁剪结果 | Audio Clipped")
                clip_message = gr.Textbox(label="⚠️ 裁剪信息 | Clipping Log")
                srt_clipped = gr.Textbox(label="📖 裁剪部分SRT字幕内容 | Clipped RST Subtitles")            
                
        demo_pick.change(load_demo_media,
                         inputs=[demo_pick],
                         outputs=[video_input, audio_input])
        recog_button.click(mix_recog,
                            inputs=[video_input,
                                    audio_input,
                                    hotwords_input,
                                    output_dir,
                                    ],
                            outputs=[video_text_output, video_srt_output, video_state, audio_state, dl_subtitled_file, dl_spk_files])
        recog_button2.click(mix_recog_speaker,
                            inputs=[video_input,
                                    audio_input,
                                    hotwords_input,
                                    output_dir,
                                    ],
                            outputs=[video_text_output, video_srt_output, video_state, audio_state, dl_subtitled_file, dl_spk_files])
        clip_button.click(mix_clip, 
                           inputs=[video_text_input, 
                                   video_spk_input, 
                                   video_start_ost, 
                                   video_end_ost, 
                                   video_state, 
                                   audio_state, 
                                   output_dir
                                   ],
                           outputs=[video_output, audio_output, clip_message, srt_clipped])
        clip_subti_button.click(video_clip_addsub,
                           inputs=[video_text_input,
                                   video_spk_input,
                                   video_start_ost,
                                   video_end_ost,
                                   video_state,
                                   output_dir,
                                   font_size,
                                   font_color,
                                   ],
                           outputs=[video_output, clip_message, srt_clipped])
        burn_sub_full_button.click(burn_full_video_subtitles,
                           inputs=[video_state,
                                   output_dir,
                                   font_size,
                                   font_color,
                                   ],
                           outputs=[video_output, clip_message, srt_clipped, dl_subtitled_file])
        spk_clip_button.click(clip_per_speaker,
                           inputs=[video_state,
                                   output_dir,
                                   ],
                           outputs=[dl_spk_files, video_output, clip_message])
        dl_subtitled_button.click(download_subtitled_video,
                           inputs=[video_state,
                                   output_dir,
                                   font_size,
                                   font_color,
                                   ],
                           outputs=[dl_subtitled_file, clip_message])
        dl_spk_clips_button.click(download_per_speaker_clips,
                           inputs=[video_state,
                                   output_dir,
                                   ],
                           outputs=[dl_spk_files, clip_message])
        voice_refresh_button.click(refresh_dub_voices,
                           inputs=[],
                           outputs=[voice_dropdown, clip_message])
        dub_button.click(dub_video_from_subtitles,
                           inputs=[video_state,
                                   voice_dropdown,
                                   dub_speed,
                                   output_dir,
                                   ],
                           outputs=[video_output, clip_message])
        llm_button.click(llm_inference,
                         inputs=[prompt_head, prompt_head2, video_srt_output, llm_model, apikey_input, video_input],
                         outputs=[llm_result])
        llm_clip_button.click(AI_clip, 
                           inputs=[llm_result,
                                   video_text_input, 
                                   video_spk_input, 
                                   video_start_ost, 
                                   video_end_ost, 
                                   video_state, 
                                   audio_state, 
                                   output_dir,
                                   ],
                           outputs=[video_output, audio_output, clip_message, srt_clipped])
        llm_clip_subti_button.click(AI_clip_subti, 
                           inputs=[llm_result,
                                   video_text_input, 
                                   video_spk_input, 
                                   video_start_ost, 
                                   video_end_ost, 
                                   video_state, 
                                   audio_state, 
                                   output_dir,
                                   ],
                           outputs=[video_output, audio_output, clip_message, srt_clipped])
    
    funclip_service.launch(
        **build_launch_kwargs(share=args.share, port=args.port, listen=args.listen)
    )
