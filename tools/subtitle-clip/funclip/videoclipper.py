#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# Copyright FunASR (https://github.com/alibaba-damo-academy/FunClip). All Rights Reserved.
#  MIT License  (https://opensource.org/licenses/MIT)

import re
import os
import sys
import copy
import librosa
import logging
import argparse
import numpy as np
import soundfile as sf
from moviepy.editor import *
import moviepy.editor as mpy
from moviepy.video.tools.subtitles import SubtitlesClip
from moviepy.editor import VideoFileClip, concatenate_videoclips
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
try:
    from .subtitle_renderer import make_text_clip
except ImportError:
    from subtitle_renderer import make_text_clip
from utils.subtitle_utils import generate_srt, generate_srt_clip, str2list
from utils.argparse_tools import ArgumentParser, get_commandline_args
from utils.trans_utils import pre_proc, proc, write_state, load_state, proc_spk, convert_pcm_to_float


MAX_SUBTITLE_DURATION_MS = 8000
MAX_SUBTITLE_TOKENS = 30
SENSEVOICE_TAG_RE = re.compile(r"<\|[^|>]+\|>")
MOSS_SEGMENT_MARKER_RE = re.compile(r"\[\d+(?:\.\d+)?\]\[S\d+\]")
MOSS_FINAL_TIMESTAMP_RE = re.compile(r"\[\d+(?:\.\d+)?\]\s*$")


def _write_standard_mp4(clip, path, temp_audiofile):
    # 出片参数全部显式指定（libx264/aac/yuv420p/faststart）：
    # 不依赖 moviepy 对扩展名的默认推断，保证各播放器兼容
    clip.write_videofile(
        path,
        codec="libx264",
        audio=True,
        audio_codec="aac",
        fps=getattr(clip, "fps", None) or 30,
        temp_audiofile=temp_audiofile,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )


def _is_valid_timestamp(timestamp):
    return (
        isinstance(timestamp, list)
        and len(timestamp) > 0
        and timestamp[0] is not None
        and timestamp[-1] is not None
    )


