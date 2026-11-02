"""Шинжээчийн (analyst) нэмэлт боломжууд — Studio (/builder) ба /ask-ийн хариултад. Нэмэлт LLM дуудахгүй, DB-д
дахин хандахгүй: бүгд детерминист, хямд.

* explain_sql      — SQL-ийн sqlglot AST-аас монгол тайлбар (хүснэгт, шүүлт, бүлэглэлт, тооцоо, эрэмбэ, хязгаар).
* confidence_info  — итгэлийн түвшин (өндөр/дунд/бага) ба татгалзсан/тодруулга асуусан шалтгааныг энгийн монголоор.
* notices          — мөрийн хязгаар, дэлгэцэнд харуулсан мөр, statement_timeout, EXPLAIN өртөг, үр дүнгийн хэмжээний мэдэгдэл.
* suggest_chart    — үр дүнгийн баганын төрлөөр bar / line / график хэрэггүй гэж санал болгоно.
* ResultCache      — бүрэн үр дүнг (≤ enforce_limit мөр) санах ойд богино хугацаанд хадгалж CSV/XLSX болгон татуулна.
                     Хэмжээ хязгаартай (cap_rows): нүд ≤ MAX_CELL_CHARS, бичлэг ≤ MAX_RESULT_BYTES, нийт ≤ MAX_CACHE_BYTES.
* FeedbackStore    — 👍/👎 үнэлгээ; 👍 авсан асуулт→SQL хосыг build/feedback/candidate_examples.jsonl-д (хянуулахаар)
                     нэмнэ. knowledge/examples.jsonl-д ХЭЗЭЭ Ч автоматаар бичихгүй.
* summarize_result — үр дүнгийн монгол хураангуй (загвартай биш, тоог үр дүнгээс тооцоолно).
* suggest_followups— SQL-ийн бүтцээс дараагийн асуулт (анхны асуултыг агуулсан бүтэн асуулт).
* VerifiedStore    — админ баталгаажуулсан асуулт→SQL сан (таарвал ✅, LLM-гүй шууд ажиллуулах).
* /analyst/rerun   — шинжээч засварласан SQL-ийг SQL guard → read-only Executor-оор дахин ажиллуулна.

Аюулгүй байдал: экспорт ба үнэлгээ нь зөвхөн серверт хадгалсан request id-аар ажиллана. Үйлчлүүлэгчээс SQL хүлээж
авдаг цорын ганц зам нь /analyst/rerun — SQL guard-д тэнцээгүй бол DB-д хүрэхгүй, read-only транзакц, statement_timeout,
EXPLAIN өртөг, мөрийн хязгаар, audit log (T2S_SQL_EDIT=0 бол хаалттай). Shadow горимд үр дүнг кэшлэхгүй.
/analyst/rerun ба /analyst/verified/{id}/run нь /ask, /flow/run_job-тэй нэг зэрэг ажиллах хязгаартай (auth.WorkSlots).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..config import resolve_path

CANDIDATES_FILE = "build/feedback/candidate_examples.jsonl"
VOTES_FILE = "build/feedback/studio_votes.jsonl"

# ---------------- SQL → монгол тайлбар ----------------

_AGG = {"Sum": "нийлбэр", "Avg": "дундаж", "Count": "тоо", "Min": "хамгийн бага", "Max": "хамгийн их",
        "Median": "медиан", "Stddev": "стандарт хазайлт", "ArrayAgg": "жагсаалт", "GroupConcat": "жагсаалт"}
_CMP = {"EQ": "=", "NEQ": "≠", "GT": ">", "GTE": "≥", "LT": "<", "LTE": "≤"}
_UNIT = {"YEAR": "он", "MONTH": "сар", "DAY": "өдөр", "QUARTER": "улирал", "WEEK": "долоо хоног"}


def labels_from_model(model) -> dict:
    """SemanticModel → {хүснэгт: (монгол нэр, {багана: монгол нэр})}. Загваргүй бол хоосон (англи нэрээр тайлбарлана)."""
    out: dict = {}
    for t, tab in (getattr(model, "tables", None) or {}).items():
        out[t] = (getattr(tab, "mn", "") or "", {c: (col.mn or "") for c, col in (tab.columns or {}).items()})
    return out


def _short(text: str, n: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class _Explainer:
    def __init__(self, labels: dict, dialect: str = "postgres"):
        self.labels, self.dialect = labels or {}, dialect
        self.alias: dict[str, str] = {}      # alias → хүснэгт
        self.tables: list[str] = []
        self.select_alias: dict[str, str] = {}

    def table_label(self, t: str) -> str:
        mn = (self.labels.get(t) or ("", {}))[0]
        return f"{mn} ({t})" if mn else t

    def col_label(self, col) -> str:
        name, q = col.name, col.table
        tabs = [self.alias.get(q, q)] if q else self.tables
        for t in tabs:
            mn = (self.labels.get(t) or ("", {}))[1].get(name)
            if mn:
                return mn
        if not q and name in self.select_alias:
            return self.select_alias[name]
        return name

    def expr(self, e) -> str:
        from sqlglot import exp
        if isinstance(e, exp.Paren):
            return self.expr(e.this)
        if isinstance(e, exp.Column):
            return self.col_label(e)
        if isinstance(e, exp.Star):
            return "бүх мөр"
        if isinstance(e, exp.Literal):
            return f"«{e.this}»" if e.is_string else str(e.this)
        if isinstance(e, exp.Null):
            return "хоосон (NULL)"
        if isinstance(e, exp.Boolean):
            return "үнэн" if e.this else "худал"
        if isinstance(e, exp.Cast):
            return self.expr(e.this)
        if isinstance(e, exp.Extract):
            unit = str(e.this.name if hasattr(e.this, "name") else e.this).upper()
            return f"{self.expr(e.expression)} ({_UNIT.get(unit, unit.lower())})"
        if type(e).__name__ in ("DateTrunc", "TimestampTrunc", "DatetimeTrunc"):
            unit = str(e.args.get("unit").name if e.args.get("unit") is not None else "").upper()
            return f"{self.expr(e.this)} ({_UNIT.get(unit, unit.lower())}-аар)"
        if isinstance(e, exp.Distinct):
            return ", ".join(self.expr(x) for x in e.expressions)
        name = type(e).__name__
        if name in _AGG:
            inner = e.this
            if isinstance(inner, exp.Distinct):
                return f"{self.expr(inner)} — давхардалгүй {_AGG[name]}"
            if inner is None or isinstance(inner, exp.Star):
                return "мөрийн тоо" if name == "Count" else _AGG[name]
            return f"{self.expr(inner)} — {_AGG[name]}"
        return _short(e.sql(dialect=self.dialect), 60)

    def cond(self, c) -> str:
        from sqlglot import exp
        if isinstance(c, exp.Paren):
            return self.cond(c.this)
        n = type(c).__name__
        if n in _CMP:
            if n == "EQ":
                return f"{self.expr(c.this)} нь {self.expr(c.expression)}"
            return f"{self.expr(c.this)} {_CMP[n]} {self.expr(c.expression)}"
        if isinstance(c, exp.Between):
            return f"{self.expr(c.this)} нь {self.expr(c.args['low'])}–{self.expr(c.args['high'])} хооронд"
        if isinstance(c, exp.In):
            if c.args.get("query") is not None:
                return f"{self.expr(c.this)} нь дэд асуулгын үр дүнд орсон"
            vals = [self.expr(v) for v in c.expressions]
            more = f" г.м. {len(vals)}" if len(vals) > 5 else ""
            return f"{self.expr(c.this)} нь {', '.join(vals[:5])}{more}-ийн аль нэг"
        if isinstance(c, (exp.Like, exp.ILike)):
            return f"{self.expr(c.this)} нь {self.expr(c.expression)} хэв маягтай"
        if isinstance(c, exp.Is):
            neg = bool(c.args.get("negate"))
            if isinstance(c.expression, exp.Null):
                return f"{self.expr(c.this)} {'хоосон биш' if neg else 'хоосон'}"
        if isinstance(c, exp.Not):
            return f"ҮГҮЙСГЭЛ: {self.cond(c.this)}"
        if isinstance(c, exp.Or):
            return " ЭСВЭЛ ".join(self.cond(x) for x in c.flatten())
        if isinstance(c, exp.Exists):
            return "дэд асуулгад тохирох мөр байгаа"
        return _short(c.sql(dialect=self.dialect), 80)

    def conds(self, node) -> list[str]:
        from sqlglot import exp
        if node is None:
            return []
        c = node.this
        parts = list(c.flatten()) if isinstance(c, exp.And) else [c]
        return [self.cond(p) for p in parts]


def explain_sql(sql: str | None, labels: dict | None = None, *, enforce_limit: int | None = None) -> dict | None:
    """SQL-ийн бүтцийг монголоор: {"parts": [[гарчиг, текст], ...], "text": нэг мөр, "complex": bool}.
    Задлаж чадахгүй бол None (тайлбаргүй үлдэнэ, хариултыг унагаахгүй)."""
    if not sql or not sql.strip():
        return None
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:  # noqa: BLE001 — задлах боломжгүй SQL-д тайлбар өгөхгүй
        return None
    ex = _Explainer(labels or {})
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if t.name in ctes:
            continue
        if t.name not in ex.tables:
            ex.tables.append(t.name)
        if t.alias:
            ex.alias[t.alias] = t.name
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    parts: list[list[str]] = []
    if ex.tables:
        head = "Хүснэгт" if len(ex.tables) == 1 else "Хүснэгтүүд (JOIN-оор холбосон)"
        parts.append([head, ", ".join(ex.table_label(t) for t in ex.tables)])
    if isinstance(tree, exp.Union) or isinstance(tree, (exp.Intersect, exp.Except)):
        parts.append(["Бүтэц", "Хэд хэдэн асуулгын үр дүнг нэгтгэсэн (UNION/INTERSECT/EXCEPT)"])
    complex_ = bool(ctes) or any(isinstance(s.parent, (exp.Subquery,)) for s in tree.find_all(exp.Select) if s is not sel)
    if sel is not None:
        for s in sel.expressions:
            if isinstance(s, exp.Alias):
                ex.select_alias[s.alias] = ex.expr(s.this)
        aggs = [ex.expr(s.unalias()) for s in sel.expressions if s.unalias().find(exp.AggFunc)]
        plain = [ex.expr(s.unalias()) for s in sel.expressions if not s.unalias().find(exp.AggFunc)]
        if sel.args.get("distinct"):
            parts.append(["Давхардал", "Давхардсан мөрийг хассан (DISTINCT)"])
        where = ex.conds(sel.args.get("where"))
        if where:
            parts.append(["Шүүлт", "; ".join(where)])
        group = sel.args.get("group")
        if group is not None:
            parts.append(["Бүлэглэлт", ", ".join(ex.expr(g) for g in group.expressions)])
        if aggs:
            parts.append(["Тооцоо", "; ".join(aggs)])
        if plain and not aggs:
            parts.append(["Харуулах багана", _short(", ".join(plain), 160)])
        having = ex.conds(sel.args.get("having"))
        if having:
            parts.append(["Бүлгийн шүүлт", "; ".join(having)])
        order = sel.args.get("order") or (tree.args.get("order") if tree is not sel else None)
        if order is not None:
            parts.append(["Эрэмбэ", ", ".join(f"{ex.expr(o.this)} {'буурахаар' if o.args.get('desc') else 'өсөхөөр'}"
                                             for o in order.expressions)])
        lim = sel.args.get("limit") or (tree.args.get("limit") if tree is not sel else None)
        if lim is not None:
            n = lim.expression if lim.expression is not None else lim.this
            try:
                nv = int(n.this) if n is not None else None
            except (TypeError, ValueError):
                nv = None
            if nv is not None and enforce_limit and nv == int(enforce_limit):
                parts.append(["Хязгаар", f"хамгаалалтын дээд хязгаар {nv} мөр"])
            elif nv is not None:
                parts.append(["Хязгаар", f"эхний {nv} мөр"])
    if complex_:
        parts.append(["Анхаар", "Дэд асуулга (CTE/subquery) ашигласан — дээрх нь гол асуулгын тайлбар"])
    text = ". ".join(f"{k}: {v}" for k, v in parts) + ("." if parts else "")
    return {"parts": parts, "text": text, "complex": complex_}


# ---------------- итгэл ба шалтгаан ----------------

_STAGE_MN = {
    "refusal": "Загвар энэ асуултыг өгөгдлийн сангаас хариулах боломжгүй гэж үзэж SQL бичээгүй.",
    "guard": "Үүссэн SQL аюулгүй байдлын шалгалтад (SQL guard) тэнцээгүй — зөвшөөрөгдөөгүй хүснэгт, багана эсвэл "
             "өөрчлөх үйлдэл агуулсан байж болно.",
    "exec": "Үүссэн SQL өгөгдлийн сан дээр ажиллахдаа алдаа өгсөн.",
    "empty": "SQL ажилласан ч үр дүн хоосон гарсан.",
    "extract": "Загварын хариунаас SQL олдсонгүй.",
    "llm": "Хэлний загвартай холбогдож чадсангүй (сервер ачаалалтай эсвэл унтарсан).",
}
_TIPS_CLARIFY = ["Хугацааг тодорхой бичнэ үү (ж: «2026 оны 6-р сард»).",
                 "Хэмжигдэхүүнийг нэрлэнэ үү (ж: «үлдэгдлийн нийлбэр», «зээлийн тоо»).",
                 "Шүүлтийг тодруулна уу (ж: «идэвхтэй зээл», «Улаанбаатар салбар»)."]


def confidence_info(status: str | None, confidence: float | None, unanimous: bool | None = None,
                    n_candidates: int = 0, error_stage: str | None = None, error_code: str | None = None,
                    stopped_early: bool = False) -> dict:
    """{level: high|medium|low|unknown|none, label, reason, tips}: шинжээчид итгэх/шалгах эсэхийг энгийнээр."""
    conf = float(confidence) if confidence is not None else None
    if status == "answered":
        if n_candidates <= 1:
            return {"level": "unknown", "label": "тодорхойгүй",
                    "reason": "Нэг л SQL хувилбар үүсгэсэн тул санал нийлэлтээр итгэл хэмжээгүй. Доорх тайлбар ба SQL-ийг "
                              "асуулттайгаа тулгаж шалгана уу.", "tips": []}
        pct = f"{(conf or 0):.0%}"
        if unanimous or (conf is not None and conf >= 0.8):
            return {"level": "high", "label": "өндөр",
                    "reason": f"{n_candidates} хувилбарын {pct} нь ижил үр дүн өгсөн.", "tips": []}
        if conf is not None and conf >= 0.5:
            return {"level": "medium", "label": "дунд",
                    "reason": f"{n_candidates} хувилбарын {pct} нь ижил үр дүн өгсөн — олонх санал нийлсэн ч бүгд биш. "
                              "Шүүлт, хугацааг шалгана уу.", "tips": []}
        return {"level": "low", "label": "бага",
                "reason": f"Хувилбаруудын зөвхөн {pct} нь санал нийлсэн. Үр дүнг шийдвэрт ашиглахаасаа өмнө шалгуулна уу.",
                "tips": _TIPS_CLARIFY[:2]}
    if status == "refused":
        if stopped_early:
            reason = ("Асуулт өгөгдлийн сангаас хариулах төрлийнх биш (жишээ нь өгөгдөл өөрчлөх, хувийн мэдээлэл, эсвэл "
                      "сангүй мэдээлэл) гэж шүүлтээр тогтоов — SQL бичээгүй.")
        else:
            reason = _STAGE_MN.get(error_stage or "refusal", _STAGE_MN["refusal"])
        return {"level": "none", "label": "татгалзсан", "reason": reason,
                "tips": ["Зээл, хадгаламж, харилцагч, эргэн төлөлтийн өгөгдлөөр хариулах асуулт болгон дахин бичнэ үү."]}
    if status == "clarify":
        reason = ("Асуулт хоёрдмол утгатай байна." if stopped_early else
                  f"Хувилбарууд өөр өөр үр дүн өгсөн (санал нийлэлт {(conf or 0):.0%}) — асуултыг хэд хэдэн янзаар ойлгож болно.")
        return {"level": "low", "label": "тодруулга", "reason": reason, "tips": _TIPS_CLARIFY}
    if status == "abstained":
        if error_stage in ("exec", "guard", "extract"):
            reason = "Сонгосон SQL алдаатай байсан тул буруу хариулт өгөхийн оронд хариулахаас татгалзав. " + \
                     _STAGE_MN.get(error_stage, "")
        else:
            reason = (f"Хувилбаруудын санал нийлэлт бага ({(conf or 0):.0%}) эсвэл үр дүн хоосон/сэжигтэй тул итгэлтэй "
                      "хариулт өгөх боломжгүй — шинжээчид шилжүүлэв.")
        return {"level": "low", "label": "итгэл бага", "reason": reason.strip(), "tips": _TIPS_CLARIFY[:2]}
    if status in ("error", "exec_error"):
        if error_code == "timeout":
            reason = "Асуулга хугацаандаа (statement_timeout) дуусаагүй тул зогсоов."
        elif error_code == "cost_too_high":
            reason = "Асуулга хэт хүнд (EXPLAIN өртөг хязгаараас их) тул ажиллуулаагүй."
        elif error_code == "too_large":
            reason = "Үр дүн хэт том (серверийн санах ойн хязгаараас их) тул татахаа зогсоов."
        else:
            reason = _STAGE_MN.get(error_stage or "", "Хариулт боловсруулах үед алдаа гарсан.")
        return {"level": "none", "label": "алдаа", "reason": reason,
                "tips": ["Хугацаа эсвэл шүүлтээр асуултыг нарийсгаад дахин оролдоно уу."]}
    return {"level": "unknown", "label": status or "—", "reason": "", "tips": []}


# ---------------- хязгаар, timeout-ийн мэдэгдэл ----------------

def notices(*, row_count: int | None, shown: int | None, truncated: bool = False, limit: int | None = None,
            timeout_ms: int | None = None, error_code: str | None = None, exportable: bool = False) -> list[dict]:
    """[{kind: limit|shown|timeout|cost|size, text}] — шинжээч үр дүн бүрэн эсэхийг мэдэх ёстой."""
    out: list[dict] = []
    if row_count is not None and limit and (truncated or row_count >= int(limit)):
        out.append({"kind": "limit", "text": f"Үр дүн {int(limit)} мөрөөр хязгаарлагдсан — бүрэн биш байж болно. "
                                             "Шүүлт эсвэл бүлэглэлт нэмж нарийсгана уу."})
    if row_count is not None and shown is not None and row_count > shown:
        out.append({"kind": "shown", "text": f"Дэлгэцэнд эхний {shown} мөрийг харуулав (нийт {row_count})."
                                             + (" Бүгдийг CSV/Excel-ээр татна уу." if exportable else "")})
    if error_code == "timeout":
        sec = f"{int(timeout_ms) / 1000:g} секунд" if timeout_ms else "хугацааны хязгаар"
        out.append({"kind": "timeout", "text": f"Асуулга {sec}-ээс удаан ажилласан тул өгөгдлийн сан зогсоов. "
                                               "Хугацааны мужийг богиносгох эсвэл шүүлт нэмнэ үү."})
    if error_code == "cost_too_high":
        out.append({"kind": "cost", "text": "Асуулга хэт хүнд гэж үнэлэгдсэн (EXPLAIN өртөг) тул ажиллуулаагүй."})
    if error_code == "too_large":   # H2: Executor-ийн max_result_bytes
        out.append({"kind": "size", "text": "Үр дүн хэт том (санах ойн хязгаар) тул бүрэн татаагүй — шүүлт, бүлэглэлт "
                                            "нэмэх эсвэл цөөн багана сонгоно уу."})
    return out


# ---------------- график санал болгох ----------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}.*)?$")
_TIME_NAME = re.compile(r"(date|month|year|period|day|week|quarter|огноо|сар|он|улирал)", re.I)
_ID_NAME = re.compile(r"(^id$|_id$|_code$|^code$|_no$|register|phone|утас)", re.I)


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = str(v).strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s):
        return None
    f = float(s)
    return f if math.isfinite(f) else None


def _kind(name: str, vals: list) -> str:
    """Баганын төрөл: temporal | numeric | id | category | empty."""
    vs = [v for v in vals if v is not None and str(v) != ""]
    if not vs:
        return "empty"
    if all(isinstance(v, (date, datetime)) or _DATE_RE.match(str(v)) for v in vs):
        return "temporal"
    if all(_num(v) is not None for v in vs):
        if _ID_NAME.search(name):
            return "id"
        if _TIME_NAME.search(name) and all(_num(v).is_integer() and 1900 <= _num(v) <= 2100 for v in vs):
            return "temporal"   # он (2024, 2025 …)
        if _TIME_NAME.search(name) and all(_num(v).is_integer() and 1 <= _num(v) <= 12 for v in vs) and \
                re.search(r"(month|сар)", name, re.I):
            return "temporal"   # сар (1..12)
        return "numeric"
    return "category"


def suggest_chart(columns: list[str] | None, rows: list | None, *, max_bar: int = 30, max_line: int = 200) -> dict:
    """{type: bar|line|none, x, y, points: [[x, y]...], reason}. Нэг тэнхлэг, нэг хэмжигдэхүүн (хоёр масштаб холихгүй)."""
    columns, rows = list(columns or []), list(rows or [])
    none = lambda why: {"type": "none", "reason": why}  # noqa: E731
    if not columns or len(rows) < 2:
        return none("Нэг мөр эсвэл хоосон үр дүнд график хэрэггүй — тоог шууд уншина.")
    kinds = [_kind(c, [r[i] for r in rows[:max_line]]) for i, c in enumerate(columns)]
    dims = [i for i, k in enumerate(kinds) if k in ("temporal", "category", "id")]
    meas = [i for i, k in enumerate(kinds) if k == "numeric"]
    if not meas:
        return none("Тоон багана алга — график зурах хэмжигдэхүүн байхгүй.")
    if len(dims) != 1:
        return none("Ангилал/хугацааны багана нэгээс олон эсвэл байхгүй — хүснэгтээр уншина уу.")
    xi, yi = dims[0], meas[0]
    pts = [[r[xi] if r[xi] is None else str(r[xi]), _num(r[yi])] for r in rows]
    pts = [p for p in pts if p[1] is not None]
    if kinds[xi] == "temporal":
        if len(pts) < 3:
            return none("Хугацааны цэг 3-аас цөөн — чиг хандлага харагдахгүй.")
        if len(pts) > max_line:
            return none(f"Цэг хэт олон ({len(pts)}) — хугацааг бүлэглэж (сар, улирал) асууна уу.")
        pts.sort(key=lambda p: (_num(p[0]) if _num(p[0]) is not None else 0, str(p[0])))
        ctype = "line"
    else:
        if len({p[0] for p in pts}) != len(pts):
            return none("Ангилал давхардсан — хүснэгтээр уншина уу.")
        if len(pts) > max_bar:
            return none(f"Ангилал хэт олон ({len(pts)}) — эхний {max_bar}-аар хязгаарлаж асууна уу.")
        ctype = "bar"
    out = {"type": ctype, "x": columns[xi], "y": columns[yi], "points": pts}
    if len(meas) > 1:
        out["note"] = f"Зөвхөн «{columns[yi]}»-г зурав; бусад тоон баганыг хүснэгтээс харна уу."
    return out


# ---------------- үр дүнгийн кэш, CSV/XLSX ----------------

def _plain(v):
    """DB-ийн утгыг JSON/Excel-д тохируулна (Decimal → float/int, огноо → ISO текст)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 10**15 else float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


