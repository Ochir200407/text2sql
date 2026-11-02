"""Text2SQL-ийн бүрэн урсгал (зааврын 1-р хэсэг, зураг "6 алхам, 2 давталт").

Асуулт → хэвийн болгох/intent → хайлт (RAG) → SQL үүсгэх (загварын протокол) → шалгах ба гүйцэтгэх
(sqlglot guard → EXPLAIN → read-only) → алдаа бол 1–3 удаа засах → (сонголттой) олон хувилбар,
санал хураалт = итгэл → хариулт (SQL, үр дүн, монгол тайлбар).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import sqlglot

from ..config import Config, load_models
from ..db.conn import bot_dsn
from ..db.executor import ExecResult, Executor
from ..eval.compare import result_hash
from ..knowledge.semantic import cached_model
from ..llm.client import LLM, MockLLM, is_transient, make_llm
from ..llm.prompts import (
    ARCTIC_PREFILL,
    ENUM_CODE_MODES,
    OMNISQL_TEMPLATE,
    SQRL_FINISH,
    SQRL_FIRST_USER,
    SQRL_OBSERVATION,
    PromptContext,
    build_generic,
    build_omnisql,
    build_omnisql_plain,
    build_sqrl_system,
    build_xiyan,
    est_tokens,
    parse_sqrl_action,
    render_ddl,
    render_mschema,
    strip_think,
)
from ..security.sql_guard import SqlGuard, extended_block, extract_sql, is_refusal
from .dialect import sqlite_to_postgres
from .options import RunOptions

log = logging.getLogger(__name__)


class InfraError(RuntimeError):
    """C4 (repair_strict): дэд бүтцийн (LLM/DB) алдаа — асуултын хариулт биш. ask() хуваарилагчийнхыг error_stage "llm"
    хувилбар болгоно; бусад нь (bool_cols) ask()-аас гарч runner асуултыг хадгалахгүй — дахин ажиллуулахад асууна."""


# C8: бие даасан дугаар — «PAR30», «3.5», «30%», «-1», «1-3», «7d», «Q1» үгүй; «[3]», «3,5», «3 and 7» тийм.
# L2 (2026-10-31): юникод зураас (‐ ‑ ‒ – — −) ASCII «-»-тэй адил («1–3» = «1-3»); тоолсон тоо («12 terms», «none of
# the 12», «3 of 12»-ийн 12) сонголт биш.
_DASH = "\\-\u2010\u2011\u2012\u2013\u2014\u2212"
_SEL_NUM = re.compile(rf"(?<![\w.{_DASH}])(?<!of )(?<!of the )(?<!of these )\[?(\d{{1,3}})\]?"
                      rf"(?![\w%]|\.\d|[{_DASH}]|\s+(?:terms?|definitions?|items?|options?|candidates?|entries)\b)",
                      flags=re.I)


def _selection_part(text: str) -> tuple[str, bool]:
    """parse_selection-ий уншдаг хэсэг: <think>…</think> хасаад сүүлийн </think>-ийн дараах, дуусаагүй <think>-ээс
    өмнөх текст; хоёр дахь утга — эх текстэд </think> байсан эсэх (бодол хаагдсан)."""
    closed = "</think>" in (text or "")
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in t:
        t = t.split("</think>")[-1]
    if "<think>" in t:                       # токены хязгаарт тасарсан бодол — хариу биш
        t = t.split("<think>")[0]
    return t, closed


def parse_selection(text: str, n_terms: int) -> list[int] | None:
    """C8 (select_parse="strict"): хуваарилагчийн хариунаас сонгосон дугаарыг (1..n_terms) найдвартай уншина.

    Зөвхөн эцсийн хариуны хэсгийг: <think> хасаад СҮҮЛИЙН <answer>…</answer> (хаагдаагүй ч), байхгүй бол сүүлийн
    хоосон биш мөр. Бие даасан бүхэл тоо л; хүрээнээс гадуурхыг хасч, давхардлыг дарааллаар нь арилгана.
    Буцаалт: дугаар байвал тэдгээр («None needed except 3» → [3]); [] = дугааргүй «none» эсвэл </think>-ийн дараах
    хоосон <answer>; None = уншиж чадсангүй (бодлын дунд/угтваргүй хоосон <answer> ч) — дуудагч хайлтын эхний glossary_k."""
    t, closed = _selection_part(text)
    answers = re.findall(r"<answer>(.*?)(?:</answer>|$)", t, flags=re.S)
    if answers:
        seg = answers[-1].strip()
    else:
        lines = [ln for ln in t.strip().splitlines() if ln.strip()]
        seg = lines[-1].strip() if lines else ""
    valid = list(dict.fromkeys(n for n in (int(x) for x in _SEL_NUM.findall(seg)) if 1 <= n <= n_terms))
    if valid:
        return valid
    if re.search(r"\bnone\b", seg, flags=re.I):            # «none», «none (12 terms checked)»
        return []
    if answers and not seg:
        return [] if closed else None        # L2: бодлын дараах хоосон <answer> = none; бусад нь уншигдаагүй
    return None


def selection_raw_full(text: str, limit: int = 1000) -> str:
    """C8 L1: бичлэгийн retrieval.select_raw_full (select_parse="strict") — parse_selection-ий уншдаг хэсгийн СҮҮЛИЙН
    limit тэмдэгт (сүүлийн <answer> хадгалагдана), бодол хаагдсан бол «</think>» угтвартай.
    parse_selection(selection_raw_full(x), n) == parse_selection(x, n) (хэсэг ≤ limit үед)."""
    t, closed = _selection_part(text)
    return ("</think>" if closed else "") + t[-limit:]


def reparse_selection(retrieval: dict | None, n_terms: int) -> list[int] | None:
    """C8 L1: хадгалсан бичлэгээс сонголтыг офлайн дахин уншина. select_raw_full (strict, 2026-10-31-ээс) байвал
    түүгээр — онлайн уншилттай ижил; хуучин бичлэгийн select_raw нь clean_reasoning-ийн (эхний <answer>-ийн доторх,
    tag-гүй) текст тул <answer>-ээр боож уншина (нэг <answer>-тэй хариунд онлайнтай ижил)."""
    r = retrieval or {}
    if r.get("select_raw_full") is not None:
        return parse_selection(r["select_raw_full"], n_terms)
    if r.get("select_raw") is None:
        return None
    return parse_selection(f"<answer>{r['select_raw']}</answer>", n_terms)

# R7 #5 rescue_truncated: тасарсан reasoning-ийг хааж SQL-ийг «шахаж» гаргах prefill ба түүний токены хязгаар
RESCUE_SUFFIX = "\n</think>\n<answer>\n```sql\n"
RESCUE_MAX_TOKENS = 512


@dataclass
class Candidate:
    raw_sql: str | None = None
    sql: str | None = None
    text: str = ""
    ok: bool = False
    rows: int = 0
    error: str | None = None
    error_stage: str | None = None     # extract | guard | exec | empty | llm | refusal
    repairs: int = 0
    agent_steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    result_hash: str | None = None
    tables: list[str] = field(default_factory=list)
    result: ExecResult | None = field(default=None, repr=False)
    # Сургалтад (C1/C2 rejection sampling): prompt, prefill, загварын түүхий гаралт (sqrl: алхам бүрийн)
    messages: list[dict] | None = field(default=None, repr=False)
    prefill: str | None = field(default=None, repr=False)
    raw_turns: list[str] | None = field(default=None, repr=False)
    agent_log: list[dict] | None = None  # G бүлэг: agent-ийн алхам бүр (tool-ийн олдвор, critic, засвар)
    degenerate: bool = False             # E4: хоосон / бүх NULL / ганц мөр 0
    bad_literals: list | None = None     # E4: DB-д байхгүй текст утга (хүснэгт, багана, утга)
    lint_flags: list | None = None       # vote_lint (sql_lint.lint_sql): chasm/fan/npl_by_count/no_active_portfolio
    rescued: bool = False                # R7 #5: max_tokens-д тасарсан гаралтыг rescue_truncated-аар үргэлжлүүлсэн


@dataclass
class Answer:
    question: str
    variant: str
    run_id: str
    model: str
    status: str = "answered"           # answered | abstained | error | clarify | refused
    sql: str | None = None
    result: ExecResult | None = field(default=None, repr=False)
    candidates: list[Candidate] = field(default_factory=list)
    confidence: float | None = None
    unanimous: bool | None = None
    explanation_mn: str = ""
    retrieval: dict = field(default_factory=dict)
    question_used: str = ""
    intent_json: str | None = None
    routed: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    timings: dict = field(default_factory=dict)
    chosen_index: int | None = None     # candidates дахь сонгогдсон хувилбар (бичлэгийн error_stage, agent_log г.м.)

    def to_dict(self, rows: int = 20) -> dict:
        d = asdict(self)
        d.pop("result", None)
        for c in d["candidates"]:
            c.pop("result", None)
        if self.result is not None and self.result.ok:
            d["columns"] = self.result.columns
            d["rows"] = [[str(v) if v is not None else None for v in r] for r in self.result.rows[:rows]]
            d["row_count"] = self.result.rowcount
        return d


# Flow builder-ийн амьд явц: FlowRunner ажлын thread-д callback тавина (thread бүрт тусдаа — зэрэг ажиллах асуултууд
# холилдохгүй); үнэлгээ, вэбийн энгийн дуудлагад callback байхгүй тул юу ч хийхгүй.
_PROGRESS = threading.local()


def set_progress(cb) -> None:
    _PROGRESS.cb = cb


def report(node: str, msg: str, state: str = "run") -> None:
    cb = getattr(_PROGRESS, "cb", None)
    if cb is not None:
        try:
            cb(node, msg, state)
        except Exception as e:  # noqa: BLE001 — явцын алдаа хариултад нөлөөлөхгүй
            log.debug("progress callback: %s", e)


class Pipeline:
    def __init__(self, cfg: Config, opts: RunOptions, model_name: str, llm: LLM | None = None, *,
                 retriever=None, executor: Executor | None = None, guard: SqlGuard | None = None,
                 translations: dict | None = None, intents: dict | None = None, router=None):
        if opts.index_dir or opts.glossary_file:
            # F9: өөр жишээний сантай индекс (build/index_f9a г.м.); J: өөр нэр томьёоны файл (glossary_plus.yaml)
            import json as _json

            from ..config import _to_config
            cfg = _to_config(_json.loads(_json.dumps(cfg, default=str)))
            if opts.index_dir:
                cfg["knowledge"]["index_dir"] = opts.index_dir
            if opts.glossary_file:
                cfg["knowledge"]["glossary"] = opts.glossary_file
        self.cfg = cfg
        self.opts = opts
        registry = load_models()
        self.models_cfg = registry.get("models", {})
        self.model_name = model_name
        self.mspec = self.models_cfg.get(model_name, {"family": "generic", "served_name": model_name,
                                                      "max_tokens": 2048, "sampling": {"temperature": 0.8}})
        self.family = self.mspec.get("family", "generic")
        if llm is None:
            llm_cfg = dict(cfg["llm"])
            llm_cfg["model"] = opts.adapter or self.mspec.get("served_name", model_name)
            if self.mspec.get("extra_body"):
                llm_cfg["extra_body"] = {**(llm_cfg.get("extra_body") or {}), **self.mspec["extra_body"]}
            llm = make_llm(llm_cfg)
        self.llm = llm
        self.model = cached_model(cfg["knowledge"]["semantic_model"])
        self.executor = executor or Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                                             max_rows=int(cfg["pipeline"]["max_result_rows"]) * 100,
                                             max_cost=float(cfg["db"]["max_explain_cost"]))
        self.guard = guard or SqlGuard(self.model.allowed(), self.model.schema,
                                       set(cfg["security"]["forbid_functions"]), str(cfg["project"]["as_of_date"]),
                                       # guard_scope (2026-11-01): alias-ийг SELECT/CTE-ийн хүрээгээр (анхдагч False = хуучин)
                                       **({"scoped_columns": True} if getattr(opts, "guard_scope", False) else {}))
        self.retriever = retriever
        if self.retriever is None and (opts.needs_retrieval() or opts.describe or opts.semantic_router):
            from ..retrieval.retriever import get_retriever
            try:
                self.retriever = get_retriever(cfg)
            except FileNotFoundError as e:
                if opts.needs_retrieval():
                    raise
                log.warning("%s — баганын жишээ утгагүй ажиллана", e)
        self.translations = translations or {}
        self.intents = intents or {}
        self.router = router
        # {eval асуултын id → family}: few-shot-д тухайн асуултын өөрийн family-ийн жишээг татахгүй (жишээний санд
        # dev-ийн асуулт орсон үед — I бүлэг; анхдагч сан eval-ээс тусдаа тул нөлөөгүй). runner дүүргэнэ.
        self.item_families: dict[str, str] = {}
        self.types, self.col_examples = self._types_and_examples()
        self.max_tokens = int(self.mspec.get("max_tokens", cfg["llm"]["max_tokens"]))

    # ------------------------------------------------------------------ туслах
    def _types_and_examples(self) -> tuple[dict, dict]:
        if self.retriever is not None:
            ex = dict(self.retriever.col_examples)
            return ex.pop("__types__", {}), ex
        try:
            from ..retrieval.build import index_dir
            from ..retrieval.indexes import load_json
            p = index_dir(self.cfg) / "column_examples.json"
            if p.exists():
                ex = load_json(p)
                return ex.pop("__types__", {}), ex
        except Exception as e:  # индексгүй орчинд (тест) төрөлгүй DDL
            log.debug("column_examples ачаалсангүй: %s", e)
        return {}, {}

    @classmethod
    def from_config(cls, cfg: Config, mock_sql: str | None = None, run_id: str = "B5",
                    model_name: str | None = None) -> Pipeline:
        from .options import resolve_run
        opts = resolve_run(run_id)
        llm = None
        if mock_sql:
            llm = MockLLM(lambda msgs: f"<answer>\n```sql\n{mock_sql}\n```\n</answer>")
        return cls(cfg, opts, model_name or cfg["llm"]["model"], llm)

    def _decoding(self, sampled: bool) -> dict:
        if not sampled:
            return {"temperature": 0.0, "top_p": 1.0}
        s = self.mspec.get("sampling") or {}
        return {"temperature": float(s.get("temperature", 0.8)), "top_p": float(s.get("top_p", 1.0))}

    def _all_tables(self) -> list[str]:
        return list(self.model.tables)

    def _example_sql(self, sql: str) -> str:
        if self.opts.dialect == "postgres_prompt":
            return sql
        try:
            out = sqlglot.transpile(sql, read="postgres", write="sqlite")[0]
        except Exception:
            return sql
        # sqlglot SQLite-д хөрвүүлж чадахгүй бүтэц (date_trunc, INTERVAL, EXTRACT(QUARTER ...)) үлдвэл SQLite биш SQL
        # харуулахгүйн тулд жишээг эх хэлбэрээр нь үлдээнэ
        if any(k in out.upper() for k in ("TIMESTAMP_TRUNC", "DATE_TRUNC", "INTERVAL", "EXTRACT(", "::", " FILTER (")):
            return sql
        return out

    # ------------------------------------------------------------------ prompt
    def build_context(self, question: str, variant: str, item_id: str | None,
                      _out: dict | None = None) -> tuple[PromptContext, dict]:
        """_out (заавал биш): RetrievalResult-ийг ``_out["ret"]``-д буцаана (flow builder-ийн prompt/RAG харагдац)."""
        o = self.opts
        info: dict = {}
        ret = None
        if self.retriever is not None and (o.values or o.glossary or o.fewshot_k > 0 or o.schema_linking):
            fam = self.item_families.get(item_id or "")
            ret = self.retriever.retrieve(question, variant, value_mode=o.value_mode, fewshot_k=o.fewshot_k,
                                          top_tables=o.top_tables, want_values=o.values, want_glossary=o.glossary,
                                          want_examples=o.fewshot_k > 0, exclude_families={fam} if fam else None,
                                          glossary_k=o.select_pool if o.knowledge_select else o.glossary_k,
                                          **({"fk_bridge": True} if o.fk_bridge else {}),   # FK-гүүр (M1fk)
                                          # M1fs (R5 З1): хүснэгт мэдэх few-shot эрэмбэ (анхдагч None → дамжуулахгүй)
                                          **({"fewshot_rerank": o.fewshot_rerank} if o.fewshot_rerank else {}),
                                          # M1vf (F1/F3/L6): анхдагч утгатай бол дамжуулахгүй — retrieve-ийн дуудлага хуучинтай ижил
                                          **self._retrieval_fix_kwargs())
            if o.glossary and o.glossary_select_cache and item_id:
                # R: сургасан reranker-ийн урьдчилсан сонголт (кэшгүй асуултад хайлтын top-k хэвээр)
                sel = self._glossary_cache().get(item_id)
                if sel is not None:
                    by_id = {g.id: g for g in self.retriever.glossary_terms}
                    ret.glossary = [by_id[t] for t in sel if t in by_id]
            elif o.glossary and o.knowledge_select and ret.glossary:
                q_sel = (self.translations.get(f"{item_id}:{variant}") if o.translate else None) or question
                report("select", f"Хуваарилагч: {len(ret.glossary)} нэр томьёоноос хэрэгтэйг сонгож байна")
                # C8: сонгогчийн уншсан хариуг (info["select_raw"]) бичлэгт хадгална — сонголт, prompt өөрчлөгдөхгүй
                ret.glossary, info["select_tokens"] = self._select_glossary(q_sel, ret.glossary, info)
            info.update({"linked_tables": ret.linked_tables, "values": [(h.table, h.column, h.value, h.score)
                                                                         for h in ret.values],
                    "glossary": [g.id for g in ret.glossary], "examples": [e.id for e in ret.examples],
                    "retrieval_ms": round(ret.ms, 1), "question_cyr": ret.question_cyr})
        tables = self._all_tables()
        columns = None
        describe_cols: dict[str, set[str]] | None = None
        if o.schema_linking and ret is not None:
            # Зөвхөн ХҮСНЭГТИЙГ шүүнэ; холбогдсон хүснэгтийн БҮХ багана DDL-д үлдэнэ. Өмнө нь хүснэгт бүрийн шилдэг 14
            # баганыг (retrieval.max_columns_per_table) л үлдээдэг байсан — BM25-тай dev-ийн хариулах боломжтой асуултын
            # 45%-д gold багана prompt-д байгаагүй (баганын recall 80.6% → бүх баганаар 99.0%). Бүх баганыг
            # тайлбартай нь өгвөл prompt ~9.2k токен (D3-ийн max_length 8192-оос урт) тул тайлбарыг (comment) зөвхөн
            # шилдэг баганад (оноогүй хүснэгтэд бүгдэд) өгч, бусдыг нэр, төрлөөр нь л үлдээнэ (медиан ~5.5k токен).
            # enum баганыг нэмж тайлбарлах нь +1.1k токеноор gold баганын тайлбарыг 70.4 → 72.8% л болгосон.
            tables = [t for t in self._all_tables() if t in set(ret.linked_tables)] or tables
            k = int(self.cfg["retrieval"]["max_columns_per_table"])
            describe_cols = {}
            for t in tables:
                scored = (ret.tables.get(t) or {}).get("columns", {})
                top = {c for c, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:k]}
                describe_cols[t] = top or set(self.model.tables[t].columns)
        lines: list[str] = []
        if o.knowledge_notes:
            lines.append(f"Today (business date) is {self.cfg['project']['as_of_date']}; CURRENT_DATE returns it.")
            for note in self.model.notes_mn[1:]:
                # date_trunc/EXTRACT зөвлөмж зөвхөн PostgreSQL-ээр бичүүлэх үед (SQLite протоколтой зөрчилдөхгүй)
                if o.dialect != "postgres_prompt" and "date_trunc" in note:
                    continue
                lines.append(note)
        if ret is not None and o.glossary:
            en_prompt = o.translate and o.translate_mode == "en_prompt"
            for g in ret.glossary:
                if o.glossary_en and en_prompt and g.definition_en:
                    # J: загварт англи асуулт өгдөг тул тодорхойлолтыг англиар, SQL-ийг яг хэрэглэхийг шаардана
                    lines.append(f"{g.en} («{g.term}»): {g.definition_en} Use exactly: {g.sql}")
                else:
                    lines.append(f"{g.term} ({g.en}): {g.definition_mn} SQL: {g.sql}")
        if ret is not None and o.values:
            for h in ret.values:
                lines.append(f"Stored value '{h.value}' exists in {h.table}.{h.column}")
        if o.table_hints:
            # M1th: төстэй хүснэгтийн хосыг ялгах ≤5 мөр (анхдагч false үед энэ блок огт ажиллахгүй — prompt ижил)
            from ..knowledge.table_hints import select_hints
            lines.extend(select_hints(ret.linked_tables if ret is not None else None, tables))
        if o.implied_preds:
            # M1ip (T1): хүснэгтийн далд нөхцөл (анхдагч false үед огт ажиллахгүй — prompt ижил). skip үгийг англи асуултаар
            from ..knowledge.table_hints import implied_predicates
            q_en = (self.translations.get(f"{item_id}:{variant}") if o.translate else None) or question
            lines.extend(implied_predicates(ret.linked_tables if ret is not None else None, q_en, tables))
        examples = []
        if ret is not None and o.fewshot_k > 0:
            for e in ret.examples:
                # en_prompt: асуулт англиар тул жишээг ч англиар (байвал) үзүүлнэ
                ex_variant = "en" if (o.translate and o.translate_mode == "en_prompt") else variant
                examples.append((e.question(ex_variant), self._example_sql(e.sql)))
        relevant: list[str] = []
        if o.describe and self.retriever is not None:
            # Тайлбарыг токены төсөвт багтаахад хүснэгтийн хамаарлын эрэмбэ хэрэгтэй (зөвхөн эрэмбэ — evidence биш)
            r = ret or self.retriever.retrieve(question, variant, top_tables=o.top_tables, want_values=False,
                                               want_glossary=False, want_examples=False,
                                               **self._retrieval_fix_kwargs())
            ranked = [t for t, _ in sorted(r.tables.items(), key=lambda kv: -kv[1]["score"])]
            relevant = list(dict.fromkeys(list(r.linked_tables) + ranked))
        ctx = PromptContext(question=question, tables=tables, columns=columns, describe=o.describe,
                            evidence_lines=lines, examples=examples, dialect=o.dialect, pivot=o.pivot,
                            intent_json=self.intents.get(f"{item_id}:{variant}") if o.intent else None,
                            max_steps=o.agent_max_steps, search_values_tool=o.search_values_tool,
                            relevant_tables=relevant, describe_columns=describe_cols,
                            gen_style=o.gen_style, value_note=bool(getattr(o, "value_list_max", 0)))
        info["n_tables"] = len(tables)
        if _out is not None:
            _out["ret"] = ret
        return ctx, info

    def _retrieval_fix_kwargs(self) -> dict:
        """M1vf-ийн хайлтын засварууд (value_match, mask_mode, translit_vocab) — зөвхөн анхдагчаас ялгаатайг нь."""
        o = self.opts
        return {k: getattr(o, k) for k, default in (("value_match", "legacy"), ("mask_mode", "legacy"),
                                                    ("translit_vocab", "pool")) if getattr(o, k, default) != default}

    def _glossary_cache(self) -> dict:
        if getattr(self, "_gsel", None) is None:
            import json
            from ..config import resolve_path
            p = resolve_path(self.opts.glossary_select_cache)
            self._gsel = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            if not self._gsel:
                log.warning("glossary_select_cache алга/хоосон: %s — хайлтын top-k", p)
        return self._gsel

    def _select_glossary(self, question: str, terms: list,
                         info: dict | None = None) -> tuple[list, tuple[int, int]]:
        """S (хуваарилагч): өргөн хайлтын нэр томьёоноос (select_pool) асуултад хэрэгтэйг SQL загвар өөрөө сонгоно —
        Arctic-аас өөр загвар ашиглахгүй. Хариунаас дугаарыг уншина; уншиж чадахгүй бол хайлтын эхний glossary_k.
        info өгвөл (C8) дугаар уншсан текстийг info["select_raw"]-д (≤1000 тэмдэгт) — сонголтын дүрэм хэвээр;
        strict үед мөн info["select_raw_full"] (L1: офлайн reparse_selection онлайнтай ижил)."""
        from .agent import clean_reasoning
        # Тодорхойлолтын ихэнх нь монголоор тул англи нэр + (байвал англи тодорхойлолт) + SQL хэлбэрийг харуулна
        listing = "\n".join(f"[{i}] {g.en or g.term}: {(g.definition_en or '')[:140]} SQL: {(g.sql or '')[:160]}"
                            for i, g in enumerate(terms, 1))
        messages = [{"role": "system", "content": (
            "You prepare context for writing a SQL query. From the numbered business-term definitions, choose only "
            "those the question needs (a metric it asks for, a filter it implies, or a status/term it mentions). "
            "Reply inside <answer></answer> with the numbers separated by commas, or 'none'.")},
            {"role": "user", "content": f"Question: {question}\n\nDefinitions:\n{listing}"}]
        try:
            resp = self.llm.chat(messages, n=1, max_tokens=1024, temperature=0.0)
            text, tok = resp.texts[0], (resp.prompt_tokens, resp.completion_tokens)
        except Exception as e:   # сонголт алгасагдвал SQL үүсгэлт үргэлжилнэ
            if getattr(self.opts, "repair_strict", False) and _transient_llm_error(e):
                # C4 (L6): түр зуурын алдаа — top-k-аар өөр prompt угсарч хариулт гэж хадгалахгүй; ask() үүнийг
                # error_stage "llm" (дэд бүтэц) болгоно → runner хадгалахгүй, дахин ажиллуулахад асууна
                raise InfraError(f"Хуваарилагчийн LLM дуудлага амжилтгүй: {type(e).__name__}: {e}"[:600]) from e
            log.warning("хуваарилагч алгасав: %s", e)
            return terms[:self.opts.glossary_k], (0, 0)
        ans = clean_reasoning(text)
        strict = getattr(self.opts, "select_parse", "legacy") == "strict"
        if info is not None:
            info["select_raw"] = ans[:1000]
            if strict:
                info["select_raw_full"] = selection_raw_full(text)
        if strict:   # C8 засвар (M1ksf); анхдагч legacy = M1ks
            nums = parse_selection(text, len(terms))
            if nums is None:
                return terms[:self.opts.glossary_k], tok
            return [terms[n - 1] for n in nums[:6]], tok
        if re.search(r"\bnone\b", ans, flags=re.I) and not re.search(r"\d", ans):
            return [], tok
        picked = [terms[n - 1] for n in dict.fromkeys(int(x) for x in re.findall(r"\d+", ans)) if 1 <= n <= len(terms)]
        return (picked[:6] or terms[:self.opts.glossary_k]), tok

    def schema_text(self, ctx: PromptContext) -> str:
        if self.family == "xiyan":
            return render_mschema(self.model, ctx.tables, self.types, self.col_examples, ctx.describe, ctx.columns)
        dcols = ctx.describe_columns
        # M1vf (F5, enum_codes="all"): enum-ийн бүх кодыг (анхдагч "first9" = YAML-ийн эхний 9 — хуучин prompt)
        ek = self._enum_kwargs()
        if getattr(self.opts, "value_list_max", 0):   # анхдагч 0 — render_ddl-ийн дуудлага хуучинтай ижил
            ek["full_values"] = self._full_values(int(self.opts.value_list_max))
        text = render_ddl(self.model, ctx.tables, self.types, self.col_examples, ctx.describe, ctx.columns,
                          describe_columns=dcols, **ek)
        budget = int(self.mspec.get("schema_token_budget") or self.cfg["retrieval"]["schema_token_budget"])
        if ctx.describe and est_tokens(text) > budget:
            # Төсвөөс хэтэрвэл бүх хүснэгт DDL-д үлдэнэ (A1-тэй ижил), харин баганын тайлбарыг асуулттай хамгийн
            # их холбоотой хүснэгтүүдэд л өгнө — хамаарлын эрэмбээр төсөвт багтах хүртэл нэмнэ.
            ranked = list(dict.fromkeys(ctx.relevant_tables)) or list(ctx.tables)
            best = render_ddl(self.model, ctx.tables, self.types, self.col_examples, True, ctx.columns,
                              describe_tables=set(), describe_columns=dcols, **ek)
            for k in range(1, len(ranked) + 1):
                cand = render_ddl(self.model, ctx.tables, self.types, self.col_examples, True, ctx.columns,
                                  describe_tables=set(ranked[:k]), describe_columns=dcols, **ek)
                if est_tokens(cand) > budget:
                    break
                best = cand
            text = best
        return text

    def _full_values(self, limit: int) -> dict:
        """value_list_max: утгын индексээс (retriever.values.entries) ≤limit ялгаатай утгатай баганын бүх утга
        {хүснэгт: {багана: [утга]}} — давтамжаар (их → бага), дараа нь утгаар эрэмбэлнэ. Индексгүй бол {}."""
        cache = getattr(self, "_fv_cache", None)
        if cache is not None and cache[0] == limit:
            return cache[1]
        entries = getattr(getattr(self.retriever, "values", None), "entries", None) or []
        cols: dict[tuple[str, str], dict[str, int]] = {}
        for e in entries:
            d = cols.setdefault((e.table, e.column), {})
            d[e.value] = d.get(e.value, 0) + int(getattr(e, "freq", 1) or 1)
        out: dict[str, dict[str, list[str]]] = {}
        for (t, c), vals in cols.items():
            if 0 < len(vals) <= limit:
                out.setdefault(t, {})[c] = [v for v, _ in sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))]
        self._fv_cache = (limit, out)
        return out

    def _enum_kwargs(self) -> dict:
        mode = getattr(self.opts, "enum_codes", "first9")
        if mode not in ENUM_CODE_MODES:
            raise ValueError(f"enum_codes: үл мэдэгдэх горим {mode!r} (боломжит: {ENUM_CODE_MODES})")
        return {"enum_limit": None} if mode == "all" else {}

    def question_forms(self, question: str, variant: str, item_id: str | None) -> tuple[str, str | None]:
        """A4 орчуулгын горимууд → (retrieval/build_context-д өгөх асуулт, загварт харуулах асуулт эсвэл None).
        replace: хоёуланд англи орчуулга; bilingual: retrieval монголоор, загварт монгол + англи мөр; en_prompt:
        retrieval монголоор, загварт зөвхөн англи. Орчуулга алга (кэшгүй) бол эх асуулт хэвээр."""
        o = self.opts
        q, q_prompt = question, None
        if o.translate:
            en = self.translations.get(f"{item_id}:{variant}")
            mode = o.translate_mode
            if en and o.translate_check == "bilingual" and self.translation_flags(question, en):
                mode = "bilingual"   # R1.2 (M1tc): сэжигтэй орчуулгад л монгол + англи (анхдагч "off" — өөрчлөлтгүй)
            if en and mode == "bilingual":
                # retrieval нь эх (монгол) асуултаар; загварт монгол асуулт + англи орчуулгын мөр
                q_prompt = f"{question}\n(English translation: {en})"
            elif en and o.translate_mode == "en_prompt":
                # retrieval нь эх (монгол) асуултаар (англи текстийг кирилл болгож хайхгүй); загварт зөвхөн англи
                q_prompt = en
            elif en:
                q = en
        return q, q_prompt

    def translation_flags(self, question: str, en: str) -> list[str]:
        """R1.2: translation_check (pipeline/translate_check.py); DB-ийн утгыг утгын индексээс (байвал) — DB-гүй."""
        from .translate_check import keys_from_values, translation_check
        if not hasattr(self, "_tc_keys"):
            vals = getattr(getattr(self.retriever, "values", None), "entries", None)
            self._tc_keys = keys_from_values(vals) if vals else None
        return translation_check(question, en, keys=self._tc_keys)

    def prompt_for(self, question: str, variant: str | None = None, item_id: str | None = None,
                   _out: dict | None = None) -> tuple[list[dict], str | None, PromptContext, dict, str]:
        """ask()-ийн үүсгэлтээс өмнөх бүх алхам — prompt-ын ЦОРЫН ГАНЦ эх сурвалж (үнэлгээ ба сургалт, D3):
        орчуулгын горим (en_prompt г.м.), build_context (item_families-ээр өөрийн family-г few-shot-оос хасна),
        initial_messages. Буцаах: (messages, prefill, ctx, retrieval info, загварт харуулсан асуулт)."""
        variant = variant or self.opts.variant
        q, q_prompt = self.question_forms(question, variant, item_id)
        ctx, info = self.build_context(q, variant, item_id, _out)
        if self.opts.translate and self.opts.translate_check != "off":
            en = self.translations.get(f"{item_id}:{variant}")
            info["translate_check"] = self.translation_flags(question, en) if en else []
        if q_prompt:
            ctx.question = q_prompt
        messages, prefill = self.initial_messages(ctx)
        return messages, prefill, ctx, info, q_prompt or q

    def initial_messages(self, ctx: PromptContext) -> tuple[list[dict], str | None]:
        schema = self.schema_text(ctx)
        if self.family == "omnisql":
            return build_omnisql(ctx, schema)
        if self.family == "omnisql_plain":
            return build_omnisql_plain(ctx, schema)
        if self.family == "xiyan":
            return build_xiyan(ctx, schema)
        if self.family == "sqrl":
            return [{"role": "system", "content": build_sqrl_system(ctx, schema)},
                    {"role": "user", "content": SQRL_FIRST_USER.format(question=ctx.question)}], None
        return build_generic(ctx, schema)

    # ------------------------------------------------------------------ SQL боловсруулах
    def _transpile_kwargs(self) -> dict:
        """transpile_fix (2026-11-01): sqlite_to_postgres-ийн F1–F5 засварт баганын төрөл/нэр (retrieval-ийн
        column_examples.json __types__ + semantic model). Анхдагч (off) үед {} — дуудлага хуучинтай байт-байтаар ижил."""
        if not getattr(self.opts, "transpile_fix", False):
            return {}
        if getattr(self, "_fix_cols", None) is None:
            from .dialect import column_types
            col_types = column_types(getattr(self, "types", None))
            model = getattr(self, "model", None)
            known = {c.lower() for cols in (model.allowed().values() if model is not None else []) for c in cols}
            self._fix_cols = (col_types, frozenset(known) | frozenset(col_types))
        col_types, known = self._fix_cols
        return {"fix": True, "col_types": col_types, "known_cols": known}

    def process_sql(self, raw_sql: str | None, hits: list | None = None) -> Candidate:
        """hits (literal_fix, 2026-11-01): тухайн асуултын утгын хайлтын [(хүснэгт, багана, утга, оноо)] — postproc R6-д;
        None (анхдагч) бол R6 ажиллахгүй."""
        cand = Candidate(raw_sql=raw_sql)
        if not raw_sql:
            cand.error, cand.error_stage = "Загварын хариунаас SQL олдсонгүй.", "extract"
            return cand
        sql = raw_sql.strip().rstrip(";")
        oracle = self._oracle_gold()
        if self.opts.dialect == "sqlite_transpile" and not oracle:
            # хөрвүүлэх боломжгүй бол эх SQL-ээрээ үлдэнэ
            sql = sqlite_to_postgres(sql, self.bool_cols(), **self._transpile_kwargs())
        if self.opts.postproc and not oracle:
            if hits and getattr(self.opts, "literal_fix", False):
                sql, _rules = self.postprocessor().apply(sql, hits=hits)
            else:
                sql, _rules = self.postprocessor().apply(sql)
            if getattr(self.opts, "trunc_date", False):
                # R7 (V27 dev): гаралтын DATE_TRUNC → DATE (анхдагч off — дуудлага огт хийгдэхгүй, гаралт хуучнаараа)
                from .postproc import cast_date_trunc
                sql, _r7 = cast_date_trunc(sql)
            kq = getattr(getattr(self, "_kpi_tls", None), "question", None)
            if kq and getattr(self.opts, "kpi_fix", False):
                # kpi_fix (2026-11-02): асуулт ask()-аас thread-local-оор (вэбийн зэрэгцээ хүсэлтэд холилдохгүй)
                from .kpi_fix import kpi_fix
                sql, _kf = kpi_fix(kq, sql, self.model)
        g = self.guard.check(sql)
        cand.tables = g.tables
        if not g.ok:
            cand.sql, cand.error, cand.error_stage = sql, g.error, "guard"
            return cand
        cand.sql = g.sql
        res = self.executor.run(g.sql)
        cand.result = res
        if not res.ok:
            cand.error, cand.error_stage = res.error, "exec"
            return cand
        cand.ok = True
        cand.rows = res.rowcount
        cand.result_hash = result_hash(res.columns, res.rows)
        if res.rowcount == 0:
            cand.error_stage = "empty"
        if self.opts.vote_filter:
            from .selection import is_degenerate
            cand.degenerate = is_degenerate(res)
            cand.bad_literals = self.literal_checker().bad_literals(cand.sql)
        return cand

    def _oracle_gold(self) -> bool:
        """eval --oracle (runner.OracleLLM, name="oracle"): хариу нь загварын SQLite биш PostgreSQL gold SQL — A7-ийн
        SQLite→PostgreSQL хөрвүүлэлт ба postproc-ийг алгасна (harness gold-ыг яг өөрөөр нь шалгана; L10)."""
        llm = getattr(self, "llm", None)
        return getattr(llm, "oracle", False) is True or getattr(llm, "name", None) == "oracle"

    def literal_checker(self):
        """E4: DB-д байгаа текст утгын шалгагч — нэг удаа үүсгэж кэшилнэ (thread-safe)."""
        if getattr(self, "_literal_checker", None) is None:
            from .selection import LiteralChecker, enum_codes
            self._literal_checker = LiteralChecker(self.executor, enums=enum_codes(self.model))
        return self._literal_checker

    def postprocessor(self):
        """H: схемээс гаргасан SQL засвар — нэг удаа үүсгэж кэшилнэ (SqlPostprocessor өөрөө thread-safe ачаална)."""
        if getattr(self, "_postproc", None) is None:
            from .postproc import SqlPostprocessor
            self._postproc = SqlPostprocessor(self.executor)
        return self._postproc

    def bool_cols(self) -> frozenset[str]:
        """DB-ийн boolean баганын нэрс (A7 хөрвүүлэлтэд 0/1 → TRUE/FALSE). Нэг удаа асууж кэшилнэ."""
        if getattr(self, "_bool_cols", None) is None:
            res = self.executor.run("SELECT DISTINCT lower(column_name) FROM information_schema.columns "
                                    "WHERE data_type = 'boolean' AND table_schema NOT IN ('pg_catalog', 'information_schema')")
            if not res.ok and getattr(getattr(self, "opts", None), "repair_strict", False):
                # C4 (L5): кэшлэхгүй, мөн энэ асуултыг хоосон олонлогоор (0/1 → TRUE/FALSE хөрвүүлэлтгүй) хариулахгүй —
                # дэд бүтцийн алдаа: runner хадгалахгүй, дахин ажиллуулахад асууна
                raise InfraError(f"bool_cols: boolean баганын жагсаалтыг уншиж чадсангүй: {res.error}"[:600])
            self._bool_cols = frozenset(r[0] for r in res.rows) if res.ok else frozenset()
        return self._bool_cols

    @staticmethod
    def _mark_refusal(cand: Candidate, text: str | None) -> Candidate:
        """SQL бичээгүй бүрэн хариу = татгалзалт (status "refused"): E1 ба agent засуулахгүй, TrustSQL-д хариулаагүй."""
        if not cand.ok and cand.error_stage in ("extract", "guard") and is_refusal(text):
            cand.sql = cand.raw_sql = None
            cand.tables = []
            cand.error, cand.error_stage = "Загвар SQL бичээгүй (асуултад хариулахаас татгалзсан).", "refusal"
        return cand

    def _explore(self, sql: str) -> str:
        """sqrl-ийн <sql> судлах асуулга: мөн guard + read-only, 20 мөр хүртэл."""
        if self.opts.dialect == "sqlite_transpile":
            sql = sqlite_to_postgres(sql, self.bool_cols(), **self._transpile_kwargs())
        g = self.guard.check(sql)
        if not g.ok:
            return f"ERROR: {g.error}"
        res = self.executor.run(g.sql, max_rows=20)
        return res.preview(n=20)

    def _search_values(self, keyword: str) -> str:
        if self.retriever is None:
            return "(value search unavailable)"
        hits = self.retriever.values.search(keyword, mode="rule", translit=self.retriever.translit, top_total=10)
        if not hits:
            return "(no matching stored values)"
        return "\n".join(f"{h.table}.{h.column} = {h.value}" for h in hits)

    # ------------------------------------------------------------------ үүсгэлт
    def _chat(self, messages, prefill, n, dec, seed) -> tuple[list[str], int, int]:
        resp = self.llm.chat(messages, n=n, max_tokens=self.max_tokens, seed=seed, prefill=prefill, **dec)
        return resp.texts, resp.prompt_tokens, resp.completion_tokens

    def _chat_gen(self, messages, prefill, n, dec, seed) -> tuple[list[str], int, int, set[int]]:
        """Эхний үүсгэлт: _chat + (rescue_truncated үед) тасарсан гаралтыг аврах. Буцаах 4-р утга: аврагдсан индекс."""
        if not self.opts.rescue_truncated:
            texts, tin, tout = self._chat(messages, prefill, n, dec, seed)
            return texts, tin, tout, set()
        resp = self.llm.chat(messages, n=n, max_tokens=self.max_tokens, seed=seed, prefill=prefill, **dec)
        texts, ti, to, rescued = self._rescue_truncated(messages, resp, seed)
        return texts, resp.prompt_tokens + ti, resp.completion_tokens + to, rescued

    def _rescue_truncated(self, messages, resp, seed) -> tuple[list[str], int, int, set[int]]:
        """R7 #5 (budget forcing, s1 — Muennighoff et al., 2025): finish_reason == "length" бөгөөд SQL задлагдахгүй
        гаралтыг RESCUE_SUFFIX («…</think><answer>```sql») prefill-ээр нэг удаа үргэлжлүүлнэ (temperature 0,
        max_tokens = RESCUE_MAX_TOKENS). Угтвар нь vLLM-ийн prefix cache-д байгаа тул нэмэлт зардал бага. Prefill
        дэмжээгүй (хариу угтваргүй) эсвэл SQL гараагүй бол анхны гаралт хэвээр."""
        choices = (resp.raw or {}).get("choices") or []
        texts, tin, tout, rescued = list(resp.texts), 0, 0, set()
        for i, text in enumerate(texts):
            reason = choices[i].get("finish_reason") if i < len(choices) and isinstance(choices[i], dict) else None
            # extract_sql нь reasoning доторх «with/select» үгийг ч SQL гэж барьдаг тул шалгуур: </think> хүрээгүй
            if reason != "length" or "</think>" in (text or ""):
                continue
            head = (text or "").rstrip() + RESCUE_SUFFIX
            try:
                r = self.llm.chat(messages, n=1, max_tokens=RESCUE_MAX_TOKENS, temperature=0.0, top_p=1.0,
                                  seed=seed, prefill=head)
            except Exception as e:  # noqa: BLE001 — аврах оролдлого амжилтгүй бол анхны (тасарсан) гаралт хэвээр
                if getattr(self.opts, "repair_strict", False) and _transient_llm_error(e):
                    # strict: дэд бүтцийн түр алдааг тасарсан гаралтаар чимээгүй орлуулахгүй — runner дахин асууна
                    raise InfraError(f"rescue_truncated: LLM дуудлага амжилтгүй: {type(e).__name__}: {e}"[:600]) from e
                log.warning("rescue_truncated: үргэлжлүүлэх дуудлага амжилтгүй: %s", str(e)[:200])
                continue
            tin, tout = tin + r.prompt_tokens, tout + r.completion_tokens
            new = r.texts[0] if r.texts else ""
            if not new.startswith(head):
                continue    # сервер prefill-ийг хүлээж аваагүй — шинэ (хамааралгүй) гаралтыг ашиглахгүй
            if "```" not in new[len(head):]:
                new = new.rstrip() + "\n```\n</answer>"    # 512 токенд хаагаагүй блокийг хаана
            if extract_sql(new):
                texts[i] = new
                rescued.add(i)
        if rescued:
            report("writer", f"{len(rescued)} тасарсан хариуг үргэлжлүүлж SQL гаргав (rescue_truncated)")
        return texts, tin, tout, rescued

    def _repair_message(self, cand: Candidate) -> str:
        if cand.error_stage == "empty":
            return ("The query executed but returned no rows. Re-check the filters and the exact stored value "
                    "formats (Cyrillic spelling, codes), then give the corrected SQL.")
        return f"The SQL query failed.\nError: {cand.error}\nFix the query and give the corrected SQL."

    def _single_turn(self, messages, prefill, text, tin, tout, seed, hits: list | None = None) -> Candidate:
        """omnisql / generic / xiyan: нэг хариу + (сонголттой) засах давталт. hits — process_sql-ийн (literal_fix)."""
        o = self.opts
        t0 = time.perf_counter()
        conv = list(messages)
        cand = self._mark_refusal(self.process_sql(extract_sql(text), hits), text)
        if getattr(o, "extract_fallback", False) and not cand.ok and cand.error_stage in ("guard", "exec"):
            # 2026-11-01: давталтад орсон гаралтын сүүлийн блок тасарсан хуулбар бол өмнөх бүтэн блок (sql_guard.extended_block)
            alt = extended_block(text, cand.raw_sql)
            if alt:
                alt_cand = self.process_sql(alt, hits)
                if alt_cand.ok:
                    cand = alt_cand
        cand.text, cand.tokens_in, cand.tokens_out = text, tin, tout
        cand.messages, cand.prefill, cand.raw_turns = list(messages), prefill, [text]
        # Татгалзалтыг засуулахгүй (SQL бичүүлэх гэж шахахгүй) — E1 нь зөвхөн алдаа ба хоосон үр дүнг засна
        while cand.repairs < o.repair_rounds and cand.error_stage != "refusal" and (
                not cand.ok or (o.repair_on_empty and cand.error_stage == "empty" and cand.repairs == 0)):
            report("repair", f"{'Хоосон үр дүн' if cand.error_stage == 'empty' else 'Алдаа'} — засуулж байна "
                             f"({cand.repairs + 1}/{o.repair_rounds}): {(cand.error or '')[:80]}")
            conv = conv + [{"role": "assistant", "content": self._repair_echo(cand)},
                           {"role": "user", "content": self._repair_message(cand) +
                            ("\nRespond in the same format, with the SQL in a ```sql code block." if True else "")}]
            try:
                texts, ti, to = self._chat(conv, prefill if self.family == "omnisql" else None, 1,
                                           self._decoding(False), seed)
            except Exception as e:  # noqa: BLE001 — засварын алдаа (жишээ нь context-ийн урт) өмнөх хариултыг хүчингүй болгохгүй
                if o.repair_strict and _transient_llm_error(e):
                    # C4: түр зуурын (сүлжээ/5xx/429) алдаа — засаагүй хувилбарыг жинхэнэ хариу гэж хадгалахгүй;
                    # error_stage "llm" = дэд бүтцийн алдаа (runner бичлэг хийхгүй, дахин ажиллуулахад асууна)
                    log.warning("засварын дуудлага амжилтгүй — хувилбарыг llm алдаа гэж тэмдэглэв: %s", str(e)[:200])
                    cand.ok, cand.result_hash = False, None
                    cand.result, cand.rows = None, 0     # L4: to_dict засварын өмнөх мөрийг хариулт болгон буцаахгүй
                    cand.error = f"Засварын LLM дуудлага амжилтгүй: {type(e).__name__}: {e}"[:600]
                    cand.error_stage = "llm"
                    break
                log.warning("засварын дуудлага амжилтгүй — өмнөх хувилбарыг үлдээв: %s", str(e)[:200])
                break
            prev = cand
            cand = self._mark_refusal(self.process_sql(extract_sql(texts[0]), hits), texts[0])
            cand.text = texts[0]
            # Засварын бүрэн яриаг хадгална (agent-ийн revise үүнийг үргэлжлүүлнэ: system, схем, асуулт, prefill)
            cand.messages, cand.prefill = list(conv), prefill
            cand.raw_turns = (prev.raw_turns or []) + [texts[0]]
            cand.repairs = prev.repairs + 1
            cand.tokens_in, cand.tokens_out = prev.tokens_in + ti, prev.tokens_out + to
            if not cand.ok and prev.ok and prev.error_stage == "empty":
                if o.repair_strict:
                    if cand.error_stage == "refusal":
                        break    # C4: засварын дараах татгалзлыг хаяхгүй
                    # C4: буцаасан ч засварын дуудлагын токен зарцуулагдсан
                    prev.tokens_in, prev.tokens_out = cand.tokens_in, cand.tokens_out
                cand = prev  # хоосон үр дүнтэй ч ажилласан хувилбарыг алдаатай шинэ хувилбараар солихгүй
                break
        cand.latency_ms += (time.perf_counter() - t0) * 1000
        return cand

    @staticmethod
    def _repair_echo(cand: Candidate, max_chars: int = 4000) -> str:
        """Засварын ярианд өмнөх хариуг богиносгож буцаана: зөвхөн гаргасан SQL (давтагдсан урт гаралт
        context-ийн хязгаараас хэтрүүлэхгүй); SQL олдоогүй бол текстийн сүүлийн хэсэг."""
        sql = (cand.raw_sql or "").strip()
        if sql:
            return f"```sql\n{sql[:max_chars]}\n```"
        return strip_think(cand.text or "")[-max_chars:]

    def _agent(self, messages: list[dict], dec: dict, seed: int | None) -> Candidate:
        """sqrl-ийн agentic протокол: <sql> судлах (≤ max_steps), <answer> эцсийн; алдаа бол observation-оор засах."""
        o = self.opts
        t0 = time.perf_counter()
        conv = list(messages)
        raw_turns: list[str] = []
        steps = 0
        tin = tout = 0
        final_text = ""
        answer_sql = None
        finish_sent = False
        for _turn in range(o.agent_max_steps + 4):
            texts, ti, to = self._chat(conv, None, 1, dec, seed)
            tin, tout = tin + ti, tout + to
            raw_turns.append(texts[0])
            content = strip_think(texts[0]).strip()
            final_text = content
            conv.append({"role": "assistant", "content": content})
            kind, body = parse_sqrl_action(content)
            if kind == "answer":
                answer_sql = body
                break
            if kind == "sql" and steps < o.agent_max_steps:
                steps += 1
                conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=self._explore(body))})
                continue
            if kind == "search_values" and o.search_values_tool and steps < o.agent_max_steps:
                steps += 1
                conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=self._search_values(body))})
                continue
            if finish_sent:
                answer_sql = extract_sql(content)
                break
            conv.append({"role": "user", "content": SQRL_FINISH})
            finish_sent = True
        cand = self._mark_refusal(self.process_sql(answer_sql), final_text)
        repairs = 0
        while repairs < o.repair_rounds and cand.error_stage != "refusal" and (
                not cand.ok or (o.repair_on_empty and cand.error_stage == "empty" and repairs == 0)):
            repairs += 1
            obs = cand.error if not cand.ok else "Query returned 0 rows (empty result)."
            conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=f"ERROR: {obs}") +
                         "\nThis was your final answer; fix it and give the corrected <answer>."})
            texts, ti, to = self._chat(conv, None, 1, dec, seed)
            tin, tout = tin + ti, tout + to
            raw_turns.append(texts[0])
            content = strip_think(texts[0]).strip()
            conv.append({"role": "assistant", "content": content})
            kind, body = parse_sqrl_action(content)
            new = self._mark_refusal(self.process_sql(body if kind == "answer" else extract_sql(content)), content)
            if not new.ok and cand.ok and not (o.repair_strict and new.error_stage == "refusal"):
                break
            cand = new
            final_text = content
        cand.repairs = repairs
        cand.agent_steps = steps
        cand.text = final_text
        cand.messages, cand.raw_turns = conv, raw_turns
        cand.tokens_in, cand.tokens_out = tin, tout
        cand.latency_ms = (time.perf_counter() - t0) * 1000
        return cand

    def generate(self, ctx: PromptContext, seed: int | None,
                 prompt: tuple[list[dict], str | None] | None = None, hits: list | None = None) -> list[Candidate]:
        """prompt: prompt_for()-оор угсарсан (messages, prefill); өгөөгүй бол ctx-ээс угсарна.
        hits: утгын хайлтын олдвор (literal_fix үед ask() дамжуулна; postproc R6)."""
        o = self.opts
        n = max(1, o.candidates)
        dec = self._decoding(o.sampling)
        messages, prefill = prompt if prompt is not None else self.initial_messages(ctx)
        if self.family == "sqrl":
            if n == 1:
                return [self._agent(messages, dec, seed)]
            with ThreadPoolExecutor(max_workers=min(8, n)) as pool:
                futs = [pool.submit(self._agent, messages, dec, (seed or 0) * 100 + i) for i in range(n)]
                return [f.result() for f in futs]
        if o.adaptive_stop and n > 1:
            return self._generate_adaptive(messages, prefill, n, dec, seed, **({"hits": hits} if hits is not None else {}))
        t0 = time.perf_counter()
        try:
            texts, tin, tout, rescued = self._chat_gen(messages, prefill, n, dec, seed)
        except RuntimeError as e:
            return [Candidate(error=str(e), error_stage="llm")]
        gen_ms = (time.perf_counter() - t0) * 1000
        report("writer", f"{len(texts)} хариу ирлээ ({gen_ms / 1000:.0f} с, {tout} токен) — SQL-ийг DB дээр ажиллуулж байна")
        share_in, share_out = tin // max(1, len(texts)), tout // max(1, len(texts))
        return _mark_rescued(self._repair_chains(messages, prefill, texts, share_in, share_out, seed, gen_ms,
                                                 totals=(tin, tout), hits=hits), rescued)

    def _warm_caches(self) -> None:
        """Залхуу кэшүүдийг (bool_cols, postproc, literal checker) үндсэн thread-д урьдчилан үүсгэнэ — зэрэгцээ
        засварын thread-үүд нэг объектыг давхар үүсгэх уралдаанаас сэргийлнэ."""
        if self.opts.dialect == "sqlite_transpile":
            self.bool_cols()
        if self.opts.postproc:
            self.postprocessor()
        if self.opts.vote_filter:
            self.literal_checker()

    def _repair_chains(self, messages, prefill, texts: list[str], share_in: int, share_out: int,
                       seed: int | None, gen_ms: float, totals: tuple[int, int] | None = None,
                       hits: list | None = None) -> list[Candidate]:
        """Хувилбар бүрийг гүйцэтгэж, алдаатай/хоосон бол засуулна (_single_turn). repair_workers = 1 бол
        дараалсан (анхны зан төлөв, byte-ижил). > 1 бол гинж бүр дотроо дараалсан хэвээр, гинжүүд зэрэг
        (хязгаартай ThreadPoolExecutor); үр дүн хувилбарын анхны дарааллаар — санал хураалт, тэнцээг шийдэх
        дүрэм өөрчлөгдөхгүй. Executor нь pool-оос дуудлага бүрт тусдаа холболт авна, httpx.Client thread-safe."""
        k = max(1, len(texts))
        shares = [(share_in, share_out)] * len(texts)
        if totals is not None and getattr(self.opts, "repair_strict", False):
            # C4: tin // k-ийн үлдэгдлийг эхний хувилбаруудад — хувилбаруудын токены нийлбэр = дуудлагын нийлбэр
            shares = [(totals[0] // k + (i < totals[0] % k), totals[1] // k + (i < totals[1] % k))
                      for i in range(len(texts))]

        hit_kw = {"hits": hits} if hits is not None else {}   # анхдагч (literal_fix off): дуудлага хуучинтай ижил

        def one(i: int, text: str) -> Candidate:
            c = self._single_turn(messages, prefill, text, shares[i][0], shares[i][1], seed, **hit_kw)
            c.latency_ms += gen_ms / k
            return c

        workers = min(max(1, int(self.opts.repair_workers or 1)), len(texts))
        if workers <= 1:
            return [one(i, t) for i, t in enumerate(texts)]
        self._warm_caches()
        cb = getattr(_PROGRESS, "cb", None)   # явцын callback thread-local тул ажлын thread бүрт дамжуулна

        def guarded(i: int, text: str) -> Candidate:
            set_progress(cb)
            try:
                return one(i, text)
            except Exception as e:  # noqa: BLE001 — нэг гинжний алдаа бусад гинжийг унагахгүй
                log.warning("засварын гинж тасарлаа — хувилбарыг алдаатай гэж тэмдэглэв: %s", str(e)[:200])
                return Candidate(text=text, error=f"Засварын гинж тасарсан: {type(e).__name__}: {e}"[:600],
                                 error_stage="llm", tokens_in=shares[i][0], tokens_out=shares[i][1],
                                 latency_ms=gen_ms / k)
            finally:
                set_progress(None)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="repair") as pool:
            futs = [pool.submit(guarded, i, t) for i, t in enumerate(texts)]
            return [f.result() for f in futs]

    def _generate_adaptive(self, messages, prefill, n: int, dec: dict, seed: int | None,
                           hits: list | None = None) -> list[Candidate]:
        """adaptive_stop (eval/adaptive.py): хувилбарыг vLLM давалгаагаар (n=w) үүсгэж, санал хангалттай
        нийлбэл зогсоно. "g+" угтвартай бол эхэнд нэг greedy хувилбар. Давалгаа бүрт өөр seed (ижил seed-тэй
        n=w дуудлага ижил түүвэр буцаана)."""
        from ..eval.adaptive import AdaptiveStopper
        stopper = AdaptiveStopper(self.opts.adaptive_stop, n, self.opts.vote_filter)
        plan = ([(1, self._decoding(False))] if stopper.policy.greedy else []) + [(w, dec) for w in stopper.waves]
        cands: list[Candidate] = []
        drawn = 0   # түүвэрлэсэн (greedy биш) хувилбарын тоо
        for i, (w, d) in enumerate(plan):
            t0 = time.perf_counter()
            # vLLM n>1 үед хүүхэд j нь seed+j-ээр түүвэрлэнэ — давалгааны seed-ийг өмнөх түүврийн тоогоор шилжүүлбэл
            # давалгаанууд нэг n=8 дуудлагын угтвар болж давхцахгүй (өмнө нь (seed+1)*1000+i — дараалсан seed давхцдаг байв)
            wseed = seed if (seed is None or d.get("temperature", 0) == 0) else seed + drawn
            if d.get("temperature", 0) != 0:
                drawn += w
            try:
                texts, tin, tout, rescued = self._chat_gen(messages, prefill, w, d, wseed)
            except RuntimeError as e:
                if cands and not (self.opts.repair_strict and _transient_llm_error(e)):
                    break        # өмнөх давалгааны хувилбараар санал хураана
                # эхний давалгаа, эсвэл (C4 L6, repair_strict) дараагийн давалгааны түр зуурын алдаа: дутуу хувилбараар
                # санал хураахгүй — дэд бүтцийн алдаа (runner хадгалахгүй, дахин ажиллуулахад асууна)
                return [Candidate(error=str(e), error_stage="llm")]
            gen_ms = (time.perf_counter() - t0) * 1000
            report("writer", f"давалгаа {i + 1}: {len(texts)} хариу ({gen_ms / 1000:.0f} с, {tout} токен)")
            share_in, share_out = tin // max(1, len(texts)), tout // max(1, len(texts))
            wave = _mark_rescued(self._repair_chains(messages, prefill, texts, share_in, share_out, wseed, gen_ms,
                                                     totals=(tin, tout), hits=hits), rescued)
            cands += wave
            if stopper.done(cands, wave):
                report("vote", f"{len(cands)} хувилбарт санал хангалттай нийлсэн — эрт зогсов")
                break
        return cands

    # ------------------------------------------------------------------ сонголт
    @staticmethod
    def vote(cands: list[Candidate], use_filter: bool = False, use_lint: bool = False) -> tuple[Candidate, float, bool]:
        good = [c for c in cands if c.ok and c.result_hash]
        if not good:
            return cands[0], 0.0, False
        if use_filter:
            # E4: хоосон/0/NULL ба DB-д байхгүй утгатай хувилбарыг хасаад (бүгд ийм бол хэвээр) олонхоор
            good = [c for c in good if not c.degenerate and not c.bad_literals] or good
        if use_lint:
            # vote_lint: fan/chasm ба KPI-ийн шалгалтад унасныг хасна (бүгд унасан бол хэвээр) — sql_lint.py
            good = [c for c in good if not c.lint_flags] or good
        counts = Counter(c.result_hash for c in good)
        best_hash, best_n = max(counts.items(), key=lambda kv: (kv[1], -min(i for i, c in enumerate(cands)
                                                                          if c.result_hash == kv[0])))
        chosen = next(c for c in good if c.result_hash == best_hash)
        conf = best_n / len(cands)
        return chosen, conf, best_n == len(cands)

    # ------------------------------------------------------------------ гол функц
    def ask(self, question: str, variant: str | None = None, item_id: str | None = None,
            seed: int | None = None) -> Answer:
        o = self.opts
        variant = variant or o.variant
        t0 = time.perf_counter()
        ans = Answer(question=question, variant=variant, run_id=o.run_id, model=self.model_name)
        if getattr(o, "kpi_fix", False):
            # kpi_fix: process_sql-д асуултыг (монгол) thread-local-оор — вэбийн зэрэгцээ хүсэлтэд холилдохгүй
            if not isinstance(getattr(self, "_kpi_tls", None), threading.local):
                self._kpi_tls = threading.local()
            self._kpi_tls.question = question
        if o.intent_check:
            report("intent", "Хариулах боломжтой эсэхийг шалгаж байна")
            from ..knowledge.semantic import load_glossary
            from .intent_check import check_question
            if not hasattr(self, "_glossary"):
                self._glossary = load_glossary(self.cfg["knowledge"]["glossary"])
            if getattr(o, "intent_rules", "legacy") == "v2":
                # H1 (2026-10-31): мэдлэгийн сангийн шошгыг маскжуулж, бичих хүсэлтийн хэлбэр (pipeline/intent_check.py)
                if getattr(self, "_intent_masker", None) is None:
                    from .intent_check import build_masker
                    self._intent_masker = build_masker(self._glossary, getattr(self, "model", None))
                dec = check_question(question, self._glossary, rules="v2", masker=self._intent_masker)
            else:
                dec = check_question(question, self._glossary)
            if dec.action != "answer" and (not o.intent_refuse_only or dec.action == "refuse"):
                ans.status = "refused" if dec.action == "refuse" else "clarify"
                ans.explanation_mn = dec.message_mn
                ans.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                return ans
        if o.rule_refuse or o.rule_clarify:
            report("rules", "Дүрмээр шүүж байна")
            from .selection import CLARIFY_MN, REFUSE_MN, rule_decision_for
            # v2: кэштэй англи орчуулга (байвал) — загварт өгөх асуулттай ижил эх
            q_en = (self.translations.get(f"{item_id}:{variant}") if (o.translate and o.rules_version == "v2")
                    else None)
            rd = rule_decision_for(o.rules_version, question, q_en, o.rule_refuse, o.rule_clarify)
            if rd is not None:
                ans.status = "refused" if rd[0] == "refuse" else "clarify"
                ans.explanation_mn = REFUSE_MN.get(rd[1], "") if rd[0] == "refuse" else CLARIFY_MN
                ans.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                return ans
        q, q_prompt = self.question_forms(question, variant, item_id)
        ans.question_used = q_prompt or q
        if o.semantic_router and self.router is not None:
            routed = self.router.route(question, item_id=item_id, variant=variant)
            if routed is not None and routed.sql:
                cand = self.process_sql(routed.sql)
                cand.text = routed.json
                ans.routed = True
                ans.candidates = [cand]
                return self._finish(ans, cand, 1.0, True, t0)
        t1 = time.perf_counter()
        report("retrieve", "RAG: хүснэгт, DB-ийн утга, нэр томьёо, жишээ хайж prompt угсарч байна")
        # Сургалтын prompt (training/sampling.py, D3) мөн prompt_for-оор угсрагдана — ижил байх баталгаа
        try:
            messages, prefill, ctx, info, _ = self.prompt_for(question, variant, item_id)
        except InfraError as e:
            # C4 L6 (зөвхөн repair_strict): хуваарилагчийн LLM алдаа — дэд бүтцийн алдаа (runner хадгалахгүй, дахин асууна)
            cand = Candidate(error=str(e), error_stage="llm")
            ans.candidates = [cand]
            return self._finish(ans, cand, 0.0, False, t0)
        report("retrieve", f"{len(info.get('linked_tables') or [])} хүснэгт, {len(info.get('values') or [])} утга, "
                           f"{len(info.get('glossary') or [])} нэр томьёо, {len(info.get('examples') or [])} жишээ", "done")
        prompt: tuple[list[dict], str | None] | None = (messages, prefill)
        ans.retrieval = info
        ans.intent_json = ctx.intent_json
        ans.timings["context_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        agent = plan_tokens = None
        if o.agent:
            from .agent import AgentLoop
            if getattr(self, "agent_loop", None) is None:
                self.agent_loop = AgentLoop(self)     # Pipeline бүрт нэг удаа (agent_llm-ийн client-ийг дахин үүсгэхгүй)
            agent = self.agent_loop
            if o.agent_plan:
                report("planner", "SQL-ийн төлөвлөгөө гаргаж байна")
                tp = time.perf_counter()
                plan, pti, pto = agent.plan(ctx)
                plan_tokens = (pti, pto)
                if plan:
                    ctx.evidence_lines.append("Plan: (draft from an analysis step; follow it unless the schema "
                                              "says otherwise)\n" + plan)
                    prompt = None          # төлөвлөгөө ctx-д нэмэгдсэн тул prompt-ыг ctx-ээс дахин угсарна
                ans.retrieval["plan"] = plan
                ans.timings["plan_ms"] = round((time.perf_counter() - tp) * 1000, 1)
        t2 = time.perf_counter()
        report("writer", f"Arctic SQL бичиж байна ({max(1, o.candidates)} хувилбар) — T4 дээр хэдэн арван секунд")
        if getattr(o, "literal_fix", False) and info.get("values"):
            # literal_fix (2026-11-01): postproc R6-д энэ асуултын утгын хайлтын олдвор (prompt-д өгсөнтэй ижил)
            cands = self.generate(ctx, seed, prompt, hits=[tuple(v) for v in info["values"]])
        else:
            cands = self.generate(ctx, seed, prompt)
        ans.timings["generate_ms"] = round((time.perf_counter() - t2) * 1000, 1)
        ans.candidates = cands
        if len(cands) > 1:
            report("vote", f"{len(cands)} хувилбараас үр дүнгээр нь санал хурааж байна")
            if getattr(o, "vote_lint", False):
                from .sql_lint import child_map, label_codes, lint_sql
                if not hasattr(self, "_lint_children"):
                    self._lint_children = child_map(self.model)
                    self._lint_labels = label_codes(self.model)
                for c in cands:
                    c.lint_flags = sorted(lint_sql(question, c.sql, self._lint_children, self._lint_labels)) or None
        chosen, conf, unanimous = (self.vote(cands, o.vote_filter, getattr(o, "vote_lint", False)) if len(cands) > 1
                                   else (cands[0], 1.0, True))
        if agent is not None and chosen.error_stage != "llm":
            report("observe", "Agent: үр дүнг шалгаж, шаардлагатай бол засуулж байна")
            ta = time.perf_counter()
            refined = agent.refine(ctx, chosen, seed)
            if plan_tokens:
                refined.tokens_in += plan_tokens[0]
                refined.tokens_out += plan_tokens[1]
            idx = next(i for i, c in enumerate(cands) if c is chosen)
            cands[idx] = refined
            if len(cands) > 1 and refined is not chosen:
                # Засварласан SQL-ийн дэмжлэгийг дахин тооцно: E3-ийн татгалзалт хариулж буй SQL-д хамаарна
                hashes = [c.result_hash for c in cands if c.ok and c.result_hash]
                conf = (hashes.count(refined.result_hash) / len(cands)) if refined.result_hash else 0.0
                unanimous = conf == 1.0
            chosen = refined
            ans.timings["agent_ms"] = round((time.perf_counter() - ta) * 1000, 1)
        return self._finish(ans, chosen, conf, unanimous, t0)

    def _finish(self, ans: Answer, chosen: Candidate, conf: float, unanimous: bool, t0: float) -> Answer:
        o = self.opts
        ans.chosen_index = next((i for i, c in enumerate(ans.candidates) if c is chosen), None)
        ans.sql = chosen.sql or chosen.raw_sql
        ans.result = chosen.result
        ans.confidence = round(conf, 3)
        ans.unanimous = unanimous
        if not chosen.ok:
            ans.status = "refused" if chosen.error_stage == "refusal" else "error"
        if o.abstain_on_error and ans.status == "error":
            ans.status = "abstained"   # H: алдаатай SQL-ийг хариулт болгон өгөхгүй (TrustSQL: татгалзал)
        if o.vote_filter and ans.status == "answered" and (chosen.degenerate or chosen.bad_literals):
            ans.status = "abstained"   # E4: шүүлтийн дараа ч хоосон/0 эсвэл DB-д байхгүй утгатай — итгэлгүй
        if o.rule_refuse and ans.status == "answered":
            from .selection import is_fromless
            if is_fromless(ans.sql):
                ans.status = "refused"   # E4: хүснэгтгүй SELECT (тогтмол утга таасан)
        if o.abstain_threshold is not None and conf < float(o.abstain_threshold) and ans.status != "refused":
            # Хувилбарууд санал нийлэхгүй бол (олонх үгүй) буруу хариулт өгөхийн оронд татгалзах эсвэл тодруулга асуух
            ans.status = "clarify" if o.low_conf_action == "clarify" else "abstained"
        ans.prompt_tokens = sum(c.tokens_in for c in ans.candidates)
        ans.completion_tokens = sum(c.tokens_out for c in ans.candidates)
        ans.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        ans.explanation_mn = explain_mn(ans, chosen)
        return ans


def _mark_rescued(cands: list[Candidate], rescued: set[int]) -> list[Candidate]:
    for i in rescued:
        if i < len(cands):
            cands[i].rescued = True
    return cands


def _transient_llm_error(e: Exception) -> bool:
    """C4 (repair_strict): LLM дуудлагын алдаа түр зуурынх уу — дахин ажиллуулахад засагдах (429, 5xx, timeout, холболт).
    L3: HTTP кодоор (LLMCallError.status; хуучин RuntimeError бол мессежийн «Client error 'NNN»): 400/404/413/422 нь
    тогтвортой (context-ийн урт г.м.) — тэр үед өмнөх хувилбар/хайлтын top-k хэвээр (llm/client.py is_transient)."""
    return is_transient(e)


def explain_mn(ans: Answer, cand: Candidate) -> str:
    """Асуултыг хэрхэн ойлгосныг монголоор (зааврын 7-р хэсэг: "Асуултыг хэрхэн ойлгосныг монголоор тайлбарлаж")."""
    if ans.status == "abstained" and not cand.ok:
        return f"SQL ажиллуулах үед алдаа гарсан тул хариулахаас татгалзав: {cand.error}"
    if ans.status == "abstained":
        return (f"Хувилбаруудын санал нийлэлт бага (итгэл {ans.confidence or 0:.0%}) тул хариултыг баталгаажуулах боломжгүй — "
                "шинжээчид шилжүүлэв.")
    if ans.status == "clarify" and len(ans.candidates) > 1:
        return clarify_mn(ans)
    if ans.status == "refused":
        return "Загвар энэ асуултад SQL бичээгүй: өгөгдлийн сангаас хариулах боломжгүй гэж үзэв."
    if not cand.ok:
        return f"SQL ажиллуулах үед алдаа гарсан: {cand.error}"
    parts = []
    if cand.tables:
        parts.append("Ашигласан хүснэгт: " + ", ".join(cand.tables))
    vals = ans.retrieval.get("values") if ans.retrieval else None
    if vals:
        parts.append("Таарсан утга: " + ", ".join(f"{v[2]} ({v[0]}.{v[1]})" for v in vals[:4]))
    gl = ans.retrieval.get("glossary") if ans.retrieval else None
    if gl:
        parts.append("Нэр томьёо: " + ", ".join(gl[:4]))
    parts.append(f"Үр дүн: {cand.rows} мөр")
    return ". ".join(parts) + "."


def clarify_mn(ans: Answer, top: int = 2) -> str:
    """Санал зөрсөн үед (I бүлэг) тодруулах асуулт: хамгийн олон санал авсан үр дүнгийн бүлгүүдийг харуулж аль нь болохыг асууна."""
    groups: dict[str, list[Candidate]] = {}
    for c in ans.candidates:
        if c.ok and c.result_hash:
            groups.setdefault(c.result_hash, []).append(c)
    ranked = sorted(groups.values(), key=lambda g: -len(g))[:top]
    if len(ranked) < 2:
        return (f"Асуултыг найдвартай ойлгож чадсангүй (хувилбаруудын санал нийлэлт {ans.confidence or 0:.0%}). Хугацаа, шүүлт эсвэл "
                "хэмжигдэхүүнээ тодруулж дахин асууна уу.")
    parts = []
    for i, g in enumerate(ranked, 1):
        c = g[0]
        preview = ""
        if c.result is not None and c.result.rows:
            preview = ", ".join(str(v) for v in c.result.rows[0][:3])
        parts.append(f"{i}) {', '.join(c.tables) or 'SQL'}: {c.rows} мөр" + (f" (эхний мөр: {preview})" if preview else ""))
    return (f"Асуултыг хэд хэдэн янзаар ойлгож болохоор байна (санал нийлэлт {ans.confidence or 0:.0%}). Аль утгаар нь асууж байгааг "
            "тодруулна уу: " + "; ".join(parts) + ".")


__all__ = ["Pipeline", "Answer", "Candidate", "explain_mn", "clarify_mn", "OMNISQL_TEMPLATE", "ARCTIC_PREFILL"]
