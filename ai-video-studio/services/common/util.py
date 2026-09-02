"""JSON 读写、LLM 输出解析、EDL 校验、快速哈希。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_llm_json(text: str) -> dict | None:
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def quick_hash(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    p = Path(path)
    size = p.stat().st_size
    with p.open("rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(size - chunk)
            h.update(f.read(chunk))
    h.update(str(size).encode())
    return h.hexdigest()[:16]


def save_image(img, path: str | Path, ext: str = ".jpg") -> bool:
    """写图片文件。opencv 5.0 headless 的 cv2.imwrite 有 bug（返回 False 不写盘），
    统一用 cv2.imencode + 手动写文件绕过。返回是否成功。"""
    try:
        import cv2

        ok, buf = cv2.imencode(ext, img)
        if not ok:
            return False
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(buf.tobytes())
        return True
    except Exception:
        return False


def validate_edl(edl: dict, catalog: dict) -> tuple[bool, list[str]]:
    errors = []
    ops = {o["id"]: o for o in catalog.get("operations", [])}
    plan = edl.get("plan")
    if not isinstance(plan, list) or not plan:
        return False, ["plan 为空"]
    for item in plan:
        op = item.get("op")
        if op not in ops:
            errors.append(f"未知操作: {op}")
            continue
        for param in ops[op].get("params", {}):
            if param not in item:
                errors.append(f"{op} 缺少参数 {param}")
    return (not errors), errors