# S3: json_agg/string_agg/array_agg нь бүхэл view-г НЭГ нүдэнд буцааж болно (мөрийн хязгаар үүнийг барихгүй).
# Кэшлэх, экспортлох, хариултад буцаах үр дүнг нүд ба нийт хэмжээгээр хязгаарлана (db/executor.py-г хөндөхгүй).
MAX_CELL_CHARS = 10_000          # нэг нүдний дээд урт (тэмдэгт) — урт текстийг тайрч тэмдэглэнэ
MAX_RESULT_BYTES = 8_000_000     # кэшлэх (CSV/XLSX экспорт) нэг үр дүнгийн дээд хэмжээ (UTF-8 байт, ойролцоо)
MAX_SHOWN_BYTES = 2_000_000      # JSON хариултад (≤ 200 мөр) буцаах мөрүүдийн дээд хэмжээ
MAX_CACHE_BYTES = 64_000_000     # ResultCache-ийн бүх бичлэгийн нийт дээд хэмжээ


def cap_rows(rows, *, max_cell_chars: int = MAX_CELL_CHARS, max_bytes: int = MAX_RESULT_BYTES) -> tuple[list, dict]:
    """Мөрүүдийг _plain-аар хувиргаад хэмжээгээр хязгаарлана: max_cell_chars-аас урт текст нүдийг тайрч «… [+N
    тэмдэгт]» гэж тэмдэглэнэ; нийт хэмжээ max_bytes хэтрэх мөрөөс эхлэн үлдсэнийг хасна. Давтан хэрэглэхэд үр дүн
    өөрчлөгдөхгүй (тайрсан нүд ≤ max_cell_chars). Буцаах: (мөрүүд, {"capped", "cells_cut", "rows_dropped", "bytes"})."""
    rows = list(rows or [])
    keep = max(1, int(max_cell_chars) - 32)          # тэмдэглэгээнд зай үлдээнэ
    out: list[list] = []
    cut = total = 0
    for i, r in enumerate(rows):
        row, size = [], 0
        for v in r:
            v = _plain(v)
            if isinstance(v, str):
                if len(v) > max_cell_chars:
                    v = f"{v[:keep]}… [+{len(v) - keep} тэмдэгт]"
                    cut += 1
                size += len(v.encode("utf-8", "replace")) + 3
            else:
                size += 8
            row.append(v)
        if total + size > max_bytes:
            dropped = len(rows) - i
            return out, {"capped": True, "cells_cut": cut, "rows_dropped": dropped, "bytes": total}
        total += size
        out.append(row)
    return out, {"capped": bool(cut), "cells_cut": cut, "rows_dropped": 0, "bytes": total}


