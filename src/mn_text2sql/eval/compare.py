"""Гүйцэтгэлийн үр дүнг харьцуулах (EX): тоон хүлцэл, огноо, баганын байрлал, эрэмбэ.

Дүрэм (docs/EVAL.md):
* Баганын тоо ижил байх ёстой; баганын дарааллыг харгалзахгүй (зөв зохицлыг хайна).
* order_matters=True бол мөрийн дараалал, үгүй бол multiset (давхардлын тоо хадгална).
* Тоо: |a−b| ≤ max(abs_tol, rel_tol·max(|a|,|b|)); огноо/цаг: 00:00 цагтай timestamp = огноо.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

UB = timezone(timedelta(hours=8))


def norm_value(v, ndigits: int = 2):
    """Харьцуулах, hash хийхэд зориулсан хэвийн утга."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        if math.isnan(f):
            return "NaN"
        # 1-ээс бага (хувь/харьцаа) тоог 2 оронд бөөрөнхийлбөл 0.0532 ≈ 0.0649 болж худал тэнцэнэ — 6 оронд
        return round(f, ndigits if abs(f) >= 1 else 6)
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(UB).replace(tzinfo=None)
        if v.time() == time(0, 0):
            return v.date().isoformat()
        return v.isoformat(sep=" ", timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple)):
        return tuple(norm_value(x, ndigits) for x in v)
    return str(v).strip()


def _num(v):
    return isinstance(v, float) and not isinstance(v, bool)


