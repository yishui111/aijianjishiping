"""L2 方案服务：对话 + 规则解析 → EDL → 预览/执行。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import clip_client, jianying_draft, media, util  # noqa: E402

PLANNER_BASE_URL = os.environ.get("PLANNER_BASE_URL", "http://ollama:11434/v1").rstrip("/")
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "qwen2.5vl:3b")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen2.5vl:3b")  # 分析用大模型（按需加载：点分析才启动）
PLANNER_API_KEY = os.environ.get("PLANNER_API_KEY", "")
# 剧本生成专用（线上 DeepSeek）：与对话模型分开，填 Key 即走 DeepSeek，不填回退本地 7B
GEN_SCRIPT_BASE_URL = os.environ.get("GEN_SCRIPT_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
GEN_SCRIPT_MODEL = os.environ.get("GEN_SCRIPT_MODEL", "deepseek-chat")
GEN_SCRIPT_API_KEY = os.environ.get("GEN_SCRIPT_API_KEY", "")
ANALYZER_URL = os.environ.get("ANALYZER_URL", "http://analyzer:8001").rstrip("/")
EXECUTOR_URL = os.environ.get("EXECUTOR_URL", "http://executor:8002").rstrip("/")
ANALYSIS = Path(os.environ.get("ANALYSIS_DIR", "/analysis"))
MATERIALS = Path(os.environ.get("MATERIALS_DIR", "/materials"))
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(MATERIALS.parent / "output")))
EXPORTS_DIR = OUTPUT_DIR / "exports"  # 剪映草稿 / SRT 等导出产物

app = FastAPI(title="AI Video Studio Planner")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_segment",
            "description": "精读指定时间段（关键帧+画面描述+该段转写）。复杂需求信息不足时调用。",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"}},
                "required": ["file", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_semantic",
            "description": "按描述检索素材（如'日落 空镜'），返回命中文件与摘要。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "number"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_edl",
            "description": "生成 EDL 后必须先调用它预览确认，不要直接执行。",
            "parameters": {
                "type": "object",
                "properties": {"edl": {"type": "object"}},
                "required": ["edl"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_edl",
            "description": "用户确认预览后，执行 EDL 出片。",
            "parameters": {
                "type": "object",
                "properties": {"edl": {"type": "object"}},
                "required": ["edl"],
            },
        },
    },
]


class ChatRequest(BaseModel):
    message: str
    files: list[str] = []  # 用户勾选的素材文件（相对 materials 的路径）


class ScanRequest(BaseModel):
    folder: str = "."
    force: bool = False  # True = 已分析也重跑（补台词/刷新标注）
    mode: str = "full"   # full=完整分析；asr=只提取字幕（语音转写）


MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".ts", ".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ANALYSIS_DIRNAME = "_analysis"


def _dir_manifest(folder: str | Path = ".") -> dict:
    """读取某目录（相对 materials）_analysis/manifest.json；目录不存在则空。"""
    # 兼容传入 Path 对象（如 list_folders 的目录迭代）：转成相对 materials 的目录名
    if isinstance(folder, Path):
        try:
            folder = str(folder.relative_to(MATERIALS.resolve()))
        except ValueError:
            return {}
    base = (MATERIALS / str(folder)).resolve() if str(folder) not in ("", ".") else MATERIALS.resolve()
    if not base.exists() or not base.is_relative_to(MATERIALS.resolve()):
        return {}
    return util.load_json(base / ANALYSIS_DIRNAME / "manifest.json", {})


def _dir_analyzed(folder: str | Path = ".") -> set[str]:
    """某目录下所有已分析文件（相对 materials 的路径集合），由 manifest 得出。"""
    return {v.get("file") for v in _dir_manifest(folder).get("videos", [])}


def _media_files(base: Path) -> list[Path]:
    def _is_derived(p: Path) -> bool:
        for parent in p.parents:
            if parent.name in (ANALYSIS_DIRNAME, "_edit_thumbs") or parent.name.endswith("_frames"):
                return True
        return p.name.endswith(".analysis.json")

    return sorted(p for p in base.rglob("*") if p.suffix.lower() in MEDIA_EXTS and p.is_file() and not _is_derived(p))


def _load_scene_brief(file: str, limit: int = 8) -> list[dict]:
    """读取素材分析中的场景摘要（含 energy 与画面描述），供模型/规则选段。"""
    src = (MATERIALS / file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return []
    a = util.load_json(src.parent / "_analysis" / (src.name + ".analysis.json"))
    if not a:
        return []
    out = []
    for s in (a.get("scenes") or [])[:limit]:
        out.append(
            {
                "start": round(s.get("start") or 0, 1),
                "end": round(s.get("end") or 0, 1),
                "energy": s.get("energy") or "?",
                "content": (s.get("content") or "")[:60],
            }
        )
    return out


# ---------- 素材完整文档前置整理 ----------
# 把选中素材的【完整分析文档】按用户需求整理后喂给模型：
# 相关的人物/台词优先全量保留，预算紧张时压缩，保证上下文极限内尽量完整。

_DOC_BUDGET = 7000  # 素材文档总预算（字符，中文 1 字 ≈ 1 token；7B 对话上下文 8K，需留余量给规则和输出）


def _material_doc(file: str) -> dict | None:
    """读取素材完整分析文档（analysis.json）。"""
    src = (MATERIALS / file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return None
    return util.load_json(src.parent / "_analysis" / (src.name + ".analysis.json"))


def _scene_block(a: dict, detail: bool = True) -> str:
    """场景列表文本：detail=True 全字段，False 只保留时间轴+内容前 40 字。"""
    lines = []
    for s in a.get("scenes") or []:
        head = f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}s]"
        if detail:
            parts = [head]
            if s.get("content"):
                parts.append(s["content"])
            subs = [str(x) for x in (s.get("subjects") or []) if x]
            if subs:
                parts.append("人物:" + "/".join(subs))
            if s.get("energy"):
                parts.append("能量:" + str(s["energy"]))
            if s.get("type"):
                parts.append("类型:" + str(s["type"]))
            lines.append(" ".join(parts))
        else:
            lines.append(f"{head} {(s.get('content') or '')[:40]}")
    return "\n".join(lines)


def _speech_block(a: dict, keywords: list[str] | None = None, max_lines: int | None = None) -> str:
    """台词文本：可选只保留含关键词的句子，可选条数上限。"""
    lines = []
    for s in a.get("speech") or []:
        t = s.get("text") or ""
        if not t:
            continue
        if keywords and not any(k in t for k in keywords):
            continue
        lines.append(f"[{s.get('start', 0):.1f}s] {t[:40]}")
        if max_lines and len(lines) >= max_lines:
            break
    return "\n".join(lines)


def _query_keywords(query: str, a: dict) -> list[str]:
    """从需求文本里提取与该素材文档相关的人物/主题词。"""
    words = set()
    for s in a.get("scenes") or []:
        for w in s.get("subjects") or []:
            w = str(w).strip()
            if len(w) >= 2 and w in query:
                words.add(w)
    for t in a.get("tags") or []:
        t = str(t).strip()
        if len(t) >= 2 and t in query:
            words.add(t)
    return list(words)


def _material_doc_block(a: dict, query: str, budget: int) -> str:
    """单个素材按需求整理的文档块（控制在预算字符内，越相关越全）。"""
    if not a:
        return ""
    kws = _query_keywords(query, a)
    head = f"【素材】{a.get('file')} | 时长{a.get('duration_sec')}s | 摘要:{a.get('summary')} | 标签:{'/'.join(a.get('tags') or [])}"
    scenes_full = _scene_block(a, detail=True)
    speech = _speech_block(a, keywords=kws or None, max_lines=60)
    if not speech and kws:
        speech = _speech_block(a, max_lines=40)
    full = f"{head}\n场景:\n{scenes_full}\n台词:\n{speech}"
    if len(full) <= budget:
        return full
    # 预算紧张：压缩场景详情，台词只保留关键词命中句
    scenes_light = _scene_block(a, detail=False)
    speech_light = _speech_block(a, keywords=kws or None, max_lines=30)
    if not speech_light and kws:
        speech_light = _speech_block(a, max_lines=20)
    compact = f"{head}\n场景:\n{scenes_light}\n台词:\n{speech_light}"
    if len(compact) <= budget:
        return compact
    # 极限：只给摘要 + 场景时间轴
    return f"{head}\n场景时间轴:\n{scenes_light}"


def _system_prompt(files: list[str] | None = None, query: str = "") -> str:
    """构建系统提示：选中素材的【完整分析文档】按需求前置整理后喂给模型。"""
    catalog = util.load_json(CONFIG_DIR / "operation-catalog.json", {})
    if files:
        docs = []
        remaining = _DOC_BUDGET
        n = max(1, len(files))
        for f in files[:10]:  # 预算内最多处理 10 个素材
            a = _material_doc(f)
            if not a:
                continue
            per = max(2500, remaining // n)
            blk = _material_doc_block(a, query, per)
            if blk:
                docs.append(blk)
            remaining = max(0, remaining - len(blk))
        docs_txt = "\n\n".join(docs) if docs else "（素材分析文档缺失，请先分析素材）"
        manifest_txt = f"以下是用户勾选素材的【完整分析文档】（场景时间轴、人物、台词转写，已按需求整理）：\n{docs_txt}"
        selected_txt = "\n用户本次对话只针对以下素材（不要使用列表之外的素材）：\n" + json.dumps(files, ensure_ascii=False)
    else:
        manifest_txt = json.dumps(_dir_manifest("."), ensure_ascii=False)[:5000]
        selected_txt = ""
    catalog_txt = json.dumps(catalog, ensure_ascii=False)
    return f"""你是视频剪辑规划器。以下是用户勾选素材的完整分析文档（场景时间轴 + 人物 + 台词转写，已按需求整理）：
{manifest_txt}
{selected_txt}
可用的剪辑操作（op 必须来自这里）：
{catalog_txt}

用户会用自然语言描述剪辑需求（如"只剪辑有声音的"、"把 5cce 的 3-10 秒剪出来 1.5 倍速"、"把选中的接起来导出"、"做个精彩集锦"、"把孙悟空的剪出来"）。
请你**根据素材分析文档**理解意图，输出一个 EDL JSON 方案。严格按以下格式输出（不要输出别的文字，不要用 markdown）：
{{"schema_version":1,"request_id":"<clip-时间戳>","user_request":"<用户原话>","plan":[{{"op":"trim","file":"<素材file字段值>","start":<秒>,"end":<秒>}},...]}}

操作说明：
- trim: 裁剪区间，file 必须用素材分析文档里的 file 字段值，start/end 是秒（0 ≤ start < end ≤ 素材时长）。
- remove_silence: 去静音/只保留有声音，必须带全参数：{{"op":"remove_silence","file":"...","threshold_db":-35,"margin_sec":0.3}}
- speed: 变速，{{"op":"speed","file":"...","factor":1.5}}（放慢用 0.5 等小数）
- concat: 拼接多段，{{"op":"concat","files":["file1","file2"]}}
- export: 导出，{{"op":"export","format":"mp4","resolution":"720p","crf":23}}（作为 plan 最后一步）