def size_notice(cap: dict, *, shown: bool = False) -> dict:
    """cap_rows-ийн мэдээллээс шинжээчид харуулах мэдэгдэл (notices-ийн хэлбэр, kind = size)."""
    parts = []
    if cap.get("cells_cut"):
        parts.append(f"{cap['cells_cut']} нүдний хэт урт утгыг {MAX_CELL_CHARS} тэмдэгтээр тайрав")
    if cap.get("rows_dropped"):
        parts.append(f"хэмжээний хязгаараас ({'дэлгэц' if shown else 'экспорт'}) сүүлийн {cap['rows_dropped']} мөрийг хасав")
    return {"kind": "size", "text": "Үр дүн хэт том: " + "; ".join(parts) + ". Шүүлт/бүлэглэлт нэмж нарийсгана уу."}


class ResultCache:
    """request id → {question, sql, columns, rows, tables, status}. Санах ойд, хамгийн ихдээ max_items, ttl секунд.
    S3: бичлэг бүр ≤ max_entry_bytes (нүд ≤ max_cell_chars), бүх бичлэг нийтдээ ≤ max_total_bytes (хуучныг шахна)."""

    def __init__(self, max_items: int = 64, ttl_s: int = 3600, *, max_entry_bytes: int = MAX_RESULT_BYTES,
                 max_total_bytes: int = MAX_CACHE_BYTES, max_cell_chars: int = MAX_CELL_CHARS):
        self.max_items, self.ttl = int(max_items), int(ttl_s)
        self.max_total_bytes = int(max_total_bytes)
        self.max_entry_bytes = min(int(max_entry_bytes), self.max_total_bytes)
        self.max_cell_chars = int(max_cell_chars)
        self.items: OrderedDict[str, dict] = OrderedDict()
        self.total_bytes = 0
        self.lock = threading.Lock()

    def put(self, rid: str, entry: dict) -> dict | None:
        """Хадгална; үр дүнг хэмжээгээр тайрсан бол cap_rows-ийн мэдээллийг буцаана (үгүй бол None)."""
        e = dict(entry)
        e["rows"], cap = cap_rows(e.get("rows"), max_cell_chars=self.max_cell_chars, max_bytes=self.max_entry_bytes)
        if cap["capped"]:
            e["capped"] = cap
        e["_bytes"] = cap["bytes"] + len(str(e.get("question") or "")) + len(str(e.get("sql") or ""))
        e["t"] = now = time.time()
        with self.lock:
            old = self.items.pop(rid, None)
            if old is not None:
                self.total_bytes -= old.get("_bytes", 0)
            self.items[rid] = e
            self.total_bytes += e["_bytes"]
            for k in [k for k, v in self.items.items() if now - v["t"] > self.ttl]:   # хугацаа дууссаныг чөлөөлнө
                self.total_bytes -= self.items.pop(k).get("_bytes", 0)
            while len(self.items) > 1 and (len(self.items) > self.max_items or self.total_bytes > self.max_total_bytes):
                _, v = self.items.popitem(last=False)
                self.total_bytes -= v.get("_bytes", 0)
        return cap if cap["capped"] else None

    def get(self, rid: str) -> dict | None:
        with self.lock:
            e = self.items.get(rid)
            if e is None:
                return None
            if time.time() - e["t"] > self.ttl:
                self.items.pop(rid, None)
                self.total_bytes -= e.get("_bytes", 0)
                return None
            return e


