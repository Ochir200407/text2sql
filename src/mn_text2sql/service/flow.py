"""Flowchart builder (вэб): pipeline-ийн алхмуудыг node болгож угсраад туршилтын тохиргоо (RunOptions) болгоно.

* Node бүр RunOptions-ийн хэдэн талбарыг эзэмшинэ (NODES). Flow-д байгаа node → түүний параметр; байхгүй node →
  талбарууд нь суурь утгаараа (experiments.yaml-ийн defaults). Ингэснээр flow ↔ тохиргоо хоёр тийш нэг утгатай:
  `decompose(resolve_run(X))` → `compile_flow` нь X-ийн тохиргоог яг сэргээнэ (tests/test_flow.py).
* Кодын гүйцэтгэлийн дараалал тогтмол (orchestrator.ask) — flow-ийн дараалал өөр бол анхааруулга өгнө, тооцоонд
  зөвхөн input → output замд орсон node-ууд орно.
* Хадгалах: config/flows.yaml (`runs` — experiments.yaml-тай ижил хэлбэр, `graphs` — зураглал). options.load_experiments
  үүнийг нэгтгэдэг тул `t2s eval --run <flow ID>` шууд ажиллана.
* Ажиллуулах (POST /flow/run_job): вэб үйлчилгээний загвар, DB-тэй үед нэг асуултыг алхам бүрийн мөрөөр харуулна.
* Хэрэглэгчийн flow-ийн хязгаар (3-р аудит C1, H1): замын тохиргоо (glossary_file, glossary_select_cache,
  index_dir) — зөвхөн knowledge/ эсвэл build/ доторх харьцангуй зам, сервер ачаалахаасаа өмнө бодит энгийн файл эсэх,
  хэмжээг шалгана (_check_spec, _vet_paths); нэг ажлын хэмжээг (candidates, rounds, k …) LIMITS-ээр хумина; graph ≤
  MAX_NODES/MAX_EDGES. experiments.yaml-ийн бүх утга хязгаарын дотор — round-trip (tests/test_flow.py) хэвээр.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import re
import stat
import threading
import time
import uuid
from dataclasses import fields
from pathlib import Path

import yaml

from ..config import resolve_path
from ..pipeline.options import RunOptions, load_experiments, resolve_run

log = logging.getLogger(__name__)

FLOWS_FILE = "config/flows.yaml"
_ID = re.compile(r"^[A-Za-z][\w.-]{0,31}$")

# rank = orchestrator.ask-ийн гүйцэтгэлийн дараалал; flag = node байвал True болох талбар; on = node нэмэхэд
# анхдагч параметр (суурь утгаас ялгаатай байх ёстой бол)
NODES: list[dict] = [
    {"type": "input", "title": "Асуулт", "rank": 0, "fixed": True, "color": "slate",
     "help": "Хэрэглэгчийн асуулт. Хувилбар: кирилл (mn), галиг (latin), англи (en).",
     "fields": {"variant": {"kind": "enum", "options": ["mn", "latin", "en"]},
                "dialect": {"kind": "enum", "options": ["sqlite_native", "postgres_prompt", "sqlite_transpile"]},
                "dataset": {"kind": "enum", "options": ["nbfi", "bird_mn"]},
                "transpile_fix": {"kind": "bool"}}},
    {"type": "intent", "title": "Intent шалгалт", "rank": 1, "color": "amber",
     "help": "Хариулах боломжгүй / хоёрдмол асуултыг татгалзах эсвэл тодруулах (glossary-ийн дүрэм); intent = F7 JSON intent, semantic_router = F8.",
     "on": {"intent_check": True},
     "fields": {"intent_check": {"kind": "bool"}, "intent_refuse_only": {"kind": "bool"}, "intent": {"kind": "bool"}, "semantic_router": {"kind": "bool"},
                "intent_rules": {"kind": "enum", "options": ["legacy", "v2"]}}},
    {"type": "rules", "title": "Дүрмийн шүүлт (E4)", "rank": 2, "color": "amber",
     "help": "AUTHORING ангиллын дүрмээр SQL бичихээс өмнө татгалзах/тодруулах; эцэст нь итгэлийн босго.",
     "on": {"rule_refuse": True, "rule_clarify": True},
     "fields": {"rule_refuse": {"kind": "bool"}, "rule_clarify": {"kind": "bool"},
                # v2 (2026-11-02): AUTHORING-ийн 5 ангилал — үйл үгийн хэлбэр + англи орчуулга (selection.rule_decision_v2)
                "rules_version": {"kind": "enum", "options": ["v1", "v2"]},
                "abstain_on_error": {"kind": "bool"}, "abstain_threshold": {"kind": "float?"},
                "low_conf_action": {"kind": "enum", "options": ["abstain", "clarify"]}}},
    {"type": "translate", "title": "Орчуулга (mn→en)", "rank": 3, "flag": "translate", "color": "teal",
     "help": "Qwen3.5-9B орчуулагч. en_prompt: загварт англи, retrieval монголоор. Кэш: .t2 = нэр томьёоны RAG + маскын засвар.",
     "on": {"translate_mode": "en_prompt", "translation_cache": "qwen3.5-9b.t2"},
     "fields": {"translate_mode": {"kind": "enum", "options": ["en_prompt", "bilingual", "replace"]},
                "translation_cache": {"kind": "str?"},
                "translate_check": {"kind": "enum", "options": ["off", "bilingual"]}}},
    {"type": "retrieve", "title": "Retrieval (RAG)", "rank": 4, "color": "blue",
     "help": "Жишээний сан (few-shot), DB-ийн утга, нэр томьёо, схемийн тайлбар.",
     "on": {"fewshot_k": 3, "values": True, "glossary": True, "describe": True},
     "fields": {"fewshot_k": {"kind": "int"}, "values": {"kind": "bool"},
                "value_mode": {"kind": "enum", "options": ["basic", "rule", "learned"]}, "glossary": {"kind": "bool"},
                "glossary_en": {"kind": "bool"}, "glossary_file": {"kind": "str?"}, "glossary_k": {"kind": "int"},
                "knowledge_select": {"kind": "bool"}, "select_pool": {"kind": "int"}, "glossary_select_cache": {"kind": "str?"}, "select_parse": {"kind": "enum", "options": ["legacy", "strict"]}, "describe": {"kind": "bool"},
                "knowledge_notes": {"kind": "bool"}, "pivot": {"kind": "bool"}, "index_dir": {"kind": "str?"},
                "fk_bridge": {"kind": "bool"}, "table_hints": {"kind": "bool"}, "implied_preds": {"kind": "bool"},
                "fewshot_rerank": {"kind": "str?"},
                # M1vf (F1/F3/F5/L6): анхдагч = хуучин зан төлөв
                "value_match": {"kind": "enum", "options": ["legacy", "strict"]},
                "mask_mode": {"kind": "enum", "options": ["legacy", "values"]},
                "enum_codes": {"kind": "enum", "options": ["first9", "all"]},
                "translit_vocab": {"kind": "enum", "options": ["pool", "no_eval"]},
                # 2026-11-02: ≤N утгатай текст баганын бүх утга DDL-д + «утгыг орчуулахгүй» заавар (0 = унтраалттай)
                "value_list_max": {"kind": "int"}}},
    {"type": "schema_link", "title": "Schema linking", "rank": 5, "flag": "schema_linking", "color": "blue",
     "help": "Prompt-д зөвхөн хамааралтай хүснэгтүүд (T4-д богино prompt).",
     "fields": {"top_tables": {"kind": "int"}}},
    {"type": "planner", "title": "Planner", "rank": 6, "flag": "agent_plan", "color": "violet", "needs": "observe",
     "help": "SQL-ээс өмнө англи төлөвлөгөө (хүснэгт, join, шүүлт, агрегат). Agent давталттай үед л ажиллана.",
     "fields": {}},
    {"type": "writer", "title": "SQL бичигч (Arctic)", "rank": 7, "fixed": True, "color": "green",
     "help": "Arctic-Text2SQL-R1-7B (+LoRA adapter). sqrl протоколд DB судлах алхам.",
     "fields": {"adapter": {"kind": "str?"}, "agent_max_steps": {"kind": "int"},
                "search_values_tool": {"kind": "bool"},
                "gen_style": {"kind": "enum", "options": ["default", "cte", "decompose"]},
                "rescue_truncated": {"kind": "bool"}, "extract_fallback": {"kind": "bool"},
                "guard_scope": {"kind": "bool"}}},
    {"type": "repair", "title": "Алдаагаар засах (E1)", "rank": 8, "color": "green",
     "help": "Гүйцэтгэлийн алдааны мессежийг буцааж өгч засуулах.",
     "on": {"repair_rounds": 2, "repair_on_empty": True},
     "fields": {"repair_rounds": {"kind": "int"}, "repair_on_empty": {"kind": "bool"},
                "repair_workers": {"kind": "int"}, "repair_strict": {"kind": "bool"}}},
    {"type": "vote", "title": "Санал хураалт (E2)", "rank": 9, "color": "green",
     "help": "N хувилбар үүсгэж гүйцэтгэлийн үр дүнгээр олонхоор сонгох; vote_filter = хоосон/буруу утгатайг хасах.",
     "on": {"candidates": 8, "sampling": True, "seeds": [1, 2, 3]},
     "fields": {"candidates": {"kind": "int"}, "sampling": {"kind": "bool"}, "seeds": {"kind": "ints"},
                "vote_filter": {"kind": "bool"}, "pick_pairs": {"kind": "bool"}, "pick_diff": {"kind": "bool"},
                "vote_lint": {"kind": "bool"},   # 2026-11-02: fan/chasm ба KPI-ийн lint-д унасан хувилбарыг саналаас хасах
                "adaptive_stop": {"kind": "str?"},
                "abstain_v2": {"kind": "bool"}}},
    {"type": "observe", "title": "Agent: tool шалгалт", "rank": 10, "flag": "agent", "color": "violet",
     "help": "LLM-гүй: гүйцэтгэлийн алдаа, хоосон үр дүн, WHERE-ийн утга DB-д байгаа эсэх → олдвор байвал засуулна (≤ rounds).",
     "fields": {"agent_literal_check": {"kind": "bool"}, "agent_rounds": {"kind": "int"}}},
    {"type": "critic", "title": "Agent: critic", "rank": 11, "flag": "agent_critic", "color": "violet",
     "needs": "observe", "help": "Загвар өөрийн SQL-ийг үнэлнэ: VERDICT OK / REVISE. agent_llm = өөр загвар.",
     "fields": {"agent_llm": {"kind": "str?"}}},
    {"type": "postproc", "title": "SQL засвар (postproc)", "rank": 12, "flag": "postproc", "color": "slate",
     "help": "Схем/хадгалагдсан утгаас гаргасан детерминист засвар; literal_fix = DB-д байхгүй утгыг утгын хайлтын "
             "ижил баганын хадгалагдсан утгаар (R6); trunc_date = гаралтын DATE_TRUNC-ийг огноо болгоно (R7).",
     "fields": {"literal_fix": {"kind": "bool"}, "trunc_date": {"kind": "bool"},
                # 2026-11-02: NPL-ийг үлдэгдлээр, loan_payments-д POSTED, ID-г зөв баганад, «багц»-д is_active_portfolio
                "kpi_fix": {"kind": "bool"}}},
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

# ---------------- хэрэглэгчийн flow-ийн хязгаар (3-р аудит C1, H1) ----------------
# H1: нэг ажлын хэмжээ (LLM дуудлага, prompt-ийн урт) — [доод, дээд]. experiments.yaml-ийн бүх тохиргооны утга
# (candidates ≤ 8, rounds ≤ 2, agent_max_steps ≤ 5, fewshot_k ≤ 4, glossary_k ≤ 8, select_pool 12, top_tables 6,
# repair_workers 1) дотор тул round-trip өөрчлөгдөхгүй; гадуурх утгыг хумиж анхааруулна.
LIMITS = {"candidates": (1, 16), "repair_rounds": (0, 5), "agent_rounds": (0, 5), "agent_max_steps": (0, 8),
          "repair_workers": (1, 8), "fewshot_k": (0, 10), "select_pool": (1, 50), "top_tables": (1, 50),
          "glossary_k": (0, 20)}
MAX_SEEDS = 16
MAX_NODES, MAX_EDGES = 50, 100
MAX_QUESTION = 1000   # /flow/inspect, /flow/run_job-ийн асуулт (api.py AskIn-тэй ижил)
# C1: файл/хавтасны замтай тохиргоо — серверийн мэдэлд. Хэрэглэгчийн утга нь зөвхөн PATH_ROOTS доторх харьцангуй зам
# (абсолют, `..`, `\`, `~`, `:` хориотой; өргөтгөл тогтмол); сервер ачаалахаас өмнө бодит (symlink задалсан) зам нь
# PATH_ROOTS дотор, энгийн файл (FIFO/төхөөрөмж биш), хэмжээ ≤ дээд (_server_path). {талбар: (өргөтгөл, дээд байт)}
PATH_FIELDS = {"glossary_file": ((".yaml", ".yml"), 5_000_000), "glossary_select_cache": ((".json",), 20_000_000),
               "index_dir": (None, None)}
PATH_ROOTS = ("knowledge", "build")
INDEX_FILES = ("meta.json", "column_examples.json", "schema_docs.json", "values.json", "examples.json")
# Нэр (замын тусгаарлагчгүй): models.yaml-ийн загвар/adapter, build/translations/<нэр>.jsonl
NAME_FIELDS = ("translation_cache", "adapter", "agent_llm")
_SEG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_STR_MAX = 200


def _path_problem(name: str, v) -> str | None:
    """Замын талбарын утгын бичлэгийн алдаа (None = зөв): PATH_ROOTS-оор эхэлсэн, `/`-оор тусгаарласан харьцангуй зам."""
    if not isinstance(v, str) or len(v) > _STR_MAX:
        return "текст зам биш эсвэл хэт урт"
    parts = v.split("/")
    if len(parts) < 2 or parts[0] not in PATH_ROOTS or not all(_SEG.match(p) for p in parts[1:]):
        return f"зөвхөн {' эсвэл '.join(r + '/' for r in PATH_ROOTS)} доторх харьцангуй зам (абсолют, `..` хориотой)"
    exts = PATH_FIELDS.get(name, (None, None))[0]
    if exts and not v.lower().endswith(exts):
        return "өргөтгөл нь " + " эсвэл ".join(exts)
    return None


def _check_str(name: str, v) -> None:
    """C1: str? талбаруудын утга (compile_flow-д; файлыг уншихгүй). Алдаа бол ValueError (→ 400)."""
    if v is None:
        return
    if name in PATH_FIELDS:
        why = _path_problem(name, v)
    elif name in NAME_FIELDS:
        why = None if isinstance(v, str) and _NAME.match(v) else "нэр (үсэг, тоо, _ . + -; ≤ 64)"
    elif name == "fewshot_rerank":
        from ..retrieval.fewshot_rerank import MODES
        why = None if v in MODES else "боломжит: " + ", ".join(MODES)
    elif name == "adaptive_stop":
        why = None
        try:
            from ..eval.adaptive import parse_spec
            if not isinstance(v, str) or len(v) > 40:
                raise ValueError(v)
            parse_spec(v)
        except (ValueError, TypeError):
            why = "жишээ: beta:0.8:w4, agree:2"
    else:   # шинэ str? талбар: зам/URL шиг утгыг (абсолют, `..`, `\`, `~`, `C:`, `://`) хориглоно
        s = str(v)
        why = None if (len(s) <= _STR_MAX and not s.startswith(("/", "~")) and ".." not in s and "://" not in s
                       and not re.match(r"^[A-Za-z]:", s) and not any(ch in s for ch in "\\\0")) \
            else "зөвшөөрөгдөөгүй утга"
    if why:
        raise ValueError(f"{name}: буруу утга {str(v)[:80]!r} — {why}")


def _check_spec(spec: dict, warn: list[str]) -> None:
    """H1/C1: эцсийн тохиргоо (хэрэглэгчийн параметр + суурь туршилт) — тоон хязгаарыг хумиж анхааруулна, str? утгыг
    шалгана. experiments.yaml-ийн утгууд бүгд хязгаарын дотор (өөрчлөгдөхгүй)."""
    for k, (lo, hi) in LIMITS.items():
        v = spec.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and not lo <= v <= hi:
            spec[k] = min(max(v, lo), hi)
            warn.append(f"{k} = {v} нь зөвшөөрөх мужаас гадуур ({lo}–{hi}) — {spec[k]} болгов")
    seeds = spec.get("seeds")
    if isinstance(seeds, list) and (len(seeds) > MAX_SEEDS or any(not 0 <= s < 2**31 for s in seeds)):
        spec["seeds"] = [min(max(int(s), 0), 2**31 - 1) for s in seeds[:MAX_SEEDS]] or [0]
        warn.append(f"seeds: ≤ {MAX_SEEDS} ширхэг, 0 ≤ seed < 2^31 — хумив")
    thr = spec.get("abstain_threshold")
    if thr is not None and not 0.0 <= thr <= 1.0:
        spec["abstain_threshold"] = min(max(float(thr), 0.0), 1.0)
        warn.append(f"abstain_threshold = {thr} — 0–1 мужид хумив")
    for n in NODES:
        for k, fmeta in n["fields"].items():
            if fmeta["kind"] == "str?":
                _check_str(k, spec.get(k))
    for k in PATH_FIELDS:   # node-гүй (ирээдүйд) байсан ч
        _check_str(k, spec.get(k))


def _check_shape(flow) -> None:
    """H1: graph-ийн хэлбэр ба хэмжээ (node ≤ MAX_NODES, сум ≤ MAX_EDGES, id/type/params-ийн төрөл)."""
    if not isinstance(flow, dict):
        raise ValueError("Flow буруу хэлбэртэй (JSON объект биш)")
    nodes, edges = flow.get("nodes") or [], flow.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Flow-ийн nodes/edges жагсаалт байх ёстой")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ValueError(f"Flow хэт том: node ≤ {MAX_NODES}, сум ≤ {MAX_EDGES}")
    for n in nodes:
        if not isinstance(n, dict) or not isinstance(n.get("id"), (str, int)) or len(str(n.get("id"))) > 64:
            raise ValueError("Node-ийн id буруу")
        if n.get("type") not in BY_TYPE:
            raise ValueError(f"Үл мэдэгдэх node: {str(n.get('type'))[:40]!r}")
        if not isinstance(n.get("params") or {}, dict):
            raise ValueError(f"«{BY_TYPE[n['type']]['title']}»: params объект байх ёстой")
    for e in edges:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), (str, int)) for k in ("from", "to")):
            raise ValueError("Сумын from/to буруу")


def _server_path(name: str, value: str, root=None):
    """C1: сервер ачаалахаас өмнөх шалгалт (түгжээгүй): бичлэг (_path_problem) + symlink задалсан бодит зам PATH_ROOTS
    дотор, байгаа; файл бол энгийн файл (FIFO, /dev/* биш) ба ≤ дээд хэмжээ; index_dir бол хавтас бөгөөд Retriever-ийн
    ачаалах INDEX_FILES бүгд PATH_ROOTS доторх энгийн файл (meta.json ≤ 1 MB). Зөв бол бодит Path, үгүй бол None
    (дуудагч анхдагч руу буцна)."""
    if _path_problem(name, value):
        return None
    try:
        base = (root or resolve_path(".")).resolve()
        roots = [(base / r).resolve() for r in PATH_ROOTS]
        real = (base / value).resolve(strict=True)
        if not any(real.is_relative_to(r) for r in roots):
            return None
        if PATH_FIELDS.get(name, (None, None))[0] is None:   # хавтас (index_dir)
            if not real.is_dir():
                return None
            for f in INDEX_FILES:
                rp = (real / f).resolve(strict=True)
                st = os.stat(rp)
                if not stat.S_ISREG(st.st_mode) or not any(rp.is_relative_to(r) for r in roots) \
                        or (f == "meta.json" and st.st_size > 1_000_000):
                    return None
            return real
        st = os.stat(real)
        cap = PATH_FIELDS.get(name, (None, None))[1]
        if not stat.S_ISREG(st.st_mode) or (cap and st.st_size > cap):
            return None
        return real
    except (OSError, RuntimeError, ValueError):
        return None


def _vet_paths(opts: RunOptions, notes: list[str], index_note: str) -> None:
    """C1: Pipeline үүсгэхээс өмнө замын тохиргоог серверийн талд шалгаж, тэнцээгүйг анхдагч (None) болгоно (тэмдэглэлтэй).
    index_note: index_dir алга үеийн тэмдэглэлийн загвар ({idx})."""
    if opts.index_dir and _server_path("index_dir", opts.index_dir) is None:
        notes.append(index_note.replace("{idx}", str(opts.index_dir)))
        opts.index_dir = None
    for k, what in (("glossary_file", "нэр томьёоны файл"), ("glossary_select_cache", "reranker-ийн сонголтын кэш")):
        v = getattr(opts, k)
        if v and _server_path(k, v) is None:
            notes.append(f"{what} ({k} = {v}) энэ сервер дээр алга эсвэл зөвшөөрөгдөөгүй — анхдагчаар ажиллав")
            setattr(opts, k, None)


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
    _check_shape(flow)   # H1: хэлбэр, node/сумын тоо
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
                spec[k] = _coerce(params[k], fmeta["kind"], k, fmeta.get("options"))
        if meta.get("flag"):
            spec[meta["flag"]] = True
    if "observe" in by_type and "critic" not in by_type:
        spec["agent_critic"] = False
    _check_spec(spec, warn)   # C1/H1: замын/нэрийн утга, ажлын хэмжээний хязгаар
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


def _coerce(v, kind: str, name: str, options: list | None = None):
    """Node-ийн параметр → RunOptions-ийн төрөл. Буруу бол ValueError (→ 400). Хязгаар/замыг _check_spec шалгана."""
    try:
        if kind == "bool":
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        if kind == "int":
            if isinstance(v, (dict, list)):
                raise TypeError(name)
            return int(v)
        if kind == "float?":
            if v in (None, "", "null"):
                return None
            f = float(v)
            if not math.isfinite(f):
                raise ValueError(name)
            return f
        if kind == "str?":
            if v in (None, "", "null"):
                return None
            if not isinstance(v, (str, int, float)) or isinstance(v, bool) or len(str(v)) > _STR_MAX:
                raise TypeError(name)
            return str(v)
        if kind == "ints":
            if isinstance(v, str):
                v = [x for x in re.split(r"[,\s]+", v) if x]
            if not isinstance(v, list):
                raise TypeError(name)
            return [int(x) for x in v[:MAX_SEEDS * 4]] or [0]
        if kind == "enum" and options is not None and v not in options:   # хэрэглэгчийн утга зөвхөн жагсаалтаас
            raise ValueError(name)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(f"{name}: буруу утга {str(v)[:80]!r} ({kind})") from e
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
    # S3: аварга нүд (json_agg г.м.) — хариултын хэмжээг хязгаарлана
    from .analyst import MAX_SHOWN_BYTES, cap_rows
    rows = cap_rows(d["rows"], max_bytes=MAX_SHOWN_BYTES)[0] if d.get("rows") is not None else d.get("rows")
    steps.append({"node": "output", "status": ans.status, "sql": ans.sql, "columns": d.get("columns"),
                  "rows": rows, "row_count": d.get("row_count"), "explanation": ans.explanation_mn})
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


class _PipeCache:
    """Тохиргоо (fingerprint) бүрт Pipeline-ийг нэг удаа, ≤ MAX_PIPES. C1: угсралтыг (индекс, embedding ачаалах — удаан)
    кэшийн түгжээн (`lock`) ДОР хийхгүй — кэштэй тохиргооны хүсэлт угсралтыг хүлээхгүй. Угсралтууд өөр хоорондоо
    `_build_lock`-оор дараалсан хэвээр (retriever/embedding-ийн lru_cache түгжээгүй — зэрэг ачаалбал санах ой 2 дахин),
    ижил тохиргоог хүлээсэн хүсэлт угсралтын дараа кэшээс авна. Замын тохиргоог угсрахаас өмнө _vet_paths шалгана."""

    MAX_PIPES = 4

    def _cache_init(self) -> None:
        self.pipes: dict[str, object] = {}
        self.lock = threading.Lock()
        self._build_lock = threading.Lock()

    def _cached(self, fp: str, build):
        with self.lock:
            if fp in self.pipes:
                return self.pipes[fp]
        with self._build_lock:
            with self.lock:
                if fp in self.pipes:
                    return self.pipes[fp]
            item = build()                     # кэшийн түгжээгүй
            with self.lock:
                while len(self.pipes) >= self.MAX_PIPES:
                    self.pipes.pop(next(iter(self.pipes)))
                self.pipes[fp] = item
            return item


class LocalInspector(_PipeCache):
    """Загвар, DB-гүй угсрах горимд (t2s flow-ui) prompt ба RAG-ийг харуулна: локал индекс (build/index)."""

    def __init__(self):
        self._cache_init()

    def pipeline(self, spec: dict, fp: str):
        return self._cached(fp, lambda: self._build(spec, fp))

    def _build(self, spec: dict, fp: str):
        from ..config import load_config
        from ..llm.client import MockLLM
        from ..pipeline.orchestrator import Pipeline
        cfg, notes = load_config(), []
        opts = RunOptions(**{k: v for k, v in spec.items() if k in _OPT_FIELDS})
        opts.run_id = f"inspect-{fp}"
        _vet_paths(opts, notes, "Локалд {idx} индекс алга (эсвэл зөвшөөрөгдөөгүй) — " + str(cfg["knowledge"]["index_dir"])
                   + "-ээр харуулав (сервер дээрх жишээний сан өөр байж болно)")
        mock = MockLLM(lambda msgs: "")
        try:
            pipe = Pipeline(cfg, opts, "arctic-r1-7b", llm=mock, executor=_NoDB())
        except (FileNotFoundError, RuntimeError) as e:
            log.warning("flow inspect: RAG-ийн индекс ачаалагдсангүй: %s", e)
            notes.append(f"RAG-ийн индекс ачаалагдсангүй ({type(e).__name__}) — RAG-гүй prompt")
            opts.fewshot_k = 0
            opts.values = opts.glossary = opts.schema_linking = opts.describe = False
            pipe = Pipeline(cfg, opts, "arctic-r1-7b", llm=mock, executor=_NoDB())
        if pipe.retriever is not None and not pipe.retriever.index_meta.get("embedding_model"):
            notes.append("Локал индекс embedding-гүй (BM25) — сервер дээрх (bge-m3) хайлтаас үр дүн өөр байж болно")
        return pipe, notes

    def inspect(self, flow: dict, question: str, base_id: str | None = None, translation: str | None = None) -> dict:
        res = compile_flow(flow, base_id)
        pipe, notes = self.pipeline(res["spec"], res["fingerprint"])
        return _inspect(pipe, res, question, translation, notes)


class FlowRunner(_PipeCache):
    """Вэб үйлчилгээний LLM/DB-г дахин ашиглана; тохиргоо бүрт Pipeline-ийг нэг удаа үүсгэнэ (≤ 4).
    M1: flow-ийн adapter, dataset, translation_cache-ийг сервер ашигладаггүй (вэбийн LLM, DB, шууд орчуулагч) —
    ялгаатай бол хариу/inspect-д анхааруулга, audit-д үйлчилсэн загвар (served_notes, served_model)."""

    def __init__(self, cfg, model_name, llm, executor, guard, translator=None, entity_keys=None, *,
                 dataset: str = "nbfi"):
        self.cfg, self.model_name, self.llm = cfg, model_name, llm
        self.executor, self.guard = executor, guard
        self.translator, self.entity_keys = translator, entity_keys
        self.dataset = dataset or "nbfi"
        self._cache_init()

    def pipeline(self, spec: dict, fp: str):
        return self._cached(fp, lambda: self._build(spec, fp))

    def _build(self, spec: dict, fp: str):
        from ..pipeline.orchestrator import Pipeline
        opts = RunOptions(**{k: v for k, v in spec.items() if k in _OPT_FIELDS})
        opts.run_id = f"flow-{fp}"
        notes: list[str] = []
        # вэб сервер дээр туршилт бүрийн индекс байхгүй (жишээ нь build/index_plus_dev) — вэбийнхээр
        _vet_paths(opts, notes, "Энэ сервер дээр {idx} индекс алга (эсвэл зөвшөөрөгдөөгүй) — "
                   + str(self.cfg["knowledge"]["index_dir"]) + "-ээр ажиллав (жишээний сан туршилтынхаас өөр байж болно)")
        pipe = Pipeline(self.cfg, opts, self.model_name, llm=self.llm, executor=self.executor, guard=self.guard)
        pipe.flow_notes = notes
        return pipe

    def served_model(self) -> str | None:
        """Энэ серверийн бодитоор дуудаж буй SQL загварын нэр (vLLM-ийн served name / adapter)."""
        return getattr(self.llm, "name", None) or self.model_name

    def served_notes(self, spec: dict) -> list[str]:
        """M1: flow-ийн заасан ба серверийн бодитоор ашиглах загвар/өгөгдөл/орчуулга ялгаатай бол анхааруулга."""
        if getattr(self, "label", None):   # ДЕМО — тусдаа анхааруулгатай
            return []
        notes: list[str] = []
        served = getattr(self.llm, "name", None)
        try:
            from ..config import load_models
            mspec = (load_models().get("models") or {}).get(self.model_name) or {}
        except Exception:  # noqa: BLE001 — models.yaml-гүй орчинд нэрээр нь харьцуулна
            mspec = {}
        want = spec.get("adapter") or mspec.get("served_name") or self.model_name
        if served and want != served:
            notes.append(f"Flow-ийн загвар/adapter «{want}» энэ сервер дээр ачаалагдаагүй — серверийн «{served}» загвараар "
                         "ажиллав (туршилтын үр дүнгээс өөр байж болно)")
        if (spec.get("dataset") or "nbfi") != self.dataset:
            notes.append(f"dataset «{spec.get('dataset')}» энд хэрэглэгдэхгүй — серверийн өгөгдлийн сан ({self.dataset}) "
                         "дээр ажиллав")
        if spec.get("translate") and spec.get("translation_cache"):
            notes.append(f"translation_cache «{spec.get('translation_cache')}» (үнэлгээний кэш) энд хэрэглэгдэхгүй — сервер "
                         "асуултыг шууд орчуулна (T2S_WEB_TRANSLATOR)")
        return notes

    def run(self, flow: dict, question: str, base_id: str | None = None, progress=None, shadow: bool = False) -> dict:
        from ..pipeline.orchestrator import set_progress
        set_progress(progress)
        try:
            return self._run(flow, question, base_id, shadow=shadow)
        finally:
            set_progress(None)

    def _run(self, flow: dict, question: str, base_id: str | None = None, shadow: bool = False) -> dict:
        from ..pipeline.orchestrator import report
        report("input", "Тохиргоог угсарч байна")
        res = compile_flow(flow, base_id)
        spec = res["spec"]
        pipe = self.pipeline(spec, res["fingerprint"])
        res["warnings"] += getattr(pipe, "flow_notes", []) + self.served_notes(spec)
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
        chosen = (ans.candidates[ans.chosen_index] if ans.chosen_index is not None
                  and ans.chosen_index < len(ans.candidates) else None)
        # "_audit": router audit log-д бичээд хэрэглэгчийн хариунаас хасна (R4 S1)
        audit_rec = {"status": ans.status, "sql": ans.sql, "tables": getattr(chosen, "tables", None) or [],
                     "row_count": ans.result.rowcount if ans.result is not None and ans.status == "answered" else None,
                     "confidence": ans.confidence, "latency_ms": latency, "flow": res["fingerprint"],
                     # M1: бодитоор үйлчилсэн загвар (flow-ийн adapter-аас ялгаатай байж болно)
                     "model": self.served_model(), "flow_adapter": spec.get("adapter")}
        if shadow:   # shadow: prompt/RAG/үр дүн/экспортын кэшгүй — зөвхөн audit (api.py /ask-тай адил)
            return {"status": "shadow_logged", "_audit": audit_rec}
        try:   # загварт очсон prompt ба RAG (ижил замаар дахин угсарна; хэдэн арван мс)
            insp = inspect_prompt(pipe, spec, question, item_id)
        except Exception as e:  # харагдацын алдаа хариултыг унагаахгүй
            log.exception("flow: prompt-ын харагдацыг угсарч чадсангүй")   # L5: дэлгэрэнгүй нь серверийн лог-д
            insp = {"error": f"Prompt-ын харагдацыг угсарч чадсангүй ({type(e).__name__}) — дэлгэрэнгүйг серверийн лог-д"}
        from .analyst import build_extras   # шинжээчийн нэмэлт (SQL тайлбар, итгэл, график, экспорт) — LLM-гүй
        return {"steps": trace(ans, spec, tr), "warnings": res["warnings"], "timings": ans.timings,
                "latency_ms": latency, "status": ans.status, "inspect": insp, "_audit": audit_rec,
                "analyst": build_extras(ans, cfg=self.cfg, model=getattr(pipe, "model", None), question=question)}

    def inspect(self, flow: dict, question: str, base_id: str | None = None, translation: str | None = None) -> dict:
        res = compile_flow(flow, base_id)
        pipe = self.pipeline(res["spec"], res["fingerprint"])
        return _inspect(pipe, res, question, translation,
                        list(getattr(pipe, "flow_notes", [])) + self.served_notes(res["spec"]))


# ---------------- FastAPI router ----------------

def make_router(runner: FlowRunner | None = None, check_key=None, audit=None, *, mode: str | None = None,
                require_admin: bool = False, max_jobs: int = 4, slots=None):
    """mode: service.mode (api.py-ээс) — "shadow" бол /flow/run_job үр дүн буцаахгүй (зөвхөн audit), /flow/inspect
    админ-only. require_admin: /flow/save, DELETE /flow/{id}, /flow/logs-д T2S_ADMIN_KEY шаардах (dev биш горимд;
    локал `t2s flow-ui` нь False). slots: auth.WorkSlots — /ask, /analyst/rerun-тэй хуваалцсан зэрэг ажиллах хязгаар
    (S1, 429); байхгүй бол max_jobs-оор шинээр үүсгэнэ (локал builder, тест). /flow/inspect нь slots-д (Pipeline
    угсрах, RAG — хүнд), /flow/compile нь хурдны хязгаартай (H1). Алдааны дэлгэрэнгүйг клиентэд биш — request id-тай
    ерөнхий мессеж, дэлгэрэнгүй нь серверийн лог/audit-д (L5)."""
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import HTMLResponse

    from . import auth
    r = APIRouter(dependencies=[auth.capture_dependency()])   # X-T2S-Key / X-T2S-Admin толгой
    shadow = auth.is_shadow(mode)   # S5: api.py/analyst.py-тэй ижил нормчлол
    slots = slots if slots is not None else auth.WorkSlots(max_jobs)
    compile_rate = auth.RateLimit(rate_per_s=10, burst=30)   # H1: /flow/compile (UI өөрчлөлт бүрт дууддаг)

    def need_admin(admin: str | None) -> None:
        if require_admin:
            auth.require_admin(admin)
    # CSV/XLSX экспорт, 👍/👎, analyst.js; runner-тай бол SQL засвар/баталгаажсан SQL-ийг guard → read-only DB-ээр
    from .analyst import make_analyst_router
    r.include_router(make_analyst_router(check_key, runner=runner, audit=audit, slots=slots))
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()
    inspector = runner if runner is not None else LocalInspector()
    ck = check_key or (lambda k: None)

    def guard(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=str(e).strip("'\"")[:600]) from e

    def question_of(body: dict) -> str:
        q = body.get("question")
        q = q.strip() if isinstance(q, str) else ""
        if len(q) < 2:
            raise HTTPException(status_code=400, detail="Асуулт бичнэ үү")
        if len(q) > MAX_QUESTION:   # H1: /ask-ийн AskIn-тэй ижил
            raise HTTPException(status_code=400, detail=f"Асуулт хэт урт (≤ {MAX_QUESTION} тэмдэгт)")
        return q

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
                           # M1: бодитоор дуудагдах загвар/adapter (flow-ийн adapter-ийг сервер ашигладаггүй)
                           "served_model": getattr(runner, "served_model", lambda: None)(),
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
        compile_rate.check_or_429()
        res = guard(compile_flow, body.get("flow") or {}, body.get("base") or None)
        rid = body.get("id") or "W1"
        res["yaml"] = yaml_line(rid, body.get("base") or None, res["diff"], body.get("desc") or "")
        res["command"] = (f"t2s eval --run {rid} --model arctic-r1-7b --split dev --variants "
                          f"{res['spec'].get('variant') or 'mn'} --workers 4")
        res.pop("spec", None)
        return res

    @r.post("/flow/save")
    def save(body: dict, k: str | None = None, admin: str | None = None):
        ck(k)
        need_admin(admin)   # R4 A5: config/flows.yaml-ийг өөрчлөх эрх
        res = guard(save_flow, body.get("id") or "", body.get("flow") or {}, body.get("base") or None,
                    body.get("desc") or "")
        res.pop("spec", None)
        return res

    @r.delete("/flow/{run_id}")
    def delete(run_id: str, k: str | None = None, admin: str | None = None):
        ck(k)
        need_admin(admin)
        if not delete_flow(run_id):
            raise HTTPException(status_code=404, detail="Хадгалсан flow олдсонгүй (experiments.yaml-ийнхийг устгахгүй)")
        return {"ok": True}

    # Серверийн лог (Debugger → Logs): зөвхөн build/logs доторх зөвшөөрсөн файлуудын сүүлийн мөрүүд
    LOGS = {"web": "web.log", "sql": "hf_d2.log", "translator": "hf_tr.log"}

    @r.get("/flow/logs")
    def logs(k: str | None = None, n: int = 200, admin: str | None = None):
        ck(k)
        need_admin(admin)   # R4 A6: серверийн лог (асуулт, SQL, алдаа)
        out = {}
        for key, name in LOGS.items():
            p = resolve_path("build/logs") / name
            if p.exists():
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                out[key] = "\n".join(lines[-max(10, min(int(n), 1000)):])
        return {"logs": out}

    @r.post("/flow/inspect")
    def inspect(body: dict, k: str | None = None, admin: str | None = None):
        ck(k)
        if shadow:   # prompt-д DB-ийн жишээ утга орно — shadow горимд зөвхөн админ
            auth.require_admin(admin)
        q = question_of(body)
        translation = body.get("translation")
        if translation is not None and (not isinstance(translation, str) or len(translation) > 2 * MAX_QUESTION):
            raise HTTPException(status_code=400, detail="Орчуулга хэт урт эсвэл буруу")
        # Хэрэглэгчийн алдаа (тохиргоо, хязгаар) → 400 өөрийн мессежээр; түүнээс цаашхи (индекс, файл, Pipeline) алдааны
        # текстийг клиентэд буцаахгүй (C1/L5 — файлын агуулга алдааны мессежээр задрахгүй)
        guard(compile_flow, body.get("flow") or {}, body.get("base") or None)
        with slots.hold():   # H1: Pipeline угсрах (индекс, embedding), RAG — /ask, /flow/run_job-тэй нэг хязгаар
            try:
                return inspector.inspect(body.get("flow") or {}, q, body.get("base") or None, translation)
            except HTTPException:
                raise
            except Exception as e:  # индекс, тохиргооны алдаа
                rid = uuid.uuid4().hex[:12]
                log.exception("flow inspect %s амжилтгүй", rid)
                raise HTTPException(status_code=500, detail=f"Prompt угсарч чадсангүй ({type(e).__name__}; request id: "
                                                            f"{rid}) — дэлгэрэнгүйг серверийн лог-д бичив.") from e

    @r.post("/flow/run_job")
    def run_job(body: dict, k: str | None = None):
        ck(k)
        if runner is None:
            raise HTTPException(status_code=503, detail="Энэ сервер загваргүй (зөвхөн угсрах горим). Colab/Kaggle дээрх "
                                                        "вэб үйлчилгээний /builder-ээс ажиллуулна.")
        q = question_of(body)
        guard(compile_flow, body.get("flow") or {}, body.get("base") or None)   # алдааг шууд буцаана
        slots.acquire_or_429()   # S1: /ask, /analyst/rerun-тэй нэг хязгаар; ажлын thread-ийн finally-д чөлөөлнө
        jid = uuid.uuid4().hex[:12]
        job_rec = {"done": False, "t0": time.time(), "progress": []}
        with jobs_lock:   # S8: түгжээн дор, хуулбар жагсаалтаар цэвэрлэнэ — дууссан job: 1 цагаас хуучин эсвэл 200-аас илүү
            now = time.time()
            for j, v in list(jobs.items()):   # оруулсан дарааллаар (хуучин нь эхэнд)
                if v["done"] and (now - v["t0"] > 3600 or len(jobs) > 200):
                    jobs.pop(j, None)
            jobs[jid] = job_rec

        def prog(node, msg, state):   # амьд явц: {node, msg, state, t (эхлэлээс секунд)}
            # S6: shadow — мессеж (англи орчуулга, төлөв, мөрийн тоо, хүснэгт) очихгүй; зөвхөн алхам ба хугацаа
            job_rec["progress"].append({"node": node, "msg": "" if shadow else str(msg)[:300], "state": state,
                                        "t": round(time.time() - job_rec["t0"], 1)})

        def work():
            rec = {"request_id": f"flow-{jid}", "question": q, "mode": mode, "source": "flow",
                   "model": getattr(runner, "served_model", lambda: None)()}   # M1: бодитоор үйлчилсэн загвар
            try:
                try:
                    res = runner.run(body["flow"], q, body.get("base") or None, progress=prog, shadow=shadow)
                    rec.update(res.pop("_audit", None) or {})
                    if shadow:   # R4 S1: shadow — хэрэглэгчид үр дүн/SQL/prompt буцаахгүй (/ask-тай адил)
                        res = {"request_id": rec["request_id"], "status": "shadow_logged", "inspect": None,
                               "steps": [{"node": "output", "status": "shadow_logged"}],
                               "warnings": ["Shadow горим: хариуг зөвхөн audit log-д бичив (хэрэглэгчид харуулахгүй)."]}
                    job_rec["result"] = res
                except Exception as e:  # LLM/DB тасалдал
                    rec.update(status="failed", error=str(e)[:300])
                    log.exception("flow job %s амжилтгүй", rec["request_id"])
                    # S6/L5: аль ч горимд алдааны текстийг (DB хаяг, файлын агуулга г.м.) клиентэд буцаахгүй —
                    # request id-аар серверийн лог/audit-аас хайна
                    job_rec["error"] = (f"Түр алдаа гарлаа ({type(e).__name__}; request id: {rec['request_id']}) — "
                                        "дэлгэрэнгүйг audit log-д бичив.")
                if audit:   # R4 S1: Studio-ийн ажиллуулалт ч audit log-д (api.py AuditLog.write — SQL/асуулт шифрлэгдэнэ)
                    try:
                        audit(rec)
                    except Exception:  # noqa: BLE001 — audit бичих алдаа хариултыг унагаахгүй
                        log.exception("flow audit бичиж чадсангүй")
            finally:
                slots.release()          # дууссан гэж харагдахаас өмнө чөлөөлнө (дараагийн хүсэлт 429 авахгүй)
                job_rec["done"] = True
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception:   # thread үүсгэж чадаагүй — slot-ийг буцааж өгнө
            slots.release()
            with jobs_lock:
                jobs.pop(jid, None)
            raise
        return {"job": jid}

    @r.get("/flow/job/{jid}")
    def job(jid: str, k: str | None = None):
        ck(k)
        with jobs_lock:
            v = jobs.get(jid)
        if v is None:
            raise HTTPException(status_code=404, detail="Ажил олдсонгүй")
        return {"done": v["done"], "elapsed_s": round(time.time() - v["t0"]), "result": v.get("result"),
                "error": v.get("error"), "progress": list(v.get("progress", []))}

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

    from .auth import max_result_bytes
    model = cached_model(cfg["knowledge"]["semantic_model"])
    guard = SqlGuard(model.allowed(), model.schema, set(cfg["security"]["forbid_functions"]),
                     str(cfg["project"]["as_of_date"]), enforce_limit=int(cfg["security"]["enforce_limit"]))
    executor = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                        max_rows=int(cfg["security"]["enforce_limit"]), max_cost=float(cfg["db"]["max_explain_cost"]),
                        max_result_bytes=max_result_bytes(cfg))   # H2: үр дүнгийн санах ойн төсөв
    runner = FlowRunner(cfg, "arctic-r1-7b", MockLLM(responder, name="demo"), executor, guard)
    runner.label = "ДЕМО (AI-гүй)"
    return runner


# L3: локал builder-ийн хандалтын горим — dev биш тул T2S_WEB_KEY-гүй үед ч зөвхөн энэ машины хөтчөөс шууд,
# итгэмжлэгдсэн Host-оор (auth.make_check_key): DNS rebinding-ээр /flow/save, /flow/run_job хийхээс хамгаална
BUILDER_MODE = "builder"


def create_builder_app(demo: bool = False):
    """Угсрах горим (локал): `t2s flow-ui` — загвар, DB-гүй; `--demo` — локал DB + AI-гүй демо ажиллуулалт.
    T2S_WEB_KEY байвал түлхүүр шаардана; үгүй бол зөвхөн localhost-оос шууд, Host ∈ auth.trusted_hosts() (L3)."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse

    from . import auth
    app = FastAPI(title="mn-text2sql flow builder", dependencies=[auth.capture_dependency()])
    app.add_middleware(auth.BodySizeLimit, max_bytes=auth.DEFAULT_MAX_BODY_BYTES)   # H1
    check_key = auth.make_check_key(os.environ.get("T2S_WEB_KEY"), BUILDER_MODE)
    app.include_router(make_router(demo_runner() if demo else None, check_key))

    @app.get("/")
    def root():
        return RedirectResponse("/builder")
    return app
