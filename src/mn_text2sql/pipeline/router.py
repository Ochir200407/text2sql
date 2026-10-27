"""F8: Semantic layer + чиглүүлэгч (туршилтын төлөвлөгөө, 6-р санаа).

Гол KPI нь knowledge/metrics.yaml-д нэг удаа тодорхойлогдсон. LLM асуултыг
{metric, dimensions, filters, time, order, limit} JSON болгоно (эсвэл metric = null); compiler SQL-ийг тогтмол
дүрмээр үүсгэнэ. JSON-д дэмжигдээгүй хэмжээс/шүүлтүүр байвал, эсвэл шүүлтүүрийн утга DB-д олдохгүй бол асуулт
чөлөөт SQL үүсгэлт рүү очно. Тайланд хамрах хувийг (routed %) ба чиглүүлсэн/чиглүүлээгүй асуултын EX-ийг гаргана.

Үнэлгээнд JSON-ыг урьдчилан кэшилнэ (build/routes/<загвар>.jsonl, команд: route) — нэг GPU дээр нэг загвар.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import yaml
from rapidfuzz import fuzz

from ..config import Config, load_models, resolve_path
from ..text.normalize import cyr_key, strip_suffix, tokens, weak_latin_keys

log = logging.getLogger(__name__)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class RouteResult:
    json: str
    sql: str | None
    reason: str = ""


def load_metrics(path: str = "knowledge/metrics.yaml") -> dict:
    with open(resolve_path(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def catalog_text(spec: dict) -> str:
    lines = ["Metrics (id: Mongolian names; kind; allowed dimensions):"]
    for mid, m in spec["metrics"].items():
        lines.append(f"- {mid}: {', '.join(m['mn'])}; {m['kind']}; dims = {', '.join(m['dims'])}")
    lines.append("Dimensions (id: meaning):")
    for did, d in spec["dimensions"].items():
        lines.append(f"- {did}: {d['mn']}")
    return "\n".join(lines)


ROUTER_SYSTEM = """You convert a question about a Mongolian non-bank financial institution's data into a KPI request,
using ONLY the metric catalog below. Today is {today}. Questions may be Mongolian Cyrillic, Latin-transliterated
Mongolian, English or mixed.
{catalog}

Return ONE JSON object and nothing else:
{{"metric": <metric id or null>, "dimensions": [<dimension ids to group by>],
  "filters": [{{"dimension": <dimension id>, "value": <value exactly as written in the question>}}],
  "time": {{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}} or null, "order": "desc" | "asc" | null, "limit": <int or null>}}