RESULTS = ResultCache()

_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(v):
    """CSV/Excel формула шахалтаас (=HYPERLINK… г.м.) хамгаална: тоо биш, томьёоны тэмдэгтээр эхэлсэн текстэд ' нэмнэ."""
    if isinstance(v, str) and v.startswith(_FORMULA) and _num(v) is None:
        return "'" + v
    return v


def to_csv(columns: list[str], rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([_safe_cell(c) for c in columns])
    for r in rows:
        w.writerow(["" if v is None else _safe_cell(v) for v in r])
    return buf.getvalue().encode("utf-8-sig")   # BOM — Excel кирилл үсгийг зөв уншина


def xlsx_available() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def to_xlsx(columns: list[str], rows: list, *, question: str | None = None, sql: str | None = None,
            note: str | None = None) -> bytes:
    """openpyxl суусан үед л. 1-р хуудас — үр дүн, 2-р хуудас — асуулт ба SQL (хянахад); note — хэмжээгээр тайрсан г.м."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Үр дүн"
    ws.append([_safe_cell(c) for c in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([_safe_cell(v) for v in r])
    for i, c in enumerate(columns, 1):
        width = max([len(str(c))] + [len(str(r[i - 1])) for r in rows[:200] if r[i - 1] is not None])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(60, max(8, width + 2))
    ws.freeze_panes = "A2"
    meta = wb.create_sheet("SQL")
    meta.append(["Асуулт", _safe_cell(question or "")])
    meta.append(["SQL", _safe_cell(sql or "")])
    meta.append(["Мөр", len(rows)])
    meta.append(["Татсан", datetime.now().isoformat(timespec="seconds")])
    if note:
        meta.append(["Анхаар", _safe_cell(note)])
    meta.column_dimensions["B"].width = 100
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------- 👍/👎 ба жишээний нэр дэвшигч ----------------

def _sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _feedback_fernet():
    """T2S_AUDIT_KEY (Fernet) байвал шифрлэгч, үгүй бол None — audit log-той ижил бодлого."""
    key = os.environ.get("T2S_AUDIT_KEY")
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


class FeedbackStore:
    """Үнэлгээ (votes) ба 👍 авсан асуулт→SQL хос (candidates). Хоёулаа build/feedback/ дотор — хянагч шалгаад өөрөө
    knowledge/examples.jsonl руу шилжүүлнэ. Санал бүрт асуултын текстийг биш hash-ийг хадгална."""

    def __init__(self, candidates: str | Path = CANDIDATES_FILE, votes: str | Path = VOTES_FILE):
        self.candidates, self.votes = resolve_path(candidates), resolve_path(votes)
        self.lock = threading.Lock()

    def _append(self, path: Path, rec: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _known(self) -> set[str]:
        if not self.candidates.exists():
            return set()
        out = set()
        for line in self.candidates.read_text(encoding="utf-8").splitlines():
            try:
                out.add(json.loads(line).get("pair_sha"))
            except json.JSONDecodeError:
                continue
        return out

    def vote(self, rid: str, entry: dict, good: bool, comment: str | None = None) -> dict:
        q, sql = (entry.get("question") or "").strip(), (entry.get("sql") or "").strip()
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.lock:
            self._append(self.votes, {"ts": ts, "request_id": rid, "verdict": "up" if good else "down",
                                      "status": entry.get("status"), "question_sha1": _sha(q), "sql_sha1": _sha(sql),
                                      "has_comment": bool(comment)})
            added = duplicate = blocked = False
            if good and q and sql and entry.get("status") == "answered":
                fernet = _feedback_fernet()
                pair = _sha(q + "\n" + sql)
                if fernet is None:
                    blocked = True   # audit-ийн бодлого (api.py AuditLog): түлхүүргүй бол текст хадгалахгүй
                elif pair in self._known():
                    duplicate = True
                else:
                    enc = lambda t: fernet.encrypt(t.encode("utf-8")).decode() if t else None   # noqa: E731
                    self._append(self.candidates, {
                        "id": f"CAND-{pair[:8]}", "enc": "fernet:T2S_AUDIT_KEY",
                        "question_mn": enc(q), "gold_sql": enc(sql),
                        "tables": entry.get("tables") or [], "source": "studio_feedback", "review": "pending",
                        "comment": enc((comment or "")[:500]), "request_id": rid, "ts": ts, "pair_sha": pair})
                    added = True
        out = {"ok": True, "candidate_added": added, "duplicate": duplicate}
        if blocked:
            out["note"] = "T2S_AUDIT_KEY тохируулаагүй тул асуулт→SQL хосыг хадгалсангүй (зөвхөн hash)"
        return out

    def export_plain(self) -> str:
        """Нэр дэвшигчдийг T2S_AUDIT_KEY-ээр тайлсан NDJSON (түлхүүргүй бол шифртэй хэвээр)."""
        fernet = _feedback_fernet()
        out = []
        for line in self.candidates.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if fernet is not None and rec.get("enc"):
                try:
                    plain = {f: fernet.decrypt(rec[f].encode()).decode("utf-8")
                             for f in ("question_mn", "gold_sql", "comment") if rec.get(f)}
                except Exception:   # хуучин түлхүүрээр шифрлэсэн мөр — шифртэй хэвээр үлдээнэ
                    rec["decrypt_error"] = True
                else:
                    rec.update(plain)
                    rec.pop("enc")
            out.append(json.dumps(rec, ensure_ascii=False))
        return "\n".join(out) + "\n"

    def count(self) -> int:
        return len(self._known() - {None})


# ---------------- үр дүнгийн монгол хураангуй (загваргүй) ----------------

_RATIO_NAME = re.compile(r"(avg|mean|rate|ratio|pct|percent|share|score|дундаж|хувь|харьцаа)", re.I)


def _fmt(v: float) -> str:
    """1234567.891 → «1 234 567.89» (мянгатын зай, ≤ 2 бутархай орон)."""
    v = float(v)
    s = f"{int(v):,}" if v.is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")
    return s.replace(",", " ")


def _fv(v) -> str:
    n = _num(v)
    if n is not None:
        return _fmt(n)
    return "хоосон (NULL)" if v is None else _short(v, 40)


def _col_mn(name: str, labels: dict | None) -> str:
    """Үр дүнгийн багана semantic model-д байвал монгол нэрээр (хаалтан доторх тайлбаргүй), alias бол хэвээр."""
    for _mn, cols in (labels or {}).values():
        if cols.get(name):
            return re.sub(r"\s*\(.*?\)", "", cols[name]).strip() or name
    return name


def summarize_result(columns: list[str] | None, rows: list | None, *, labels: dict | None = None,
                     row_count: int | None = None, truncated: bool = False) -> dict | None:
    """Үр дүнгийн 1–4 өгүүлбэр монгол хураангуй: {"text", "facts"}. Тоог зөвхөн үр дүнгээс тооцоолно (LLM-гүй) —
    «юу» гэдгийг хэлнэ, «яагаад» гэдгийг тайлбарлахгүй. Хоосон багана бол None."""
    columns, rows = list(columns or []), list(rows or [])
    if not columns:
        return None
    n = int(row_count if row_count is not None else len(rows))
    if not rows:
        t = "Үр дүн хоосон — шүүлтэд тохирох мөр олдсонгүй. Хугацаа, шүүлтийн утгыг шалгана уу."
        return {"text": t, "facts": [t]}
    lab = [_col_mn(c, labels) for c in columns]
    facts: list[str] = []
    if len(rows) == 1:
        if len(columns) == 1:
            facts.append(f"Хариулт: {_fv(rows[0][0])}.")
        else:
            facts.append("Нэг мөр: " + "; ".join(f"{lab[i]} — {_fv(v)}" for i, v in enumerate(rows[0][:6])) + ".")
    else:
        kinds = [_kind(c, [r[i] for r in rows[:200]]) for i, c in enumerate(columns)]
        dims = [i for i, k in enumerate(kinds) if k in ("temporal", "category", "id")]
        meas = [i for i, k in enumerate(kinds) if k == "numeric"]
        facts.append(f"Нийт {n} мөр.")
        if meas:
            yi = meas[0]
            y = lab[yi]
            xi = dims[0] if dims else None
            pts = [(None if xi is None else r[xi], _num(r[yi])) for r in rows]
            pts = [p for p in pts if p[1] is not None]
            if pts and xi is not None and kinds[xi] == "temporal" and len(pts) >= 2:
                pts.sort(key=lambda p: (_num(p[0]) if _num(p[0]) is not None else 0, str(p[0])))
                (x0, v0), (x1, v1) = pts[0], pts[-1]
                ch = f" ({(v1 - v0) / abs(v0):+.1%})" if v0 else ""
                facts.append(f"«{y}»: {x0} үед {_fmt(v0)} → {x1} үед {_fmt(v1)}{ch}.")
                top = max(pts, key=lambda p: p[1])
                facts.append(f"Хамгийн өндөр нь {top[0]} ({_fmt(top[1])}).")
            elif pts and xi is not None:
                hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
                facts.append(f"«{y}» хамгийн их нь «{_short(hi[0], 40)}» ({_fmt(hi[1])}), хамгийн бага нь "
                             f"«{_short(lo[0], 40)}» ({_fmt(lo[1])}).")
                total = sum(p[1] for p in pts)
                if not _RATIO_NAME.search(columns[yi]) and len(pts) > 2 and total > 0 and all(p[1] >= 0 for p in pts):
                    facts.append(f"Нийлбэр {_fmt(total)}; хамгийн их нь нийлбэрийн {hi[1] / total:.0%}-ийг эзэлнэ.")
            elif pts:
                vs = [p[1] for p in pts]
                facts.append(f"«{y}» {_fmt(min(vs))}–{_fmt(max(vs))} хооронд, дундаж {_fmt(sum(vs) / len(vs))}.")
    if truncated:
        facts.append(f"Анхаар: үр дүн {n} мөрөөр хязгаарлагдсан тул дээрх тоо бүрэн биш байж болно.")
    return {"text": " ".join(facts), "facts": facts}


# ---------------- дараагийн асуулт санал болгох (загваргүй) ----------------

_DIM_COL = re.compile(r"(status_name|type_name|segment_name|_type$|^channel|channel_name|currency_code|province|gender|"
                      r"category|dpd_bucket|classification_code|risk_level|risk_grade_name|stage_name|severity|"
                      r"industry_sector|product_name|branch_name)", re.I)
_DATE_COL = re.compile(r"(_date$|^date$)", re.I)
_DATE_SKIP = re.compile(r"(closed|termination|discontinue|release|maturity|next_|due_date|valid_|called|paid_date)", re.I)


def _strip_q(q: str | None) -> str:
    return re.sub(r"[\s?？.!]+$", "", " ".join(str(q or "").split()))


def suggest_followups(question: str | None, sql: str | None, labels: dict | None = None, *, n: int = 4,
                      enforce_limit: int | None = None) -> list[dict]:
    """[{text: товч шошго, question: бүтэн асуулт}] — SQL-ийн бүтцээс (sqlglot), LLM-гүй. Pipeline өмнөх асуултыг
    санадаггүй (stateless) тул санал болгох асуулт бүр анхны асуултыг агуулсан бүтэн асуулт — контекст алдагдахгүй."""
    q = _strip_q(question)
    if not q or not sql:
        return []
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:  # noqa: BLE001 — задлахгүй бол санал болгохгүй
        return []
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if sel is None:
        return []
    labels = labels or {}
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    tables = list(dict.fromkeys(t.name for t in tree.find_all(exp.Table) if t.name not in ctes))
    cols: list[tuple[str, str]] = []
    for t in tables:
        for c, mn in (labels.get(t) or ("", {}))[1].items():
            if c not in (x[0] for x in cols):
                cols.append((c, re.sub(r"\s*\(.*?\)", "", mn or c).strip() or c))
    has_agg = any(s.unalias().find(exp.AggFunc) for s in sel.expressions)
    group = sel.args.get("group")
    grouped = {c.name for g in group.expressions for c in g.find_all(exp.Column)} if group is not None else set()
    where = sel.args.get("where")
    filtered = {c.name for c in where.find_all(exp.Column)} if where is not None else set()
    lim = sel.args.get("limit")
    small_limit = False
    if lim is not None:
        node = lim.expression if lim.expression is not None else lim.this
        try:
            small_limit = int(node.this) != int(enforce_limit or -1)
        except (TypeError, ValueError, AttributeError):
            small_limit = True
    dims = [(c, mn) for c, mn in cols if _DIM_COL.search(c) and c not in grouped and c not in filtered]
    date_col = next(((c, mn) for c, mn in cols if _DATE_COL.search(c) and not _DATE_SKIP.search(c)), None)
    has_branch = any(c == "branch_id" for c, _ in cols) and not ({"branch_id", "branch_name"} & (grouped | filtered))
    time_grouped = any(_DATE_COL.search(c) or "month" in c.lower() for c in grouped) or \
        (group is not None and any(g.find(exp.Extract) or type(g).__name__ in ("DateTrunc", "TimestampTrunc")
                                   for g in group.expressions))
    out: list[dict] = []

    def add(text: str, suffix: str) -> None:
        if len(out) < n and text not in (o["text"] for o in out):
            out.append({"text": text, "question": f"{q} — {suffix}"})

    if has_agg and group is None:
        if has_branch:
            add("Салбар тус бүрээр", "салбар тус бүрээр задлан харуулна уу")
        if date_col:
            add(f"Сар бүрээр ({date_col[1]})", f"{date_col[1]}-ийн сар бүрээр задлан харуулна уу")
        for _c, mn in dims[:2]:
            add(f"{mn} тус бүрээр", f"{mn} тус бүрээр задлан харуулна уу")
    elif group is not None:
        if not small_limit:
            add("Хамгийн их 5", "хамгийн их 5-ыг буурах эрэмбээр харуулна уу")
        if date_col and not time_grouped:
            add(f"Сар бүрийн чиг хандлага ({date_col[1]})", f"{date_col[1]}-ийн сар бүрээр чиг хандлагыг харуулна уу")
        add("Нийт дүн (задаргаагүй)", "задаргаагүй нийт дүнг нэг тоогоор харуулна уу")
    else:
        add("Нийт тоо", "нийт хэдэн мөр байгааг тоолж харуулна уу")
        if has_branch:
            add("Салбар тус бүрийн тоо", "салбар тус бүрээр тоолж харуулна уу")
    if where is not None and any(isinstance(lit, exp.Literal) and re.search(r"\b(19|20)\d{2}\b", str(lit.this))
                                 for lit in where.find_all(exp.Literal)):
        add("Өмнөх онтой харьцуулах", "өмнөх оны мөн үетэй харьцуулж харуулна уу")
    return out


# ---------------- баталгаажсан асуулга (verified queries) ----------------

VERIFIED_FILE = "build/feedback/verified_queries.jsonl"


def _norm_q(q: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(q or "").lower()).split())


def _stems(q: str | None) -> set[str]:
    """Монгол нөхцөл залгаврыг ойролцоогоор хасах: үг бүрийн эхний 5 үсэг («зээлийн», «зээлүүд» → «зээлү»/«зээли»)."""
    return {w[:5] for w in _norm_q(q).split() if len(w) > 1}


def _norm_sql(s: str | None) -> str:
    return " ".join(str(s or "").lower().strip().rstrip(";").split())


class VerifiedStore:
    """Админ баталгаажуулсан асуулт→SQL сан. build/feedback/verified_queries.jsonl (git-д ордоггүй).

    * Шинжээч ижил/төстэй асуултад «✅ баталгаажсан» тэмдэг, баталгаажсан SQL-ийг LLM-гүй шууд ажиллуулах товч харна.
    * Админ `as_examples()`-ийг knowledge/examples.jsonl-ийн хэлбэрээр татаж, хянаад ГАРААР нэмж индексийг дахин бүтээнэ
      (few-shot / RAG). Систем knowledge/ руу хэзээ ч автоматаар бичихгүй.
    * Энд хадгалсан асуулт бүх шинжээчид харагдах зориулалттай тул шифрлэхгүй — хувийн мэдээллийг админ батлахаасаа
      өмнө асуултаас хасна (`question` талбараар засаж болно)."""

    def __init__(self, path: str | Path = VERIFIED_FILE):
        self.path = resolve_path(path)
        self.lock = threading.Lock()

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def get(self, vid: str) -> dict | None:
        return next((r for r in self.all() if r.get("id") == vid), None)

    def add(self, question: str, sql: str, *, tables: list | None = None, source: str = "admin",
            note: str | None = None) -> dict:
        q, s = " ".join(str(question or "").split()), str(sql or "").strip().rstrip(";").strip()
        if not q or not s:
            raise ValueError("Асуулт ба SQL хоёулаа шаардлагатай")
        pair = _sha(_norm_q(q) + "\n" + _norm_sql(s))
        with self.lock:
            old = next((r for r in self.all() if r.get("pair_sha") == pair), None)
            if old is not None:
                return {"ok": True, "duplicate": True, "id": old["id"]}
            rec = {"id": f"VQ-{pair[:8]}", "question_mn": q, "gold_sql": s, "tables": list(tables or []),
                   "source": source, "note": (note or "")[:300] or None,
                   "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pair_sha": pair}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "duplicate": False, "id": rec["id"]}

    def remove(self, vid: str) -> bool:
        with self.lock:
            recs = self.all()
            keep = [r for r in recs if r.get("id") != vid]
            if len(keep) == len(recs):
                return False
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep), encoding="utf-8")
            tmp.replace(self.path)
            return True

    def match(self, question: str | None, *, n: int = 3, min_score: float = 0.6) -> list[dict]:
        """Ижил (нормчилсон) асуулт → 1.0; бусад нь үгийн язгуурын Jaccard. [{id, question, sql, score}]."""
        nq, st = _norm_q(question), _stems(question)
        if not nq:
            return []
        scored = []
        for r in self.all():
            rq = r.get("question_mn") or ""
            if _norm_q(rq) == nq:
                sc = 1.0
            else:
                rs = _stems(rq)
                sc = len(st & rs) / len(st | rs) if st and rs else 0.0
            if sc >= min_score:
                scored.append({"id": r["id"], "question": rq, "sql": r.get("gold_sql"), "score": round(sc, 2)})
        scored.sort(key=lambda x: -x["score"])
        return scored[:n]

    def as_examples(self) -> str:
        """knowledge/examples.jsonl-ийн хэлбэр (id, question_mn, gold_sql, tables) — админ хянаад гараар нэмнэ."""
        return "".join(json.dumps({"id": r["id"], "category": "answerable", "question_mn": r.get("question_mn"),
                                   "gold_sql": r.get("gold_sql"), "alt_sql": [], "tables": r.get("tables") or [],
                                   "source": "studio_verified"}, ensure_ascii=False) + "\n" for r in self.all())


_VERIFIED: VerifiedStore | None = None


def default_verified() -> VerifiedStore:
    global _VERIFIED
    if _VERIFIED is None:
        _VERIFIED = VerifiedStore()
    return _VERIFIED


def _canon_sql(s: str | None, enforce_limit: int | None = None) -> str:
    """Харьцуулах хэлбэр: sqlglot-оор форматлаад, guard-ын нэмсэн `LIMIT enforce_limit`-ийг хасна."""
    s = _norm_sql(s)
    try:
        import sqlglot
        s = _norm_sql(sqlglot.parse_one(s, read="postgres").sql(dialect="postgres"))
    except Exception:  # noqa: BLE001 — задлахгүй бол текстээр нь харьцуулна
        pass
    if enforce_limit:
        s = re.sub(rf"\s+limit\s+{int(enforce_limit)}$", "", s)
    return s


def verified_info(question: str | None, sql: str | None, store: VerifiedStore | None, *,
                  enforce_limit: int | None = None) -> list[dict]:
    """Хариултын асуулттай таарах баталгаажсан асуулга; `same_sql` — сонгосон SQL баталгаажсантай ижил эсэх."""
    if store is None or not question:
        return []
    out = []
    for m in store.match(question):
        m = dict(m)
        m["same_sql"] = bool(sql) and _canon_sql(m.get("sql"), enforce_limit) == _canon_sql(sql, enforce_limit)
        out.append(m)
    return out


# ---------------- хариултаас нэмэлт мэдээлэл угсрах ----------------

def _chosen(ans):
    cands = getattr(ans, "candidates", None) or []
    i = getattr(ans, "chosen_index", None)
    if i is not None and i < len(cands):
        return cands[i]
    return next((c for c in cands if (c.sql or c.raw_sql) == ans.sql), cands[0] if cands else None)


def _error_code(ans, chosen) -> str | None:
    for res in [getattr(ans, "result", None), getattr(chosen, "result", None)] + \
               [getattr(c, "result", None) for c in getattr(ans, "candidates", None) or []]:
        code = getattr(res, "error_code", None)
        if code in ("timeout", "cost_too_high", "too_large"):
            return code
    for c in getattr(ans, "candidates", None) or []:
        if c.error and "statement_timeout" in c.error:
            return "timeout"
    return None


def build_extras(ans, *, cfg=None, model=None, labels: dict | None = None, shown: int = 20,
                 question: str | None = None, cache: ResultCache | None = RESULTS, rid: str | None = None,
                 verified: VerifiedStore | None | bool = True) -> dict:
    """Answer → {"rid", "sql_explain", "confidence", "notices", "chart", "export", "xlsx", "summary", "followups",
    "verified", "sql", "sql_edit"}. verified=True — анхдагч сан (build/feedback), False/None — шалгахгүй.
    Алдаа гарвал хариултыг унагаахгүй — {"error": …} буцаана."""
    try:
        sec = (cfg or {}).get("security", {}) if cfg is not None else {}
        limit = int(sec.get("enforce_limit")) if sec.get("enforce_limit") else None
        timeout_ms = (cfg or {}).get("db", {}).get("statement_timeout_ms") if cfg is not None else None
        if labels is None and model is not None:
            labels = labels_from_model(model)
        chosen = _chosen(ans)
        res = getattr(ans, "result", None)
        ok = res is not None and getattr(res, "ok", False)
        code = _error_code(ans, chosen)
        rid = rid or uuid.uuid4().hex[:12]
        n_cands = len(getattr(ans, "candidates", None) or [])
        # S3: график, хураангуй, кэш (экспорт) нь хэмжээгээр хязгаарласан мөрөөр (json_agg г.м. аварга нүд)
        rows_c, cap = cap_rows(res.rows) if ok else ([], {"capped": False})
        rows_v = rows_c if cap["capped"] else (res.rows if ok else [])   # хэвийн үр дүнд хуучин зан төлөв
        out = {"rid": rid,
               "sql_explain": explain_sql(ans.sql, labels, enforce_limit=limit) if ans.sql else None,
               "confidence": confidence_info(ans.status, ans.confidence, ans.unanimous, n_cands,
                                             getattr(chosen, "error_stage", None), code, stopped_early=n_cands == 0),
               "notices": notices(row_count=res.rowcount if ok else None, shown=shown if ok else None,
                                  truncated=bool(getattr(res, "truncated", False)) if ok else False, limit=limit,
                                  timeout_ms=timeout_ms, error_code=code,
                                  exportable=cache is not None),
               "chart": suggest_chart(res.columns, rows_v) if ok else {"type": "none", "reason": ""},
               "export": False, "xlsx": False,
               "summary": summarize_result(res.columns, rows_v, labels=labels, row_count=res.rowcount,
                                           truncated=bool(getattr(res, "truncated", False))) if ok else None,
               "followups": suggest_followups(question or ans.question, ans.sql, labels, enforce_limit=limit)
               if ok and ans.sql else [],
               "sql": ans.sql or None,
               "sql_edit": bool(ans.sql) and sql_edit_enabled(cfg)}
        vstore = default_verified() if verified is True else (verified or None)
        out["verified"] = verified_info(question or ans.question, ans.sql, vstore, enforce_limit=limit)
        cap_info = cap if cap["capped"] else None
        if cache is not None and ok and res.columns:
            cap_info = cache.put(rid, {"question": question or ans.question, "sql": ans.sql,   # put өөрөө хязгаарлана
                                       "columns": list(res.columns), "rows": res.rows,
                                       "tables": list(getattr(chosen, "tables", None) or []),
                                       "status": ans.status}) or cap_info
            out["export"], out["xlsx"] = True, xlsx_available()
        elif cache is not None and ans.sql:
            # үр дүнгүй (татгалзсан г.м.) — экспортгүй, гэхдээ 👍/👎 үнэлгээнд асуулт ба SQL хэрэгтэй
            cache.put(rid, {"question": question or ans.question, "sql": ans.sql, "columns": [], "rows": [],
                            "tables": list(getattr(chosen, "tables", None) or []), "status": ans.status})
        if cap_info:
            out["notices"].append(size_notice(cap_info))
        out["feedback"] = cache is not None
        return out
    except Exception as e:  # noqa: BLE001 — нэмэлт мэдээлэл хариултыг унагаахгүй
        logging.getLogger(__name__).exception("build_extras амжилтгүй")   # L5: дэлгэрэнгүй нь серверийн лог-д
        return {"error": f"Нэмэлт мэдээллийг угсарч чадсангүй ({type(e).__name__})"}


# ---------------- FastAPI router ----------------

# FastAPI нь `from __future__ import annotations`-тай үед загварыг модулийн түвшинд харах ёстой (api.py-тэй адил)
from pydantic import BaseModel, Field  # noqa: E402


class VoteIn(BaseModel):
    rid: str = Field(min_length=4, max_length=40, pattern=r"^[0-9a-f]+$")
    good: bool
    comment: str | None = Field(default=None, max_length=500)


class RerunIn(BaseModel):
    sql: str = Field(min_length=1, max_length=8000)
    rid: str | None = Field(default=None, min_length=4, max_length=40, pattern=r"^[0-9a-f]+$")


class VerifiedIn(BaseModel):
    rid: str | None = Field(default=None, min_length=4, max_length=40, pattern=r"^[0-9a-f]+$")
    question: str | None = Field(default=None, max_length=500)
    sql: str | None = Field(default=None, max_length=8000)
    note: str | None = Field(default=None, max_length=300)


def sql_edit_enabled(cfg=None) -> bool:
    """SQL засвар ба баталгаажсан SQL-ийг шууд ажиллуулах эсэх. T2S_SQL_EDIT=0 — хаалттай, =1 — нээлттэй;
    тохируулаагүй бол service.mode «shadow» биш үед л (shadow-д хэрэглэгчид үр дүн харуулдаггүй)."""
    # S5: mode-ийг api.py/flow.py-тэй ижил нормчилно ('Shadow ' = shadow)
    from .auth import is_shadow, service_mode

    flag = os.environ.get("T2S_SQL_EDIT")
    if flag in ("0", "1"):
        return flag == "1"
    return not is_shadow(service_mode(cfg))


def _admin_ok(admin: str | None) -> bool:
    """Хэрэглэгчийн нийтлэг (T2S_WEB_KEY) түлхүүрээр биш — тусдаа T2S_ADMIN_KEY; тохируулаагүй бол хаалттай."""
    from .auth import header_admin  # `?admin=` эсвэл X-T2S-Admin толгой (R4 A3)

    akey = os.environ.get("T2S_ADMIN_KEY")
    admin = admin or header_admin()
    return bool(akey and admin and hmac.compare_digest(admin.encode(), akey.encode()))


def make_analyst_router(check_key=None, cache: ResultCache | None = None, store: FeedbackStore | None = None, *,
                        runner=None, verified: VerifiedStore | None = None, audit=None, max_parallel: int = 2,
                        slots=None):
    """runner: FlowRunner (guard, executor, cfg) — SQL засварлаж дахин ажиллуулах, баталгаажсан SQL ажиллуулахад.
    audit: callable(record) — гараар ажиллуулсан SQL-ийг audit log-д (api.py AuditLog.write, SQL шифрлэгдэнэ).
    slots: auth.WorkSlots — /ask, /flow/run_job-тэй хуваалцсан зэрэг ажиллах хязгаар (S1); байхгүй бол max_parallel."""
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse, Response

    from .auth import WorkSlots, capture_dependency
    r = APIRouter(dependencies=[capture_dependency()])   # X-T2S-Key / X-T2S-Admin толгойг check_key/_admin_ok-д
    ck = check_key or (lambda k: None)
    cache = cache if cache is not None else RESULTS
    fstore = store
    slots = slots if slots is not None else WorkSlots(max_parallel)
    label_cache: dict = {}

    def get_store() -> FeedbackStore:
        nonlocal fstore
        if fstore is None:
            fstore = FeedbackStore()
        return fstore

    def get_verified() -> VerifiedStore:
        nonlocal verified
        if verified is None:
            verified = default_verified()
        return verified

    def need_admin(admin: str | None) -> None:
        if not _admin_ok(admin):
            raise HTTPException(status_code=403, detail="Админ түлхүүр (T2S_ADMIN_KEY) шаардлагатай.")

    def need_exec() -> None:
        if not sql_edit_enabled(getattr(runner, "cfg", None)):
            raise HTTPException(status_code=403, detail="SQL шууд ажиллуулах боломж хаалттай (T2S_SQL_EDIT=0 эсвэл "
                                                        "service.mode=shadow).")

    def labels() -> dict:
        if "l" not in label_cache:
            try:
                from ..knowledge.semantic import cached_model
                label_cache["l"] = labels_from_model(cached_model(runner.cfg["knowledge"]["semantic_model"]))
            except Exception:  # noqa: BLE001 — монгол нэргүй (англи нэрээр) тайлбарлана
                label_cache["l"] = {}
        return label_cache["l"]

    def execute(sql: str, question: str, status: str, conf: dict) -> dict:
        """SQL guard (зөвхөн нэг SELECT, зөвшөөрөгдсөн view/багана, хориотой функцгүй, LIMIT) → read-only Executor
        (statement_timeout, EXPLAIN өртөг, мөрийн хязгаар). Guard-д тэнцээгүй SQL DB-д огт хүрэхгүй."""
        guard, executor = getattr(runner, "guard", None), getattr(runner, "executor", None)
        if guard is None or executor is None:
            raise HTTPException(status_code=501, detail="Энэ сервер өгөгдлийн сантай холбогдоогүй (SQL ажиллуулах боломжгүй).")
        g = guard.check(sql)
        if not g.ok:
            if audit:
                audit({"request_id": None, "question": question, "status": f"{status}_rejected", "sql": sql,
                       "guard": {"code": g.code, "error": (g.error or "")[:200]}})
            raise HTTPException(status_code=400, detail=f"SQL зөвшөөрөгдөхгүй ({g.code}): {g.error}")
        t0 = time.perf_counter()
        with slots.hold():   # S1: /ask, /flow/run_job-тэй нэг хязгаар; дүүрсэн бол 429, finally-д чөлөөлнө
            res = executor.run(g.sql)
        rid = uuid.uuid4().hex[:12]
        ans = type("EditedAnswer", (), {})()
        ans.question, ans.sql, ans.status, ans.result = question, g.sql, status if res.ok else "exec_error", res
        ans.confidence, ans.unanimous, ans.candidates, ans.chosen_index = None, None, [], None
        extras = build_extras(ans, cfg=runner.cfg, labels=labels(), shown=200, question=question, cache=cache, rid=rid,
                              verified=get_verified())
        if res.ok and "error" not in extras:
            extras["confidence"] = conf
        if audit:
            audit({"request_id": rid, "question": question, "status": ans.status, "sql": g.sql, "tables": g.tables,
                   "row_count": res.rowcount if res.ok else None, "error": (res.error or "")[:200] or None,
                   "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
        rows, cap = cap_rows(res.rows[:200], max_bytes=MAX_SHOWN_BYTES) if res.ok else ([], {"capped": False})
        if cap["capped"] and isinstance(extras.get("notices"), list):   # S3: хариултын нүд/хэмжээний хязгаар
            extras["notices"].append(size_notice(cap, shown=True))
        return {"request_id": rid, "status": ans.status, "sql": g.sql, "tables": g.tables,
                "columns": list(res.columns) if res.ok else [], "rows": rows, "row_count": res.rowcount if res.ok else None,
                "error": res.error if not res.ok else None, "error_code": getattr(res, "error_code", None),
                "analyst": extras}

    @r.post("/analyst/rerun")
    def rerun(inp: RerunIn, k: str | None = None):
        """Шинжээч засварласан SQL-ийг дахин ажиллуулна. Анхны асуултыг серверийн кэшээс (rid) авна."""
        ck(k)
        need_exec()
        e = cache.get(inp.rid) if inp.rid else None
        q = ((e or {}).get("question") or "").strip()
        return execute(inp.sql, q + " [SQL засварласан]" if q else "[SQL засварласан]", "edited",
                       {"level": "unknown", "label": "гараар засварласан",
                        "reason": "SQL-ийг шинжээч засварласан — загварын санал нийлэлт хамаарахгүй. Үр дүнг асуулттайгаа "
                                  "тулгаж шалгана уу.", "tips": []})

    @r.get("/analyst/verified")
    def verified_list(k: str | None = None, q: str | None = None):
        ck(k)
        vs = get_verified()
        if q:
            return {"items": vs.match(q, n=10, min_score=0.3)}
        return {"items": [{"id": x["id"], "question": x.get("question_mn"), "sql": x.get("gold_sql"),
                           "tables": x.get("tables") or [], "ts": x.get("ts")} for x in vs.all()[-200:]]}

    @r.post("/analyst/verified")
    def verified_add(inp: VerifiedIn, admin: str | None = None):
        need_admin(admin)
        e = cache.get(inp.rid) if inp.rid else None
        if inp.rid and e is None:
            raise HTTPException(status_code=404, detail="Хариулт олдсонгүй (хугацаа дууссан) — дахин асууна уу.")
        question = (inp.question or (e or {}).get("question") or "").strip()
        sql = (e or {}).get("sql") if e is not None and not inp.sql else (inp.sql or "")
        tables = list((e or {}).get("tables") or [])
        if not question or not (sql or "").strip():
            raise HTTPException(status_code=400, detail="rid эсвэл question+sql шаардлагатай.")
        guard = getattr(runner, "guard", None)
        if guard is not None:
            g = guard.check(sql, rewrite=False)
            if not g.ok:
                raise HTTPException(status_code=400, detail=f"SQL зөвшөөрөгдөхгүй ({g.code}): {g.error}")
            tables = tables or g.tables
        elif e is None or inp.sql:
            # кэшийн SQL guard-аар аль хэдийн шалгагдсан; гаднаас ирсэн SQL-ийг guard-гүйгээр батлахгүй
            raise HTTPException(status_code=501, detail="SQL guard алга — зөвхөн rid-ээр баталгаажуулна.")
        try:
            return get_verified().add(question, sql, tables=tables, source="studio_admin", note=inp.note)
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @r.delete("/analyst/verified/{vid}")
    def verified_delete(vid: str, admin: str | None = None):
        need_admin(admin)
        if not re.fullmatch(r"VQ-[0-9a-f]{8}", vid) or not get_verified().remove(vid):
            raise HTTPException(status_code=404, detail="Баталгаажсан асуулга олдсонгүй.")
        return {"ok": True}

    @r.get("/analyst/verified/export")
    def verified_export(admin: str | None = None):
        need_admin(admin)
        return Response(get_verified().as_examples(), media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="verified_examples.jsonl"'})

    @r.post("/analyst/verified/{vid}/run")
    def verified_run(vid: str, k: str | None = None):
        """Баталгаажсан SQL-ийг LLM-гүй шууд ажиллуулна (SQL нь серверийн сангаас — үйлчлүүлэгчээс биш)."""
        ck(k)
        need_exec()
        rec = get_verified().get(vid) if re.fullmatch(r"VQ-[0-9a-f]{8}", vid) else None
        if rec is None:
            raise HTTPException(status_code=404, detail="Баталгаажсан асуулга олдсонгүй.")
        return execute(rec["gold_sql"], rec.get("question_mn") or "", "verified",
                       {"level": "high", "label": "баталгаажсан",
                        "reason": f"Админ баталгаажуулсан SQL ({vid}) — загвар SQL бичээгүй.", "tips": []})

    @r.get("/analyst/analyst.js")
    def analyst_js():
        return FileResponse(Path(__file__).with_name("static") / "analyst.js", media_type="application/javascript")

    @r.get("/analyst/export/{rid}")
    def export(rid: str, fmt: str = "csv", k: str | None = None):
        ck(k)
        e = cache.get(rid)
        if e is None or not e.get("columns"):
            raise HTTPException(status_code=404, detail="Үр дүн олдсонгүй (1 цагаас хуучин эсвэл сервер дахин эхэлсэн) — "
                                                        "асуултаа дахин ажиллуулна уу.")
        # S3: кэшийн мөр аль хэдийн хэмжээгээр хязгаарлагдсан (ResultCache.put) — тайрсан бол толгой/хуудсанд тэмдэглэнэ
        capped = e.get("capped")
        if fmt == "xlsx":
            if not xlsx_available():
                raise HTTPException(status_code=501, detail="openpyxl суугаагүй — CSV-ээр татна уу.")
            body = to_xlsx(e["columns"], e["rows"], question=e.get("question"), sql=e.get("sql"),
                           note=size_notice(capped)["text"] if capped else None)
            mt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif fmt == "csv":
            body, mt = to_csv(e["columns"], e["rows"]), "text/csv; charset=utf-8"
        else:
            raise HTTPException(status_code=400, detail="fmt нь csv эсвэл xlsx")
        headers = {"Content-Disposition": f'attachment; filename="t2s_{rid}.{fmt}"', "Cache-Control": "no-store"}
        if capped:
            headers["X-T2S-Truncated"] = f"cells={capped.get('cells_cut', 0)}; rows_dropped={capped.get('rows_dropped', 0)}"
        return Response(body, media_type=mt, headers=headers)

    @r.post("/analyst/feedback")
    def vote(inp: VoteIn, k: str | None = None):
        ck(k)
        e = cache.get(inp.rid)
        if e is None:
            raise HTTPException(status_code=404, detail="Хариулт олдсонгүй (хугацаа дууссан) — дахин асууна уу.")
        return get_store().vote(inp.rid, e, inp.good, inp.comment)

    @r.get("/analyst/candidates")
    def candidates(admin: str | None = None):
        need_admin(admin)   # хэрэглэгчийн нийтлэг (T2S_WEB_KEY) түлхүүр хүрэлцэхгүй
        p = get_store().candidates
        if not p.exists():
            raise HTTPException(status_code=404, detail="Одоогоор 👍 авсан хос алга.")
        # хянагчид тайлж өгнө; диск дээр шифртэй хэвээр
        return Response(get_store().export_plain(), media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="candidate_examples.jsonl"'})

    return r
