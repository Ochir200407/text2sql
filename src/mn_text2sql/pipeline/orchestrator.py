"""Text2SQL-ийн бүрэн урсгал (зааврын 1-р хэсэг, зураг "6 алхам, 2 давталт").

Асуулт → хэвийн болгох/intent → хайлт (RAG) → SQL үүсгэх (загварын протокол) → шалгах ба гүйцэтгэх
(sqlglot guard → EXPLAIN → read-only) → алдаа бол 1–3 удаа засах → (сонголттой) олон хувилбар,
санал хураалт = итгэл → хариулт (SQL, үр дүн, монгол тайлбар).
"""

from __future__ import annotations

import logging
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
from ..llm.client import LLM, MockLLM, make_llm
from ..llm.prompts import (
    ARCTIC_PREFILL,
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
from ..security.sql_guard import SqlGuard, extract_sql, is_refusal
from .dialect import sqlite_to_postgres
from .options import RunOptions

log = logging.getLogger(__name__)


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
                                       set(cfg["security"]["forbid_functions"]), str(cfg["project"]["as_of_date"]))
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
                                          want_examples=o.fewshot_k > 0, exclude_families={fam} if fam else None)
            info = {"linked_tables": ret.linked_tables, "values": [(h.table, h.column, h.value, h.score)
                                                                    for h in ret.values],
                    "glossary": [g.id for g in ret.glossary], "examples": [e.id for e in ret.examples],
                    "retrieval_ms": round(ret.ms, 1), "question_cyr": ret.question_cyr}
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
                                               want_glossary=False, want_examples=False)
            ranked = [t for t, _ in sorted(r.tables.items(), key=lambda kv: -kv[1]["score"])]
            relevant = list(dict.fromkeys(list(r.linked_tables) + ranked))
        ctx = PromptContext(question=question, tables=tables, columns=columns, describe=o.describe,
                            evidence_lines=lines, examples=examples, dialect=o.dialect, pivot=o.pivot,
                            intent_json=self.intents.get(f"{item_id}:{variant}") if o.intent else None,
                            max_steps=o.agent_max_steps, search_values_tool=o.search_values_tool,
                            relevant_tables=relevant, describe_columns=describe_cols)
        info["n_tables"] = len(tables)
        if _out is not None:
            _out["ret"] = ret
        return ctx, info

    def schema_text(self, ctx: PromptContext) -> str:
        if self.family == "xiyan":
            return render_mschema(self.model, ctx.tables, self.types, self.col_examples, ctx.describe, ctx.columns)
        dcols = ctx.describe_columns
        text = render_ddl(self.model, ctx.tables, self.types, self.col_examples, ctx.describe, ctx.columns,
                          describe_columns=dcols)
        budget = int(self.mspec.get("schema_token_budget") or self.cfg["retrieval"]["schema_token_budget"])
        if ctx.describe and est_tokens(text) > budget:
            # Төсвөөс хэтэрвэл бүх хүснэгт DDL-д үлдэнэ (A1-тэй ижил), харин баганын тайлбарыг асуулттай хамгийн
            # их холбоотой хүснэгтүүдэд л өгнө — хамаарлын эрэмбээр төсөвт багтах хүртэл нэмнэ.
            ranked = list(dict.fromkeys(ctx.relevant_tables)) or list(ctx.tables)
            best = render_ddl(self.model, ctx.tables, self.types, self.col_examples, True, ctx.columns,
                              describe_tables=set(), describe_columns=dcols)
            for k in range(1, len(ranked) + 1):
                cand = render_ddl(self.model, ctx.tables, self.types, self.col_examples, True, ctx.columns,
                                  describe_tables=set(ranked[:k]), describe_columns=dcols)
                if est_tokens(cand) > budget:
                    break
                best = cand
            text = best
        return text

    def question_forms(self, question: str, variant: str, item_id: str | None) -> tuple[str, str | None]:
        """A4 орчуулгын горимууд → (retrieval/build_context-д өгөх асуулт, загварт харуулах асуулт эсвэл None).
        replace: хоёуланд англи орчуулга; bilingual: retrieval монголоор, загварт монгол + англи мөр; en_prompt:
        retrieval монголоор, загварт зөвхөн англи. Орчуулга алга (кэшгүй) бол эх асуулт хэвээр."""
        o = self.opts
        q, q_prompt = question, None
        if o.translate:
            en = self.translations.get(f"{item_id}:{variant}")
            if en and o.translate_mode == "bilingual":
                # retrieval нь эх (монгол) асуултаар; загварт монгол асуулт + англи орчуулгын мөр
                q_prompt = f"{question}\n(English translation: {en})"
            elif en and o.translate_mode == "en_prompt":
                # retrieval нь эх (монгол) асуултаар (англи текстийг кирилл болгож хайхгүй); загварт зөвхөн англи
                q_prompt = en
            elif en:
                q = en
        return q, q_prompt

    def prompt_for(self, question: str, variant: str | None = None, item_id: str | None = None,
                   _out: dict | None = None) -> tuple[list[dict], str | None, PromptContext, dict, str]:
        """ask()-ийн үүсгэлтээс өмнөх бүх алхам — prompt-ын ЦОРЫН ГАНЦ эх сурвалж (үнэлгээ ба сургалт, D3):
        орчуулгын горим (en_prompt г.м.), build_context (item_families-ээр өөрийн family-г few-shot-оос хасна),
        initial_messages. Буцаах: (messages, prefill, ctx, retrieval info, загварт харуулсан асуулт)."""
        variant = variant or self.opts.variant
        q, q_prompt = self.question_forms(question, variant, item_id)
        ctx, info = self.build_context(q, variant, item_id, _out)
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
    def process_sql(self, raw_sql: str | None) -> Candidate:
        cand = Candidate(raw_sql=raw_sql)
        if not raw_sql:
            cand.error, cand.error_stage = "Загварын хариунаас SQL олдсонгүй.", "extract"
            return cand
        sql = raw_sql.strip().rstrip(";")
        if self.opts.dialect == "sqlite_transpile":
            sql = sqlite_to_postgres(sql, self.bool_cols())   # хөрвүүлэх боломжгүй бол эх SQL-ээрээ үлдэнэ
        if self.opts.postproc:
            sql, _rules = self.postprocessor().apply(sql)
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
            sql = sqlite_to_postgres(sql, self.bool_cols())
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

    def _repair_message(self, cand: Candidate) -> str:
        if cand.error_stage == "empty":
            return ("The query executed but returned no rows. Re-check the filters and the exact stored value "
                    "formats (Cyrillic spelling, codes), then give the corrected SQL.")
        return f"The SQL query failed.\nError: {cand.error}\nFix the query and give the corrected SQL."

    def _single_turn(self, messages, prefill, text, tin, tout, seed) -> Candidate:
        """omnisql / generic / xiyan: нэг хариу + (сонголттой) засах давталт."""
        o = self.opts
        t0 = time.perf_counter()
        conv = list(messages)
        cand = self._mark_refusal(self.process_sql(extract_sql(text)), text)
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
                log.warning("засварын дуудлага амжилтгүй — өмнөх хувилбарыг үлдээв: %s", str(e)[:200])
                break
            prev = cand
            cand = self._mark_refusal(self.process_sql(extract_sql(texts[0])), texts[0])
            cand.text = texts[0]
            # Засварын бүрэн яриаг хадгална (agent-ийн revise үүнийг үргэлжлүүлнэ: system, схем, асуулт, prefill)
            cand.messages, cand.prefill = list(conv), prefill
            cand.raw_turns = (prev.raw_turns or []) + [texts[0]]
            cand.repairs = prev.repairs + 1
            cand.tokens_in, cand.tokens_out = prev.tokens_in + ti, prev.tokens_out + to
            if not cand.ok and prev.ok and prev.error_stage == "empty":
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
            if not new.ok and cand.ok:
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
                 prompt: tuple[list[dict], str | None] | None = None) -> list[Candidate]:
        """prompt: prompt_for()-оор угсарсан (messages, prefill); өгөөгүй бол ctx-ээс угсарна."""
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
        t0 = time.perf_counter()
        try:
            texts, tin, tout = self._chat(messages, prefill, n, dec, seed)
        except RuntimeError as e:
            return [Candidate(error=str(e), error_stage="llm")]
        gen_ms = (time.perf_counter() - t0) * 1000
        report("writer", f"{len(texts)} хариу ирлээ ({gen_ms / 1000:.0f} с, {tout} токен) — SQL-ийг DB дээр ажиллуулж байна")
        share_in, share_out = tin // max(1, len(texts)), tout // max(1, len(texts))
        cands = []
        for text in texts:
            c = self._single_turn(messages, prefill, text, share_in, share_out, seed)
            c.latency_ms += gen_ms / max(1, len(texts))
            cands.append(c)
        return cands

    # ------------------------------------------------------------------ сонголт
    @staticmethod
    def vote(cands: list[Candidate], use_filter: bool = False) -> tuple[Candidate, float, bool]:
        good = [c for c in cands if c.ok and c.result_hash]
        if not good:
            return cands[0], 0.0, False
        if use_filter:
            # E4: хоосон/0/NULL ба DB-д байхгүй утгатай хувилбарыг хасаад (бүгд ийм бол хэвээр) олонхоор
            good = [c for c in good if not c.degenerate and not c.bad_literals] or good
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
        if o.intent_check:
            report("intent", "Хариулах боломжтой эсэхийг шалгаж байна")
            from ..knowledge.semantic import load_glossary
            from .intent_check import check_question
            if not hasattr(self, "_glossary"):
                self._glossary = load_glossary(self.cfg["knowledge"]["glossary"])
            dec = check_question(question, self._glossary)
            if dec.action != "answer" and (not o.intent_refuse_only or dec.action == "refuse"):
                ans.status = "refused" if dec.action == "refuse" else "clarify"
                ans.explanation_mn = dec.message_mn
                ans.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                return ans
        if o.rule_refuse or o.rule_clarify:
            report("rules", "Дүрмээр шүүж байна")
            from .selection import CLARIFY_MN, REFUSE_MN, rule_decision
            rd = rule_decision(question, o.rule_refuse, o.rule_clarify)
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
        messages, prefill, ctx, info, _ = self.prompt_for(question, variant, item_id)
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
        cands = self.generate(ctx, seed, prompt)
        ans.timings["generate_ms"] = round((time.perf_counter() - t2) * 1000, 1)
        ans.candidates = cands
        if len(cands) > 1:
            report("vote", f"{len(cands)} хувилбараас үр дүнгээр нь санал хурааж байна")
        chosen, conf, unanimous = self.vote(cands, o.vote_filter) if len(cands) > 1 else (cands[0], 1.0, True)
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
