"""Flowchart builder (вэб): pipeline-ийн алхмуудыг node болгож угсраад туршилтын тохиргоо (RunOptions) болгоно.

* Node бүр RunOptions-ийн хэдэн талбарыг эзэмшинэ (NODES). Flow-д байгаа node → түүний параметр; байхгүй node →
  талбарууд нь суурь утгаараа (experiments.yaml-ийн defaults). Ингэснээр flow ↔ тохиргоо хоёр тийш нэг утгатай:
  `decompose(resolve_run(X))` → `compile_flow` нь X-ийн тохиргоог яг сэргээнэ (tests/test_flow.py).
* Кодын гүйцэтгэлийн дараалал тогтмол (orchestrator.ask) — flow-ийн дараалал өөр бол анхааруулга өгнө, тооцоонд
  зөвхөн input → output замд орсон node-ууд орно.
* Хадгалах: config/flows.yaml (`runs` — experiments.yaml-тай ижил хэлбэр, `graphs` — зураглал). options.load_experiments
  үүнийг нэгтгэдэг тул `t2s eval --run <flow ID>` шууд ажиллана.
* Ажиллуулах (POST /flow/run_job): вэб үйлчилгээний загвар, DB-тэй үед нэг асуултыг алхам бүрийн мөрөөр харуулна.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import fields
from pathlib import Path

import yaml

from ..config import resolve_path
from ..pipeline.options import RunOptions, load_experiments, resolve_run

FLOWS_FILE = "config/flows.yaml"
_ID = re.compile(r"^[A-Za-z][\w.-]{0,31}$")

# rank = orchestrator.ask-ийн гүйцэтгэлийн дараалал; flag = node байвал True болох талбар; on = node нэмэхэд
# анхдагч параметр (суурь утгаас ялгаатай байх ёстой бол)
NODES: list[dict] = [
    {"type": "input", "title": "Асуулт", "rank": 0, "fixed": True, "color": "slate",
     "help": "Хэрэглэгчийн асуулт. Хувилбар: кирилл (mn), галиг (latin), англи (en).",
     "fields": {"variant": {"kind": "enum", "options": ["mn", "latin", "en"]},
                "dialect": {"kind": "enum", "options": ["sqlite_native", "postgres_prompt", "sqlite_transpile"]},
                "dataset": {"kind": "enum", "options": ["nbfi", "bird_mn"]}}},
    {"type": "intent", "title": "Intent шалгалт", "rank": 1, "color": "amber",
     "help": "Хариулах боломжгүй / хоёрдмол асуултыг татгалзах эсвэл тодруулах (glossary-ийн дүрэм); intent = F7 JSON intent, semantic_router = F8.",
     "on": {"intent_check": True},
     "fields": {"intent_check": {"kind": "bool"}, "intent_refuse_only": {"kind": "bool"}, "intent": {"kind": "bool"}, "semantic_router": {"kind": "bool"}}},
    {"type": "rules", "title": "Дүрмийн шүүлт (E4)", "rank": 2, "color": "amber",
     "help": "AUTHORING ангиллын дүрмээр SQL бичихээс өмнө татгалзах/тодруулах; эцэст нь итгэлийн босго.",
     "on": {"rule_refuse": True, "rule_clarify": True},
     "fields": {"rule_refuse": {"kind": "bool"}, "rule_clarify": {"kind": "bool"},
                "abstain_on_error": {"kind": "bool"}, "abstain_threshold": {"kind": "float?"},
                "low_conf_action": {"kind": "enum", "options": ["abstain", "clarify"]}}},
    {"type": "translate", "title": "Орчуулга (mn→en)", "rank": 3, "flag": "translate", "color": "teal",
     "help": "Qwen3.5-9B орчуулагч. en_prompt: загварт англи, retrieval монголоор. Кэш: .t2 = нэр томьёоны RAG + маскын засвар.",
     "on": {"translate_mode": "en_prompt", "translation_cache": "qwen3.5-9b.t2"},
     "fields": {"translate_mode": {"kind": "enum", "options": ["en_prompt", "bilingual", "replace"]},
                "translation_cache": {"kind": "str?"}}},
    {"type": "retrieve", "title": "Retrieval (RAG)", "rank": 4, "color": "blue",
     "help": "Жишээний сан (few-shot), DB-ийн утга, нэр томьёо, схемийн тайлбар.",
     "on": {"fewshot_k": 3, "values": True, "glossary": True, "describe": True},
     "fields": {"fewshot_k": {"kind": "int"}, "values": {"kind": "bool"},
                "value_mode": {"kind": "enum", "options": ["basic", "rule", "learned"]}, "glossary": {"kind": "bool"},
                "glossary_en": {"kind": "bool"}, "glossary_file": {"kind": "str?"}, "describe": {"kind": "bool"},
                "knowledge_notes": {"kind": "bool"}, "pivot": {"kind": "bool"}, "index_dir": {"kind": "str?"}}},
    {"type": "schema_link", "title": "Schema linking", "rank": 5, "flag": "schema_linking", "color": "blue",
     "help": "Prompt-д зөвхөн хамааралтай хүснэгтүүд (T4-д богино prompt).",
     "fields": {"top_tables": {"kind": "int"}}},
    {"type": "planner", "title": "Planner", "rank": 6, "flag": "agent_plan", "color": "violet", "needs": "observe",
     "help": "SQL-ээс өмнө англи төлөвлөгөө (хүснэгт, join, шүүлт, агрегат). Agent давталттай үед л ажиллана.",
     "fields": {}},
    {"type": "writer", "title": "SQL бичигч (Arctic)", "rank": 7, "fixed": True, "color": "green",
     "help": "Arctic-Text2SQL-R1-7B (+LoRA adapter). sqrl протоколд DB судлах алхам.",
     "fields": {"adapter": {"kind": "str?"}, "agent_max_steps": {"kind": "int"},
                "search_values_tool": {"kind": "bool"}}},
    {"type": "repair", "title": "Алдаагаар засах (E1)", "rank": 8, "color": "green",
     "help": "Гүйцэтгэлийн алдааны мессежийг буцааж өгч засуулах.",
     "on": {"repair_rounds": 2, "repair_on_empty": True},
     "fields": {"repair_rounds": {"kind": "int"}, "repair_on_empty": {"kind": "bool"}}},
    {"type": "vote", "title": "Санал хураалт (E2)", "rank": 9, "color": "green",
     "help": "N хувилбар үүсгэж гүйцэтгэлийн үр дүнгээр олонхоор сонгох; vote_filter = хоосон/буруу утгатайг хасах.",
     "on": {"candidates": 8, "sampling": True, "seeds": [1, 2, 3]},
     "fields": {"candidates": {"kind": "int"}, "sampling": {"kind": "bool"}, "seeds": {"kind": "ints"},
                "vote_filter": {"kind": "bool"}}},
    {"type": "observe", "title": "Agent: tool шалгалт", "rank": 10, "flag": "agent", "color": "violet",
     "help": "LLM-гүй: гүйцэтгэлийн алдаа, хоосон үр дүн, WHERE-ийн утга DB-д байгаа эсэх → олдвор байвал засуулна (≤ rounds).",
     "fields": {"agent_literal_check": {"kind": "bool"}, "agent_rounds": {"kind": "int"}}},
    {"type": "critic", "title": "Agent: critic", "rank": 11, "flag": "agent_critic", "color": "violet",
     "needs": "observe", "help": "Загвар өөрийн SQL-ийг үнэлнэ: VERDICT OK / REVISE. agent_llm = өөр загвар.",
     "fields": {"agent_llm": {"kind": "str?"}}},
    {"type": "postproc", "title": "SQL засвар (postproc)", "rank": 12, "flag": "postproc", "color": "slate",
     "help": "Схем/хадгалагдсан утгаас гаргасан детерминист засвар.", "fields": {}},
    {"type": "output", "title": "Хариулт", "rank": 13, "fixed": True, "color": "slate",
     "help": "SQL, үр дүн, итгэл, монгол тайлбар.", "fields": {}},
]
BY_TYPE = {n["type"]: n for n in NODES}
# Builder нээгдэхэд ачаалах одоогийн систем (эхнийх нь анхдагч; docs/WORKLOG.md 6.13–6.14)
PRESETS = [
    {"id": "I3t2", "title": "Одоогийн систем (I3t2)", "desc": "T2 орчуулга → RAG (жишээний сан + dev) → Arctic D2"},
    {"id": "I3tr", "title": "Test-ийн шилдэг (I3tr, 45%)", "desc": "I3t + E4-ийн дүрэм"},
    {"id": "K1", "title": "Agent (K1)", "desc": "I3t2 + tool шалгалт + critic, ≤2 засвар"},
    {"id": "WEB", "title": "Вэб үйлчилгээ (WEB)", "desc": "I3t + schema linking, 1 хувилбар (T4)"},
]
_OPT_FIELDS = {f.name for f in fields(RunOptions)}
# critic-ийн flag (agent_critic) анхдагчаар True — agent асаагүй үед утгагүй тул суурьтайгаа ижил үлдээнэ
_FLAG_WHEN_ABSENT = {"agent_critic": None}


def baseline(exp: dict | None = None) -> dict:
    """Node байхгүй үеийн утга: RunOptions-ийн анхдагч + experiments.yaml defaults."""
    exp = exp or load_experiments()
    base = {f.name: copy.deepcopy(getattr(RunOptions(), f.name)) for f in fields(RunOptions)}
    base.update({k: v for k, v in (exp.get("defaults") or {}).items() if k in base})
    return base


def _present(ntype: str, spec: dict, base: dict) -> bool:
    n = BY_TYPE[ntype]
    if n.get("fixed"):
        return True
    if ntype == "critic":
        return bool(spec.get("agent")) and bool(spec.get("agent_critic"))
    if ntype == "planner":
        return bool(spec.get("agent")) and bool(spec.get("agent_plan"))
    if n.get("flag"):
        return bool(spec.get(n["flag"]))
    if ntype == "vote":
        return int(spec.get("candidates") or 1) > 1 or bool(spec.get("vote_filter"))
    return any(spec.get(k) != base.get(k) for k in n["fields"])


def decompose(opts: RunOptions | dict, exp: dict | None = None) -> dict:
    """Тохиргоо → flow (node, edge, байрлал) — каноник дарааллаар."""
    spec = opts if isinstance(opts, dict) else {f.name: getattr(opts, f.name) for f in fields(RunOptions)}
    base = baseline(exp)
    nodes = []
    for n in NODES:
        if not _present(n["type"], spec, base):
            continue
        nodes.append({"id": n["type"], "type": n["type"],
                      "params": {k: copy.deepcopy(spec.get(k, base.get(k))) for k in n["fields"]}})
    for i, nd in enumerate(nodes):   # могой хэлбэр: 5 node-оор мөр, мөр бүр эсрэг чиглэлд
        row, col = divmod(i, 5)
        nd["x"], nd["y"] = 40 + (col if row % 2 == 0 else 4 - col) * 230, 40 + row * 170
    edges = [{"from": a["id"], "to": b["id"]} for a, b in zip(nodes, nodes[1:], strict=False)]
    return {"nodes": nodes, "edges": edges}


def _path_nodes(flow: dict) -> tuple[list[dict], list[str]]:
    """input-ээс output хүрэх замд орсон node-ууд (BFS-ийн дарааллаар) ба анхааруулга."""
    warn: list[str] = []
    nodes = {n["id"]: n for n in flow.get("nodes") or []}
    out_edges: dict[str, list[str]] = {}
    in_edges: dict[str, list[str]] = {}
    for e in flow.get("edges") or []:
        if e.get("from") in nodes and e.get("to") in nodes:
            out_edges.setdefault(e["from"], []).append(e["to"])
            in_edges.setdefault(e["to"], []).append(e["from"])
    starts = [i for i, n in nodes.items() if n["type"] == "input"]
    ends = {i for i, n in nodes.items() if n["type"] == "output"}
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Flow-д яг нэг «Асуулт» ба нэг «Хариулт» node байх ёстой")

    def reach(src: list[str], adj: dict[str, list[str]]) -> list[str]:
        seen, order, queue = set(src), list(src), list(src)
        while queue:
            for nb in adj.get(queue.pop(0), []):
                if nb not in seen:
                    seen.add(nb)
                    order.append(nb)
                    queue.append(nb)
        return order

    fwd = reach(starts, out_edges)
    back = set(reach(list(ends), in_edges))
    on_path = [i for i in fwd if i in back]
    if not ends & set(on_path):
        raise ValueError("«Асуулт»-аас «Хариулт» хүртэл холбогдсон зам алга — node-уудыг сумаар холбоно уу")
    if any(n["type"] == "writer" for i, n in nodes.items() if i in on_path) is False:
        raise ValueError("Замд «SQL бичигч» node заавал байна")
    off = [nodes[i] for i in nodes if i not in on_path]
    if off:
        warn.append("Замд ороогүй тул тооцохгүй: " + ", ".join(BY_TYPE[n["type"]]["title"] for n in off))
    branching = [i for i in on_path if len([t for t in out_edges.get(i, []) if t in back]) > 1]
    if branching:
        warn.append("Салаалсан зам: бүх салааны node-ууд нэг дор хэрэглэгдэнэ (код нэг урсгалтай)")
    return [nodes[i] for i in on_path], warn


def compile_flow(flow: dict, base_id: str | None = None, exp: dict | None = None) -> dict:
    """Flow → {spec (RunOptions-ийн бүх талбар), diff (base_id-аас ялгаа), yaml мөр, анхааруулга}."""
    exp = exp or load_experiments()
    path, warn = _path_nodes(flow)
    types = [n["type"] for n in path]
    dup = sorted({t for t in types if types.count(t) > 1})
    if dup:
        raise ValueError("Давхардсан node: " + ", ".join(BY_TYPE[t]["title"] for t in dup))
    ranks = [BY_TYPE[t]["rank"] for t in types]
    if ranks != sorted(ranks):
        canon = " → ".join(BY_TYPE[t]["title"] for t in sorted(types, key=lambda t: BY_TYPE[t]["rank"]))
        warn.append("Кодын гүйцэтгэлийн дараалал тогтмол — бодит дараалал: " + canon)
    for t in types:
        need = BY_TYPE[t].get("needs")
        if need and need not in types:
            raise ValueError(f"«{BY_TYPE[t]['title']}» нь «{BY_TYPE[need]['title']}» node-тэй үед л ажиллана")
    base_spec = baseline(exp)
    if base_id:
        bo = resolve_run(base_id, exp)
        ref = {f.name: getattr(bo, f.name) for f in fields(RunOptions)}
    else:
        ref = dict(base_spec)
    spec = dict(ref)
    for n in NODES:   # байхгүй node-ийн талбарууд суурь утгаараа
        if n["type"] in types or n.get("fixed"):
            continue
        for k in n["fields"]:
            spec[k] = copy.deepcopy(base_spec[k])
        if n.get("flag"):
            spec[n["flag"]] = False if n["flag"] not in _FLAG_WHEN_ABSENT else base_spec[n["flag"]]
    by_type = {n["type"]: n for n in path}
    for t, nd in by_type.items():
        meta = BY_TYPE[t]
        params = nd.get("params") or {}
        for k, fmeta in meta["fields"].items():
            if k in params:
                spec[k] = _coerce(params[k], fmeta["kind"], k)
        if meta.get("flag"):
            spec[meta["flag"]] = True
    if "observe" in by_type and "critic" not in by_type:
        spec["agent_critic"] = False
    if "vote" in by_type and int(spec["candidates"] or 1) <= 1 and not spec.get("vote_filter"):
        warn.append("Санал хураалт: candidates ≤ 1 бөгөөд vote_filter унтраалттай — node нөлөөгүй")
    if spec.get("translate") and spec.get("translate_mode") == "en_prompt" and spec.get("variant") == "en":
        warn.append("Англи асуултыг орчуулах шаардлагагүй (variant = en)")
    opts = RunOptions(**{k: v for k, v in spec.items() if k in _OPT_FIELDS})
    fp = opts.fingerprint()
    diff = {k: v for k, v in spec.items()
            if k not in ("run_id", "desc", "group", "base", "compare_to", "models") and v != ref.get(k)}
    return {"spec": spec, "diff": diff, "warnings": warn, "path": types,
            "fingerprint": hashlib.sha1(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()[:10]}


def _coerce(v, kind: str, name: str):
    try:
        if kind == "bool":
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        if kind == "int":
            return int(v)
        if kind == "float?":
            return None if v in (None, "", "null") else float(v)
        if kind == "str?":
            return None if v in (None, "", "null") else str(v)
        if kind == "ints":
            if isinstance(v, str):
                v = [x for x in re.split(r"[,\s]+", v) if x]
            return [int(x) for x in v] or [0]
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name}: буруу утга {v!r} ({kind})") from e
    return v


def yaml_line(run_id: str, base_id: str | None, diff: dict, desc: str) -> str:
    body = {"base": base_id} if base_id else {}
    body.update(diff)
    body["desc"] = desc or "Flow builder-ээр угсарсан"
    body["group"] = "W"
    inner = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in body.items())
    return f"  {run_id}: {{{inner}}}"


def suggestions(exp: dict | None = None) -> dict:
    """Талбар бүрд experiments.yaml-д хэрэглэгдсэн утгууд (str? талбарын сонголт)."""
    exp = exp or load_experiments()
    out: dict[str, set] = {}
    for spec in (exp.get("runs") or {}).values():
        for k, v in (spec or {}).items():
            if isinstance(v, str) and k in _OPT_FIELDS and not v.startswith("${"):
                out.setdefault(k, set()).add(v)
    return {k: sorted(v) for k, v in out.items()}


# ---------------- хадгалах ----------------

def load_flows() -> dict:
    p = resolve_path(FLOWS_FILE)
    if not p.exists():
        return {"runs": {}, "graphs": {}}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {"runs": d.get("runs") or {}, "graphs": d.get("graphs") or {}}


def save_flow(run_id: str, flow: dict, base_id: str | None, desc: str) -> dict:
    if not _ID.match(run_id or ""):
        raise ValueError("ID: латин үсгээр эхэлсэн, ≤ 32 тэмдэгт (үсэг, тоо, _ . -)")
    exp_main = yaml.safe_load(resolve_path("config/experiments.yaml").read_text(encoding="utf-8")) or {}
    if run_id in (exp_main.get("runs") or {}):
        raise ValueError(f"{run_id} нь config/experiments.yaml-д аль хэдийн бий — өөр ID сонгоно уу")
    res = compile_flow(flow, base_id)
    body = {"base": base_id} if base_id else {}
    body.update(res["diff"])
    body.update(desc=desc or "Flow builder-ээр угсарсан", group="W")
    data = load_flows()
    data["runs"][run_id] = body
    data["graphs"][run_id] = {"base": base_id, "flow": flow, "saved": time.strftime("%Y-%m-%d %H:%M")}
    _write_flows(data)
    resolve_run(run_id)   # нэгтгэсэн тохиргоогоор задрах эсэхийг шалгана
    return {"ok": True, "id": run_id, "file": FLOWS_FILE, **res}


def delete_flow(run_id: str) -> bool:
    data = load_flows()
    if run_id not in data["runs"]:
        return False
    data["runs"].pop(run_id, None)
    data["graphs"].pop(run_id, None)
    _write_flows(data)
    return True


def _write_flows(data: dict) -> None:
    head = ("# Flow builder-ээр (вэб /builder) угсарсан туршилтууд. options.load_experiments нь `runs`-ийг\n"
            "# experiments.yaml-тай нэгтгэнэ (ID давхцахгүй). `graphs` = node-уудын зураглал (builder дахин ачаална).\n")
    resolve_path(FLOWS_FILE).write_text(head + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200),
                                        encoding="utf-8")


# ---------------- нэг асуултаар ажиллуулах (алхам бүрийн мөр) ----------------

def trace(ans, spec: dict, tr: dict | None) -> list[dict]:
    """Answer → node бүрийн тайлбар (builder дээр node-ийн доор харуулна)."""
    d = ans.to_dict(rows=20)
    ret = ans.retrieval or {}
    steps: list[dict] = [{"node": "input", "text": ans.question}]
    if tr:
        steps.append({"node": "translate", "text": tr.get("text"), "hints": tr.get("hints"), "spans": tr.get("spans")})
    if ret:
        steps.append({"node": "retrieve", "examples": (ret.get("examples") or [])[:5],
                      "values": len(ret.get("values") or []), "glossary": ret.get("glossary"),
                      "tables": ret.get("linked_tables") or ret.get("tables")})
    if ret.get("plan") is not None:
        steps.append({"node": "planner", "text": ret.get("plan")})
    cands = d.get("candidates") or []
    steps.append({"node": "writer", "candidates": [{"sql": c.get("sql") or c.get("raw_sql"), "ok": c.get("ok"),
                                                    "rows": c.get("rows"), "error": c.get("error"),
                                                    "repairs": c.get("repairs")} for c in cands[:8]]})
    if len(cands) > 1:
        steps.append({"node": "vote", "confidence": ans.confidence, "unanimous": ans.unanimous,
                      "chosen": ans.chosen_index})
    chosen = cands[ans.chosen_index] if ans.chosen_index is not None and ans.chosen_index < len(cands) else {}
    if spec.get("agent"):
        steps.append({"node": "observe", "log": chosen.get("agent_log")})
    steps.append({"node": "output", "status": ans.status, "sql": ans.sql, "columns": d.get("columns"),
                  "rows": d.get("rows"), "row_count": d.get("row_count"), "explanation": ans.explanation_mn})
    # Бусад алхам (шүүлт, хүснэгт сонголт, цэгцлэл): юу хийснийг товч — builder дээр ногоон болж, Trace-д гарна
    stopped = ans.status in ("refused", "clarify") and not cands
    if spec.get("intent_check"):
        steps.append({"node": "intent", "text": ans.explanation_mn if stopped else "Хариулах боломжтой асуулт"})
    if spec.get("rule_refuse") or spec.get("rule_clarify") or spec.get("abstain_on_error") or spec.get("abstain_threshold"):
        steps.append({"node": "rules", "text": "Хориотой хүсэлт биш" if not stopped else "Шүүлтээр зогссон"})
    if spec.get("translate") and not tr and not stopped:
        steps.append({"node": "translate", "text": "Орчуулагч загвар алга — монгол асуултаар үргэлжлүүлэв"})
    if spec.get("schema_linking") and ret.get("linked_tables"):
        steps.append({"node": "schema_link", "text": f"{len(ret['linked_tables'])} хүснэгт: " + ", ".join(ret["linked_tables"][:6])})
    if spec.get("postproc") and cands:
        steps.append({"node": "postproc", "text": "SQL-ийг шалгаж цэгцэлсэн"})
    steps.sort(key=lambda s: BY_TYPE.get(s["node"], {}).get("rank", 99))
    return steps


# ---------------- prompt ба RAG-ийн харагдац (загвар дуудахгүй) ----------------

def _rel(path) -> str:
    """Төслийн үндсээс харьцангуй зам (дэлгэцэнд богино, машины зам ил гаргахгүй)."""
    try:
        return Path(path).resolve().relative_to(resolve_path(".").resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def _src(name: str, path, used: bool, detail: str) -> dict:
    return {"name": name, "path": _rel(path), "used": bool(used), "detail": detail}


def inspect_prompt(pipe, spec: dict, question: str, item_id: str | None = None) -> dict:
    """Загварт очих prompt-ыг ask()-тай ЯГ ижил замаар (Pipeline.prompt_for) угсарч, RAG-ийн татсан мэдлэг, эх
    сурвалж, токены задаргааг буцаана. LLM ба DB дуудахгүй (planner-ийн төлөвлөгөө, agent-ийн засвар орохгүй)."""
    from ..llm.prompts import est_tokens
    variant = spec.get("variant") or "mn"
    out: dict = {}
    messages, prefill, ctx, info, shown = pipe.prompt_for(question, variant, item_id, _out=out)
    ret = out.get("ret")
    q_ret, _ = pipe.question_forms(question, variant, item_id)
    o, cfg, rtv = pipe.opts, pipe.cfg, pipe.retriever
    kn = cfg["knowledge"]
    idx = getattr(rtv, "index_dir", None) or kn.get("index_dir")
    meta = getattr(rtv, "index_meta", None) or {}
    rag = None
    if ret is not None:
        linked = set(ret.linked_tables)
        ranked = sorted(ret.tables.items(), key=lambda kv: -kv[1]["score"])
        rag = {"ms": round(ret.ms, 1), "question_cyr": ret.question_cyr, "linked": list(ret.linked_tables),
               "schema_linking": bool(o.schema_linking), "n_prompt_tables": len(ctx.tables),
               "tables": [{"table": t, "score": round(v["score"], 3), "linked": t in linked,
                           "columns": [[c, round(sc, 3)] for c, sc in sorted((v.get("columns") or {}).items(),
                                                                            key=lambda kv: -kv[1])[:5]]}
                          for t, v in ranked[:12]],
               "values": [{"table": h.table, "column": h.column, "value": h.value, "score": h.score,
                           "keyword": getattr(h, "keyword", None)} for h in ret.values],
               "glossary": [{"id": g.id, "term": g.term, "en": g.en, "definition": g.definition_mn, "sql": g.sql}
                            for g in ret.glossary],
               "examples": [{"id": e.id, "question": q, "sql": sql}
                            for e, (q, sql) in zip(ret.examples, ctx.examples or [], strict=False)]}
    schema = pipe.schema_text(ctx)
    ev = "\n".join(ctx.evidence_lines or [])
    ex = "\n".join(f"{q}\n{sql}" for q, sql in ctx.examples or [])
    total = sum(est_tokens(m["content"]) for m in messages) + (est_tokens(prefill) if prefill else 0)
    parts = {"schema": est_tokens(schema), "evidence": est_tokens(ev) if ev else 0,
             "examples": est_tokens(ex) if ex else 0}
    parts["other"] = max(0, total - sum(parts.values()))
    n_tab = info.get("n_tables", len(ctx.tables))
    sources = [
        _src("Схемийн загвар (хүснэгт, багана, тайлбар)", kn["semantic_model"], True,
             f"{n_tab} хүснэгт prompt-д" + ("; тайлбартай" if o.describe else "; тайлбаргүй")),
        _src("Бизнесийн тэмдэглэл (notes)", kn["semantic_model"], o.knowledge_notes,
             f"{len(pipe.model.notes_mn)} тэмдэглэл" if o.knowledge_notes else "унтраалттай"),
        _src("Нэр томьёо (glossary)", kn["glossary"], bool(o.glossary and ret is not None),
             f"{len(ret.glossary)} нэр томьёо татсан" if ret is not None and o.glossary else "унтраалттай"),
        _src("DB-ийн утгын индекс", f"{idx}/values.json", bool(o.values and ret is not None),
             (f"{len(ret.values)} утга таарсан" if ret is not None and o.values else "унтраалттай")
             + (f" (индекст {meta.get('values')})" if meta.get("values") else "")),
        _src("Жишээний сан (few-shot)", f"{idx}/examples.json", bool(o.fewshot_k > 0 and ret is not None),
             (f"{len(ret.examples)}/{o.fewshot_k} жишээ" if ret is not None and o.fewshot_k > 0 else "унтраалттай")
             + (f" (санд {meta.get('examples')})" if meta.get("examples") else "")),
        _src("Баганын жишээ утга (DDL-д)", f"{idx}/column_examples.json", bool(pipe.col_examples),
             f"{len(pipe.col_examples)} багана" if pipe.col_examples else "индекс алга"),
    ]
    search = ("BM25 + embedding (" + str(meta["embedding_model"]) + ")" if meta.get("embedding_model")
              else "BM25 (embedding-гүй)" if rtv is not None else "RAG алга (индекс ачаалагдаагүй)")
    return {"question": {"original": question, "cyrillic": ret.question_cyr if ret is not None else None,
                         "retrieval": q_ret, "shown": shown, "translated": shown != question},
            "rag": rag, "evidence": list(ctx.evidence_lines or []), "sources": sources,
            "messages": [{"role": m["role"], "content": m["content"], "tokens": est_tokens(m["content"])}
                         for m in messages],
            "prefill": prefill, "tokens": {"total": total, **parts},
            "model": pipe.model_name, "family": pipe.family, "search": search, "index_dir": _rel(idx)}


def _inspect(pipe, res: dict, question: str, translation: str | None, notes: list[str]) -> dict:
    spec, warn = res["spec"], list(res["warnings"]) + list(notes)
    key = None
    if spec.get("translate"):
        if translation and translation.strip():
            item_id = f"inspect-{uuid.uuid4().hex[:8]}"
            key = f"{item_id}:{spec.get('variant') or 'mn'}"
            pipe.translations[key] = translation.strip()
        else:
            warn.append("Орчуулга алхам асаалттай: англи орчуулгыг талбарт бичвэл загварт яг харагдах асуултыг "
                        "харуулна (хоосон бол монгол асуултаар угсарлаа)")
    try:
        out = inspect_prompt(pipe, spec, question, key.split(":")[0] if key else None)
    finally:
        if key:
            pipe.translations.pop(key, None)
    if spec.get("intent_check") or spec.get("rule_refuse") or spec.get("rule_clarify"):
        warn.append("Шүүлтийн алхмууд асуултыг SQL бичихээс өмнө татгалзаж/тодруулж болно — энд тэдгээрийг алгасаж "
                    "prompt-ыг үргэлж харуулав")
    if spec.get("agent") and spec.get("agent_plan"):
        warn.append("Planner-ийн төлөвлөгөө (LLM) энд орохгүй — бодит ажиллуулалтад evidence-д нэмэгдэнэ")
    out["warnings"] = warn
    return out


class _NoDB:
    """Prompt угсрахад DB хэрэггүй — санамсаргүй дуудвал тодорхой алдаа өгнө."""

    def __getattr__(self, name):
        raise RuntimeError("Энэ горимд өгөгдлийн сан холбоогүй (зөвхөн prompt харах)")


class LocalInspector:
    """Загвар, DB-гүй угсрах горимд (t2s flow-ui) prompt ба RAG-ийг харуулна: локал индекс (build/index)."""

    def __init__(self):
        self.pipes: dict[str, tuple] = {}
        self.lock = threading.Lock()

    def pipeline(self, spec: dict, fp: str):
        from ..config import load_config
        from ..llm.client import MockLLM
        from ..pipeline.orchestrator import Pipeline
        with self.lock:
            if fp not in self.pipes:
                if len(self.pipes) >= 4:
                    self.pipes.pop(next(iter(self.pipes)))
                cfg, notes = load_config(), []
                opts = RunOptions(**{k: v for k, v in spec.items() if k in _OPT_FIELDS})
                opts.run_id = f"inspect-{fp}"
                if opts.index_dir and not (resolve_path(opts.index_dir) / "meta.json").exists():
                    notes.append(f"Локалд {opts.index_dir} индекс алга — {cfg['knowledge']['index_dir']}-ээр харуулав "
                                 "(сервер дээрх жишээний сан өөр байж болно)")
                    opts.index_dir = None
                mock = MockLLM(lambda msgs: "")
                try:
                    pipe = Pipeline(cfg, opts, "arctic-r1-7b", llm=mock, executor=_NoDB())
                except (FileNotFoundError, RuntimeError) as e:
                    notes.append(f"RAG-ийн индекс ачаалагдсангүй ({e}) — RAG-гүй prompt")
                    opts.fewshot_k = 0
                    opts.values = opts.glossary = opts.schema_linking = opts.describe = False
                    pipe = Pipeline(cfg, opts, "arctic-r1-7b", llm=mock, executor=_NoDB())
                if pipe.retriever is not None and not pipe.retriever.index_meta.get("embedding_model"):
                    notes.append("Локал индекс embedding-гүй (BM25) — сервер дээрх (bge-m3) хайлтаас үр дүн өөр байж болно")
                self.pipes[fp] = (pipe, notes)
            return self.pipes[fp]

    def inspect(self, flow: dict, question: str, base_id: str | None = None, translation: str | None = None) -> dict:
        res = compile_flow(flow, base_id)
        pipe, notes = self.pipeline(res["spec"], res["fingerprint"])
        return _inspect(pipe, res, question, translation, notes)


class FlowRunner:
    """Вэб үйлчилгээний LLM/DB-г дахин ашиглана; тохиргоо бүрт Pipeline-ийг нэг удаа үүсгэнэ (≤ 4)."""

    def __init__(self, cfg, model_name, llm, executor, guard, translator=None, entity_keys=None):
        self.cfg, self.model_name, self.llm = cfg, model_name, llm
        self.executor, self.guard = executor, guard
        self.translator, self.entity_keys = translator, entity_keys
        self.pipes: dict[str, object] = {}
        self.lock = threading.Lock()

    def pipeline(self, spec: dict, fp: str):
        from ..pipeline.orchestrator import Pipeline
        with self.lock:
            if fp not in self.pipes:
                if len(self.pipes) >= 4:
                    self.pipes.pop(next(iter(self.pipes)))
                opts = RunOptions(**{k: v for k, v in spec.items() if k in _OPT_FIELDS})
                opts.run_id = f"flow-{fp}"
                notes = []
                if opts.index_dir and not (resolve_path(opts.index_dir) / "meta.json").exists():
                    # вэб сервер дээр туршилт бүрийн индекс байхгүй (жишээ нь build/index_plus_dev) — вэбийнхээр
                    notes.append(f"Энэ сервер дээр {opts.index_dir} индекс алга — {self.cfg['knowledge']['index_dir']}-ээр "
                                 "ажиллав (жишээний сан туршилтынхаас өөр байж болно)")
                    opts.index_dir = None
                pipe = Pipeline(self.cfg, opts, self.model_name, llm=self.llm, executor=self.executor, guard=self.guard)
                pipe.flow_notes = notes
                self.pipes[fp] = pipe
            return self.pipes[fp]

    def run(self, flow: dict, question: str, base_id: str | None = None, progress=None) -> dict:
        from ..pipeline.orchestrator import set_progress
        set_progress(progress)
        try:
            return self._run(flow, question, base_id)
        finally:
            set_progress(None)

    def _run(self, flow: dict, question: str, base_id: str | None = None) -> dict:
        from ..pipeline.orchestrator import report
        report("input", "Тохиргоог угсарч байна")
        res = compile_flow(flow, base_id)
        spec = res["spec"]
        pipe = self.pipeline(spec, res["fingerprint"])
        res["warnings"] += getattr(pipe, "flow_notes", [])
        if getattr(self, "label", None):
            res["warnings"].append("ДЕМО горим: SQL-ийг AI бичээгүй — prompt дахь хамгийн төстэй жишээний SQL (хариу асуултад "
                                   "тохирохгүй байж болно). Бодит AI — Kaggle-ийн t2s-web.")
        t0 = time.perf_counter()
        tr = item_id = None
        variant = spec.get("variant") or "mn"
        if spec.get("translate"):
            if self.translator is None:
                res["warnings"].append("Орчуулагч загвар тохируулаагүй (T2S_WEB_TRANSLATOR) — орчуулгагүй ажиллав")
            else:
                from ..config import load_models
                from ..pipeline.translate import translate_one
                report("translate", "Qwen монгол асуултыг англи руу орчуулж байна")
                tr = translate_one(question, self.translator, pipe.retriever, keys=self.entity_keys, hints=True,
                                   fmt=load_models().get("translator_prompt", "v1"))
                item_id = f"flow-{uuid.uuid4().hex[:8]}"
                pipe.translations[f"{item_id}:{variant}"] = tr["text"]
                report("translate", tr["text"], "done")
        ans = pipe.ask(question, variant, item_id=item_id)
        report("output", f"{ans.status}" + (f", {ans.result.rowcount} мөр" if ans.status == "answered" and ans.result is not None else ""), "done")
        latency = round((time.perf_counter() - t0) * 1000, 1)
        try:   # загварт очсон prompt ба RAG (ижил замаар дахин угсарна; хэдэн арван мс)
            insp = inspect_prompt(pipe, spec, question, item_id)
        except Exception as e:  # харагдацын алдаа хариултыг унагаахгүй
            insp = {"error": f"{type(e).__name__}: {e}"[:300]}
        return {"steps": trace(ans, spec, tr), "warnings": res["warnings"], "timings": ans.timings,
                "latency_ms": latency, "status": ans.status, "inspect": insp}

    def inspect(self, flow: dict, question: str, base_id: str | None = None, translation: str | None = None) -> dict:
        res = compile_flow(flow, base_id)
        pipe = self.pipeline(res["spec"], res["fingerprint"])
        return _inspect(pipe, res, question, translation, getattr(pipe, "flow_notes", []))


# ---------------- FastAPI router ----------------

def make_router(runner: FlowRunner | None = None, check_key=None):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import HTMLResponse

    r = APIRouter()
    jobs: dict[str, dict] = {}
    inspector = runner if runner is not None else LocalInspector()
    ck = check_key or (lambda k: None)

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e

    @r.get("/builder")
    def builder():
        return HTMLResponse((Path(__file__).with_name("static") / "builder.html").read_text(encoding="utf-8"))

    vendor_types = {"drawflow.min.js": "application/javascript", "drawflow.min.css": "text/css"}

    @r.get("/builder/vendor/{name}")
    def builder_vendor(name: str):
        from fastapi.responses import FileResponse
        if name not in vendor_types:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(Path(__file__).with_name("static") / "vendor" / name, media_type=vendor_types[name])

    @r.get("/flow/catalog")
    def catalog(k: str | None = None):
        ck(k)
        exp = load_experiments()
        runs = exp.get("runs") or {}
        saved = load_flows()
        return {"nodes": NODES, "baseline": baseline(exp), "suggestions": suggestions(exp), "presets": PRESETS,
                "experiments": [{"id": i, "desc": (s or {}).get("desc", ""), "group": (s or {}).get("group", ""),
                                 "flow": i in saved["graphs"]} for i, s in runs.items()],
                "can_run": runner is not None, "can_inspect": True,
                "server": {"model": getattr(runner, "label", None) or getattr(runner, "model_name", None),
                           "demo": bool(getattr(runner, "label", None)),
                           "translator": getattr(runner, "translator", None) is not None} if runner else None}

    @r.get("/flow/from/{run_id}")
    def from_run(run_id: str, k: str | None = None):
        ck(k)
        g = load_flows()["graphs"].get(run_id)
        if g:
            return {"flow": g["flow"], "base": g.get("base"), "saved": True}
        return {"flow": guard(lambda: decompose(resolve_run(run_id))), "base": run_id, "saved": False}

    @r.post("/flow/compile")
    def compile_(body: dict, k: str | None = None):
        ck(k)
        res = guard(compile_flow, body.get("flow") or {}, body.get("base") or None)
        rid = body.get("id") or "W1"
        res["yaml"] = yaml_line(rid, body.get("base") or None, res["diff"], body.get("desc") or "")
        res["command"] = (f"t2s eval --run {rid} --model arctic-r1-7b --split dev --variants "
                          f"{res['spec'].get('variant') or 'mn'} --workers 4")
        res.pop("spec", None)
        return res

    @r.post("/flow/save")
    def save(body: dict, k: str | None = None):
        ck(k)
        res = guard(save_flow, body.get("id") or "", body.get("flow") or {}, body.get("base") or None,
                    body.get("desc") or "")
        res.pop("spec", None)
        return res

    @r.delete("/flow/{run_id}")
    def delete(run_id: str, k: str | None = None):
        ck(k)
        if not delete_flow(run_id):
            raise HTTPException(status_code=404, detail="Хадгалсан flow олдсонгүй (experiments.yaml-ийнхийг устгахгүй)")
        return {"ok": True}

    # Серверийн лог (Debugger → Logs): зөвхөн build/logs доторх зөвшөөрсөн файлуудын сүүлийн мөрүүд
    LOGS = {"web": "web.log", "sql": "hf_d2.log", "translator": "hf_tr.log"}

    @r.get("/flow/logs")
    def logs(k: str | None = None, n: int = 200):
        ck(k)
        out = {}
        for key, name in LOGS.items():
            p = resolve_path("build/logs") / name
            if p.exists():
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                out[key] = "\n".join(lines[-max(10, min(int(n), 1000)):])
        return {"logs": out}

    @r.post("/flow/inspect")
    def inspect(body: dict, k: str | None = None):
        ck(k)
        q = (body.get("question") or "").strip()
        if len(q) < 2:
            raise HTTPException(status_code=400, detail="Асуулт бичнэ үү")
        try:
            return guard(inspector.inspect, body.get("flow") or {}, q, body.get("base") or None,
                         body.get("translation"))
        except HTTPException:
            raise
        except Exception as e:  # индекс, тохиргооны алдаа
            raise HTTPException(status_code=500, detail=f"Prompt угсарч чадсангүй: {type(e).__name__}: {e}"[:600]) from e

    @r.post("/flow/run_job")
    def run_job(body: dict, k: str | None = None):
        ck(k)
        if runner is None:
            raise HTTPException(status_code=503, detail="Энэ сервер загваргүй (зөвхөн угсрах горим). Colab/Kaggle дээрх "
                                                        "вэб үйлчилгээний /builder-ээс ажиллуулна.")
        q = (body.get("question") or "").strip()
        if len(q) < 2:
            raise HTTPException(status_code=400, detail="Асуулт бичнэ үү")
        guard(compile_flow, body.get("flow") or {}, body.get("base") or None)   # алдааг шууд буцаана
        jid = uuid.uuid4().hex[:12]
        jobs[jid] = {"done": False, "t0": time.time(), "progress": []}

        def prog(node, msg, state):   # амьд явц: {node, msg, state, t (эхлэлээс секунд)}
            jobs[jid]["progress"].append({"node": node, "msg": str(msg)[:300], "state": state,
                                          "t": round(time.time() - jobs[jid]["t0"], 1)})

        def work():
            try:
                jobs[jid]["result"] = runner.run(body["flow"], q, body.get("base") or None, progress=prog)
            except Exception as e:  # LLM/DB тасалдал
                jobs[jid]["error"] = f"{type(e).__name__}: {e}"[:800]
            jobs[jid]["done"] = True
        threading.Thread(target=work, daemon=True).start()
        for old in [j for j, v in jobs.items() if time.time() - v["t0"] > 3600]:
            jobs.pop(old, None)
        return {"job": jid}

    @r.get("/flow/job/{jid}")
    def job(jid: str, k: str | None = None):
        ck(k)
        if jid not in jobs:
            raise HTTPException(status_code=404, detail="Ажил олдсонгүй")
        v = jobs[jid]
        return {"done": v["done"], "elapsed_s": round(time.time() - v["t0"]), "result": v.get("result"),
                "error": v.get("error"), "progress": v.get("progress", [])}

    return r


def demo_runner() -> FlowRunner:
    """Локал ДЕМО (`t2s flow-ui --demo`): GPU/AI-гүй. SQL-ийг prompt-д орсон хамгийн төстэй жишээнээс (few-shot) авна —
    UI, RAG, DB, амьд явцыг бодит өгөгдлийн сан дээр турших зориулалттай. Хариу нь асуултад тохирохгүй байж болно."""
    from ..config import load_config
    from ..db.conn import bot_dsn
    from ..db.executor import Executor
    from ..knowledge.semantic import cached_model
    from ..llm.client import MockLLM
    from ..security.sql_guard import SqlGuard
    cfg = load_config()

    def responder(msgs: list[dict]) -> str:
        time.sleep(1.5)   # явц харагдах хугацаа
        text = "\n".join(m["content"] for m in msgs if m["role"] != "system")
        blocks = [b.strip() for b in re.findall(r"```sql\s*(.*?)```", text, re.S)
                  if re.match(r"(?is)\s*(select|with)\b", b)]
        sql = blocks[0] if blocks else "SELECT 1 AS demo"
        return (f"\nДемо: AI-гүй, хамгийн төстэй жишээний SQL.\n</think>\n<answer>\n```sql\n{sql}\n```\n</answer>")

    model = cached_model(cfg["knowledge"]["semantic_model"])
    guard = SqlGuard(model.allowed(), model.schema, set(cfg["security"]["forbid_functions"]),
                     str(cfg["project"]["as_of_date"]), enforce_limit=int(cfg["security"]["enforce_limit"]))
    executor = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                        max_rows=int(cfg["security"]["enforce_limit"]), max_cost=float(cfg["db"]["max_explain_cost"]))
    runner = FlowRunner(cfg, "arctic-r1-7b", MockLLM(responder, name="demo"), executor, guard)
    runner.label = "ДЕМО (AI-гүй)"
    return runner


def create_builder_app(demo: bool = False):
    """Угсрах горим (локал): `t2s flow-ui` — загвар, DB-гүй; `--demo` — локал DB + AI-гүй демо ажиллуулалт."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse
    app = FastAPI(title="mn-text2sql flow builder")
    app.include_router(make_router(demo_runner() if demo else None))

    @app.get("/")
    def root():
        return RedirectResponse("/builder")
    return app