def values_equal(a, b, abs_tol: float = 0.01, rel_tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if _num(a) and _num(b):
        tol = abs_tol if max(abs(a), abs(b)) >= 1 else min(abs_tol, 1e-4)   # бага тоонд нарийн хүлцэл
        return abs(a - b) <= max(tol, rel_tol * max(abs(a), abs(b)))
    if _num(a) != _num(b):
        # "12" (текст) ба 12.0 — текстийн тоог зөвшөөрнө
        try:
            fa, fb = float(a), float(b)
            return abs(fa - fb) <= max(abs_tol, rel_tol * max(abs(fa), abs(fb)))
        except (TypeError, ValueError):
            return False
    return a == b


def _norm_rows(rows, ndigits=2):
    return [tuple(norm_value(v, ndigits) for v in r) for r in rows]


def _key(v):
    """Тоог бүдүүвчлэн бүлэглэх түлхүүр (хүлцэлтэй тэнцүү байдлын урьдчилсан шүүлт)."""
    if _num(v):
        return ("n", round(v, 1))
    return ("v", v)


def _rows_match(g_rows, p_rows, mapping, ordered, abs_tol, rel_tol) -> bool:
    if len(g_rows) != len(p_rows):
        return False
    proj = [tuple(r[i] for i in mapping) for r in p_rows]
    if ordered:
        return all(all(values_equal(a, b, abs_tol, rel_tol) for a, b in zip(g, p)) for g, p in zip(g_rows, proj))
    # multiset: бүдүүн түлхүүрээр бүлэглээд дотор нь хүлцэлтэй тааруулна
    if Counter(tuple(_key(v) for v in r) for r in g_rows) == Counter(tuple(_key(v) for v in r) for r in proj):
        return _greedy_match(g_rows, proj, abs_tol, rel_tol)
    return _greedy_match(g_rows, proj, abs_tol, rel_tol)


def _greedy_match(g_rows, p_rows, abs_tol, rel_tol) -> bool:
    exact_g = Counter(g_rows)
    exact_p = Counter(p_rows)
    if exact_g == exact_p:
        return True
    remaining = list((exact_p - exact_g).elements())
    for g in (exact_g - exact_p).elements():
        hit = None
        for i, p in enumerate(remaining):
            if all(values_equal(a, b, abs_tol, rel_tol) for a, b in zip(g, p)):
                hit = i
                break
        if hit is None:
            return False
        remaining.pop(hit)
    return not remaining


def results_match(gold_cols, gold_rows, pred_cols, pred_rows, order_matters: bool = False,
                  abs_tol: float = 0.01, rel_tol: float = 1e-6, max_perm_cols: int = 7,
                  lenient_periods: bool = True, value_maps: list[dict] | None = None) -> bool:
    """Гүйцэтгэлийн үр дүнгийн тэнцүү байдал (EX). Уян горимд (docs/EVAL.md) дүрслэлийн зөрүүг зөвшөөрнө:
    * хугацааны бүлэг: сарын эхний огноо ↔ 'YYYY-MM' ↔ нэг жилийн доторх сарын дугаар; улирал, жил;
    * код ↔ нэр (value_maps): нэг хэмжээсийн код баганыг (segment_code) нэр баганаар (segment_name) гаргасан."""
    if strict_match(gold_cols, gold_rows, pred_cols, pred_rows, order_matters, abs_tol, rel_tol, max_perm_cols):
        return True
    if not lenient_periods or gold_rows is None or pred_rows is None:
        return False
    g, p = _norm_rows(gold_rows), _norm_rows(pred_rows)
    bases = [(g, p)]
    for mp in value_maps or []:
        g2, p2 = _apply_code_map(g, mp), _apply_code_map(p, mp)
        if g2 is g and p2 is p:
            continue
        if strict_match(gold_cols, g2, pred_cols, p2, order_matters, abs_tol, rel_tol, max_perm_cols, normalized=True):
            return True
        bases.append((g2, p2))
    for gg, pp in bases:
        for name, transform in PERIOD_TRANSFORMS:
            if name in ("month_num", "quarter_num"):
                # жилийг алддаг хувиргалт: зөвхөн нэг талд (нөгөө тал нь аль хэдийн дугаар) — 2025-03 ≠ 2026-03
                pairs = [(transform(gg), pp), (gg, transform(pp))]
            else:
                pairs = [(transform(gg), transform(pp))]
            for g2, p2 in pairs:
                if (g2 is not gg or p2 is not pp) and strict_match(gold_cols, g2, pred_cols, p2, order_matters,
                                                                     abs_tol, rel_tol, max_perm_cols, normalized=True):
                    return True
    return False


def _apply_code_map(rows, mp: dict):
    """Багана бүрийн бүх утга нэг хэмжээсийн код байвал нэрээр нь солино (код ба нэр хоёулаа map-ийн түлхүүр)."""
    if not rows:
        return rows

    def fn(vals):
        if all(isinstance(v, str) and v in mp for v in vals) and any(mp[v] != v for v in vals):
            return {v: mp[v] for v in vals}
        return None

    return _transform_columns(rows, fn)


def build_code_maps(executor, schema: str = "t2s") -> list[dict]:
    """t2s view-үүдийн (x_code, x_name) хосоос код→нэрийн map (нэг код = нэг нэр байх хэмжээс л)."""
    res = executor.run(
        "SELECT c1.table_name, c1.column_name, c2.column_name FROM information_schema.columns c1 "
        "JOIN information_schema.columns c2 ON c2.table_schema = c1.table_schema AND c2.table_name = c1.table_name "
        f"WHERE c1.table_schema = '{schema}' AND c1.column_name LIKE '%\\_code' "
        "AND c2.column_name IN (replace(c1.column_name, '_code', '_name'), replace(c1.column_name, '_code', '_name_mn'))")
    maps: dict[tuple[str, str], dict] = {}
    if not res.ok:
        return []
    for table, ccol, ncol in res.rows:
        r = executor.run(f'SELECT DISTINCT "{ccol}"::text, "{ncol}"::text FROM {schema}."{table}" '
                         f'WHERE "{ccol}" IS NOT NULL AND "{ncol}" IS NOT NULL')
        if not r.ok:
            continue
        mp: dict[str, str] = {}
        ok = True
        for code, name in r.rows:
            if mp.get(code, name) != name:
                ok = False
                break
            mp[code] = name
        if ok and mp:
            key = (ccol, ncol)
            merged = maps.setdefault(key, {})
            if any(merged.get(k, v) != v for k, v in mp.items()):
                maps[(table + "." + ccol, ncol)] = mp      # ижил нэртэй багана өөр утгатай бол тусад нь
            else:
                merged.update(mp)
    out = []
    for mp in maps.values():
        full = dict(mp)
        full.update({v: v for v in mp.values()})          # нэр нь өөрөө хэвээр (нэг багана холимог биш)
        out.append(full)
    return out


_YM = re.compile(r"^(\d{4})-(\d{2})(?:-01)?$")
_YQ = re.compile(r"^(\d{4})[- ]?Q([1-4])$", re.I)


def _col_values(rows, i):
    return [r[i] for r in rows if r[i] is not None]


def _as_month(v):
    """'YYYY-MM' / 'YYYY-MM-01' (огноо нь norm_value-ээр ISO текст болсон) → (жил, сар) эсвэл None."""
    if isinstance(v, str):
        m = _YM.match(v)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def _transform_columns(rows, fn):
    """fn(column_values) → шинэ утгын map эсвэл None. Нэг ч багана өөрчлөгдөөгүй бол эх rows-ийг буцаана."""
    if not rows:
        return rows
    ncol = len(rows[0])
    maps = {}
    for i in range(ncol):
        vals = _col_values(rows, i)
        if vals:
            mp = fn(vals)
            if mp is not None:
                maps[i] = mp
    if not maps:
        return rows
    return [tuple(maps[i].get(v, v) if i in maps and v is not None else v for i, v in enumerate(r)) for r in rows]


def _to_month_str(vals):
    ms = [_as_month(v) for v in vals]
    if all(ms):
        return {v: f"{y:04d}-{m:02d}" for v, (y, m) in zip(vals, ms)}
    return None


def _to_month_num(vals):
    ms = [_as_month(v) for v in vals]
    if all(ms) and len({y for y, _ in ms}) == 1:
        return {v: float(m) for v, (y, m) in zip(vals, ms)}
    return None


def _to_quarter_num(vals):
    ms = [_as_month(v) for v in vals]
    if all(ms) and len({y for y, _ in ms}) == 1 and all(m in (1, 4, 7, 10) for _, m in ms):
        return {v: float((m - 1) // 3 + 1) for v, (y, m) in zip(vals, ms)}
    qs = [_YQ.match(v) if isinstance(v, str) else None for v in vals]
    if all(qs) and len({q.group(1) for q in qs}) == 1:
        return {v: float(q.group(2)) for v, q in zip(vals, qs)}
    return None


def _to_year_num(vals):
    ms = [_as_month(v) for v in vals]
    if all(ms) and all(m == 1 for _, m in ms):
        return {v: float(y) for v, (y, m) in zip(vals, ms)}
    if all(isinstance(v, str) and re.fullmatch(r"\d{4}", v) for v in vals):
        return {v: float(v) for v in vals}
    return None


PERIOD_TRANSFORMS = [
    ("month_str", lambda rows: _transform_columns(rows, _to_month_str)),
    ("month_num", lambda rows: _transform_columns(rows, _to_month_num)),
    ("quarter_num", lambda rows: _transform_columns(rows, _to_quarter_num)),
    ("year_num", lambda rows: _transform_columns(rows, _to_year_num)),
]


def strict_match(gold_cols, gold_rows, pred_cols, pred_rows, order_matters: bool = False,
                 abs_tol: float = 0.01, rel_tol: float = 1e-6, max_perm_cols: int = 7,
                 normalized: bool = False) -> bool:
    if gold_rows is None or pred_rows is None:
        return False
    g = gold_rows if normalized else _norm_rows(gold_rows)
    p = pred_rows if normalized else _norm_rows(pred_rows)
    if len(g) != len(p):
        return False
    if not g:
        # Хоёулаа хоосон: BIRD-ийн set дүрмээр тэнцүү (баганын тоо хамаарахгүй). ББСБ-ийн eval set-д хоосон gold
        # байхгүй (check-gold алдаа). Нөлөөлөх газар: thesis benchmark-ийн «gold_empty» кейс, test-suite-ийн хуулбар DB
        # дээр хоосон болсон gold, BIRD сургалтын reward (BIRD-ийн албан ёсны set дүрэмтэй нийцнэ). docs/EVAL.md
        return True
    ng = len(gold_cols) if gold_cols else len(g[0])
    np_ = len(pred_cols) if pred_cols else len(p[0])
    if ng != np_:
        return False
    # Багана бүрийн боломжит харгалзаа (утгын multiset-ээр шүүнэ)
    cands = []
    for gi in range(ng):
        gvals = sorted((_key(r[gi]) for r in g), key=repr)
        opts = [pi for pi in range(np_) if sorted((_key(r[pi]) for r in p), key=repr) == gvals]
        if not opts:
            opts = list(range(np_))  # тоон хүлцлийн улмаас бүдүүн түлхүүр зөрж болно
        cands.append(opts)
    if ng > max_perm_cols:
        return _rows_match(g, p, list(range(ng)), order_matters, abs_tol, rel_tol)
    for mapping in itertools.product(*cands):
        if len(set(mapping)) != ng:
            continue
        if _rows_match(g, p, list(mapping), order_matters, abs_tol, rel_tol):
            return True
    return False


def result_hash(cols, rows, order_matters: bool = False) -> str:
    nr = _norm_rows(rows or [])
    nr = [tuple(sorted(map(repr, r))) for r in nr]  # баганын дараалалаас үл хамаарах
    if not order_matters:
        nr = sorted(nr)
    h = hashlib.sha1(repr((len(cols or []), nr)).encode("utf-8")).hexdigest()
    return h[:16]


def soft_f1(gold_rows, pred_rows, max_pairs: int = 4_000_000) -> float:
    """BIRD mini-dev маягийн Soft-F1: мөр бүрийг утгын олонлогоор харьцуулж хэсэгчилсэн оноо өгнө.
    Ижил мөрүүдийг эхлээд multiset-ээр шууд тааруулна (O(n)); үлдсэн мөрүүдийг л хос хосоор (greedy) харьцуулна.
    Үлдэгдлийн хос max_pairs-аас олон бол (жишээ: 20k × 20k) хэсэгчилсэн оноо өгөхгүй — бүхэлдээ fn/fp."""
    if not gold_rows and not pred_rows:
        return 1.0
    if not gold_rows or not pred_rows:
        return 0.0
    def key(c):
        return tuple(sorted(c.items()))
    g_all = [Counter(map(repr, r)) for r in _norm_rows(gold_rows)]
    p_all = [Counter(map(repr, r)) for r in _norm_rows(pred_rows)]
    cg, cp = Counter(key(c) for c in g_all), Counter(key(c) for c in p_all)
    common = cg & cp
    tp = float(sum(sum(v for _, v in k) * n for k, n in common.items()))
    fp = fn = 0.0
    g = [Counter(dict(k)) for k in (cg - common).elements()]
    p = [Counter(dict(k)) for k in (cp - common).elements()]
    if len(g) * len(p) > max_pairs:
        fn += sum(sum(c.values()) for c in g)
        fp += sum(sum(c.values()) for c in p)
        g, p = [], []
    used = set()
    for gr in g:
        best, best_j = -1.0, None
        for j, pr in enumerate(p):
            if j in used:
                continue
            inter = sum((gr & pr).values())
            if inter > best:
                best, best_j = inter, j
        if best_j is None:
            fn += sum(gr.values())
            continue
        used.add(best_j)
        pr = p[best_j]
        inter = sum((gr & pr).values())
        tp += inter
        fn += sum(gr.values()) - inter
        fp += sum(pr.values()) - inter
    for j, pr in enumerate(p):
        if j not in used:
            fp += sum(pr.values())
    if tp == 0:
        return 0.0
    prec, rec = tp / (tp + fp), tp / (tp + fn)
    return 2 * prec * rec / (prec + rec)
