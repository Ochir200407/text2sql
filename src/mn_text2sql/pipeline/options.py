"""Туршилтын тохиргоо (config/experiments.yaml) → RunOptions. base-ийн гинж ба ${vars} орлуулалт."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, fields

import yaml

from ..config import resolve_path


@dataclass
class RunOptions:
    run_id: str = "custom"
    desc: str = ""
    group: str = ""
    variant: str = "mn"
    translate: bool = False
    dialect: str = "sqlite_native"
    describe: bool = False
    knowledge_notes: bool = False
    values: bool = False
    value_mode: str = "basic"
    glossary: bool = False
    fewshot_k: int = 0
    schema_linking: bool = False
    top_tables: int = 6
    agent_max_steps: int = 5
    repair_rounds: int = 0
    repair_on_empty: bool = False
    candidates: int = 1
    sampling: bool = False
    seeds: list[int] = field(default_factory=lambda: [0])
    abstain_threshold: float | None = None
    low_conf_action: str = "abstain"     # I: санал нийлэлт < abstain_threshold үед "abstain" эсвэл "clarify" (тодруулга)
    vote_filter: bool = False           # E4: хоосон/0/NULL ба DB-д байхгүй утгатай хувилбарыг санал хураалтаас хасах
    rule_refuse: bool = False           # E4: AUTHORING-ийн хариулах боломжгүй ангиллын дүрэм + хүснэгтгүй SQL → татгалзах
    rule_clarify: bool = False          # E4: бүдэг хугацаа/хэмжүүрийн дүрэм → тодруулга
    translation_cache: str | None = None  # I: build/translations/<нэр>.jsonl (анхдагч: models.yaml translator; «qwen3.5-9b.fix» = masking засвар)
    glossary_en: bool = False           # J: en_prompt үед нэр томьёог англиар (definition_en) + «Use exactly: SQL»
    glossary_file: str | None = None    # J: өөр нэр томьёоны файл (knowledge/glossary_plus.yaml — 7 шинэ нэр томьёо); анхдагч knowledge.glossary
    pivot: bool = False
    intent: bool = False
    semantic_router: bool = False
    search_values_tool: bool = False
    agent: bool = False                 # G: agentic давталт (tool-ийн шалгалт → critic → засвар; pipeline/agent.py)
    agent_rounds: int = 2               # засварын дээд тоо
    agent_plan: bool = False            # G3: SQL-ээс өмнө англи төлөвлөгөө
    agent_critic: bool = True           # LLM critic (false = зөвхөн детерминист tool)
    agent_literal_check: bool = True    # WHERE-ийн текст утга баганад хадгалагдсан эсэх
    agent_llm: str | None = None        # planner/critic-д өөр загвар (models.yaml; T2S_AGENT_BASE_URL)
    adapter: str | None = None
    index_dir: str | None = None
    dataset: str = "nbfi"
    models: list[str] = field(default_factory=list)
    intent_check: bool = False          # production: хариулах боломжгүй/хоёрдмол асуултыг тодруулах
    intent_refuse_only: bool = False    # H: intent_check-ээс зөвхөн "refuse" (PII, бичих хүсэлт) — тодруулга асуухгүй
    abstain_on_error: bool = False      # H: эцсийн SQL алдаатай бол хариулахаас татгалзах (TrustSQL)
    postproc: bool = False              # H: схем/хадгалагдсан утгаас гаргасан SQL засвар (pipeline/postproc.py)
    translate_mode: str = "replace"     # A4: "replace" = англи орчуулгаар солих (retrieval ч англиар); "bilingual" = монгол + англи мөр; "en_prompt" = загварт англи, retrieval монголоор
    base: str | None = None             # өвлөсөн тохиргоо (тайланд харьцуулах суурь)
    compare_to: str | None = None       # base-ээс өөр суурьтай харьцуулах бол

    def needs_retrieval(self) -> bool:
        return any([self.values, self.glossary, self.fewshot_k > 0, self.schema_linking, self.search_values_tool])

    def baseline(self) -> str | None:
        return self.compare_to or self.base

    def fingerprint(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        for k in ("run_id", "desc", "group", "models", "base", "compare_to"):
            d.pop(k, None)
        # Хожим нэмэгдсэн талбар анхдагч утгатай бол hash-д оруулахгүй (өмнөх run-уудаас үргэлжлүүлэх боломж)
        for k, default in _LATE_DEFAULTS.items():
            if d.get(k) == default:
                d.pop(k, None)
        return d


_LATE_DEFAULTS = {"intent_refuse_only": False, "abstain_on_error": False, "postproc": False,
                  "translate_mode": "replace", "low_conf_action": "abstain", "vote_filter": False,
                  "rule_refuse": False, "rule_clarify": False, "translation_cache": None,
                  "glossary_en": False, "glossary_file": None}

_VAR = re.compile(r"\$\{(\w+)\}")


def load_experiments(path: str = "config/experiments.yaml") -> dict:
    with open(resolve_path(path), encoding="utf-8") as fh:
        exp = yaml.safe_load(fh)
    flows = resolve_path("config/flows.yaml")   # вэб flow builder-ээр угсарсан туршилтууд (service/flow.py)
    if path == "config/experiments.yaml" and flows.exists():
        extra = (yaml.safe_load(flows.read_text(encoding="utf-8")) or {}).get("runs") or {}
        exp["runs"] = {**{k: v for k, v in extra.items() if k not in exp["runs"]}, **exp["runs"]}
    return exp


def resolve_run(run_id: str, exp: dict | None = None, overrides: dict | None = None,
                var_overrides: dict | None = None) -> RunOptions:
    exp = exp or load_experiments()
    vars_ = dict(exp.get("vars") or {})
    vars_.update(var_overrides or {})
    runs = exp["runs"]

    def subst(v):
        if isinstance(v, str):
            m = _VAR.fullmatch(v.strip())
            if m:
                return vars_.get(m.group(1), v)
            return _VAR.sub(lambda mm: str(vars_.get(mm.group(1), mm.group(0))), v)
        return v

    def chain(rid: str, seen: tuple = ()) -> dict:
        rid = subst(rid)
        if rid in seen:
            raise ValueError(f"experiments.yaml-д давталттай base: {' → '.join(seen + (rid,))}")
        if rid not in runs:
            raise KeyError(f"Туршилтын ID олдсонгүй: {rid}")
        spec = {k: subst(v) for k, v in (runs[rid] or {}).items()}
        base = spec.pop("base", None)
        merged = chain(base, seen + (rid,)) if base else copy.deepcopy(exp.get("defaults") or {})
        # Удамшдаггүй талбарууд: тайлбар, харьцуулах суурь
        for k in ("desc", "compare_to"):
            merged.pop(k, None)
        merged.update(spec)
        merged["base"] = base
        return merged

    spec = chain(run_id)
    spec.update(overrides or {})
    known = {f.name for f in fields(RunOptions)}
    opts = RunOptions(run_id=run_id, **{k: v for k, v in spec.items() if k in known})
    if not opts.models:
        opts.models = list((exp.get("defaults") or {}).get("models") or [])
    return opts


def parse_set(pairs: list[str] | None) -> dict:
    """CLI --set key=value → dict (true/false/тоо автоматаар)."""
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        val: object = v
        if v.lower() in ("true", "false"):
            val = v.lower() == "true"
        else:
            try:
                val = int(v)
            except ValueError:
                try:
                    val = float(v)
                except ValueError:
                    val = v
        out[k.strip()] = val
    return out
