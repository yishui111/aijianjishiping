"""剪映（JianYing）桌面版草稿生成器：片段选区 → 可直接打开的草稿文件夹。

剪映草稿 = 一个文件夹，含 draft_content.json 与 draft_meta_info.json：
- 所有时间单位一律微秒（1 秒 = 1_000_000）
- materials.videos[] 是本地素材条目（绝对路径），tracks[0].segments[] 按时间线顺序排列
- 每个片段 target_timerange = 在成片时间轴上的位置，source_timerange = 取自源素材的位置
- 片段的 extra_material_refs 引用 speeds / canvases（速度 1.0、默认画布）
- 打开草稿时剪映会自动补全缺失字段，因此只写必要字段即可
  （剪映 6/7/10.x：明文草稿可直接打开；保存后剪映自行加密，不影响我们生成）

结构参考开源实现 GuanYixuan/pyJianYingDraft。本模块只用标准库。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

US = 1_000_000  # 微秒/秒


def _uid() -> str:
    return uuid.uuid4().hex.upper()


def _us(sec: float) -> int:
    return max(0, int(round(float(sec) * US)))


def _platform() -> dict:
    return {
        "os": "windows",
        "os_version": "10.0.19045",
        "os_arch": "x64",
        "app_id": 3703,
        "app_source": "process",
        "device_id": "",
        "hard_disk_id": "",
        "version": "5.9.0",
    }


def _video_material(mid: str, path: str, dur_sec: float, w: int, h: int, has_audio: bool) -> dict:
    return {
        "id": mid,
        "type": "shot",
        "material_name": Path(path).name,
        "path": path,
        "duration": _us(dur_sec),
        "width": int(w or 0),
        "height": int(h or 0),
        "has_audio": bool(has_audio),
        "category_name": "local",
        "category_id": "",
        "crop": {
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
        },
        "crop_ratio": "original",
        "crop_scale": 1.0,
        "audio_fade": None,
        "local_material_id": "",
        "reverse_path": None,
        "source_platform": 0,
        "team_id": "",
        "time_lapse": None,
        "trim": {"left_offset": 0, "right_offset": 0},
        "extra_type_option": 0,
        "is_ai_generate_content": False,
        "is_unified_beauty_mode": False,
        "matting": None,
        "motion": None,
        "producer": None,
        "stable": None,
        "freeze": None,
        "path_replace": None,
    }


def _segment(material_id: str, tl_start_us: int, src_start_us: int, dur_us: int,
             speed_id: str, canvas_id: str, render_index: int) -> dict:
    return {
        "id": _uid(),
        "material_id": material_id,
        "target_timerange": {"start": tl_start_us, "duration": dur_us},
        "source_timerange": {"start": src_start_us, "duration": dur_us},
        "extra_material_refs": [speed_id, canvas_id],
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        },
        "speed": 1.0,
        "volume": 1.0,
        "render_index": render_index,
        "visible": True,
        "enable_adjust": True,
        "enable_color_curves": True,
        "enable_color_wheels": True,
        "enable_lut": True,
        "uniform_scale": {"on": True, "value": 1.0},
        "last_nonzero_volume": 1.0,
        "common_keyframes": [],
        "keyframe_refs": [],
        "cartoon": False,
    }


def build_draft(selections: list[dict], *, name: str, materials_info: dict,
                canvas: tuple[int, int] = (1920, 1080)) -> tuple[dict, dict]:
    """生成 (draft_content, draft_meta_info) 两份 dict。

    selections: [{file, start, end}]，按时间线顺序；file 是 materials_info 的键
    materials_info: {file: {path(绝对), duration_sec, width, height, has_audio}}
    """
    now = int(time.time())
    draft_id = _uid()
    vids: dict[str, dict] = {}
    segs: list[dict] = []
    speeds: list[dict] = []
    canvases: list[dict] = []
    tl_us = 0
    for i, s in enumerate(selections):
        f = s["file"]
        info = materials_info[f]
        if f not in vids:
            vids[f] = _video_material(_uid(), info["path"], float(info.get("duration_sec") or 0),
                                      info.get("width") or 0, info.get("height") or 0,
                                      bool(info.get("has_audio", True)))
        speed_id, canvas_id = _uid(), _uid()
        speeds.append({"id": speed_id, "type": "speed", "mode": 0, "speed": 1.0,
                       "name": "", "curve_speed": None})
        canvases.append({"id": canvas_id, "type": "canvas_color", "color": ""})
        d_us = _us(s["end"] - s["start"])
        segs.append(_segment(vids[f]["id"], tl_us, _us(s["start"]), d_us, speed_id, canvas_id, i))
        tl_us += d_us
    content = {
        "fps": 30,
        "duration": tl_us,
        "id": draft_id,
        "name": name,
        "platform": _platform(),
        "relationships": [],
        "materials": {
            "videos": list(vids.values()),
            "audios": [],
            "texts": [],
            "stickers": [],
            "effects": [],
            "canvases": canvases,
            "speeds": speeds,
            "transitions": [],
            "audio_fades": [],
            "sound_channel_mappings": [],
            "video_effects": [],
            "beats": [],
            "placeholder": [],
        },
        "tracks": [{
            "id": _uid(),
            "type": "video",
            "attribute": 0,
            "flag": 0,
            "is_default_name": True,
            "name": "",
            "segments": segs,
        }],
        "canvas_config": {"width": int(canvas[0]), "height": int(canvas[1]), "ratio": "original"},
        "create_time": now,
        "update_time": now,
        "version": "3.7.0",
        "extra_info": None,
        "keyframes": {"materials": []},
    }
    meta = {
        "draft_cloud_last_action_download": False,
        "draft_cloud_package_info": {},
        "draft_cloud_purchase_info": {},
        "draft_cloud_template_info": {},
        "draft_cloud_tutorial_info": {},
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_id": "", "draft_enterprise_name": "",
                                  "enterprise_material": []},
        "draft_fold_path": "",  # write_draft 里补
        "draft_id": draft_id,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_materials": [{"type": 0, "value": [v["path"] for v in vids.values()]}],
        "draft_name": name,
        "draft_new_version": "6.5.0",
        "draft_removable_storage_device": "",
        "draft_root_path": "",
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "tm_draft_create": now * US,
        "tm_draft_modified": now * US,
        "tm_duration": tl_us,
        "user_material_cover": "",
    }
    return content, meta


def write_draft(folder: str | Path, selections: list[dict], *, name: str,
                materials_info: dict, canvas: tuple[int, int] = (1920, 1080)) -> Path:
    """写出草稿文件夹（draft_content.json + draft_meta_info.json），返回文件夹路径。

    文件夹名即剪映首页显示的草稿名，需与 meta.draft_name 一致。
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    content, meta = build_draft(selections, name=name, materials_info=materials_info, canvas=canvas)
    meta["draft_fold_path"] = str(folder)
    meta["draft_root_path"] = str(folder)
    (folder / "draft_content.json").write_text(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (folder / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return folder