Rules: choose a metric only if the question asks exactly that metric and every condition in the question is expressible
as the listed dimensions/filters/time; otherwise return {{"metric": null}}. "time" is only for flow metrics (inclusive
dates); snapshot metrics are as of today (time = null). "Top N ..." means order desc with limit N."""

ROUTER_EXAMPLES = [
    ("Салбар бүрийн PAR30-ийг гарга", {"metric": "par30", "dimensions": ["branch"], "filters": [], "time": None,
                                       "order": None, "limit": None}),
    ("2025 онд Эрдэнэт салбар хэдэн зээл олгосон бэ?",
     {"metric": "disbursement_count", "dimensions": [], "filters": [{"dimension": "branch", "value": "Эрдэнэт"}],
      "time": {"from": "2025-01-01", "to": "2025-12-31"}, "order": None, "limit": None}),
    ("Хамгийн олон зээлтэй 3 харилцагчийн нэр", {"metric": None}),
]


def router_messages(question: str, spec: dict, today: str) -> list[dict]:
    msgs = [{"role": "system", "content": ROUTER_SYSTEM.format(today=today, catalog=catalog_text(spec))}]
    for q, a in ROUTER_EXAMPLES:
        msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": json.dumps(a, ensure_ascii=False)}]
    msgs.append({"role": "user", "content": question})
    return msgs


def parse_route(text: str) -> dict | None:
    text = text.split("</think>")[-1]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


class SemanticRouter:
    def __init__(self, cfg: Config, model_name: str | None = None, llm=None, cache: dict | None = None):
        self.cfg = cfg
        self.spec = load_metrics(cfg["knowledge"].get("metrics", "knowledge/metrics.yaml"))
        registry = load_models()
        self.model_name = model_name or registry.get("router_model") or registry.get("intent_model")
        self.llm = llm
        self.sources: dict[str, str] = {}
        self.cache = cache if cache is not None else self._load_cache()
        self.today = str(cfg["project"]["as_of_date"])
        self._values = None

    def _load_cache(self) -> dict:
        path = resolve_path("build/routes") / f"{self.model_name}.jsonl"
        out = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    out[f"{d['id']}:{d['variant']}"] = d.get("json")
                    if d.get("source"):
                        self.sources[f"{d['id']}:{d['variant']}"] = d["source"]
        return out

    def _value_entries(self):
        if self._values is None:
            from ..retrieval.retriever import get_retriever
            self._values = get_retriever(self.cfg).values.entries
        return self._values

    def resolve_value(self, column: str, value: str) -> str | None:
        """Асуултын бичлэг (кирилл/галиг, дагавартай) → DB-ийн яг утга (тухайн баганын)."""
        best, best_v = 0.0, None
        q_c = " ".join(strip_suffix(t) for t in tokens(cyr_key(value)))
        q_l = weak_latin_keys(value)
        for e in self._value_entries():
            if e.column != column:
                continue
            v_c = " ".join(strip_suffix(t) for t in tokens(cyr_key(e.value)))
            v_l = weak_latin_keys(e.value)[0]
            s = max(fuzz.ratio(q_c, v_c), max(fuzz.ratio(k, v_l) for k in q_l))
            # "Эрдэнэт" / "erdenet" ↔ "Эрдэнэт салбар": асуултын үг утгын эхэнд бүтнээрээ байвал
            if len(q_c) >= 4 and (v_c.startswith(q_c) or any(v_l.startswith(k) and len(k) >= 4 for k in q_l)):
                s = max(s, 92)
            # тэнцвэл богино утгыг (Баянгол → "Баянгол салбар", "Баянгол-2 тооцооны төв" биш)
            if s > best or (s == best and best_v is not None and len(e.value) < len(best_v)):
                best, best_v = s, e.value
        return best_v if best >= 85 else None

    def route(self, question: str, item_id: str | None = None, variant: str = "mn") -> RouteResult | None:
        js = self.cache.get(f"{item_id}:{variant}") if item_id else None
        if js is None and self.llm is not None:
            resp = self.llm.chat(router_messages(question, self.spec, self.today), temperature=0.0, max_tokens=400)
            obj = parse_route(resp.texts[0])
            js = json.dumps(obj, ensure_ascii=False) if obj else None
        if not js:
            return None
        obj = json.loads(js) if isinstance(js, str) else js
        if not obj or not obj.get("metric"):
            return None
        sql, reason = self.compile(obj)
        if sql is None:
            log.debug("router fallback: %s", reason)
            return None
        return RouteResult(json.dumps(obj, ensure_ascii=False), sql, reason)

    def compile(self, obj: dict) -> tuple[str | None, str]:
        metrics, dims_spec = self.spec["metrics"], self.spec["dimensions"]
        m = metrics.get(obj.get("metric"))
        if m is None:
            return None, f"үл мэдэгдэх metric {obj.get('metric')}"
        a, tcol = "a", m.get("time")
        dims = list(dict.fromkeys(obj.get("dimensions") or []))
        filters = obj.get("filters") or []
        for d in dims + [f.get("dimension") for f in filters]:
            if d not in m["dims"]:
                return None, f"хэмжээс {d} metric {obj['metric']}-д дэмжигдэхгүй"
        joins, where = [], [m["where"].format(a=a)]
        for f in filters:
            d = dims_spec[f["dimension"]]
            if not d.get("value_column"):
                return None, f"{f['dimension']}-ээр шүүх боломжгүй"
            val = self.resolve_value(d["value_column"], str(f.get("value", "")))
            if val is None:
                return None, f"утга олдсонгүй: {f.get('value')}"
            if d.get("join"):
                joins.append(d["join"].format(a=a))
            where.append(f"{d['label'].format(a=a, t=f'{a}.{tcol}')} = '{val.replace(chr(39), chr(39) * 2)}'")
        time = obj.get("time")
        if time:
            if m["kind"] != "flow" or not tcol:
                return None, "агшин metric-д хугацааны шүүлтүүр дэмжихгүй"
            frm, to = str(time.get("from", "")), str(time.get("to", ""))
            if not (_DATE.match(frm) and _DATE.match(to)):
                return None, "хугацааны формат буруу"
            date.fromisoformat(frm), date.fromisoformat(to)
            where.append(f"{a}.{tcol} BETWEEN DATE '{frm}' AND DATE '{to}'")
        select = []
        for d in dims:
            ds = dims_spec[d]
            if ds.get("time") and (m["kind"] != "flow" or not tcol):
                return None, f"{d} хэмжээс агшин metric-д хамаарахгүй"
            if ds.get("join"):
                joins.append(ds["join"].format(a=a))
            select.append(f"{ds['label'].format(a=a, t=f'{a}.{tcol}')} AS {d}")
        select.append(f"{m['expr'].format(a=a)} AS value")
        sql = f"SELECT {', '.join(select)} FROM t2s.{m['base']} {a}"
        for j in dict.fromkeys(joins):
            sql += f" {j}"
        sql += " WHERE " + " AND ".join(f"({w})" for w in where)
        if dims:
            sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        order = (obj.get("order") or "").lower()
        if order in ("desc", "asc"):
            sql += f" ORDER BY value {order.upper()} NULLS LAST"
        elif dims:
            sql += " ORDER BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        limit = obj.get("limit")
        if isinstance(limit, int) and limit > 0:
            sql += f" LIMIT {limit}"
        return sql, "ok"


def build_routes(cfg: Config, items, variants: list[str], model_name: str | None = None,
                 base_url: str | None = None, workers: int = 8) -> dict:
    from ..llm.client import make_llm
    registry = load_models()
    model_name = model_name or registry.get("router_model") or registry.get("intent_model")
    mspec = registry["models"].get(model_name, {})
    llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
    if base_url:
        llm_cfg["base_url"] = base_url
    if mspec.get("extra_body"):
        llm_cfg["extra_body"] = mspec["extra_body"]
    llm = make_llm(llm_cfg)
    spec = load_metrics(cfg["knowledge"].get("metrics", "knowledge/metrics.yaml"))
    today = str(cfg["project"]["as_of_date"])
    path = resolve_path("build/routes") / f"{model_name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["id"], d["variant"]))
    jobs = [(it, v) for it in items for v in variants if it.question(v) and (it.id, v) not in done]
    lock = threading.Lock()
    stats = {"done": 0, "routed_json": 0, "skipped_done": len(done)}

    def work(job):
        it, v = job
        resp = llm.chat(router_messages(it.question(v), spec, today), temperature=0.0, max_tokens=400)
        obj = parse_route(resp.texts[0])
        rec = {"id": it.id, "variant": v, "source": it.question(v),
               "json": json.dumps(obj, ensure_ascii=False) if obj else None, "raw": resp.texts[0]}
        with lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["done"] += 1
            stats["routed_json"] += 1 if obj and obj.get("metric") else 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, jobs))
    stats["file"] = str(path)
    return stats