规则：
1. 只使用用户勾选的素材（文档里的 file 字段值），不要编造素材名。
2. **按人物/主题/要求选段时，先逐条核对文档里的【场景列表】，把所有相关场景【完整】列出（用文档里的 start/end 秒数），再生成 trim**。严禁：只选部分相关场景、截取场景中间一小段、或编造不在文档里的时间。
3. 如果用户需求模糊（如只说"随便剪剪"），给出一个合理的默认方案（如保留前 10 秒 + 导出）并简短说明。
4. 如果用户只是闲聊（如"你好"），或者素材分析文档里没有用户要的内容 → 输出 {{"op":"none","message":"<用中文向用户如实说明情况，必要时给出替代建议，message 内容要具体，不要写'用中文简短回应'这类占位>}}，不要生成剪辑方案。
5. 去静音/只保留有声音 → 用 remove_silence（必须带 threshold_db 和 margin_sec 参数）。
6. "精彩集锦/最激烈/高潮/精彩部分" → 优先根据场景时间轴的 energy=high/medium 场景用 trim 精确选段；若素材无 energy 信息，可对整段做 remove_silence 提速（speed 1.2~1.5x）剪出节奏。
7. "只要 X 的部分 / 把 X 剪出来"（X 是人物或主体，如孙悟空）→ 只保留场景描述里包含 X 的场景，trim 的 start/end 必须对齐该场景边界，**不得连带相邻的其他人物/主体场景**；同一素材匹配到多段时用多个 trim，最后 concat 拼接。
8. 素材分析文档里没有的内容（如某人物/主题不在文档里）→ 按规则 4 输出 op:none，并在 message 里如实说明"素材库里没有 X 的画面/内容，但有 Y（列出文档里实际有的）"，给出替代方案，不要硬编不存在的选段。
9. 最后一步必须是 export（除非 plan 为空）。"""


def _llm_plan_edl(message: str, files: list[str]) -> dict | None:
    """让模型直接输出 EDL JSON（不依赖 tools）。失败/超时返回 None。"""
    try:
        msgs = [
            {"role": "system", "content": _system_prompt(files, message)},
            {"role": "user", "content": message},
        ]
        msg = _llm_chat(msgs, tools=None, timeout=480)
        text = msg.get("content") or ""
        # 先试直接解析
        edl = util.parse_llm_json(text)
        ok_ops = {"trim", "remove_silence", "speed", "concat", "export", "none"}
        if edl:
            # 情况1：模型输出完整 {"plan":[...]}
            plan = edl.get("plan") if isinstance(edl, dict) else None
            if isinstance(plan, list):
                if plan and plan[0].get("op") == "none":
                    return {"_chat_reply": plan[0].get("message", "")}
                return _normalize_model_edl(edl, message, files)
            # 情况2：模型输出单个操作 {"op":"trim",...}（none 单独处理）
            elif isinstance(edl, dict) and edl.get("op") == "none":
                # 规则 4/8：闲聊回应 / 素材库里没有对应内容时的如实说明
                return {"_chat_reply": edl.get("message") or "无法满足你的需求，请换个说法试试"}
            elif isinstance(edl, dict) and edl.get("op") in ok_ops and edl["op"] != "none":
                return _normalize_model_edl(
                    {
                        "schema_version": 1,
                        "request_id": f"clip-{int(time.time())}",
                        "user_request": message,
                        "plan": [edl],
                    },
                    message,
                    files,
                )
            # 情况3：模型输出 {"plan": 单个操作}
            elif isinstance(edl, dict) and isinstance(edl.get("plan"), dict) and edl["plan"].get("op") in ok_ops:
                op = edl["plan"]
                if op.get("op") == "none":
                    return {"_chat_reply": op.get("message", "")}
                return _normalize_model_edl(
                    {
                        "schema_version": 1,
                        "request_id": f"clip-{int(time.time())}",
                        "user_request": message,
                        "plan": [op],
                    },
                    message,
                    files,
                )
    except Exception as exc:
        print(f"[chat] 模型规划失败: {exc}")
    return None


def _ensure_export(edl: dict, message: str) -> dict:
    """确保 EDL 以 export 收尾（模型常漏，补上）。"""
    plan = edl.get("plan") or []
    if plan and not any(op.get("op") == "export" for op in plan):
        plan.append({"op": "export", "format": "mp4", "resolution": "720p", "crf": 23})
        edl["plan"] = plan
    return edl


# remove_silence 缺省参数（模型经常漏写，executor 校验又严格要求）
_SILENCE_DEFAULTS = {"threshold_db": -35, "margin_sec": 0.3}


def _snap_trim_to_scene(op: dict):
    """把 trim 的 start/end 吸附到最近的场景边界（±2s），修 LLM 输出的轻微偏移。

    若区间与某个场景重叠 ≥80%，则对齐到该场景完整边界（防 LLM 只截场景一小段）。
    """
    a = _material_doc(op.get("file", ""))
    if not a or not a.get("scenes"):
        return
    start, end = float(op.get("start", 0)), float(op.get("end", 0))
    dur = end - start
    if dur <= 0:
        return
    bounds = []
    for s in a["scenes"]:
        bounds.append(float(s.get("start", 0)))
        bounds.append(float(s.get("end", 0)))
    # 场景重叠对齐
    for s in a["scenes"]:
        ss, se = float(s.get("start", 0)), float(s.get("end", 0))
        overlap = max(0.0, min(end, se) - max(start, ss))
        if overlap / dur >= 0.8:
            op["start"], op["end"] = round(ss, 2), round(se, 2)
            return
    # 点吸附（±2s 内贴到最近边界）
    for key in ("start", "end"):
        v = float(op.get(key) or 0)
        best = min(bounds, key=lambda b: abs(b - v))
        if abs(best - v) <= 2.0:
            op[key] = round(best, 2)


# ---------- 模型主导的两阶段流水线：分析需求 → 逐素材筛选 → 汇总生成 ----------

_FILTER_PROMPT = """你是视频素材检索助手。下面是【一个素材】的场景清单和用户的一个剪辑需求。
请【分析需求】后判断：这个素材里哪些场景与需求相关。
只输出严格 JSON（不要 markdown、不要解释）：
{{"useful_scenes":[{{"start":<秒>,"end":<秒>}}],"reason":"<一句话说明>"}}
规则：
- start/end 必须【完全照抄】场景清单里的值，禁止编造时间。
- 相关场景全部列出，不要遗漏；无关的不要选。
- 一个都不相关时输出 {{"useful_scenes":[],"reason":"<说明为什么>"}}

用户需求：{query}

场景清单：
{scenes}"""


def _llm_filter_scenes(query: str, a: dict, batch_size: int = 8) -> list[dict]:
    """阶段1：让 7B 分析需求 + 读素材文档，返回它认为相关的场景（必须引用清单里的时间）。

    素材文档再长也不怕：场景按批次（每批 batch_size 个）分段，一段一段和需求匹配，
    匹配上的留下——单批始终在 8K 上下文内，适合长视频/多场景文档。
    """
    scenes = a.get("scenes") or []
    if not scenes:
        return []
    kept: list[dict] = []
    for i in range(0, len(scenes), batch_size):
        batch = scenes[i : i + batch_size]
        a_batch = dict(a)
        a_batch["scenes"] = batch
        # 用完整场景清单（含人物/能量/类型）——精简版会丢 subjects，导致 7B 判不出相关性
        scenes_txt = _scene_block(a_batch, detail=True)
        prompt = _FILTER_PROMPT.format(query=query, scenes=scenes_txt)
        try:
            msg = _llm_chat(
                [{"role": "system", "content": "你是视频素材检索助手，严格按格式输出 JSON。"}, {"role": "user", "content": prompt}],
                timeout=480,
            )
            edl = util.parse_llm_json(msg.get("content") or "")
            if edl and isinstance(edl.get("useful_scenes"), list):
                for s in edl["useful_scenes"]:
                    if isinstance(s, dict):
                        try:
                            start, end = round(float(s["start"]), 1), round(float(s["end"]), 1)
                            # 校验：只能保留本批清单里真实存在的场景区间
                            if any(abs(start - float(sc.get("start", 0))) < 0.1 and abs(end - float(sc.get("end", 0))) < 0.1 for sc in batch):
                                kept.append({"start": start, "end": end})
                        except (KeyError, TypeError, ValueError):
                            continue
        except Exception as exc:
            print(f"[filter] 素材筛选失败: {exc}")
    return kept


def _build_kept_txt(kept: dict[str, list[dict]]) -> str:
    """把各素材保留的场景拼成喂给模型的文档块。"""
    blocks = []
    for f, scenes in kept.items():
        lines = [f"【素材】{f}"]
        for s in scenes:
            subs = "/".join(str(x) for x in (s.get("subjects") or []))
            line = f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}s] {s.get('content', '')}"
            if subs:
                line += f" 人物:{subs}"
            if s.get("energy"):
                line += f" 能量:{s['energy']}"
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _llm_plan_from_kept(message: str, kept: dict[str, list[dict]]) -> dict | None:
    """阶段2：把筛选后保留的场景汇总 + 需求 → 7B 生成 EDL。"""
    if not kept:
        return None
    docs_txt = _build_kept_txt(kept)
    catalog = util.load_json(CONFIG_DIR / "operation-catalog.json", {})
    files = list(kept.keys())
    system = f"""你是视频剪辑规划器。以下是【筛选后保留的】相关素材场景（已剔除与需求无关的内容）：
{docs_txt}
用户本次只针对这些素材剪辑：{json.dumps(files, ensure_ascii=False)}
可用的剪辑操作（op 必须来自这里）：
{json.dumps(catalog, ensure_ascii=False)}

用户会用自然语言描述剪辑需求。请根据上述【已筛选的场景】生成 EDL JSON，严格按以下格式输出（不要输出别的文字，不要用 markdown）：
{{"schema_version":1,"request_id":"<clip-时间戳>","user_request":"<用户原话>","plan":[{{"op":"trim","file":"<素材file字段值>","start":<秒>,"end":<秒>}},...]}}

操作说明：
- trim: 裁剪区间，file 用素材 file 字段值，start/end 是秒（0 ≤ start < end ≤ 素材时长）。
- remove_silence: 去静音，{{"op":"remove_silence","file":"...","threshold_db":-35,"margin_sec":0.3}}
- speed: 变速，{{"op":"speed","file":"...","factor":1.5}}
- concat: 拼接多段，{{"op":"concat","files":["file1","file2"]}}
- export: 导出，{{"op":"export","format":"mp4","resolution":"720p","crf":23}}（作为 plan 最后一步）

规则：
1. 只使用上述保留场景里的时间区间（start/end），**必须完全对齐场景边界**，禁止编造。
2. 用户要"把 X 剪出来"时，把保留场景里包含 X 的所有段都 trim 出来，多段用多个 trim + concat。
3. 如果保留场景不足（用户要的内容已在上一步筛掉/没有）→ 输出 {{"op":"none","message":"<如实说明素材库里没有的内容，给替代建议>"}}。
4. 如果用户只是闲聊 → {{"op":"none","message":"<中文简短回应>"}}。
5. 最后一步必须是 export（除非 plan 为空）。"""
    try:
        msg = _llm_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": message}],
            timeout=480,
        )
        text = msg.get("content") or ""
        edl = util.parse_llm_json(text)
        if edl:
            plan = edl.get("plan") if isinstance(edl, dict) else None
            if isinstance(plan, list):
                if plan and plan[0].get("op") == "none":
                    return {"_chat_reply": plan[0].get("message", "素材库里没有相关内容")}
                return _normalize_model_edl(edl, message, files)
            if isinstance(edl, dict) and edl.get("op") == "none":
                return {"_chat_reply": edl.get("message", "素材库里没有相关内容")}
            if isinstance(edl, dict) and edl.get("op") in ("trim", "remove_silence", "speed", "concat", "export"):
                wrapped = {"schema_version": 1, "request_id": f"clip-{int(time.time())}", "user_request": message, "plan": [edl]}
                return _normalize_model_edl(wrapped, message, files)
    except Exception as exc:
        print(f"[plan] 生成方案失败: {exc}")
    return None


def _llm_pipeline(message: str, files: list[str], session: str = "") -> dict | None:
    """两阶段流水线：逐素材筛选（模型分析需求+读文档）→ 汇总保留场景生成 EDL。

    每个素材单独处理，天然不超上下文窗口；只保留与需求相关的场景。
    """
    kept: dict[str, list[dict]] = {}
    for f in files:
        a = _material_doc(f)
        if not a:
            if session:
                _plog(session, f"⚠️ 素材 {f} 未找到分析文档（可能未分析）")
            continue
        if session:
            _plog(session, f"📄 读取素材 {f} 的分析文档，筛选与需求相关的场景（分批处理）...")
        scenes = _llm_filter_scenes(message, a)
        if scenes:
            kept[f] = scenes
            if session:
                desc = "、".join(f"{s['start']}-{s['end']}s" for s in scenes[:6])
                _plog(session, f"✅ 素材 {f} 保留 {len(scenes)} 个相关场景：[{desc}{'...' if len(scenes) > 6 else ''}]")
        elif session:
            _plog(session, f"➖ 素材 {f} 无相关场景，已剔除")
    if not kept:
        if session:
            _plog(session, "⚠️ 所有素材均无与需求相关的内容，如实回复用户")
        return {"_chat_reply": "我逐一分析了所有素材的文档，没有找到与你的需求相关的内容。可以换个说法，或确认素材已分析。"}
    if session:
        _plog(session, "🧩 汇总保留场景，让模型生成剪辑方案...")
    result = _llm_plan_from_kept(message, kept)
    if session and result:
        if result.get("_chat_reply"):
            _plog(session, f"💬 模型回复：{result['_chat_reply'][:80]}")
        else:
            plan = result.get("plan") or []
            _plog(session, f"✅ 方案生成完成（{len(plan)} 步）")
    return result


def _normalize_model_edl(edl: dict, message: str, files: list[str]) -> dict | None:
    """规范化模型输出：补缺省参数、clamp 时间、剔除非法素材。失败返回 None。"""
    plan = edl.get("plan")
    if not isinstance(plan, list) or not plan:
        return None
    file_set = set(files)
    valid = []
    for op in plan:
        if not isinstance(op, dict) or not op.get("op"):
            continue
        oid = op["op"]
        if oid == "none":
            return {"_chat_reply": op.get("message", "")}
        if oid == "remove_silence":
            op["threshold_db"] = float(op.get("threshold_db", _SILENCE_DEFAULTS["threshold_db"]))
            op["margin_sec"] = float(op.get("margin_sec", _SILENCE_DEFAULTS["margin_sec"]))
            op["threshold_db"] = min(-10.0, op["threshold_db"])
            op["margin_sec"] = max(0.0, min(2.0, op["margin_sec"]))
        elif oid == "speed":
            op["factor"] = max(0.25, min(4.0, float(op.get("factor") or 1.0)))
        elif oid == "trim":
            op["start"] = max(0.0, float(op.get("start") or 0.0))
            op["end"] = float(op.get("end") or 0.0)
            dur = _material_duration(op.get("file", "")) or 0.0
            if dur > 0:
                op["end"] = min(op["end"], dur)
                if op["end"] <= op["start"]:
                    op["end"] = min(dur, op["start"] + 3.0)
            # 场景边界吸附：LLM 输出的时间对齐到文档场景边界，防止乱截
            _snap_trim_to_scene(op)
        elif oid == "concat":
            op["files"] = [f for f in (op.get("files") or []) if f in file_set]
            if not op["files"]:
                continue
        if oid in ("trim", "remove_silence", "speed") and op.get("file") not in file_set:
            # 模型偶尔输出勾选列表之外的素材，重映射到唯一勾选素材（单素材时最安全）
            if len(files) == 1:
                op["file"] = files[0]
            else:
                continue
        valid.append(op)
    if not valid:
        return None
    # concat 修正：模型常在 concat.files 里写重复的原始文件名（它不知道同素材多段
    # 要用 file#n 派生 key），executor 会把第一段重复拼接。按本方案内 trim 的真实
    # 顺序重写派生 key，保证拼接顺序=裁剪顺序、不重不漏。
    if any(o.get("op") == "concat" for o in valid):
        keys: list[str] = []
        seen_keys: dict[str, int] = {}
        for o in valid:
            if o.get("op") == "trim":
                f = o.get("file")
                n = seen_keys.get(f, 0)
                keys.append(f if n == 0 else f"{f}#{n}")
                seen_keys[f] = n + 1
        for o in valid:
            if o.get("op") == "concat":
                o["files"] = keys
                break
    edl["plan"] = valid
    return _ensure_export(edl, message)


def _llm_chat(messages: list, tools=None, timeout: float = 180) -> dict:
    headers = {"Content-Type": "application/json"}
    if PLANNER_API_KEY:
        headers["Authorization"] = f"Bearer {PLANNER_API_KEY}"
    payload = {"model": PLANNER_MODEL, "messages": messages, "temperature": 0.2}
    # num_ctx 是 Ollama 专属参数；走线上 OpenAI 兼容 API（DeepSeek 等）时不能带，部分服务会报错
    if any(h in PLANNER_BASE_URL for h in ("127.0.0.1", "localhost", "11434")):
        payload["num_ctx"] = 8192
    if tools:
        payload["tools"] = tools
    resp = httpx.post(f"{PLANNER_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def _llm_chat_script(messages: list, timeout: float = 480) -> dict:
    """剧本生成专用：有 DeepSeek Key 走线上，否则回退本地 7B。"""
    if not GEN_SCRIPT_API_KEY:
        return _llm_chat(messages, timeout=timeout)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {GEN_SCRIPT_API_KEY}"}
    payload = {"model": GEN_SCRIPT_MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 4000}
    resp = httpx.post(f"{GEN_SCRIPT_BASE_URL}/chat/completions", json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def _call_tool(name: str, args: dict) -> str:
    if name == "query_segment":
        r = httpx.post(f"{ANALYZER_URL}/query_segment", json=args, timeout=180)
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False)
    if name == "search_semantic":
        r = httpx.post(f"{ANALYZER_URL}/search", json=args, timeout=60)
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False)
    if name in ("preview_edl", "execute_edl"):
        r = httpx.post(f"{EXECUTOR_URL}/{name}", json={"edl": args.get("edl", args)}, timeout=3600)
        r.raise_for_status()
        return json.dumps(r.json(), ensure_ascii=False)
    return f"未知工具 {name}"


@app.get("/health")
def health():
    return {"ok": True, "model": PLANNER_MODEL, "base_url": PLANNER_BASE_URL}


@app.get("/api/folders")
def list_folders():
    """列出素材目录，每个目录标注：素材总数、已分析数、是否分析完毕。"""
    folders = []
    root_media = []
    if MATERIALS.exists():
        # 根目录直接媒体文件
        root_media = [p for p in MATERIALS.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTS]
        for d in sorted([p for p in MATERIALS.iterdir() if p.is_dir()], key=lambda p: p.name):
            files = _media_files(d)
            rel_files = [f.relative_to(MATERIALS).as_posix() for f in files]
            analyzed = [f for f in rel_files if f in _dir_analyzed(d)]
            folders.append(
                {
                    "name": d.name,
                    "count": len(files),
                    "analyzed": len(analyzed),
                    "done": len(files) > 0 and len(analyzed) == len(files),
                    "total_duration": round(sum((v.get("duration_sec") or 0) for v in _dir_manifest(d).get("videos", [])), 1),
                }
            )
    # 根目录作为一个虚拟"目录"，方便直接选根目录剪辑
    root_manifest = _dir_manifest(".")
    root_analyzed = {v.get("file") for v in root_manifest.get("videos", [])}
    root_rel = [f.relative_to(MATERIALS).as_posix() for f in root_media]
    root_done = len(root_rel) > 0 and all(f in root_analyzed for f in root_rel)
    root_entry = {
        "name": "（根目录）",
        "count": len(root_rel),
        "analyzed": sum(1 for f in root_rel if f in root_analyzed),
        "done": root_done,
        "total_duration": round(sum((v.get("duration_sec") or 0) for v in root_manifest.get("videos", [])), 1),
        "is_root": True,
    }
    if root_rel:
        folders.insert(0, root_entry)
    return {"folders": folders, "root_files": len(root_rel)}


@app.get("/api/materials")
def list_materials(folder: str = "."):
    """列出指定目录下的素材（含分析状态）。folder 为空或 '.' 表示根目录。"""
    base = (MATERIALS / folder).resolve() if folder and folder != "." else MATERIALS
    if not base.exists() or not base.is_relative_to(MATERIALS.resolve()):
        return {"materials": [], "folder": folder}
    manifest = _dir_manifest(folder)
    analyzed = {v.get("file"): v for v in manifest.get("videos", [])}
    items = []
    for p in sorted(base.iterdir()):
        if p.is_dir() or p.suffix.lower() not in MEDIA_EXTS:
            continue
        rel = p.relative_to(MATERIALS).as_posix()
        a = analyzed.get(rel)
        items.append(
            {
                "file": rel,
                "name": p.name,
                "size_mb": round(p.stat().st_size / 1024 / 1024, 1),
                "analyzed": a is not None,
                "summary": (a or {}).get("summary"),
                "tags": (a or {}).get("tags") or [],
                "duration_sec": (a or {}).get("duration_sec"),
                "scenes": (a or {}).get("scene_count"),
            }
        )
    return {"materials": items, "folder": folder}


@app.post("/api/scan")
def api_scan(req: ScanRequest):
    """代理 analyzer 的 /scan：启动异步分析，返回 scan_id（前端轮询进度）。"""
    try:
        r = httpx.post(f"{ANALYZER_URL}/scan", json={"folder": req.folder, "force": req.force, "mode": req.mode}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"scan_id": None, "total": 0, "started": False, "error": str(exc)}


@app.get("/api/scan_status")
def api_scan_status(scan_id: str):
    """代理 analyzer 的 /scan_status：查询扫描进度。"""
    try:
        r = httpx.get(f"{ANALYZER_URL}/scan_status", params={"scan_id": scan_id}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": str(exc)}


# ---------- 规则解析（离线可用，不依赖模型 tools） ----------

_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|到|至|~|–|—)\s*(\d+(?:\.\d+)?)\s*秒")
_SEC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*秒")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_SPEED_RE = re.compile(r"(?:倍速|变速|加速|x|×)\s*(\d+(?:\.\d+)?)\s*倍?|(\d+(?:\.\d+)?)\s*倍速")



def _resolve_material(name: str, files: list[str]) -> str | None:
    """把用户说的文件名（可能截断/无扩展名）映射到勾选素材。"""
    n = name.lower().strip().lstrip("第")
    # 去掉常见后缀词
    for suf in (".mp4", ".mov", ".mkv", ".avi", ".webm"):
        if n.endswith(suf):
            n = n[: -len(suf)]
            break
    cands = [f for f in files if n and n in f.lower()]
    if len(cands) == 1:
        return cands[0]
    # 前缀匹配（用户常说"5cce 视频"）
    pref = [f for f in files if n and f.lower().startswith(n)]
    if len(pref) == 1:
        return pref[0]
    return None


def _pick_materials(text: str, files: list[str]) -> list[str]:
    """从勾选素材里挑出文本提到的（支持空格分隔多个、'和'、'、'）。"""
    if not files:
        return []
    lower = text.lower()
    # 全选词
    if any(w in lower for w in ("全部", "所有", "全部素材", "都")):
        return list(files)
    hits = []
    # 逐素材匹配：文件名前缀出现在文本中
    for f in sorted(files, key=lambda x: -len(x)):
        stem = f.rsplit("/", 1)[-1]
        base = stem.rsplit(".", 1)[0]
        for key in (base[:12], base[:8], base):
            if key and key.lower() in lower:
                if f not in hits:
                    hits.append(f)
                break
    return hits


# 素材时长缓存（相对计算用，如"剪掉开头5秒"）
_dur_cache: dict[str, float] = {}


def _material_duration(file: str) -> float | None:
    if file in _dur_cache:
        return _dur_cache[file]
    try:
        r = httpx.get(f"{ANALYZER_URL}/material_duration", params={"file": file}, timeout=10)
        if r.status_code == 200:
            d = r.json().get("duration_sec")
            if d:
                _dur_cache[file] = float(d)
                return float(d)
    except Exception:
        pass
    return None


_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|到|至|~|–|—)\s*(\d+(?:\.\d+)?)\s*(分钟|分|秒)?")
_SEC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*秒")
_SPEED_RE = re.compile(r"(?:倍速|变速|加速|放慢|减速|慢放|x|×)\s*(\d+(?:\.\d+)?)\s*倍?|(\d+(?:\.\d+)?)\s*倍速|(\d+(?:\.\d+)?)\s*倍")
_MIN_SEC_RE = re.compile(r"(\d+)\s*分\s*(\d+)\s*秒")
# 剪掉/去掉/删掉 ... (开头|最前面|前面) N 分钟/秒 —— 素材名可能夹在中间
_TRIM_HEAD_RE = re.compile(r"(?:剪掉|去掉|删掉|移除|删除).{0,15}?(?:开头|最前面|前面|头部)?\s*(\d+(?:\.\d+)?)\s*(分钟|分|秒)")
# 保留 3-10 秒 / 保留 2-4 分钟
_KEEP_RE = re.compile(r"保留\s*(\d+(?:\.\d+)?)\s*(?:-|到|至|~)\s*(\d+(?:\.\d+)?)\s*(分钟|分|秒)?")


def _to_sec(v: float, unit: str | None) -> float:
    """时间单位换算：'分钟'/'分' → ×60；无单位/秒 → 原值。"""
    if unit in ("分钟", "分"):
        return v * 60
    return v


# 口语概念词表：用户说"女的/女性" → 匹配文档里的女性标签（宫女/皇后/妃子等）
_CONCEPT_WORDS = {
    "女": ("女", "女性", "女子", "女人", "宫女", "皇后", "妃", "太后", "夫人", "姑娘", "小姐", "奶奶"),
    "男": ("男", "男性", "男子", "男人", "皇帝", "官员", "官", "侍卫", "将军", "僧"),
}


def _concept_target(text: str) -> str | None:
    """从用户文本识别性别等概念词（精确主体匹配失败时的兜底）。"""
    for concept in _CONCEPT_WORDS:
        if any(w in text for w in (f"{concept}的", f"{concept}性", concept)):
            return concept
    return None


def _subject_clip_plan(text: str, files: list[str]) -> tuple[list[dict], str] | None:
    """'把孙悟空的剪出来' / '宫女的吧'：按主体/人物筛选场景，精确到场景边界，多场景自动拼接。

    只要需求里提到了文档里的人物/主体词（不限"剪/只要"等意图词），
    就只保留包含该主体的场景做 trim，避免把相邻的其他人物场景一起剪进来。
    返回 (plan, reply) 或 None（未识别到主体）。
    """
    scene_list = []
    subjects: set[str] = set()
    for f in files:
        src = (MATERIALS / f).resolve()
        if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
            continue
        a = util.load_json(src.parent / "_analysis" / (src.name + ".analysis.json"))
        if not a:
            continue
        for s in a.get("scenes") or []:
            for w in s.get("subjects") or []:
                if w:
                    subjects.add(str(w).strip())
            text_parts = [str(x) for x in (s.get("subjects") or []) if x]
            if s.get("content"):
                text_parts.append(str(s["content"]))
            scene_list.append({"file": f, "scene": s, "text": " ".join(text_parts)})
        for t in a.get("tags") or []:
            t = str(t).strip()
            if len(t) >= 2:
                subjects.add(t)
    if not scene_list:
        return None
    # 用户文本里提到的主体词（优先长词，避免"孙"误配"孙悟空"）
    mentioned = [w for w in sorted(subjects, key=len, reverse=True) if len(w) >= 2 and w in text]
    target = None
    matched = None
    if mentioned:
        target = mentioned[0]
        matched = [sc for sc in scene_list if target in sc["text"]]
    # 策略2：口语概念（"女的"→ 匹配女性标签：宫女/皇后/妃子/女性角色...）
    if not matched:
        concept = _concept_target(text)
        if concept:
            words = _CONCEPT_WORDS[concept]
            matched = [sc for sc in scene_list if any(w in sc["text"] for w in words)]
            if matched:
                target = f"{concept}性角色"
    # 策略3："随便一个人物" → 自动选出现场景最多的主体
    if not matched and any(w in text for w in ("随便", "任意", "随便的", "看看")):
        from collections import Counter

        cnt: Counter = Counter()
        for sc in scene_list:
            for w in subjects:
                if len(w) >= 2 and w in sc["text"]:
                    cnt[w] += 1
        if cnt:
            target = cnt.most_common(1)[0][0]
            matched = [sc for sc in scene_list if target in sc["text"]]
    if not matched:
        return None
    matched.sort(key=lambda sc: sc["scene"]["start"])
    if not matched:
        return None
    matched.sort(key=lambda sc: sc["scene"]["start"])
    plan = []
    for sc in matched:
        s, e = sc["scene"]["start"], sc["scene"]["end"]
        if e and e > s:
            plan.append({"op": "trim", "file": sc["file"], "start": round(s, 1), "end": round(e, 1)})
    if not plan:
        return None
    return plan, f"已理解：只剪包含「{target}」的场景（{len(plan)} 段），已精确对齐场景边界，不连带其他人物。"


def _highlight_plan(files: list[str]) -> tuple[list[dict], list[str]] | None:
    """精彩集锦智能兜底：按场景 energy + 战斗/激烈关键词挑最佳片段。

    每个文件挑最多 2 个得分最高的片段（间隔≥2s），返回 (plan, used)。
    """
    plan: list[dict] = []
    used: list[str] = []
    battle_kw = ("战斗", "对决", "打斗", "爆炸", "激烈", "冲突", "魔法", "能量", "光芒", "攻击", "挥剑", "碰撞")
    for f in files:
        src = (MATERIALS / f).resolve()
        if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
            continue
        a = util.load_json(src.parent / "_analysis" / (src.name + ".analysis.json"))
        if not a or not a.get("scenes"):
            continue
        dur = a.get("duration_sec") or 0
        # 场景打分：medium=1 / high=2，战斗词 +2
        scored = []
        for s in a["scenes"]:
            e = (s.get("energy") or "").lower()
            c = s.get("content") or ""
            score = (2 if e == "high" else 1 if e == "medium" else 0) + (2 if any(k in c for k in battle_kw) else 0)
            scored.append({"start": s.get("start") or 0, "end": s.get("end") or dur, "score": score, "content": c})
        # 合并相邻热区（gap < 5s 视为连续）
        segs = []
        for s in scored:
            if s["score"] < 1:
                continue
            if segs and s["start"] - segs[-1]["end"] < 5.0:
                segs[-1]["end"] = max(segs[-1]["end"], s["end"])
                segs[-1]["score"] = max(segs[-1]["score"], s["score"])
                segs[-1]["content"] += s["content"]
            else:
                segs.append(dict(s))
        if not segs:
            continue
        # 每个片段评分：基础分 - 时长惩罚（20~40s 最佳，过长过短都扣分）
        def seg_score(seg: dict) -> float:
            length = seg["end"] - seg["start"]
            penalty = abs(length - 25) / 10.0
            battle = 2 if any(k in seg["content"] for k in battle_kw) else 0
            return seg["score"] + battle - penalty

        # 每个素材挑最多 2 个得分最高的片段（片段间至少间隔 2s，避免重叠）
        ranked = sorted(segs, key=seg_score, reverse=True)
        picked: list[tuple[float, float]] = []
        for best in ranked:
            start = max(0.0, best["start"])
            end = min(dur, best["end"])
            if end - start < 1.0:
                end = min(dur, start + 3.0)
            if any(not (end <= s - 2 or start >= e + 2) for s, e in picked):
                continue
            picked.append((start, end))
            if len(picked) >= 2:
                break
        for start, end in picked:
            plan.append({"op": "trim", "file": f, "start": round(start, 1), "end": round(end, 1)})
            used.append(f)
    if not plan:
        return None
    return plan, used


def _parse_edl_request(message: str, files: list[str]) -> dict | None:
    """把用户自然语言解析成 EDL。返回 {"edl":..., "reply":...} 或 None（无法解析）。"""
    text = message.strip()
    if not text:
        return None
    picks = _pick_materials(text, files)
    if not picks:
        picks = files  # 没指名就用全部勾选素材

    plan = []
    use = []
    parts = re.split(r"[，,。；;\n]+", text)
    for part in parts:
        p = part.strip()
        if not p:
            continue
        # 1) 保留区间（"保留 3-10 秒" / "保留 2-4 分钟"）
        km = _KEEP_RE.search(p)
        if km:
            start, end = _to_sec(float(km.group(1)), km.group(3)), _to_sec(float(km.group(2)), km.group(3))
            if end < start:
                start, end = end, start
            for f in picks:
                plan.append({"op": "trim", "file": f, "start": start, "end": end})
            use.extend(picks)
        # 2) 剪掉开头 X 秒/分钟（需要素材时长算保留区间）
        th = _TRIM_HEAD_RE.search(p)
        if th and not km:
            cut = _to_sec(float(th.group(1)), th.group(2))
            for f in picks:
                dur = _material_duration(f)
                if dur:
                    plan.append({"op": "trim", "file": f, "start": cut, "end": dur})
                else:
                    plan.append({"op": "trim", "file": f, "start": cut, "end": 0.0})  # 未知时长则整段之后
                use.append(f)
        # 3) 时间区间（"3-10 秒" / "3到10秒" / "2-4分钟"）
        m = _TIME_RE.search(p)
        if m and not km and not th:
            start, end = _to_sec(float(m.group(1)), m.group(3)), _to_sec(float(m.group(2)), m.group(3))
            if end < start:
                start, end = end, start
            for f in picks:
                plan.append({"op": "trim", "file": f, "start": start, "end": end})
            use.extend(picks)
        # 4) 分钟:秒（"1分30秒"）
        ms = _MIN_SEC_RE.search(p)
        if ms and not any(o["op"] == "trim" for o in plan if o.get("file") in picks):
            start, end = float(ms.group(1)) * 60, float(ms.group(1)) * 60 + float(ms.group(2))
            for f in picks:
                plan.append({"op": "trim", "file": f, "start": start, "end": end})
            use.extend(picks)
        # 5) 倍速（"2倍速"=2x，"0.5倍速"=0.5x，"放慢一半"→0.5x）
        sm = _SPEED_RE.search(p)
        if sm:
            factor = float(sm.group(1) or sm.group(2) or sm.group(3) or 1)
            if any(w in p for w in ("放慢", "减速", "慢放")):
                if factor > 1:  # "放慢2倍" → 0.5x（慢一半）
                    factor = 1 / factor
                # "放慢0.5倍" → 0.5x（更慢）
            factor = max(0.25, min(4.0, factor))
            for f in picks:
                plan.append({"op": "speed", "file": f, "factor": factor})
            use.extend(picks)
        # 6) 去静音/只保留有声音（remove_silence）
        has_silence_intent = (
            any(w in p for w in ("去静音", "去掉静音", "剪掉静音", "只剪有声音", "只剪辑有声音", "只保留有声音", "去掉无声", "只要声音", "要声音的部分", "没声音", "无声的部分"))
            or ("静音" in p and ("去掉" in p or "剪掉" in p or "去除" in p or "删除" in p))
            or ("有声音" in p and ("只" in p or "仅" in p or "就要" in p or "要" in p))
        )
        if has_silence_intent:
            for f in picks:
                plan.append({"op": "remove_silence", "file": f, "threshold_db": -35, "margin_sec": 0.3})
            use.extend(picks)
        # 7) 拼接/导出
        if "拼接" in p or "合并" in p or "接起来" in p or "连起来" in p or "合在一起" in p:
            plan.append({"op": "concat", "files": picks})
            use.extend(picks)
        if "导出" in p or "输出" in p or "出片" in p or "生成" in p:
            plan.append({"op": "export", "format": "mp4", "resolution": "720p", "crf": 23})

    if not plan:
        # 按主体/人物筛选场景（"把孙悟空的剪出来" → 只剪含孙悟空的场景）
        sp = _subject_clip_plan(text, picks)
        if sp:
            sp_plan, sp_reply = sp
            plan = sp_plan
            use = list(dict.fromkeys(o["file"] for o in plan))
            concat_files = []
            for f in dict.fromkeys(use):
                cnt = sum(1 for o in plan if o["op"] == "trim" and o["file"] == f)
                concat_files.extend(f if i == 0 else f"{f}#{i}" for i in range(cnt))
            if len(concat_files) > 1:
                plan.append({"op": "concat", "files": concat_files})
            plan.append({"op": "export", "format": "mp4", "resolution": "720p", "crf": 23})
            req_id = f"clip-{int(time.time())}"
            return {
                "edl": {"schema_version": 1, "request_id": req_id, "user_request": text, "plan": plan},
                "reply": sp_reply,
            }
        # 精彩集锦/高能片段 类需求：按场景 energy 智能选段
        if any(w in text for w in ("集锦", "高能", "精彩", "最激烈", "高潮", "燃", "战斗部分", "名场面", "亮点", "气势", "名场面", "精华", "大片感")):
            hp = _highlight_plan(picks)
            if hp:
                hp_plan, hp_use = hp
                plan = hp_plan
                use = hp_use
                # 拼接所有挑选的片段 → 集锦（同一素材多段用 file#n 引用，第1段用原名）
                concat_files = []
                for f in dict.fromkeys(hp_use):
                    cnt = sum(1 for o in plan if o["op"] == "trim" and o["file"] == f)
                    concat_files.extend(f if i == 0 else f"{f}#{i}" for i in range(cnt))
                plan.append({"op": "concat", "files": concat_files})
                use = list(dict.fromkeys(hp_use))
                plan.append({"op": "export", "format": "mp4", "resolution": "720p", "crf": 23})
                names = "、".join(f.rsplit("/", 1)[-1][:12] for f in use) or "选中素材"
                req_id = f"clip-{int(time.time())}"
                return {
                    "edl": {
                        "schema_version": 1,
                        "request_id": req_id,
                        "user_request": text,
                        "plan": plan,
                    },
                    "reply": f"已理解：自动挑选「{names}」中 energy 最高的精彩片段拼接成集锦。请确认预览后出片。",
                }
        # 退路：没识别到任何操作，提示可用指令
        return {
            "edl": None,
            "reply": (
                "我没听懂你要的操作，试试这样说：\n"
                "· 「保留 5cce 3-10 秒」（取 3~10 秒片段）\n"
                "· 「剪掉 5cce 开头 5 秒」\n"
                "· 「只保留有声音的」/「去静音」（自动剪掉无声段）\n"
                "· 「5cce 1.5 倍速」或「放慢 0.5 倍」\n"
                "· 「把选中的接起来，导出 720p」\n"
                "· 不说素材名 = 对勾选的全部素材生效"
            ),
        }

    # 默认加导出（有素材操作时自动收尾）
    if not any(op["op"] == "export" for op in plan):
        plan.append({"op": "export", "format": "mp4", "resolution": "720p", "crf": 23})

    # 保证依赖顺序：trim → remove_silence → speed → concat → export
    rank = {"trim": 0, "remove_silence": 1, "speed": 2, "concat": 3, "export": 4}
    plan.sort(key=lambda o: rank.get(o["op"], 0))

    req_id = f"clip-{int(time.time())}"
    edl = {
        "schema_version": 1,
        "request_id": req_id,
        "user_request": text,
        "plan": plan,
    }
    names = "、".join(f.rsplit("/", 1)[-1][:12] for f in dict.fromkeys(use)) or "选中素材"
    ops_txt = " → ".join(o["op"] for o in plan)
    return {
        "edl": edl,
        "reply": f"已理解：对「{names}」执行 [{ops_txt}]。请确认预览后出片。",
    }


def _estimate_docs_chars(files: list[str]) -> int:
    """估算选中素材【完整】分析文档的总字符数（用于超限检测，不压缩）。"""
    total = 0
    for f in files:
        a = _material_doc(f)
        if not a:
            continue
        parts = [a.get("summary") or ""]
        parts.extend(str(x) for x in (a.get("tags") or []))
        for s in a.get("scenes") or []:
            parts.append(s.get("content") or "")
            parts.extend(str(x) for x in (s.get("subjects") or []))
        for sp in a.get("speech") or []:
            parts.append(sp.get("text") or "")
        total += sum(len(p) for p in parts)
    return total


@app.post("/chat")
def chat(req: ChatRequest):
    # 日志：记录用户输入，方便迭代规则
    print(f"[chat] files={len(req.files or [])} msg={req.message[:200]!r}")
    session = f"chat-{int(time.time())}"
    if not req.files:
        return {"reply": "请先在上方勾选至少一个素材，再输入剪辑指令。", "edl": None, "preview": None, "session": session}
    files = req.files or []
    _plog(session, f"📥 收到需求：{req.message}（勾选 {len(files)} 个素材）")
    # 方案一：规则引擎优先（明确指令时确定性最强：保留/剪掉/倍速/拼接/去静音）
    parsed = _parse_edl_request(req.message, files)
    if parsed and parsed.get("edl"):
        edl = parsed["edl"]
        _plog(session, f"⚙️ 规则引擎命中：{parsed['reply']}")
        try:
            _plog(session, "🔍 预览校验方案（调用 executor）...")
            r = httpx.post(f"{EXECUTOR_URL}/preview", json={"edl": edl}, timeout=60)
            preview = r.json() if r.status_code == 200 else {"error": r.text}
            if not (isinstance(preview, dict) and preview.get("error")):
                _plog(session, f"✅ 预览通过：{' → '.join(preview.get('plan') or [])}")
        except Exception as exc:
            preview = {"error": str(exc)}
            _plog(session, f"❌ 预览失败：{exc}")
        return {"reply": parsed["reply"], "edl": edl, "preview": preview, "session": session}
    # 方案二：模型理解兜底（模糊需求）——两阶段流水线：
    # ① 逐素材筛选（7B 分析需求+读文档，保留相关场景，剔除无关）
    # ② 汇总保留场景 → 7B 生成 EDL
    _plog(session, "🧠 规则引擎未覆盖，交给对话模型分析（两阶段流水线）...")
    model_edl = _llm_pipeline(req.message, files, session)
    if model_edl and model_edl.get("_chat_reply"):
        _plog(session, f"💬 模型回复：{model_edl['_chat_reply'][:80]}")
        return {"reply": model_edl["_chat_reply"], "edl": None, "preview": None, "session": session}
    if model_edl:
        _plog(session, "🔍 预览校验方案（调用 executor）...")
        try:
            r = httpx.post(f"{EXECUTOR_URL}/preview", json={"edl": model_edl}, timeout=60)
            preview = r.json() if r.status_code == 200 else {"error": r.text}
        except Exception as exc:
            preview = {"error": str(exc)}
        if not (isinstance(preview, dict) and preview.get("error")):
            _plog(session, f"✅ 预览通过：{' → '.join(preview.get('plan') or [])}")
            return {
                "reply": "已理解你的需求，方案如下（点击执行即可出片）：",
                "edl": model_edl,
                "preview": preview,
                "session": session,
            }
    # 规则引擎也无法解析时的提示文案
    if parsed is not None and parsed.get("reply"):
        return {"reply": parsed["reply"], "edl": None, "preview": None, "session": session}
    return {"reply": "请勾选至少一个素材再输入指令。", "edl": None, "preview": None, "session": session}


# 过程日志：/chat 流水线把思考/处理过程记录到内存（配 /api/process_log 供 API 客户端轮询）
_PROCESS_LOGS: dict[str, list[dict]] = {}
_plog_lock = threading.Lock()


def _plog(session: str, msg: str):
    """记录一条过程日志（前端可轮询展示）。"""
    with _plog_lock:
        _PROCESS_LOGS.setdefault(session, []).append({"t": time.strftime("%H:%M:%S"), "msg": msg})


@app.get("/api/process_log")
def api_process_log(session: str):
    """查询某次处理的过程日志（前端实时展示思考过程）。"""
    with _plog_lock:
        return {"session": session, "logs": list(_PROCESS_LOGS.get(session, []))}


class GenScriptRequest(BaseModel):
    instruction: str = "根据这些素材的文档内容，魔改一版分镜剧本"
    files: list[str] = []


@app.post("/api/generate_script")
def api_generate_script(req: GenScriptRequest):
    """把所有素材场记（含台词）+ 指令 → 线上 DeepSeek（填 Key）或本地 7B → 生成"向量友好"剧本，
    并自动逐镜头向量匹配场记，返回每个镜头的候选场景（剧本一段 ↔ 场景一段，勾选即剪）。
    """
    if not req.files:
        return {"error": "请先勾选素材"}
    docs = _script_docs(req.files)
    if not docs:
        return {"error": "素材未分析，请先分析素材"}
    system = (
        "你是资深影视编剧。下面会给你【全部素材的场记单】（每个场景：时间轴、画面描述、人物、台词、标签）。"
        "请根据场记单魔改一版分镜剧本。\n\n"
        "【与编剧的约定——必须严格遵守，这决定剧本能否被向量检索匹配到】\n"
        "1. 每个镜头的 prompt（画面描述）必须【直接采用或复述场记里的词汇】：人物名（孙悟空/皇后/官员/唐僧）、"
        "动作（行礼/对话/祭祀/跪拜）、场景（金銮殿/室内/庭院）、服饰颜色（蓝官袍/绿长袍/黄衣）、道具（香炉/金箍棒）、"
        "以及台词里的关键短语（比如'陛下''臣'）。\n"
        "2. 【禁止文艺化改写】：不要把『身穿蓝色官袍的官员在室内向皇后行礼，周围有宫女侍立』写成『官僚朝拜的庄严时刻』。"
        "因为剧本将直接交给向量搜索匹配场记，用词越贴近场记原文，匹配越准。宁可写大白话，也不要文学修辞。\n"
        "3. 镜头内容必须来自场记（人物/场景/剧情都基于素材），禁止编造场记里没有的内容。\n"
        "4. 可以重复使用素材片段（同一画面在不同镜头出现允许）。\n"
        "5. 每个镜头还要给出 dialogue（该段对白，尽量用场记台词原文）、narration（旁白，可空）、duration（秒，3~12 之间）。\n"
        "6. 只输出严格 JSON：{\"title\":\"剧本名\",\"shots\":[{\"id\":1,\"prompt\":\"用场记词汇写的画面描述\",\"dialogue\":\"对白\",\"narration\":\"旁白\",\"duration\":8}]}，"
        "不要 markdown、不要解释。"
    )
    user_txt = f"{req.instruction}\n\n全部素材场记单：\n" + "\n\n".join(docs)
    try:
        msg = _llm_chat_script([{"role": "system", "content": system}, {"role": "user", "content": user_txt}])
        edl = util.parse_llm_json(msg.get("content") or "")
        if edl and edl.get("shots"):
            model_name = GEN_SCRIPT_MODEL if GEN_SCRIPT_API_KEY else PLANNER_MODEL
            matches = _match_shot_scenes(edl["shots"], req.files)
            return {"script": edl, "model": model_name, "matches": matches}
        return {"error": "生成失败：模型未返回有效剧本", "raw": (msg.get("content") or "")[:300]}
    except Exception as exc:
        return {"error": f"生成失败: {exc}"}


def _script_docs(files: list[str]) -> list[str]:
    """生成剧本时喂给模型的场记文档：场景描述 + 人物 + 该段台词（向量匹配侧也用这些字段）。

    台词能让模型写出和场记检索文本更贴合的 prompt；每段只截 80 字控制 token。
    总量超预算时降级为"仅场景内容 + 时间轴"。
    """
    docs = []
    for f in files:
        a = _material_doc(f)
        if not a:
            continue
        head = f"【{f}】摘要:{a.get('summary')} 标签:{'/'.join(a.get('tags') or [])}"
        lines = [head]
        for s in a.get("scenes") or []:
            ss, se = float(s.get("start", 0)), float(s.get("end", 0))
            parts = [f"[{ss:.1f}-{se:.1f}s] {s.get('content', '')}"]
            subs = [str(x) for x in (s.get("subjects") or []) if x]
            if subs:
                parts.append("人物:" + "/".join(subs))
            sp = _scene_speech(a, ss, se)
            if sp:
                parts.append("台词:" + " ".join(x.get("text", "") for x in sp)[:80])
            lines.append(" ".join(parts))
        docs.append("\n".join(lines))
    total = sum(len(d) for d in docs)
    if total > 9000:  # 本地 7B 上下文 8K 的保守预算：降级为纯场景时间轴
        docs = []
        for f in files:
            a = _material_doc(f)
            if not a:
                continue
            head = f"【{f}】摘要:{a.get('summary')} 标签:{'/'.join(a.get('tags') or [])}"
            lines = [head]
            for s in a.get("scenes") or []:
                lines.append(f"[{s.get('start', 0):.1f}-{s.get('end', 0):.1f}s] {(s.get('content') or '')[:60]}")
            docs.append("\n".join(lines))
    return docs


# ---------- 场记单 + 向量检索 + 勾选剪片（文档为主，检索辅助，人决策） ----------

# 本地 Ollama（bge-m3 向量化 / 模型加载状态 / 卸载）——与对话模型地址解耦：
# PLANNER_BASE_URL 切到线上 DeepSeek 时，bge-m3 仍走本地 Ollama
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434").rstrip("/")


def _embed(texts: list[str]) -> list[list[float]]:
    """调 Ollama bge-m3 批量文本向量化（本地、毫秒级、确定性）。"""
    if not texts:
        return []
    try:
        r = httpx.post(f"{OLLAMA_BASE}/api/embed", json={"model": "bge-m3", "input": texts}, timeout=120)
        r.raise_for_status()
        return r.json().get("embeddings") or []
    except Exception as exc:
        print(f"[embed] 失败: {exc}")
        return []


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


@app.get("/api/model_status")
def api_model_status():
    """查询 Ollama 当前已加载的模型（用于页面显示'模型加载中'提示）。"""
    try:
        r = httpx.get(f"{OLLAMA_BASE}/api/ps", timeout=5)
        r.raise_for_status()
        loaded = [m.get("name", "") for m in (r.json().get("models") or []) if m.get("name")]
        return {"ok": True, "loaded": loaded, "vlm_model": VLM_MODEL}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "loaded": [], "vlm_model": VLM_MODEL}


@app.post("/api/model_unload")
def api_model_unload(req: dict = None):
    """立即卸载指定模型（默认分析模型 qwen2.5vl:3b），释放显存。"""
    model = (req or {}).get("model") or VLM_MODEL
    try:
        # keep_alive=0 + 空 prompt：Ollama 会立即把该模型从内存/显存卸载
        r = httpx.post(f"{OLLAMA_BASE}/api/generate", json={"model": model, "keep_alive": 0, "prompt": ""}, timeout=60)
        r.raise_for_status()
        return {"ok": True, "unloaded": model}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "unloaded": model}


def _scene_speech(a: dict, start: float, end: float) -> list[dict]:
    """取某场景时间范围内的台词。"""
    return [
        {"start": sp.get("start"), "end": sp.get("end"), "text": sp.get("text", "")}
        for sp in (a.get("speech") or [])
        if sp.get("start", -1) >= start - 0.1 and sp.get("end", 1e9) <= end + 0.1
    ]


@app.get("/api/ledger")
def api_ledger(file: str):
    """场记单：单素材完整场景清单（时间轴/描述/人物/台词/关键帧），时间戳来自文档。"""
    a = _material_doc(file)
    if not a:
        return {"error": "素材未分析或不存在"}
    scenes = []
    for s in a.get("scenes") or []:
        ss, se = float(s.get("start", 0)), float(s.get("end", 0))
        scenes.append(
            {
                "start": round(ss, 1),
                "end": round(se, 1),
                "content": s.get("content", ""),
                "subjects": s.get("subjects") or [],
                "energy": s.get("energy", ""),
                "type": s.get("type", ""),
                "speech": _scene_speech(a, ss, se),
                "keyframes": s.get("keyframes") or [],
            }
        )
    return {"file": file, "duration": a.get("duration_sec"), "summary": a.get("summary"), "scenes": scenes}


def _scene_search_text(a: dict, s: dict, file: str = "") -> str:
    """场景的"向量化文本"：喂给 bge-m3 的内容越全越规范，检索越准。

    拼上：文件名 + 场景描述 + 人物 + 素材标签 + 素材摘要 + 该段台词（截断）。
    """
    ss, se = float(s.get("start", 0)), float(s.get("end", 0))
    speech = " ".join(sp.get("text", "") for sp in _scene_speech(a, ss, se))[:200]
    parts = [
        file.split("/")[-1] if file else "",
        s.get("content", ""),
        "人物:" + "/".join(str(x) for x in (s.get("subjects") or [])),
        "标签:" + "/".join(str(x) for x in (a.get("tags") or [])),
    ]
    if a.get("summary"):
        parts.append("素材摘要:" + str(a["summary"])[:150])
    if speech:
        parts.append("台词:" + speech)
    return " ".join(p for p in parts if p)


class LedgerSearchRequest(BaseModel):
    query: str
    files: list[str] = []


# 场记向量索引缓存：分析完/搜索时把场景向量化存内存，搜索毫秒级（无卡点）
_LEDGER_INDEX: dict[str, list[dict]] = {}  # file -> [{start,end,content,subjects,keyframes,speech,vector}]
_LEDGER_INDEX_FILES: list[str] = []
_LEDGER_INDEX_MTIME: dict[str, float] = {}
_index_lock = threading.Lock()


def _analysis_mtime(f: str) -> float:
    """素材分析文档的修改时间（用于索引自动失效重建）。"""
    src = (MATERIALS / f).resolve()
    if not src.exists():
        return 0.0
    p = src.parent / "_analysis" / (src.name + ".analysis.json")
    try:
        return p.stat().st_mtime if p.exists() else 0.0
    except OSError:
        return 0.0


def _ensure_ledger_index(files: list[str]):
    """懒建索引：选中素材的场记场景一次性向量化缓存；文件列表或分析文档变化时重建。"""
    global _LEDGER_INDEX, _LEDGER_INDEX_FILES, _LEDGER_INDEX_MTIME
    files = sorted(files)
    mt = {f: _analysis_mtime(f) for f in files}
    with _index_lock:
        if _LEDGER_INDEX_FILES == files and _LEDGER_INDEX and mt == _LEDGER_INDEX_MTIME:
            return
        idx: dict[str, list[dict]] = {}
        texts: list[str] = []
        meta: list[tuple[str, dict, dict]] = []  # (file, analysis, scene)
        for f in files:
            a = _material_doc(f)
            if not a:
                continue
            for s in a.get("scenes") or []:
                meta.append((f, a, s))
                texts.append(_scene_search_text(a, s, f))
        embs = _embed(texts) if texts else []
        for (f, a, s), vec in zip(meta, embs):
            ss, se = float(s.get("start", 0)), float(s.get("end", 0))
            idx.setdefault(f, []).append(
                {
                    "start": round(ss, 1),
                    "end": round(se, 1),
                    "content": s.get("content", ""),
                    "subjects": s.get("subjects") or [],
                    "speech": _scene_speech(a, ss, se),
                    "keyframes": s.get("keyframes") or [],
                    "vector": vec,                      # bge-m3 描述向量（文字通道）
                    "clip_vec": s.get("clip_vec") or [],  # CLIP 画面向量（画面通道）
                }
            )
        _LEDGER_INDEX = idx
        _LEDGER_INDEX_FILES = files
        _LEDGER_INDEX_MTIME = mt
        print(f"[ledger] 索引重建：{len(meta)} 个场景")


def _match_shot_scenes(shots: list[dict], files: list[str], top_k: int = 5) -> list[dict]:
    """剧本逐镜头 → 向量检索场记 → 每镜头返回候选场景（双通道加权融合）。

    这就是"剧本一段 ↔ 场景一段"：每个镜头描述向量化后和全库场记比对，
    最像的几段场景按相似度排出来（file/start/end 都来自场记单，剪出来必然对）。
    文字通道（bge-m3 匹配描述）+ 画面通道（CLIP 匹配关键帧）加权融合，画面通道兜底低清。
    """
    _ensure_ledger_index(files)
    out = []
    for shot in shots:
        prompt = str(shot.get("prompt") or "").strip()
        sid = shot.get("id")
        if not prompt:
            continue
        qemb = _embed([prompt])
        qclip = clip_client.text_embed_single(prompt)
        scored: list[tuple[dict, float, float | None, str]] = []
        with _index_lock:
            for f, scenes in _LEDGER_INDEX.items():
                if f not in files:
                    continue
                for sc in scenes:
                    s_text = _cosine(sc["vector"], qemb[0]) if qemb else None
                    s_clip = None
                    if qclip is not None and sc.get("clip_vec"):
                        s_clip = float(clip_client.cosine(sc["clip_vec"], qclip))
                    if s_text is None and s_clip is None:
                        continue
                    scored.append((sc, s_text, s_clip, f))
        # 加权融合排序：画面通道（剧本 prompt 描述的就是画面）权重更高
        # （只读缓存条目，融合分放在元组里算，不往缓存写临时字段，避免污染后续请求）
        ranked = []
        for sc, st, sc2, _f in scored:
            if sc2 is not None and st is not None:
                rrf = 0.6 * sc2 + 0.4 * st
            elif sc2 is not None:
                rrf = sc2
            else:
                rrf = st if st is not None else 0.0
            ranked.append((sc, st, sc2, _f, rrf))
        ranked.sort(key=lambda x: -x[4])
        cands = []
        for sc, st, sc2, _f, rrf in ranked[:top_k]:
            c = dict(sc)
            c.pop("vector", None)
            c.pop("clip_vec", None)
            c["file"] = _f
            c["score"] = round(rrf, 4)
            if sc2 is not None and st is not None:
                c["score_text"], c["score_clip"] = round(st, 3), round(sc2, 3)
            cands.append(c)
        out.append({"id": sid, "prompt": prompt, "dialogue": shot.get("dialogue", ""), "duration": shot.get("duration"), "candidates": cands})
    return out


@app.post("/api/ledger_search")
def api_ledger_search(req: LedgerSearchRequest):
    """跨素材向量检索（预建索引 + 内存比对，毫秒级）。

    双通道融合：
    - 文字通道：查询 → bge-m3 → 匹配场景"描述向量"（VLM 写的内容/人物/台词）
    - 画面通道：查询 → Chinese-CLIP → 匹配场景"画面向量"（关键帧 CLIP 编码）
    两通道按权重加权平均（画面 0.55 / 文字 0.45），低清视频描述不准时画面通道兜底。
    """
    if not req.query or not req.files:
        return {"error": "需要搜索词和素材"}
    _ensure_ledger_index(req.files)
    # 文字通道查询向量
    qemb = _embed([req.query])
    if not qemb:
        return {"error": "向量化失败（bge-m3 未就绪）"}
    # 画面通道查询向量（Chinese-CLIP，首次调用会加载模型 ~10s）
    qclip = clip_client.text_embed_single(req.query)
    rank_clip = bool(qclip is not None and any(
        sc.get("clip_vec") for scenes in _LEDGER_INDEX.values() if scenes for sc in scenes
    ))

    results = []
    with _index_lock:
        for f, scenes in _LEDGER_INDEX.items():
            if f not in req.files:
                continue
            for sc in scenes:
                r = dict(sc)
                r.pop("vector", None)
                r.pop("clip_vec", None)
                r["file"] = f
                # 双通道融合：文字 + 画面 加权平均（画面通道缺失时退化为纯文字）
                st = _cosine(sc["vector"], qemb[0])
                sc2 = None
                if qclip is not None and sc.get("clip_vec"):
                    sc2 = float(clip_client.cosine(sc["clip_vec"], qclip))
                r["score_text"] = round(st, 3)
                if sc2 is not None:
                    # 画面通道可信时两者加权（画面 0.55 / 文字 0.45，画面兜底低清描述不准）
                    r["score_clip"] = round(sc2, 3)
                    r["score"] = round(0.55 * sc2 + 0.45 * st, 4)
                else:
                    r["score_clip"] = None
                    r["score"] = round(st, 4)
                results.append(r)
    results.sort(key=lambda x: -x["score"])
    return {"query": req.query, "total": len(results), "results": results[:60], "channels": "text+clip" if rank_clip else "text"}


class ScriptMatchRequest(BaseModel):
    script: dict
    files: list[str] = []


# ---------- AI 自动剪辑：需求+目标时长 → 向量打分选段 → 自动出片（不依赖视觉大模型） ----------
# 信号全部来自确定性/轻量模型：场景切点(OpenCV) + 画面向量(Chinese-CLIP) + 描述向量(bge-m3)
# + 台词(ASR) + 动量能量(帧间差异)。时间戳全部来自场记（分析文档），剪出来必然对。

class AutoEditRequest(BaseModel):
    files: list[str] = []
    query: str = ""            # 想要什么（如"孙悟空打斗"）；空 = 不按语义，均匀精选铺满目标时长
    target_sec: float = 60.0   # 目标成片时长（秒）
    order: str = "time"        # time=按素材+时间线顺序（叙事连贯）/ score=按相关度从高到低（集锦感）
    execute: bool = True       # True=直接无损出片（秒级）；False=只返回方案不执行
    max_segments: int = 40     # 最多选多少段，防止碎成几百段


def _scene_pool(files: list[str]) -> list[dict]:
    """选中素材的全部场记场景摊平成候选池（只读索引的浅拷贝，逐条带上 file）。"""
    _ensure_ledger_index(files)
    pool: list[dict] = []
    with _index_lock:
        for f, scenes in _LEDGER_INDEX.items():
            if f not in files:
                continue
            for sc in scenes:
                item = dict(sc)
                item["file"] = f
                pool.append(item)
    return pool


def _score_pool(query: str, pool: list[dict]) -> bool:
    """原地给候选池打分。有 query：画面0.55/文字0.45 双通道融合；无 query：时长适配+台词占比启发式。"""
    if query:
        qemb = _embed([query])
        if not qemb:
            return False
        qclip = clip_client.text_embed_single(query)
        for sc in pool:
            st = _cosine(sc.get("vector") or [], qemb[0])
            s_clip = None
            if qclip is not None and sc.get("clip_vec"):
                s_clip = float(clip_client.cosine(sc["clip_vec"], qclip))
            if s_clip is not None:
                sc["score"] = 0.55 * s_clip + 0.45 * st
                sc["score_text"], sc["score_clip"] = round(st, 3), round(s_clip, 3)
            else:
                sc["score"] = st
        return True
    for sc in pool:
        dur = (sc.get("end") or 0) - (sc.get("start") or 0)
        fit = 1.0 if 8 <= dur <= 40 else (0.5 if 4 <= dur <= 60 else 0.2)
        speech_len = sum((x.get("end") or 0) - (x.get("start") or 0) for x in sc.get("speech") or [])
        speech_ratio = min(1.0, speech_len / dur) if dur else 0.0
        sc["score"] = 0.7 * fit + 0.3 * speech_ratio
    return True


def _auto_select(pool: list[dict], files: list[str], target_sec: float, order: str, max_segments: int) -> list[dict]:
    """贪心选段：按分数从高到低拿，跳过重叠/明显超时长的，凑够目标时长后收手。"""
    picked: list[dict] = []
    total = 0.0
    for sc in sorted(pool, key=lambda s: -(s.get("score") or 0)):
        if len(picked) >= max(1, max_segments):
            break
        dur = (sc.get("end") or 0) - (sc.get("start") or 0)
        if dur < 1.0:
            continue
        # 同素材时间重叠去重
        if any(
            p["file"] == sc["file"] and not (sc["end"] <= p["start"] + 0.05 or sc["start"] >= p["end"] - 0.05)
            for p in picked
        ):
            continue
        # 已有选段时，加上会明显超过目标（>1.3×）的就跳过，让更短的补位
        if picked and total + dur > target_sec * 1.3:
            continue
        picked.append(sc)
        total += dur
        if total >= target_sec * 0.9:
            break
    if not picked:
        return []
    if order == "score":
        picked.sort(key=lambda s: -(s.get("score") or 0))
    else:
        pos = {f: i for i, f in enumerate(files)}
        picked.sort(key=lambda s: (pos.get(s["file"], 1 << 30), s.get("start") or 0))
    return picked


def _edl_from_pairs(pairs: list[tuple[str, float, float]], request_text: str) -> dict:
    """(file,start,end) 序列 → EDL。concat key 严格按序列顺序生成，保证出片顺序=勾选顺序。"""
    plan: list[dict] = []
    keys: list[str] = []
    seen: dict[str, int] = {}
    for f, s, e in pairs:
        n = seen.get(f, 0)
        keys.append(f if n == 0 else f"{f}#{n}")
        seen[f] = n + 1
        plan.append({"op": "trim", "file": f, "start": round(s, 1), "end": round(e, 1)})
    if len(keys) > 1:
        plan.append({"op": "concat", "files": keys})
    plan.append({"op": "export", "format": "mp4", "resolution": "原样", "crf": 23})
    return {
        "schema_version": 1,
        "request_id": f"auto-{int(time.time())}",
        "user_request": request_text,
        "plan": plan,
    }


@app.post("/api/auto_edit")
def api_auto_edit(req: AutoEditRequest):
    """AI 自动剪辑：一句话需求 + 目标时长 → 全库场景打分 → 自动选段 → 无损出片。

    一次调用完成"理解需求 → 找素材 → 定区间 → 剪辑"全链路，不需要视觉大模型参与。
    """
    if not req.files:
        return {"error": "请先勾选素材"}
    pool = _scene_pool(req.files)
    if not pool:
        return {"error": "素材没有场景数据，请先在目录页完成分析"}
    if not _score_pool(req.query.strip(), pool):
        return {"error": "向量化失败（bge-m3 未就绪，请确认 Ollama 在运行）"}
    target = max(5.0, min(3600.0, float(req.target_sec or 60)))
    picked = _auto_select(pool, req.files, target, req.order, req.max_segments)
    if not picked:
        return {"error": "没有可用的场景片段"}
    selections = [
        {
            "file": p["file"],
            "start": p["start"],
            "end": p["end"],
            "score": round(p.get("score") or 0, 3),
            "content": p.get("content", ""),
            "speech": p.get("speech") or [],
        }
        for p in picked
    ]
    est = sum((p.get("end") or 0) - (p.get("start") or 0) for p in picked)
    edl = _edl_from_pairs([(p["file"], p["start"], p["end"]) for p in picked], req.query.strip() or "AI 自动剪辑")
    resp = {
        "query": req.query.strip(),
        "target_sec": target,
        "estimated_sec": round(est, 1),
        "selections": selections,
        "edl": edl,
        "output": None,
    }
    if not req.execute:
        try:
            r = httpx.post(f"{EXECUTOR_URL}/preview", json={"edl": edl}, timeout=60)
            resp["preview"] = r.json() if r.status_code == 200 else {"error": r.text}
        except Exception as exc:
            resp["preview"] = {"error": str(exc)}
        return resp
    # 无损路径（trim copy + concat copy + 原样导出）通常秒级，直接出片
    try:
        r = httpx.post(f"{EXECUTOR_URL}/execute", json={"edl": edl}, timeout=3600)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
    except Exception as exc:
        data = {"error": str(exc)}
    resp["output"] = data.get("output")
    resp["job"] = data.get("job")
    resp["steps_ok"] = data.get("steps_ok")
    if data.get("error"):
        resp["error"] = data["error"]
    return resp


# ---------- 剪映草稿 / SRT 导出：把选区交给专业剪辑软件精修 ----------
class ExportSel(BaseModel):
    file: str
    start: float
    end: float


class ExportJianyingRequest(BaseModel):
    name: str = "AI粗剪"
    selections: list[ExportSel]
    copy_to_jianying: bool = True


class ExportSrtRequest(BaseModel):
    name: str = "AI粗剪"
    selections: list[ExportSel]


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", (name or "").strip()).strip("_") or "AI粗剪"


def _jianying_draft_dir() -> Path | None:
    """探测剪映草稿库目录（JIANYING_DRAFT_DIR 环境变量优先），找不到返回 None。"""
    env = os.environ.get("JIANYING_DRAFT_DIR")
    cands = [Path(env)] if env else []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        base = Path(la) / "JianyingPro" / "User Data" / "Projects"
        cands += [base / "com.lveditor.draft", base / "com.lveditor.draft.videoeditor"]
    for c in cands:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return None


def _export_materials_info(files: list[str]) -> dict:
    """导出用的素材信息：绝对路径 + 时长/分辨率/音轨（剪映 material 条目要这些）。"""
    out: dict[str, dict] = {}
    for f in dict.fromkeys(files):  # 去重保序
        src = (MATERIALS / f).resolve()
        if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
            raise HTTPException(404, f"素材不存在: {f}")
        info = media.probe(src)
        res = str(info.get("resolution") or "")
        w, h = (int(x) for x in res.split("x")) if "x" in res else (0, 0)
        out[f] = {
            "path": str(src),
            "duration_sec": float(info.get("duration_sec") or 0),
            "width": w,
            "height": h,
            "has_audio": bool(info.get("has_audio")),
        }
    return out


def _export_sels(sels: list[ExportSel]) -> tuple[list[dict], dict]:
    """选区清洗：去掉过短段，末端截到素材时长内。"""
    infos = _export_materials_info([s.file for s in sels])
    out = []
    for s in sels:
        dur_src = infos[s.file]["duration_sec"]
        start = max(0.0, float(s.start))
        end = min(float(s.end), dur_src) if dur_src else float(s.end)
        if end - start > 0.05:
            out.append({"file": s.file, "start": round(start, 3), "end": round(end, 3)})
    if not out:
        raise HTTPException(400, "没有可导出的片段（选区过短或越界）")
    return out, infos


@app.post("/api/export_jianying")
def api_export_jianying(req: ExportJianyingRequest):
    """选区 → 剪映草稿（draft_content.json + draft_meta_info.json）。

    - 写到 output/exports/<草稿名>/，同时打 zip 供下载
    - 找到剪映草稿库（%LOCALAPPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft）
      则自动复制进去，剪映首页刷新即见
    - 轨道为单视频轨顺序拼接，时间单位微秒；剪映打开时自动补全其余字段
    """
    try:
        sels, infos = _export_sels(req.selections)
    except HTTPException as exc:
        return {"error": exc.detail}
    draft_name = f"{_safe_filename(req.name)}_{time.strftime('%Y%m%d_%H%M%S')}"
    first = infos[sels[0]["file"]]
    canvas = (first["width"], first["height"]) if first["width"] and first["height"] else (1920, 1080)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    folder = jianying_draft.write_draft(EXPORTS_DIR / draft_name, sels, name=draft_name,
                                        materials_info=infos, canvas=canvas)
    zip_path = EXPORTS_DIR / f"{draft_name}.zip"
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", root_dir=str(EXPORTS_DIR), base_dir=draft_name)
    copied_to, copy_error = None, None
    if req.copy_to_jianying:
        jd = _jianying_draft_dir()
        if jd:
            dest = jd / draft_name
            try:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(folder, dest)
                copied_to = str(dest)
            except Exception as exc:
                copy_error = str(exc)
        else:
            copy_error = "未找到剪映草稿库（可下载 zip 手动解压到剪映草稿目录）"
    return {
        "draft_name": draft_name,
        "folder": str(folder),
        "zip_url": f"/exports/{draft_name}.zip",
        "copied_to": copied_to,
        "copy_error": copy_error,
        "segments": len(sels),
        "duration_sec": round(sum(s["end"] - s["start"] for s in sels), 1),
        "materials": len(infos),
    }


# ---------- 字幕剪辑：本地字幕模型出台词 → 勾选/智能选台词 → 剪出片段 ----------
class TranscribeRequest(BaseModel):
    file: str
    force: bool = False


@app.post("/api/transcribe")
def api_transcribe(req: TranscribeRequest):
    """提取字幕：代理 analyzer /transcribe（只跑本地语音转写，不跑画面理解）。"""
    try:
        r = httpx.post(f"{ANALYZER_URL}/transcribe",
                       json={"file": req.file, "force": req.force}, timeout=3600)
        return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as exc:
        return {"error": str(exc)}


class MatchLinesRequest(BaseModel):
    query: str
    lines: list[dict]  # [{i, start, end, text}]


@app.post("/api/match_lines")
def api_match_lines(req: MatchLinesRequest):
    """一句话智能勾选：把台词清单交给对话模型（线上 DeepSeek / 本地 qwen），挑出相关的台词行。

    台词文本+时间戳都是确定性数据，模型只做"文本相关性挑选"，不猜画面——这是可靠的用法。
    """
    if not req.lines:
        return {"error": "台词清单为空（先提取字幕）"}
    def _fmt_line(l: dict) -> str:
        sp = f"（说话人:{l.get('speaker')}）" if l.get("speaker") else ""
        return f"{l.get('i')}: [{float(l.get('start', 0)):.1f}-{float(l.get('end', 0)):.1f}s]{sp} {l.get('text', '')}"

    listing = "\n".join(_fmt_line(l) for l in req.lines)
    prompt = (
        "你是字幕选段助手。下面是一段视频的全部台词（行号: [起-止秒]（说话人） 台词）。\n"
        f"用户想要的内容：「{req.query}」\n\n"
        "请挑出与用户想要相关的所有台词行，宁可多选不要漏选，但明显无关的不要选。\n"
        "如果用户指定了说话人（如\"把孙悟空说的话剪出来\"），只挑说话人标记与之一致的行（一句不漏，但别人说的绝不选）；"
        "没有说话人标记时，结合对话上下文、称呼推断是谁说的，只挑他本人说的行。\n"
        "只输出 JSON：{\"pick\":[{\"i\":行号,\"reason\":\"不超过12字的理由\"}]}\n\n"
        "台词清单：\n" + listing
    )
    try:
        msg = _llm_chat(
            [{"role": "system", "content": "你是严谨的字幕选段助手，严格按格式输出 JSON。"},
             {"role": "user", "content": prompt}],
            timeout=180,
        )
        data = util.parse_llm_json(msg.get("content") or "")
        picked = []
        if isinstance(data, dict) and isinstance(data.get("pick"), list):
            valid = {int(l.get("i")) for l in req.lines if str(l.get("i", "")).lstrip("-").isdigit()}
            for item in data["pick"]:
                try:
                    i = int(item.get("i"))
                except (TypeError, ValueError):
                    continue
                if i in valid:
                    picked.append({"i": i, "reason": str(item.get("reason", ""))[:24]})
        if not picked:
            return {"error": "模型没挑出台词（换个说法再试，或手动勾选）", "picked": []}
        return {"picked": picked, "total": len(req.lines)}
    except Exception as exc:
        return {"error": f"智能选台词失败: {exc}", "picked": []}


class BurnSrtRequest(BaseModel):
    output: str       # 成片相对路径（取末两段 job/文件名）
    srt: str          # SRT 文本
    font_size: int = 18


@app.post("/api/burn_srt")
def api_burn_srt(req: BurnSrtRequest):
    """给成片烧录硬字幕（libass）：选中的台词直接压进画面，交付即带字幕。"""
    parts = [x for x in re.split("[\\/]+", req.output) if x][-2:]
    if len(parts) != 2:
        return {"error": "成片路径格式不对"}
    src = (OUTPUT_DIR / parts[0] / parts[1]).resolve()
    if not src.is_relative_to(OUTPUT_DIR.resolve()) or not src.is_file():
        return {"error": f"成片不存在: {parts[0]}/{parts[1]}"}
    srt_path = src.with_suffix(".srt")
    srt_path.write_text(req.srt, encoding="utf-8-sig")
    dst = src.with_name(src.stem + "_sub.mp4")
    style = f"FontName=Microsoft YaHei,FontSize={int(req.font_size)},Outline=1,Shadow=0,MarginV=24"
    # subtitles 滤镜对 Windows 路径转义很挑剔：切到成片目录用相对文件名
    import subprocess

    proc = subprocess.run(
        [media.ffmpeg(), "-y", "-v", "error", "-i", src.name,
         "-vf", f"subtitles={srt_path.name}:force_style='{style}'",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", dst.name],
        cwd=str(src.parent), capture_output=True, text=True, errors="replace", timeout=3600,
    )
    if proc.returncode != 0:
        return {"error": (proc.stderr or "烧录失败")[-400:]}
    return {"output": f"{parts[0]}/{dst.name}", "url": f"/output/{parts[0]}/{dst.name}"}


# ---------- 说话人标注 / 按剧本匹配台词 / 台词 JSON 导出 ----------
class AttrSpeakersRequest(BaseModel):
    file: str
    force: bool = False


@app.post("/api/attribute_speakers")
def api_attribute_speakers(req: AttrSpeakersRequest):
    """给台词标注说话人：把全量台词交给对话模型，结合称呼/上下文推断每句是谁说的，写回分析文档。

    说明：这是文本推断（谁在说话通常能从称呼和对话看出来）；要做到声纹级精准，
    后续可接 FunASR CAM++ 说话人分离（同一接口，只换实现）。
    """
    src = (MATERIALS / req.file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return {"error": f"素材不存在: {req.file}"}
    doc_path = src.parent / "_analysis" / (src.name + ".analysis.json")
    doc = util.load_json(doc_path) if doc_path.exists() else None
    speech = (doc or {}).get("speech") or []
    if not speech:
        return {"error": "该素材还没有台词（先提取字幕）"}
    if all(sp.get("speaker") for sp in speech) and not req.force:
        return {"speakers": [{"i": i, "speaker": sp.get("speaker")} for i, sp in enumerate(speech)], "cached": True}
    listing = "\n".join(
        f"{i}: [{float(sp.get('start') or 0):.1f}-{float(sp.get('end') or 0):.1f}s] {sp.get('text', '')}"
        for i, sp in enumerate(speech))
    prompt = (
        "你是影视剧台词整理助手。下面按顺序给出一部视频的全部台词（行号: [起-止秒] 台词）。\n"
        "请推断每一句是谁说的：结合台词内容、上下文称呼（如\"师父/悟空/陛下\"）、人名和常识。\n"
        "知道名字就用名字；不知道名字就按角色称呼（如：女人/男人/师父/徒弟/皇帝/旁白），尽量不要写\"未知\"。只输出 JSON：\n"
        '{"speakers":[{"i":行号,"speaker":"名字"}]}\n\n台词：\n' + listing
    )
    try:
        msg = _llm_chat(
            [{"role": "system", "content": "你是严谨的台词整理助手，严格按格式输出 JSON。"},
             {"role": "user", "content": prompt}],
            timeout=300,
        )
        data = util.parse_llm_json(msg.get("content") or "")
        rows = data.get("speakers") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {"error": "模型没有返回说话人列表（再试一次）"}
        changed = 0
        for row in rows:
            try:
                i = int(row.get("i"))
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(speech):
                name = str(row.get("speaker") or "").strip()[:20]
                if name:
                    speech[i]["speaker"] = name
                    changed += 1
        if doc is not None:
            doc["speech"] = speech
            doc.setdefault("analysis_meta", {})["speakers_at"] = util.now_iso()
            util.save_json(doc_path, doc)
        return {"speakers": [{"i": i, "speaker": sp.get("speaker")} for i, sp in enumerate(speech)],
                "changed": changed, "total": len(speech)}
    except Exception as exc:
        return {"error": f"说话人标注失败: {exc}"}


class ScriptMatchLinesRequest(BaseModel):
    queries: list[str]          # 剧本条目（每条一句话/一句对白）
    lines: list[dict]           # 全量台词 [{i,start,end,text,speaker}]


@app.post("/api/script_match_lines")
def api_script_match_lines(req: ScriptMatchLinesRequest):
    """按剧本找台词：剧本逐条 ↔ 台词清单，一次对话模型调用批量匹配，返回每条剧本命中的台词行。

    命中的行由前端按剧本顺序勾选、按剧本顺序剪接——时间戳是确定性的，模型只做文本匹配。
    """
    if not req.queries:
        return {"error": "剧本为空"}
    if not req.lines:
        return {"error": "台词清单为空（先提取字幕）"}
    qlisting = "\n".join(f"[剧本{qi}] {q.strip()}" for qi, q in enumerate(req.queries) if q.strip())
    llisting = "\n".join(
        f"{l.get('i')}: [{float(l.get('start', 0)):.1f}-{float(l.get('end', 0)):.1f}s]"
        + (f"（说话人:{l.get('speaker')}）" if l.get("speaker") else "")
        + f" {l.get('text', '')}"
        for l in req.lines)
    prompt = (
        "你是剪辑助手。给你一份剧本条目清单和一段视频的全部台词。\n"
        "对每一条剧本条目，从台词里找出所有与之对应的台词行（内容相近、就是这件事/这句话，包括它的前后呼应）。\n"
        "某条剧本在台词里找不到对应就给空数组。宁可多选不要漏选，但明显无关的不要选。\n"
        '只输出 JSON：{"matches":[{"q":剧本条目号,"pick":[{"i":行号,"reason":"≤12字"}]}]}\n\n'
        "剧本条目：\n" + qlisting + "\n\n台词清单：\n" + llisting
    )
    try:
        msg = _llm_chat(
            [{"role": "system", "content": "你是严谨的剪辑助手，严格按格式输出 JSON。"},
             {"role": "user", "content": prompt}],
            timeout=300,
        )
        data = util.parse_llm_json(msg.get("content") or "")
        rows = data.get("matches") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {"error": "模型没有返回匹配结果（再试一次）"}
        valid = {int(l.get("i")) for l in req.lines if str(l.get("i", "")).lstrip("-").isdigit()}
        matches = []
        for row in rows:
            try:
                q = int(row.get("q"))
            except (TypeError, ValueError):
                continue
            if not (0 <= q < len(req.queries)):
                continue
            pick = []
            for item in row.get("pick") or []:
                try:
                    i = int(item.get("i"))
                except (TypeError, ValueError):
                    continue
                if i in valid:
                    pick.append({"i": i, "reason": str(item.get("reason", ""))[:24]})
            matches.append({"q": q, "query": req.queries[q], "pick": pick})
        return {"matches": matches, "total_lines": len(req.lines)}
    except Exception as exc:
        return {"error": f"剧本匹配失败: {exc}"}


class ExportSubtitlesRequest(BaseModel):
    folder: str = "."


def _folder_speech_videos(folder: str) -> tuple[list[dict], str | None]:
    """收集目录（含子目录）下所有已提取字幕素材的台词。返回 (videos, 错误信息)。"""
    base = (MATERIALS / folder).resolve() if folder and folder != "." else MATERIALS.resolve()
    if not base.exists() or not base.is_relative_to(MATERIALS.resolve()):
        return [], f"目录不存在: {folder}"
    videos = []
    for md in sorted(base.rglob("_analysis")):
        if not md.is_dir():
            continue
        for jf in sorted(md.glob("*.analysis.json")):
            doc = util.load_json(jf)
            if not doc or not (doc.get("speech") or []):
                continue
            videos.append({
                "file": doc.get("file") or jf.name.replace(".analysis.json", ""),
                "duration_sec": doc.get("duration_sec"),
                "lines": [{"start": sp.get("start"), "end": sp.get("end"),
                           "text": sp.get("text"), "speaker": sp.get("speaker")}
                          for sp in doc["speech"]],
            })
    return videos, None


class FolderLinesRequest(BaseModel):
    folder: str = "."


@app.post("/api/folder_lines")
def api_folder_lines(req: FolderLinesRequest):
    """全目录台词池（跨视频剧本匹配用）：所有已提取字幕素材的对白，统一编号返回。"""
    videos, err = _folder_speech_videos(req.folder)
    if err:
        return {"error": err}
    lines = []
    for v in videos:
        for sp in v["lines"]:
            lines.append({"i": len(lines), "file": v["file"], "start": sp["start"],
                          "end": sp["end"], "text": sp["text"], "speaker": sp.get("speaker")})
    return {"video_count": len(videos), "lines": lines}


@app.post("/api/export_subtitles_json")
def api_export_subtitles_json(req: ExportSubtitlesRequest):
    """导出目录级台词 JSON：所有已提取字幕的视频的对白+时间点，合成一个文件（喂给剧本剪辑用）。"""
    videos, err = _folder_speech_videos(req.folder)
    if err:
        return {"error": err}
    if not videos:
        return {"error": "该目录（含子目录）还没有任何台词——先提取字幕"}
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{_safe_filename(req.folder)}_台词_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out = EXPORTS_DIR / fname
    payload = {"schema_version": 1, "folder": req.folder, "generated_at": util.now_iso(),
               "video_count": len(videos),
               "line_count": sum(len(v["lines"]) for v in videos), "videos": videos}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"url": f"/exports/{fname}", "file": str(out), "video_count": len(videos),
            "line_count": payload["line_count"]}


class KeywordLinesRequest(BaseModel):
    keywords: list[str]   # 关键词（OR 语义：包含任意一个即命中）
    lines: list[dict]     # 台词 [{i,start,end,text,speaker}]


@app.post("/api/keyword_lines")
def api_keyword_lines(req: KeywordLinesRequest):
    """关键词剪辑：字幕里包含任意关键词的台词行全部命中（确定性子串匹配，不走模型）。"""
    kws = [k.strip() for k in req.keywords if str(k).strip()]
    if not kws:
        return {"error": "关键词为空"}
    if not req.lines:
        return {"error": "台词清单为空（先提取字幕）"}
    picked = []
    for l in req.lines:
        text = str(l.get("text") or "")
        hit = next((k for k in kws if k in text), None)
        if hit is not None:
            picked.append({"i": int(l.get("i")), "start": l.get("start"), "end": l.get("end"),
                           "text": text, "speaker": l.get("speaker"), "hit": hit})
    return {"picked": picked, "total": len(req.lines), "keywords": kws}


@app.post("/api/voice_input")
async def api_voice_input(file: UploadFile = File(...)):
    """语音对话：前端录音转发给 analyzer，本地语音模型转成文字后返回。"""
    try:
        data = await file.read()
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(
                f"{ANALYZER_URL}/voice_transcribe",
                files={"file": (file.filename or "voice.webm", data, file.content_type or "audio/webm")},
            )
        return r.json() if r.status_code == 200 else {"error": r.text}
    except Exception as exc:
        return {"error": str(exc)}


def _fmt_srt_ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@app.post("/api/export_srt")
def api_export_srt(req: ExportSrtRequest):
    """选区 → SRT 字幕：把每个片段的转写台词平移到成片时间轴上。

    台词来自分析文档（ASR）；无台词的片段自动跳过。
    """
    try:
        sels, _infos = _export_sels(req.selections)
    except HTTPException as exc:
        return {"error": exc.detail}
    cues = []
    tl = 0.0
    for s in sels:
        a = _material_doc(s["file"]) or {}
        for sp in a.get("speech") or []:
            cs = max(s["start"], float(sp.get("start") or 0))
            ce = min(s["end"], float(sp.get("end") or 0))
            text = (sp.get("text") or "").strip()
            if ce - cs > 0.05 and text:
                cues.append({"start": tl + (cs - s["start"]), "end": tl + (ce - s["start"]), "text": text})
        tl += s["end"] - s["start"]
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{_fmt_srt_ts(c['start'])} --> {_fmt_srt_ts(c['end'])}", c["text"], ""]
    srt = "\n".join(lines)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{_safe_filename(req.name)}_{time.strftime('%Y%m%d_%H%M%S')}.srt"
    (EXPORTS_DIR / fname).write_text(srt, encoding="utf-8-sig")  # 带签名，记事本/剪映都不乱码
    return {
        "url": f"/exports/{fname}",
        "file": str(EXPORTS_DIR / fname),
        "cues": len(cues),
        "duration_sec": round(tl, 1),
        "srt": srt,
    }


@app.get("/exports/{path:path}")
def exports_file(path: str):
    """导出产物下载（zip / srt）。"""
    p = (EXPORTS_DIR / path).resolve()
    if not p.is_relative_to(EXPORTS_DIR.resolve()) or not p.is_file():
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    mt = "application/zip" if p.suffix.lower() == ".zip" else "application/octet-stream"
    return FileResponse(p, media_type=mt, filename=p.name)


@app.post("/api/script_match")
def api_script_match(req: ScriptMatchRequest):
    """剧本 → 逐镜头向量检索全库场记 → 每个镜头返回候选场景（相似度排序）。

    这就是"拿所有场记整理一遍改剧本"：系统按镜头把全库场景过一遍，
    把最匹配的候选摆给你，勾选后剪（时间戳来自场记单，必然对）。
    """
    shots = (req.script or {}).get("shots") or []
    if not shots:
        return {"error": "剧本没有镜头"}
    out_shots = _match_shot_scenes(shots, req.files or [])
    _ensure_ledger_index(req.files or [])
    return {"shots": out_shots, "scene_total": sum(len(v) for v in _LEDGER_INDEX.values())}


class ClipSelectedRequest(BaseModel):
    selections: list[dict]  # [{file, start, end}]


@app.post("/api/clip_selected")
def api_clip_selected(req: ClipSelectedRequest):
    """勾选场记单场景 → 一个 EDL（多 trim + concat + export）→ executor 一次出片。

    时间戳全部来自场记单（文档），不经过模型猜测，剪出来的必然对。
    """
    if not req.selections:
        return {"error": "没有勾选任何场景"}
    plan = []
    order: list[tuple[str, float, float]] = []
    for sel in req.selections:
        f = sel.get("file")
        try:
            s, e = float(sel.get("start", 0)), float(sel.get("end", 0))
        except (TypeError, ValueError):
            continue
        if not f or e <= s:
            continue
        order.append((f, s, e))
    if not order:
        return {"error": "没有有效的勾选场景"}
    # 剪切工艺①：同一素材里时间连续/重叠的选区合并成一个区间（间隙≤0.5s 视为连续），
    # 保持勾选顺序不变、不跨素材合并——避免"连续几句话被剪成多段再拼接"的接缝感。
    relaxed: list[list] = []
    for f, s, e in order:
        if relaxed and relaxed[-1][0] == f and s - relaxed[-1][2] <= 0.5:
            relaxed[-1][2] = max(relaxed[-1][2], e)
        else:
            relaxed.append([f, s, e])
    # 剪切工艺②：呼吸余量——剪切点头部提前 0.15s、尾部延后 0.25s（钳制在素材时长内），
    # 防止吃字和爆音；专业剪辑软件的默认做法。
    durations: dict[str, float] = {}
    for r in relaxed:
        if r[0] not in durations:
            try:
                durations[r[0]] = float((media.probe(MATERIALS / r[0]) or {}).get("duration_sec") or 0)
            except Exception:
                durations[r[0]] = 0
        d = durations[r[0]]
        r[1] = max(0.0, r[1] - 0.15)
        if d:
            r[2] = min(d, r[2] + 0.25)
    order = [(f, s, e) for f, s, e in relaxed]
    # 同素材多段：executor 的 trim 用原始文件，registry 自动生成 file#n，concat 引用。
    # concat 顺序必须按勾选顺序逐条生成 key（先出现的用原名，同素材后续段用 file#n），
    # 否则 A,B,A 交叉勾选会被拼成 A,A,B，破坏用户排序。
    keys = []
    seen: dict[str, int] = {}
    for f, s, e in order:
        n = seen.get(f, 0)
        keys.append(f if n == 0 else f"{f}#{n}")
        seen[f] = n + 1
        plan.append({"op": "trim", "file": f, "start": round(s, 1), "end": round(e, 1)})
    if len(keys) > 1:
        plan.append({"op": "concat", "files": keys})
    # 原样输出：不转码，保持素材画质（边听边剪/场记勾选都走无损）
    plan.append({"op": "export", "format": "mp4", "resolution": "原样", "crf": 23})
    edl = {
        "schema_version": 1,
        "request_id": f"clip-{int(time.time())}",
        "user_request": "场记单勾选剪辑",
        "plan": plan,
    }
    try:
        r = httpx.post(f"{EXECUTOR_URL}/execute", json={"edl": edl}, timeout=3600)
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
    except Exception as exc:
        return {"error": str(exc)}


# ---------- 边听边剪：波形图 ----------

class WaveformRequest(BaseModel):
    file: str
    points: int = 1000  # 波形点数（前端画图密度）


@app.post("/api/waveform")
def api_waveform(req: WaveformRequest):
    """提取素材音频波形峰值（ffmpeg 降采样 PCM → 峰值数组），供「边听边剪」页面画波形。

    - 无音频轨返回 zeros（前端显示平线并提示）
    - 峰值按 points 分桶：每桶取 |sample| 最大值（显示响度轮廓，对白/音乐位置一目了然）
    - 数据量小（1000 点），秒级返回
    """
    src = (MATERIALS / req.file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return {"error": f"素材不存在: {req.file}"}
    info = media.probe(src)
    dur = float(info.get("duration_sec") or 0)
    points = max(50, min(4000, int(req.points)))
    n = max(1, points)
    # 磁盘缓存：同一文件、同样点数、未修改过 → 直接回缓存（时间线重复打开不重算）
    wave_cache = src.parent / "_analysis" / (src.name + ".wave.json")
    try:
        cached = util.load_json(wave_cache) if wave_cache.exists() else None
        if cached and cached.get("mtime") == src.stat().st_mtime and int(cached.get("points") or 0) == points and cached.get("peaks"):
            return {"file": req.file, "duration_sec": dur, "points": points,
                    "peaks": cached["peaks"], "has_audio": bool(cached.get("has_audio", True)), "cached": True}
    except OSError:
        pass
    # 提取单声道 8kHz 16bit PCM 到内存
    try:
        import subprocess

        proc = subprocess.run(
            [media.ffmpeg(), "-v", "error", "-i", str(src), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0 or not proc.stdout:
            return {"file": req.file, "duration_sec": dur, "points": n, "peaks": [0.0] * n, "has_audio": False}
        raw = proc.stdout
    except Exception as exc:
        return {"file": req.file, "duration_sec": dur, "points": n, "peaks": [0.0] * n, "has_audio": False, "error": str(exc)}
    import array

    samples = array.array("h")  # signed short
    samples.frombytes(raw)
    if not len(samples):
        return {"file": req.file, "duration_sec": dur, "points": n, "peaks": [0.0] * n, "has_audio": False}
    # 分桶取峰值，归一化到 0~1
    bucket = max(1, len(samples) // n)
    peaks = []
    for i in range(n):
        chunk = samples[i * bucket : (i + 1) * bucket]
        if chunk:
            peaks.append(round(abs(max(chunk, key=abs)) / 32768.0, 4))
        else:
            peaks.append(0.0)
    # 归一化策略：用 p95 分位（而非最大值）做基准——避免单个响点把全局压平，
    # 让对白/音乐段落的高低起伏可读；整体偏响的视频也不会变成满格平线。
    import statistics

    arr = sorted(peaks)
    p95 = arr[min(len(arr) - 1, int(len(arr) * 0.95))] or 1e-6
    norm_peaks = [round(min(1.0, p / p95 * 0.9), 4) if p > 0.005 else 0.0 for p in peaks]
    try:
        wave_cache.parent.mkdir(parents=True, exist_ok=True)
        util.save_json(wave_cache, {"mtime": src.stat().st_mtime, "points": points,
                                    "has_audio": True, "peaks": norm_peaks})
    except OSError:
        pass
    return {"file": req.file, "duration_sec": dur, "points": n, "peaks": norm_peaks, "has_audio": True}


class SegmentsRequest(BaseModel):
    file: str
    cuts: list[float] = []  # 用户打的切点（秒），空 = 整段一段

@app.post("/api/segments")
def api_segments(req: SegmentsRequest):
    """按用户打的切点分段：每段附中帧缩略图。

    - cuts 为空 → 整段一段（[0, duration]）
    - cuts = [t1, t2, ...] → 切成 [0,t1] [t1,t2] [t2,...] 段
    - 每段抽中帧 384px JPG 缩略图（存 _edit_thumbs/，前端 <img> 直接加载）
    """
    src = (MATERIALS / req.file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return {"error": f"素材不存在: {req.file}"}
    info = media.probe(src)
    dur = float(info.get("duration_sec") or 0)
    if dur <= 0:
        return {"error": "无法获取视频时长"}
    # 归一化切点：去重、去越界、排序
    cuts = sorted({max(0.0, min(dur - 0.1, float(c))) for c in (req.cuts or []) if float(c) > 0.05})
    bounds = [0.0] + cuts + [dur]
    segs = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        if e - s >= 0.3:
            segs.append({"index": len(segs), "start": round(s, 1), "end": round(e, 1), "duration": round(e - s, 1)})
    # 抽每段中帧缩略图
    thumbs_dir = MATERIALS / "_edit_thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    import cv2

    cap = cv2.VideoCapture(str(src))
    stem = Path(req.file).stem.replace(" ", "_")[:20]
    for seg in segs:
        mid = (seg["start"] + seg["end"]) / 2
        # HEVC seek 后第一帧可能失败：回退到从 0 连续读到目标位置
        cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000)
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_MSEC, 0)
            target = int(mid * 1000)
            pos = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                pos = int(cap.get(cv2.CAP_PROP_POS_MSEC))
                if pos >= target:
                    break
        name = f"seg_{stem}_{seg['index']:03d}.jpg"
        if ok:
            h, w = frame.shape[:2]
            if w > 384:
                frame = cv2.resize(frame, (384, int(h * 384 / w)))
            if util.save_image(frame, str(thumbs_dir / name)):
                seg["thumb"] = f"/materials/_edit_thumbs/{name}"
            else:
                seg["thumb"] = ""
        else:
            seg["thumb"] = ""
    cap.release()
    return {"file": req.file, "duration_sec": dur, "cuts": cuts, "segments": segs}


class FilmstripRequest(BaseModel):
    file: str
    cuts: list[float] = []
    frames_per_seg: int = 6  # 每段抽几帧拼胶片条


@app.post("/api/filmstrip")
def api_filmstrip(req: FilmstripRequest):
    """生成时间线轨道胶片条：每段抽 N 帧拼成一张横向长图（剪映式缩略图轨道）。

    返回每段一张胶片图（seg_strip_xxx.jpg），前端作轨道块的背景图铺满。
    帧间等距抽取（首/尾略收），适配任意时长段。
    """
    src = (MATERIALS / req.file).resolve()
    if not src.exists() or not src.is_relative_to(MATERIALS.resolve()):
        return {"error": f"素材不存在: {req.file}"}
    info = media.probe(src)
    dur = float(info.get("duration_sec") or 0)
    if dur <= 0:
        return {"error": "无法获取视频时长"}
    cuts = sorted({max(0.0, min(dur - 0.1, float(c))) for c in (req.cuts or []) if float(c) > 0.05})
    bounds = [0.0] + cuts + [dur]
    segs = []
    for i in range(len(bounds) - 1):
        s, e = bounds[i], bounds[i + 1]
        if e - s >= 0.3:
            segs.append({"index": len(segs), "start": round(s, 1), "end": round(e, 1)})
    fps = max(2, min(12, int(req.frames_per_seg)))
    thumbs_dir = MATERIALS / "_edit_thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    import cv2
    import numpy as np

    stem = Path(req.file).stem.replace(" ", "_")[:16]
    cap = cv2.VideoCapture(str(src))
    out = []
    for seg in segs:
        s, e = seg["start"], seg["end"]
        cell_w, cell_h = 120, 68  # 每帧单元
        frames = []
        for k in range(fps):
            t = s + (e - s) * (k + 0.5) / fps
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, f = cap.read()
            if not ok:
                # seek 失败回退：从 0 连续读
                cap.set(cv2.CAP_PROP_POS_MSEC, 0)
                target = int(t * 1000)
                while True:
                    ok, f = cap.read()
                    if not ok:
                        break
                    if int(cap.get(cv2.CAP_PROP_POS_MSEC)) >= target:
                        break
            if not ok:
                f = np.zeros((cell_h, cell_w, 3), np.uint8)
            h, w = f.shape[:2]
            f = cv2.resize(f, (cell_w, cell_h))
            frames.append(f)
        strip = np.hstack(frames) if frames else np.zeros((cell_h, cell_w * fps, 3), np.uint8)
        name = f"strip_{stem}_{seg['index']:03d}.jpg"
        if util.save_image(strip, str(thumbs_dir / name)):
            out.append(
                {
                    "index": seg["index"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "img": f"/materials/_edit_thumbs/{name}",
                    "frames": fps,
                }
            )
    cap.release()
    return {"file": req.file, "duration_sec": dur, "strips": out}


class ScriptTextRequest(BaseModel):
    text: str
    files: list[str] = []
    threshold: float = 0.35  # 相似度阈值，低于视为不匹配


# ---------- 免分析批量粗剪（文件夹按时间规则批量切，如电视剧去片头片尾） ----------

_BATCHES: dict[str, dict] = {}
_batch_lock = threading.Lock()


def _cn2num(s: str) -> float:
    """中文数字转数值：三→3、十五→15、三十→30、十一→11。"""
    units = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    total = 0
    for ch in s:
        if ch == "十":
            total = total * 10 if total else 10
        elif ch in units:
            total = total * 10 + units[ch] if total >= 10 and ch != "一" else total + units[ch]
    return float(total) if total else 0.0


def _numval(s: str) -> float:
    """解析数字：阿拉伯（12 / 12.5）或中文（三 / 十五）。"""
    if s.replace(".", "", 1).isdigit():
        return float(s)
    return _cn2num(s)


def _parse_batch_rule(rule: str) -> tuple[float, float]:
    """解析批量剪辑规则 → (去头秒, 去尾秒)。支持：去头1分30秒 / 去尾三分钟 / 去头90秒 / 保留2分钟到结尾。"""
    text = rule.strip()
    start = end = 0.0
    n = r"(\d+(?:\.\d+)?|[一二三四五六七八九十两]+)"
    # 保留 X 到结尾（= 去头 X）："保留2分钟到结尾" / "保留90秒到最后"
    m = re.search(r"保留\s*" + n + r"\s*(分钟|分|秒)?\s*(?:到|至|~|—|－)?\s*(?:结尾|最后|末尾|结束|到最后|尾)", text)
    if m:
        v = _numval(m.group(1))
        start = v * 60 if m.group(2) in ("分钟", "分") else v
    # 保留前 X 秒（= 去尾 X）："保留前2分钟" / "保留开头90秒"
    m = re.search(r"保留\s*(?:前|开头|头)?\s*" + n + r"\s*(分钟|分|秒)?\s*(?!到|至|结尾|最后)", text)
    if m and start == 0 and end == 0:
        v = _numval(m.group(1))
        end = v * 60 if m.group(2) in ("分钟", "分") else v
    # 去头：X分Y秒
    m = re.search(r"(?:去掉|剪掉|去|掐|删|移除)(?:掉)?(?:片头|开头|前|头).{0,4}?" + n + r"\s*分\s*" + n + r"\s*秒", text)
    if m:
        start = _numval(m.group(1)) * 60 + _numval(m.group(2))
    else:
        m = re.search(r"(?:去掉|剪掉|去|掐|删)(?:掉)?(?:片头|开头|前|头).{0,4}?" + n + r"\s*(?:分钟|分)\b", text)
        if m:
            start = _numval(m.group(1)) * 60
        else:
            m = re.search(r"(?:去掉|剪掉|去|掐|删)(?:掉)?(?:片头|开头|前|头).{0,4}?" + n + r"\s*秒", text)
            if m:
                start = _numval(m.group(1))
    # 去尾
    m = re.search(r"(?:去掉|剪掉|去|掐|删|移除)(?:掉)?(?:片尾|结尾|尾|后).{0,4}?" + n + r"\s*分\s*" + n + r"\s*秒", text)
    if m:
        end = _numval(m.group(1)) * 60 + _numval(m.group(2))
    else:
        m = re.search(r"(?:去掉|剪掉|去|掐|删)(?:掉)?(?:片尾|结尾|尾|后).{0,4}?" + n + r"\s*(?:分钟|分)\b", text)
        if m:
            end = _numval(m.group(1)) * 60
        else:
            m = re.search(r"(?:去掉|剪掉|去|掐|删)(?:掉)?(?:片尾|结尾|尾|后).{0,4}?" + n + r"\s*秒", text)
            if m:
                end = _numval(m.group(1))
    return start, end


def _batch_run(batch_id: str, folder: str, rule: str, resolution: str, start_in=None, end_in=None):
    """后台：遍历文件夹视频，按（起点,终点）逐个无损剪切出片。"""
    try:
        base = (MATERIALS / folder).resolve() if folder and folder != "." else MATERIALS.resolve()
        files = [p for p in _media_files(base) if "/批量/" not in p.relative_to(MATERIALS).as_posix()] if base.exists() else []
        # 优先用直接输入的起点/终点（秒），否则解析规则文本
        if start_in is not None or end_in is not None:
            start_sec = float(start_in or 0)
            end_sec = float(end_in or 0) if end_in else 0  # 0 = 到结尾
        else:
            start_sec, end_sec = _parse_batch_rule(rule)
        if start_sec < 0:
            start_sec = 0
        if end_sec > 0 and end_sec <= start_sec:
            with _batch_lock:
                _BATCHES[batch_id]["error"] = f"终点({end_sec}s)必须大于起点({start_sec}s)"
            return
        for p in files:
            rel = p.relative_to(MATERIALS).as_posix()
            dur = _material_duration(rel) or 0
            item = {"file": rel, "ok": False, "error": ""}
            if not dur:
                item["error"] = "无法获取时长"
            else:
                s = start_sec
                e = end_sec if end_sec > 0 else dur
                if e <= s:
                    item["error"] = f"范围无效（时长 {dur:.0f}s）"
                else:
                    name = Path(rel).stem
                    if resolution == "原样":
                        # 无损剪切（stream copy）：去片头片尾等纯时间裁剪，秒级完成，不转码
                        try:
                            r = httpx.post(
                                f"{EXECUTOR_URL}/api/cut_copy",
                                json={"file": rel, "start": round(s, 2), "end": round(e, 2), "out_name": name + ".mp4"},
                                timeout=600,
                            )
                            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
                        except Exception as exc:
                            data = {"error": str(exc)}
                    else:
                        # 用户明确要转码（720p/1080p）才走完整 EDL（慢，但保留画质调整）
                        edl = {
                            "schema_version": 1,
                            "request_id": name,
                            "user_request": f"批量剪辑 {rule}",
                            "plan": [
                                {"op": "trim", "file": rel, "start": round(s, 2), "end": round(e, 2)},
                                {"op": "export", "format": "mp4", "resolution": resolution, "crf": 23},
                            ],
                        }
                        try:
                            r = httpx.post(f"{EXECUTOR_URL}/execute", json={"edl": edl}, timeout=3600)
                            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text}
                        except Exception as exc:
                            data = {"error": str(exc)}
                    if data.get("output"):
                        # 成片移到「素材/选中文件夹/批量/」子目录（唯一保留地），清理 output 中间产物；源文件保留
                        out_path = Path(str(data["output"]).replace("\\", "/"))
                        batch_dir = (MATERIALS / folder / "批量").resolve()
                        try:
                            batch_dir.mkdir(parents=True, exist_ok=True)
                            target = batch_dir / Path(rel).name
                            shutil.move(str(out_path), str(target))
                            # 清理 output 里的临时 job 目录（成片已移走，不残留）
                            try:
                                if out_path.parent.exists():
                                    shutil.rmtree(str(out_path.parent), ignore_errors=True)
                            except Exception:
                                pass
                            item["ok"] = True
                            item["output"] = str(target.relative_to(MATERIALS.resolve())).replace("\\", "/")
                        except Exception as exc:
                            item["error"] = "移动成片失败:" + str(exc)[:150]
                    else:
                        item["error"] = "执行失败:" + json.dumps(data, ensure_ascii=False)[:300]
            with _batch_lock:
                b = _BATCHES[batch_id]
                b["results"].append(item)
                b["done"] += 1
        with _batch_lock:
            _BATCHES[batch_id]["finished"] = True
    except Exception as exc:
        with _batch_lock:
            _BATCHES[batch_id]["error"] = str(exc)
            _BATCHES[batch_id]["finished"] = True


class BatchClipRequest(BaseModel):
    folder: str = "."
    rule: str = ""           # 兼容规则文本（如"去头3分钟"），与 start/end 二选一
    start: float | None = None  # 起点（秒），如 90
    end: float | None = None    # 终点（秒），None/0 = 到结尾
    resolution: str = "原样"


@app.post("/api/batch_clip")
def api_batch_clip(req: BatchClipRequest):
    """免分析批量粗剪：文件夹所有视频按同一（起点,终点）批量切（如去片头片尾），后台执行。"""
    if req.start is None and req.end is None and not req.rule:
        return {"error": "请填写起点/终点（秒）"}
    base = (MATERIALS / req.folder).resolve() if req.folder and req.folder != "." else MATERIALS.resolve()
    if not base.exists():
        return {"error": f"目录不存在: {req.folder}"}
    files = [p for p in _media_files(base) if "/批量/" not in p.relative_to(MATERIALS).as_posix()] if base.exists() else []
    if not files:
        return {"error": "该目录没有视频文件（批量/ 子目录已排除）"}
    batch_id = f"batch-{int(time.time())}-{os.urandom(2).hex()}"
    with _batch_lock:
        _BATCHES[batch_id] = {"folder": req.folder, "rule": req.rule, "total": len(files), "done": 0, "results": [], "finished": False}
    threading.Thread(target=_batch_run, args=(batch_id, req.folder, req.rule, req.resolution, req.start, req.end), daemon=True).start()
    return {"batch_id": batch_id, "total": len(files), "rule": req.rule or f"{req.start}→{req.end or '结尾'}"}


@app.get("/api/batch_status")
def api_batch_status(batch_id: str):
    """批量剪辑进度：total/done/finished/每集结果。"""
    with _batch_lock:
        b = _BATCHES.get(batch_id)
        if not b:
            return {"error": "批次不存在"}
        return dict(b)


def _split_script_text(text: str) -> list[str]:
    """剧本文本 → 镜头列表：按行/序号/句号/分号切分，过滤短句。"""
    parts = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去掉行首序号（"1." "1、" "第一幕" 等）
        line = re.sub(r"^\s*(第[一二三四五六七八九十百\d]+[幕场节回]|\d+[\.、．])\s*", "", line)
        for seg in re.split(r"[。；;\n]+", line):
            seg = seg.strip()
            if len(seg) >= 4:
                parts.append(seg)
    return parts


@app.post("/api/script_text_clip")
def api_script_text_clip(req: ScriptTextRequest):
    """文本剧本 → 自动切分镜头 → 每镜头向量匹配场记 → 按序出片。

    用户只需把剧本写出来，向量搜索自动把每个镜头对应到场记，直接剪成视频。
    """
    if not req.text or not req.files:
        return {"error": "需要剧本文本和素材"}
    shots = _split_script_text(req.text)
    if not shots:
        return {"error": "剧本文本太短，无法切分镜头"}
    _ensure_ledger_index(req.files)
    selections = []
    shot_info = []
    for st in shots:
        qemb = _embed([st])
        best = None
        with _index_lock:
            if qemb:
                for f, scenes in _LEDGER_INDEX.items():
                    if f not in req.files:
                        continue
                    for sc in scenes:
                        score = _cosine(sc["vector"], qemb[0])
                        if best is None or score > best[0]:
                            best = (score, f, sc)
        if best and best[0] >= req.threshold:
            score, f, sc = best
            selections.append({"file": f, "start": sc["start"], "end": sc["end"]})
            shot_info.append(
                {
                    "text": st,
                    "matched": True,
                    "file": f,
                    "start": sc["start"],
                    "end": sc["end"],
                    "content": sc["content"],
                    "score": round(score, 3),
                }
            )
        else:
            shot_info.append({"text": st, "matched": False, "score": round(best[0], 3) if best else 0})
    if not selections:
        return {"error": f"没有任何镜头匹配到场记（相似度阈值 {req.threshold}，可降低阈值或先分析素材）", "shots": shot_info}
    # 复用勾选剪片逻辑出片
    clip_req = ClipSelectedRequest(selections=selections)
    clip_resp = api_clip_selected(clip_req)
    clip_resp["shots"] = shot_info
    clip_resp["matched_count"] = len(selections)
    return clip_resp


@app.get("/output/{job}/{name}")
def output_file(job: str, name: str):
    """从 executor 拉取成片文件。"""
    try:
        r = httpx.get(f"{EXECUTOR_URL}/output/{job}/{name}", timeout=60)
    except Exception as exc:
        return JSONResponse({"error": f"无法获取成片: {exc}"}, status_code=404)
    if r.status_code != 200:
        return JSONResponse({"error": f"成片不存在: {r.text}"}, status_code=404)
    return Response(content=r.content, media_type="video/mp4", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/materials/{path:path}")
def material_file(path: str, request: Request):
    """素材文件访问（支持 HTTP Range，浏览器可拖动进度条预览）。

    HEVC(hvc1) 等浏览器不支持直接播放的编码 → 实时转码 H.264 MP4 流（带 Range）。
    只允许访问 MATERIALS 目录内的媒体文件，其余一律 404/403。
    """
    p = (MATERIALS / path).resolve()
    if not p.is_relative_to(MATERIALS.resolve()) or not p.is_file():
        return JSONResponse({"error": "素材不存在"}, status_code=404)
    if p.suffix.lower() not in MEDIA_EXTS:
        return JSONResponse({"error": "不允许的文件类型"}, status_code=403)
    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    # HEVC(hvc1) / AV1 浏览器兼容性差 → 转码代理（只对视频文件做探测）
    need_transcode = False
    if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".ts"}:
        try:
            info = media.probe(p)
            vc = str(info.get("video_codec") or "").lower()
            need_transcode = vc in ("hevc", "av1", "vp9") or "hevc" in vc or "av1" in vc
        except Exception:
            need_transcode = False

    if not need_transcode:
        return _material_file_raw(p, media_type, request)

    # ---- 转码代理：ffmpeg 实时输出 H.264 MP4（fragmented，支持 Range 播放） ----
    import subprocess

    def _gen():
        try:
            proc = subprocess.Popen(
                [
                    media.ffmpeg(), "-v", "error", "-i", str(p),
                    "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "frag_keyframe+empty_moov+faststart",
                    "-f", "mp4", "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
            proc.stdout.close()
            proc.wait()
        except Exception:
            pass

    # 转码流不支持任意 Range seek，浏览器会退化；用流式响应（无 Content-Length）
    return StreamingResponse(_gen(), media_type="video/mp4", headers={"Cache-Control": "no-store"})


def _material_file_raw(p: Path, media_type: str, request: Request) -> Response:
    """原始文件访问（支持 Range），供兼容编码直接播放。"""
    size = p.stat().st_size
    range_hdr = request.headers.get("range")
    if range_hdr:
        m = re.match(r"bytes=(\d*)-(\d*)", range_hdr.strip())
        if m and (m.group(1) or m.group(2)):
            start_s, end_s = m.group(1), m.group(2)
            if not start_s and end_s:  # 后缀范围 bytes=-N
                start = max(0, size - int(end_s))
                end = size - 1
            elif start_s and not end_s:  # 开放范围 bytes=N-
                start = int(start_s)
                end = size - 1
            else:
                start = int(start_s)
                end = int(end_s)
            if start >= size or start > end:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            length = end - start + 1
            with open(p, "rb") as f:
                f.seek(start)
                data = f.read(length)
            return Response(
                content=data,
                status_code=206,
                media_type=media_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                },
            )
    with open(p, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        status_code=200,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
    )


class ClipRequest(BaseModel):
    file: str
    start: float = 0.0
    end: float = 0.0  # <=0 表示到素材结尾
    resolution: str = "720p"  # 720p / 1080p / 原样


@app.post("/api/clip")
def api_clip(req: ClipRequest):
    """手动剪辑：按素材 + 起止时间直接出片（不经过 LLM/规则解析，结果确定）。"""
    if not req.file:
        return {"error": "缺少素材 file"}
    dur = _material_duration(req.file)
    if not dur:
        return {"error": f"无法获取素材时长: {req.file}"}
    start = max(0.0, float(req.start))
    end = float(req.end) if req.end and req.end > 0 else dur
    end = min(end, dur)
    if end <= start:
        return {"error": f"结束时间 {end:.1f}s 必须大于开始时间 {start:.1f}s"}
    resolution = req.resolution if req.resolution in ("720p", "1080p", "4k") else "原样"
    edl = {
        "schema_version": 1,
        "request_id": f"clip-{int(time.time())}",
        "user_request": f"手动剪辑 {req.file} {start:.2f}-{end:.2f} 秒",
        "plan": [
            {"op": "trim", "file": req.file, "start": round(start, 2), "end": round(end, 2)},
            {"op": "export", "format": "mp4", "resolution": resolution, "crf": 23},
        ],
    }
    try:
        r = httpx.post(f"{EXECUTOR_URL}/execute", json={"edl": edl}, timeout=3600)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return {"error": r.text}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
