# -*- coding: utf-8 -*-
"""一：后台补关键词包含匹配接口（一句话剪辑的确定性核心）。"""
from pathlib import Path

p = Path("services/planner/app.py")
t = p.read_text(encoding="utf-8")

marker = "def _fmt_srt_ts(sec: float) -> str:"
NEW = '''class KeywordLinesRequest(BaseModel):
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


''' + marker
assert marker in t
t = t.replace(marker, NEW, 1)
p.write_text(t, encoding="utf-8")
print("planner keyword endpoint ok")