def _clean_recognition_text(text):
    if text is None:
        return ""
    text = SENSEVOICE_TAG_RE.sub("", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("“”")


def _split_long_sentence(sent):
    timestamp = sent.get("timestamp")
    if not _is_valid_timestamp(timestamp):
        return []

    cleaned_text = _clean_recognition_text(sent.get("text"))
    normalized = dict(sent)
    normalized["text"] = cleaned_text
    normalized["timestamp"] = timestamp

    tokens = str2list(cleaned_text)
    if len(timestamp) <= 1 or len(tokens) != len(timestamp):
        return [normalized]

    chunks = []
    start = 0
    for idx in range(len(tokens)):
        duration = timestamp[idx][1] - timestamp[start][0]
        token_count = idx - start + 1
        should_split = (
            idx > start
            and (
                duration >= MAX_SUBTITLE_DURATION_MS
                or token_count >= MAX_SUBTITLE_TOKENS
            )
        )
        if should_split:
            chunk = dict(normalized)
            # 注意：text 必须是拼接后的字符串（曾误存 tokens 列表，
            # 下游 str() 会写成 "['王', '呃', ...]" 导致关键词/剧本匹配失效）
            chunk["text"] = "".join(tokens[start : idx + 1])
            chunk["timestamp"] = timestamp[start : idx + 1]
            # 切分后各片段必须用自己时间戳的起止，否则继承父句区间会重复剪同一段
            chunk["start"] = timestamp[start][0]
            chunk["end"] = timestamp[idx][1]
            chunks.append(chunk)
            start = idx + 1

    if not chunks:
        return [normalized]

    if start < len(tokens):
        chunk = dict(normalized)
        chunk["text"] = "".join(tokens[start:])
        chunk["timestamp"] = timestamp[start:]
        chunk["start"] = timestamp[start][0]
        chunk["end"] = timestamp[-1][1]
        chunks.append(chunk)

    return chunks


def _normalize_recognition_result(result):
    text = _clean_recognition_text(
        result.get("text") or result.get("text_tn") or result.get("raw_text") or ""
    )
    raw_value = result.get("raw_text") or result.get("text_tn") or text
    raw_text = _clean_recognition_text(raw_value)
    if isinstance(raw_value, str) and MOSS_SEGMENT_MARKER_RE.search(raw_value):
        if not MOSS_FINAL_TIMESTAMP_RE.search(raw_value):
            raise RuntimeError(
                "truncated MOSS transcript: increase --moss-max-tokens and retry"
            )
        raw_text = text
    timestamp = result.get("timestamp") or result.get("timestamps") or []

    sentence_info = []
    for sent in result.get("sentence_info") or []:
        if _is_valid_timestamp(sent.get("timestamp")):
            sentence_info.extend(_split_long_sentence(sent))

    if not sentence_info and text and _is_valid_timestamp(timestamp):
        sentence_info = _split_long_sentence({"text": text, "timestamp": timestamp})

    return text, raw_text, timestamp, sentence_info


class VideoClipper():
    def __init__(self, funasr_model):
        logging.warning("Initializing VideoClipper.")
        self.funasr_model = funasr_model
        self.GLOBAL_COUNT = 0

    def recog(self, audio_input, sd_switch='no', state=None, hotwords="", output_dir=None):
        if state is None:
            state = {}
        sr, data = audio_input

        # Convert to float64 consistently (includes data type checking)
        data = convert_pcm_to_float(data)

        # assert sr == 16000, "16kHz sample rate required, {} given.".format(sr)
        if sr != 16000: # resample with librosa
            data = librosa.resample(data, orig_sr=sr, target_sr=16000)
        if len(data.shape) == 2:  # multi-channel wav input
            logging.warning("Input wav shape: {}, only first channel reserved.".format(data.shape))
            data = data[:,0]
        state['audio_input'] = (sr, data)
        if sd_switch == 'Yes':
            rec_result = self.funasr_model.generate(data, 
                                                    return_spk_res=True,
                                                    return_raw_text=True, 
                                                    is_final=True,
                                                    output_dir=output_dir, 
                                                    hotword=hotwords, 
                                                    pred_timestamp=self.lang=='en',
                                                    en_post_proc=self.lang=='en',
                                                    cache={})
            res_text, raw_text, timestamp, sentence_info = _normalize_recognition_result(rec_result[0])
            res_srt = generate_srt(sentence_info)
            state['sd_sentences'] = sentence_info
        else:
            rec_result = self.funasr_model.generate(data, 
                                                    return_spk_res=False, 
                                                    sentence_timestamp=True, 
                                                    return_raw_text=True, 
                                                    is_final=True, 
                                                    hotword=hotwords,
                                                    output_dir=output_dir,
                                                    pred_timestamp=self.lang=='en',
                                                    en_post_proc=self.lang=='en',
                                                    cache={})
            res_text, raw_text, timestamp, sentence_info = _normalize_recognition_result(rec_result[0])
            res_srt = generate_srt(sentence_info)
        state['recog_res_raw'] = raw_text
        state['timestamp'] = timestamp
        state['sentences'] = sentence_info
        return res_text, res_srt, state

    def clip(self, dest_text, start_ost, end_ost, state, dest_spk=None, output_dir=None, timestamp_list=None):
        # get from state
        audio_input = state['audio_input']
        recog_res_raw = state['recog_res_raw']
        timestamp = state['timestamp']
        sentences = state['sentences']
        sr, data = audio_input
        data = data.astype(np.float64)

        log_append = ""
        if timestamp_list is None:
            all_ts = []
            warning_messages = []
            if dest_spk is None or dest_spk == '' or 'sd_sentences' not in state:
                for text_index, _dest_text in enumerate(dest_text.split('#'), start=1):
                    offset_match = None
                    if '[' in _dest_text:
                        offset_match = re.search(r'\[(\d+),\s*(\d+)\]', _dest_text)
                        if offset_match:
                            offset_b, offset_e = map(int, offset_match.groups())
                        else:
                            offset_b, offset_e = 0, 0
                            warning_messages.append(
                                "(Bracket detected in dest_text but offset time matching failed)"
                            )
                        _dest_text = _dest_text[:_dest_text.find('[')]
                    else:
                        offset_b, offset_e = 0, 0
                    _dest_text = pre_proc(_dest_text)
                    ts = proc(recog_res_raw, timestamp, _dest_text)
                    for _ts in ts: all_ts.append([_ts[0]+offset_b*16, _ts[1]+offset_e*16])
                    if len(ts) > 1 and offset_match:
                        warning_messages.append(
                            "(offsets detected but No.{} sub-sentence matched to {} "
                            "periods in audio, offsets are applied to all periods)".format(
                                text_index, len(ts)
                            )
                        )
            else:
                for _dest_spk in dest_spk.split('#'):
                    ts = proc_spk(_dest_spk, state['sd_sentences'])
                    for _ts in ts: all_ts.append(_ts)
            if warning_messages:
                log_append = " " + " ".join(warning_messages)
        else:
            all_ts = timestamp_list
        ts = all_ts
        # ts.sort()
        srt_index = 0
        clip_srt = ""
        if len(ts):
            start, end = ts[0]
            start = min(max(0, start+start_ost*16), len(data))
            end = min(max(0, end+end_ost*16), len(data))
            res_audio = data[start:end]
            start_end_info = "from {} to {}".format(start/16000, end/16000)
            srt_clip, _, srt_index = generate_srt_clip(sentences, start/16000.0, end/16000.0, begin_index=srt_index)
            clip_srt += srt_clip
            for _ts in ts[1:]:  # multiple sentence input or multiple output matched
                start, end = _ts
                start = min(max(0, start+start_ost*16), len(data))
                end = min(max(0, end+end_ost*16), len(data))
                start_end_info += ", from {} to {}".format(start, end)
                res_audio = np.concatenate([res_audio, data[start:end]], -1)
                srt_clip, _, srt_index = generate_srt_clip(sentences, start/16000.0, end/16000.0, begin_index=srt_index-1)
                clip_srt += srt_clip
        if len(ts):
            message = "{} periods found in the speech: ".format(len(ts)) + start_end_info + log_append
        else:
            message = "No period found in the speech, return raw speech. You may check the recognition result and try other destination text." + log_append
            res_audio = data
        return (sr, res_audio), message, clip_srt

    def video_recog(self, video_filename, sd_switch='no', hotwords="", output_dir=None):
        video = mpy.VideoFileClip(video_filename)
        # Extract the base name, add '_clip.mp4', and 'wav'
        if output_dir is not None:
            os.makedirs(output_dir, exist_ok=True)
            _, base_name = os.path.split(video_filename)
            base_name, _ = os.path.splitext(base_name)
            clip_video_file = base_name + '_clip.mp4'
            audio_file = base_name + '.wav'
            audio_file = os.path.join(output_dir, audio_file)
        else:
            base_name, _ = os.path.splitext(video_filename)
            clip_video_file = base_name + '_clip.mp4'
            audio_file = base_name + '.wav'

        if video.audio is None:
            logging.error("No audio information found.")
            sys.exit(1)

        video.audio.write_audiofile(audio_file)
        wav = librosa.load(audio_file, sr=16000)[0]
        # delete the audio file after processing
        if os.path.exists(audio_file):
            os.remove(audio_file)
        state = {
            'video_filename': video_filename,
            'clip_video_file': clip_video_file,
            'video': video,
        }
        # res_text, res_srt = self.recog((16000, wav), state)
        return self.recog((16000, wav), sd_switch, state, hotwords, output_dir)

    def video_clip(self, 
                   dest_text, 
                   start_ost, 
                   end_ost, 
                   state, 
                   font_size=32, 
                   font_color='white', 
                   add_sub=False, 
                   dest_spk=None, 
                   output_dir=None,
                   timestamp_list=None):
        # get from state
        recog_res_raw = state['recog_res_raw']
        timestamp = state['timestamp']
        sentences = state['sentences']
        video = state['video']
        clip_video_file = state['clip_video_file']
        video_filename = state['video_filename']
        
        log_append = ""
        if timestamp_list is None:
            all_ts = []
            warning_messages = []
            if dest_spk is None or dest_spk == '' or 'sd_sentences' not in state:
                for text_index, _dest_text in enumerate(dest_text.split('#'), start=1):
                    offset_match = None
                    if '[' in _dest_text:
                        offset_match = re.search(r'\[(\d+),\s*(\d+)\]', _dest_text)
                        if offset_match:
                            offset_b, offset_e = map(int, offset_match.groups())
                        else:
                            offset_b, offset_e = 0, 0
                            warning_messages.append(
                                "(Bracket detected in dest_text but offset time matching failed)"
                            )
                        _dest_text = _dest_text[:_dest_text.find('[')]
                    else:
                        offset_b, offset_e = 0, 0
                    # import pdb; pdb.set_trace()
                    _dest_text = pre_proc(_dest_text)
                    ts = proc(recog_res_raw, timestamp, _dest_text)
                    for _ts in ts: all_ts.append([_ts[0]+offset_b*16, _ts[1]+offset_e*16])
                    if len(ts) > 1 and offset_match:
                        warning_messages.append(
                            "(offsets detected but No.{} sub-sentence matched to {} "
                            "periods in audio, offsets are applied to all periods)".format(
                                text_index, len(ts)
                            )
                        )
            else:
                for _dest_spk in dest_spk.split('#'):
                    ts = proc_spk(_dest_spk, state['sd_sentences'])
                    for _ts in ts: all_ts.append(_ts)
            if warning_messages:
                log_append = " " + " ".join(warning_messages)
        else:  # AI clip pass timestamp as input directly
            all_ts = [[i[0]*16.0, i[1]*16.0] for i in timestamp_list]
        
        srt_index = 0
        time_acc_ost = 0.0
        ts = all_ts
        # ts.sort()
        clip_srt = ""
        if len(ts):
            if self.lang == 'en' and isinstance(sentences, str):
                sentences = sentences.split()
            start, end = ts[0][0] / 16000, ts[0][1] / 16000
            srt_clip, subs, srt_index = generate_srt_clip(sentences, start, end, begin_index=srt_index, time_acc_ost=time_acc_ost)
            start, end = start+start_ost/1000.0, end+end_ost/1000.0
            start = max(0.0, start)
            end = min(end, video.duration - 0.01) if video.duration and video.duration > 0 else end
            video_clip = video.subclip(start, end)
            start_end_info = "from {} to {}".format(start, end)
            clip_srt += srt_clip
            if add_sub:
                generator = lambda txt: make_text_clip(
                    txt, font_size=font_size, color=font_color
                )
                subtitles = SubtitlesClip(subs, generator)
                video_clip = CompositeVideoClip([video_clip, subtitles.set_pos(('center','bottom'))])
            concate_clip = [video_clip]
            time_acc_ost += end - start
            for _ts in ts[1:]:
                start, end = _ts[0] / 16000, _ts[1] / 16000
                srt_clip, subs, srt_index = generate_srt_clip(sentences, start, end, begin_index=srt_index-1, time_acc_ost=time_acc_ost)
                if not len(subs):
                    continue
                chi_subs = []
                sub_starts = subs[0][0][0]
                for sub in subs:
                    chi_subs.append(((sub[0][0]-sub_starts, sub[0][1]-sub_starts), sub[1]))
                start, end = start+start_ost/1000.0, end+end_ost/1000.0
                start = max(0.0, start)
                end = min(end, video.duration - 0.01) if video.duration and video.duration > 0 else end
                _video_clip = video.subclip(start, end)
                start_end_info += ", from {} to {}".format(str(start)[:5], str(end)[:5])
                clip_srt += srt_clip
                if add_sub:
                    generator = lambda txt: make_text_clip(
                        txt, font_size=font_size, color=font_color
                    )
                    subtitles = SubtitlesClip(chi_subs, generator)
                    _video_clip = CompositeVideoClip([_video_clip, subtitles.set_pos(('center','bottom'))])
                    # _video_clip.write_videofile("debug.mp4", audio_codec="aac")
                concate_clip.append(copy.copy(_video_clip))
                time_acc_ost += end - start
            message = "{} periods found in the audio: ".format(len(ts)) + start_end_info + log_append
            logging.warning("Concating...")
            if len(concate_clip) > 1:
                video_clip = concatenate_videoclips(concate_clip)
            # clip_video_file = clip_video_file[:-4] + '_no{}.mp4'.format(self.GLOBAL_COUNT)
            if output_dir is not None:
                os.makedirs(output_dir, exist_ok=True)
                _, file_with_extension = os.path.split(clip_video_file)
                clip_video_file_name, _ = os.path.splitext(file_with_extension)
                print(output_dir, clip_video_file)
                clip_video_file = os.path.join(output_dir, "{}_no{}.mp4".format(clip_video_file_name, self.GLOBAL_COUNT))
                temp_audio_file = os.path.join(output_dir, "{}_tempaudio_no{}.mp4".format(clip_video_file_name, self.GLOBAL_COUNT))
            else:
                clip_video_file = clip_video_file[:-4] + '_no{}.mp4'.format(self.GLOBAL_COUNT)
                temp_audio_file = clip_video_file[:-4] + '_tempaudio_no{}.mp4'.format(self.GLOBAL_COUNT)
            _write_standard_mp4(video_clip, clip_video_file, temp_audio_file)
            self.GLOBAL_COUNT += 1
        else:
            clip_video_file = None
            message = "No period found in the video; no output was generated. You may check the recognition result and try other destination text." + log_append
            logging.warning(message)
            srt_clip = ''
        return clip_video_file, message, clip_srt

    def _new_output_file(self, state, output_dir, tag):
        """按「原视频名_tag_no{N}.mp4」生成不冲突的出片路径。"""
        source_name, _ = os.path.splitext(os.path.split(state['video_filename'])[1])
        if output_dir is not None:
            os.makedirs(output_dir, exist_ok=True)
            return os.path.join(output_dir, "{}_{}_no{}.mp4".format(source_name, tag, self.GLOBAL_COUNT))
        return source_name + "_{}_no{}.mp4".format(tag, self.GLOBAL_COUNT)

    def video_burn_subtitles(self, state, font_size=32, font_color='white', output_dir=None):
        """整片烧录字幕：不裁剪，把识别出的全部台词烧进原视频生成新片。"""
        if not state or 'video' not in state or 'sentences' not in state:
            return None, "请先完成识别（推荐「识别+区分说话人」）再整片加字幕。", ''
        video = state['video']
        sentences = state['sentences']
        duration = float(video.duration or 0)
        if duration <= 0:
            return None, "无法读取视频时长，未生成文件。", ''
        srt_text, subs, _ = generate_srt_clip(sentences, 0.0, duration)
        if not len(subs):
            return None, "识别结果里没有可用台词，未生成文件。", ''
        generator = lambda txt: make_text_clip(
            txt, font_size=font_size, color=font_color
        )
        subtitles = SubtitlesClip(subs, generator).set_pos(('center', 'bottom'))
        final_clip = CompositeVideoClip([video, subtitles])
        out_file = self._new_output_file(state, output_dir, 'subtitled')
        temp_audio_file = out_file[:-4] + '_tempaudio_no{}.mp4'.format(self.GLOBAL_COUNT)
        _write_standard_mp4(final_clip, out_file, temp_audio_file)
        self.GLOBAL_COUNT += 1
        message = "已把 {} 条字幕烧录进整片：{}".format(len(subs), out_file)
        logging.warning(message)
        return out_file, message, srt_text

    def video_clip_per_speaker(self, state, output_dir=None):
        """按说话人分开剪：每位说话人单独出一个成片（其全部台词区间依序拼接）。"""
        if not state or 'video' not in state:
            return [], "请先完成识别再按说话人剪辑。"
        sd_sentences = state.get('sd_sentences') or []
        if not sd_sentences:
            return [], "本次识别未开启说话人区分：请先点「识别+区分说话人 | ASR+SD」。"
        spk_ids = []
        for d in sd_sentences:
            if d.get('spk') not in spk_ids:
                spk_ids.append(d.get('spk'))
        files, notes = [], []
        for spk in spk_ids:
            ranges = proc_spk("spk{}".format(spk), sd_sentences)
            if not ranges:
                continue
            clip_file, _, _ = self.video_clip(
                "", 0, 0, state, dest_spk="spk{}".format(spk), output_dir=output_dir)
            if not clip_file:
                continue
            target = re.sub(r'_no\d+\.mp4$', '_spk{}.mp4'.format(spk), clip_file)
            os.replace(clip_file, target)
            files.append(target)
            notes.append("spk{}（{} 段）".format(spk, len(ranges)))
        if not files:
            return [], "没有剪出任何说话人成片。"
        where = "，输出目录：{}".format(output_dir) if output_dir else ""
        message = "共 {} 位说话人，已分别剪出：{}{}".format(
            len(files), "、".join(notes), where)
        logging.warning(message)
        return files, message


def get_parser():
    parser = ArgumentParser(
        description="ClipVideo Argument",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=(1, 2),
        help="Stage, 0 for recognizing and 1 for clipping",
        required=True
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Input file path",
        required=True
    )
    parser.add_argument(
        "--sd_switch",
        type=str,
        choices=("no", "yes"),
        default="no",
        help="Turn on the speaker diarization or not",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default='./output',
        help="Output files path",
    )
    parser.add_argument(
        "--dest_text",
        type=str,
        default=None,
        help="Destination text string for clipping",
    )
    parser.add_argument(
        "--dest_spk",
        type=str,
        default=None,
        help="Destination spk id for clipping",
    )
    parser.add_argument(
        "--start_ost",
        type=int,
        default=0,
        help="Offset time in ms at beginning for clipping"
    )
    parser.add_argument(
        "--end_ost",
        type=int,
        default=0,
        help="Offset time in ms at ending for clipping"
    )
    parser.add_argument(
        "--output_file",
        type=str,
        default=None,
        help="Output file path"
    )
    parser.add_argument(
        "--lang",
        type=str,
        default='zh',
        help="language"
    )
    return parser


def runner(stage, file, sd_switch, output_dir, dest_text, dest_spk, start_ost, end_ost, output_file, config=None, lang='zh'):
    audio_suffixs = ['.wav','.mp3','.aac','.m4a','.flac']
    video_suffixs = ['.mp4','.avi','.mkv','.flv','.mov','.webm','.ts','.mpeg']
    _,ext = os.path.splitext(file)
    if ext.lower() in audio_suffixs:
        mode = 'audio'
    elif ext.lower() in video_suffixs:
        mode = 'video'
    else:
        logging.error("Unsupported file format: {}\n\nplease choise one of the following: {}".format(file),audio_suffixs+video_suffixs)
        sys.exit(1) # exit if the file is not supported
    while output_dir.endswith('/'):
        output_dir = output_dir[:-1]
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)
    if stage == 1:
        from funasr import AutoModel
        # initialize funasr automodel
        logging.warning("Initializing modelscope asr pipeline.")
        if lang == 'zh':
            funasr_model = AutoModel(model="iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                    vad_model="damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    punc_model="damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
                    spk_model="damo/speech_campplus_sv_zh-cn_16k-common",
                    )
            audio_clipper = VideoClipper(funasr_model)
            audio_clipper.lang = 'zh'
        elif lang == 'en':
            funasr_model = AutoModel(model="iic/speech_paraformer_asr-en-16k-vocab4199-pytorch",
                                vad_model="damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                                punc_model="damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
                                spk_model="damo/speech_campplus_sv_zh-cn_16k-common",
                                )
            audio_clipper = VideoClipper(funasr_model)
            audio_clipper.lang = 'en'
        if mode == 'audio':
            logging.warning("Recognizing audio file: {}".format(file))
            wav, sr = librosa.load(file, sr=16000)
            res_text, res_srt, state = audio_clipper.recog((sr, wav), sd_switch)
        if mode == 'video':
            logging.warning("Recognizing video file: {}".format(file))
            res_text, res_srt, state = audio_clipper.video_recog(file, sd_switch)
        total_srt_file = output_dir + '/total.srt'
        with open(total_srt_file, 'w') as fout:
            fout.write(res_srt)
            logging.warning("Write total subtitle to {}".format(total_srt_file))
        write_state(output_dir, state)
        logging.warning("Recognition successed. You can copy the text segment from below and use stage 2.")
        print(res_text)
    if stage == 2:
        audio_clipper = VideoClipper(None)
        if mode == 'audio':
            state = load_state(output_dir)
            wav, sr = librosa.load(file, sr=16000)
            state['audio_input'] = (sr, wav)
            (sr, audio), message, srt_clip = audio_clipper.clip(dest_text, start_ost, end_ost, state, dest_spk=dest_spk)
            if output_file is None:
                output_file = output_dir + '/result.wav'
            clip_srt_file = output_file[:-3] + 'srt'
            logging.warning(message)
            sf.write(output_file, audio, 16000)
            assert output_file.endswith('.wav'), "output_file must ends with '.wav'"
            logging.warning("Save clipped wav file to {}".format(output_file))
            with open(clip_srt_file, 'w') as fout:
                fout.write(srt_clip)
                logging.warning("Write clipped subtitle to {}".format(clip_srt_file))
        if mode == 'video':
            state = load_state(output_dir)
            state['video_filename'] = file
            if output_file is None:
                state['clip_video_file'] = file[:-4] + '_clip.mp4'
            else:
                state['clip_video_file'] = output_file
            clip_srt_file = state['clip_video_file'][:-3] + 'srt'
            state['video'] = mpy.VideoFileClip(file)
            clip_video_file, message, srt_clip = audio_clipper.video_clip(dest_text, start_ost, end_ost, state, dest_spk=dest_spk)
            logging.warning("Clipping Log: {}".format(message))
            logging.warning("Save clipped mp4 file to {}".format(clip_video_file))
            with open(clip_srt_file, 'w') as fout:
                fout.write(srt_clip)
                logging.warning("Write clipped subtitle to {}".format(clip_srt_file))


def main(cmd=None):
    print(get_commandline_args(), file=sys.stderr)
    parser = get_parser()
    args = parser.parse_args(cmd)
    kwargs = vars(args)
    runner(**kwargs)


if __name__ == '__main__':
    main()
