"""G бүлэг: agentic урсгал — thesis-ийн Planner → Writer → Critic санааг шинэ системийн guard / executor / retrieval дээр.

Нэг асуултын алхам (docs/AGENT.md):
  1. plan   (opts.agent_plan)  — загвар SQL бичихээс өмнө англи товч төлөвлөгөө гаргана (хүснэгт, join, шүүлт, агрегат,
                                 эрэмбэ). Төлөвлөгөө нь writer-ийн prompt-ын evidence хэсэгт орно. SQL биш.
  2. write                     — тухайн загварын ӨӨРИЙН протоколоор (omnisql / sqrl / generic) SQL; E1-ийн засах давталт,
                                 E2-ийн санал хураалт хэвээр ажиллана (agent нь сонгогдсон хувилбар дээр).
  3. observe (tool, LLM биш)   — гүйцэтгэлийн алдаа (хатуу); хоосон / бүгд NULL (зөөлөн — critic-д л дамжина);
                                 литерал шалгалт: WHERE/IN/LIKE-ийн текст утга тухайн баганад хадгалагдсан эсэх; байхгүй
                                 бол тухайн баганын хамгийн ойр хадгалсан утгууд (opts.agent_literal_check).
  4. critic (opts.agent_critic) — асуулт + SQL + үр дүнгийн preview + tool-ийн олдвор → «VERDICT: OK» эсвэл
                                 «VERDICT: REVISE - …». LLM нь writer-тай ижил загвар (эсвэл opts.agent_llm).
  5. revise                    — олдвор/critic-ийн санал байвал writer-т өгч шинэ SQL (≤ opts.agent_rounds). Шинэ
                                 хувилбар гүйцэтгэгдэхгүй, эсвэл ажилласан үр дүнг хоосон болговол өмнөхийг хадгална.

Загварын бүх SQL (sqrl-ийн судлах асуулга ч) guard → read-only executor-оор дамжина. Литерал tool-ийн асуулга нь
guard-аар дамжихгүй: semantic model-д бүртгэлтэй хүснэгт/текст баганын нэр ба '' escape хийсэн утгаар кодоос
бүрдэнэ (bot role read-only). Хувийн мэдээллийн (pii) баганад хадгалсан утгыг санал болгохгүй.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from ..llm.prompts import SQRL_OBSERVATION, PromptContext, parse_sqrl_action, strip_think
from ..security.sql_guard import extract_sql

log = logging.getLogger(__name__)

VERDICT = re.compile(r"VERDICT\s*:\s*(OK|REVISE)\b\s*[-:–]?\s*(.*)", re.I)

PLAN_PROMPT = """You are planning a SQL query. Do NOT write SQL.

Database engine: {engine}. Business date (today): {today}.
Schema:
{schema}
{notes}
Question: {question}

Write a short plan in English (at most 8 bullet lines):
- tables to use and how to join them (join keys),
- filters (with the exact stored values if they are listed above),
- grouping / aggregation,
- ordering / limit,
- the output columns the question asks for."""

CRITIC_PROMPT = """You are a careful reviewer of SQL queries for a {engine} database.

Business date (today): {today}.
Relevant schema:
{schema}
{notes}
Question: {question}

SQL:
```sql
{sql}
```

Execution result: {result}

Tool findings:
{findings}

Check whether the SQL answers the question exactly: the right tables and joins, filters (values, dates, \
inclusive ranges), grouping, aggregation, DISTINCT, ordering/limit, and that it returns the columns the question asks \
for. Do not ask for cosmetic changes (aliases, formatting, column order). Think briefly, then end with exactly one line:
VERDICT: OK
or
VERDICT: REVISE - <one sentence: what must change>"""

REVISE_MSG = ("Review of your query:\n{feedback}\n\nRevise the SQL to fix these problems (keep what is correct). "
              "Respond in the same format, with the SQL in a ```sql code block.")
SQRL_REVISE = "\nThis was your final answer; fix it and give the corrected <answer>."

TEXT_TYPES = {"text", "character varying", "character", "varchar", "char"}


@dataclass
class Finding:
    kind: str          # exec_error | empty | all_null | literal_missing | no_sql
    text: str
    hard: bool = True  # hard = critic-ээс үл хамааран засуулна


@dataclass
class AgentStep:
    round: int
    sql: str | None
    ok: bool
    rows: int
    findings: list[str] = field(default_factory=list)
    verdict: str | None = None       # OK | REVISE | UNPARSED | ERROR | None (critic дуудаагүй)
    issue: str | None = None
    accepted: bool = True


def _engine(dialect: str) -> str:
    return "PostgreSQL" if dialect == "postgres_prompt" else "SQLite"


def _meaningful_rows(res) -> bool:
    """Үр дүн «утгатай» эсэх: хоосон биш, мөн агрегатын ганц 0/NULL мөр биш (count(*) = 0 нь шүүлт юу ч олоогүй гэсэн үг)."""
    if res is None or not res.ok or res.rowcount == 0:
        return False
    if res.rowcount == 1 and res.rows and all(v is None or v == 0 for v in res.rows[0]):
        return False
    return True


def clean_reasoning(text: str) -> str:
    """Reasoning загварын гаралтаас дүгнэлтийн хэсгийг: <think>…</think>-ийг хасна; дуусаагүй <think> (токены
    хязгаарт тасарсан) бол түүнээс хойшхийг хасна (заавраа давтсан текстийг VERDICT гэж андуурахгүйн тулд);
    <answer>…</answer> байвал түүний доторхыг авна."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    if "<think>" in text:
        text = text.split("<think>")[0]
    m = re.search(r"<answer>(.*?)(?:</answer>|$)", text, flags=re.S)
    return (m.group(1) if m else text).strip()


class AgentLoop:
    """Pipeline бүрт нэг удаа үүснэ (Pipeline.agent_loop)."""

    def __init__(self, pipe):
        self.pipe = pipe
        self.opts = pipe.opts
        self.llm = pipe.llm
        self.max_tokens = max(1024, int(getattr(pipe, "max_tokens", 2048)))   # reasoning загварын <think> багтана
        if self.opts.agent_llm:
            import os

            from ..config import load_models
            from ..llm.client import make_llm
            spec = load_models()["models"].get(self.opts.agent_llm, {"served_name": self.opts.agent_llm})
            llm_cfg = dict(pipe.cfg["llm"], model=spec.get("served_name", self.opts.agent_llm))
            if os.environ.get("T2S_AGENT_BASE_URL"):
                llm_cfg["base_url"] = os.environ["T2S_AGENT_BASE_URL"]
            if spec.get("extra_body"):
                llm_cfg["extra_body"] = spec["extra_body"]
            self.llm = make_llm(llm_cfg)
            self.max_tokens = int(spec.get("max_tokens", self.max_tokens))
        self._col_types: dict[tuple[str, str], str] | None = None

    # ------------------------------------------------------------------ контекст
    def _schema(self, tables: list[str]) -> str:
        m = self.pipe.model
        lines = []
        for t in tables:
            if t not in m.tables:
                continue
            tb = m.tables[t]
            cols = []
            for c in tb.columns.values():
                note = c.en or c.mn
                if c.values:
                    note = (note + "; " if note else "") + "values: " + ", ".join(list(c.values)[:8])
                cols.append(f"{c.name}" + (f" ({note})" if note else ""))
            lines.append(f"{t} — {tb.en or tb.mn}: " + "; ".join(cols))
        return "\n".join(lines)

    def _notes(self, ctx: PromptContext) -> str:
        lines = [x for x in ctx.evidence_lines if not x.startswith("Plan:")][:30]
        return ("Notes:\n" + "\n".join(f"- {x}" for x in lines)) if lines else ""

    def _ask(self, prompt: str) -> tuple[str, int, int]:
        r = self.llm.chat([{"role": "user", "content": prompt}], n=1, max_tokens=self.max_tokens, temperature=0.0,
                          top_p=1.0)
        return clean_reasoning(r.texts[0]), r.prompt_tokens, r.completion_tokens

    # ------------------------------------------------------------------ 1. plan
    def plan(self, ctx: PromptContext) -> tuple[str, int, int]:
        # Хамаарлын эрэмбэ (describe/retrieval) байвал түүгээр, үгүй бол бүх хүснэгт (≤ 25)
        tables = list(dict.fromkeys(ctx.relevant_tables + ctx.tables))[:25]
        try:
            text, ti, to = self._ask(PLAN_PROMPT.format(engine=_engine(self.opts.dialect),
                                                        today=self.pipe.cfg["project"]["as_of_date"],
                                                        schema=self._schema(tables), notes=self._notes(ctx),
                                                        question=ctx.question))
        except RuntimeError as e:   # LLM-ийн алдаа — төлөвлөгөөгүй үргэлжилнэ
            log.warning("agent plan: %s", e)
            return "", 0, 0
        text = re.sub(r"```.*?(```|$)", "", text, flags=re.S).strip()   # SQL бичсэн бол хасна
        return text[:1500], ti, to

    # ------------------------------------------------------------------ 3. observe
    def observe(self, cand) -> list[Finding]:
        out: list[Finding] = []
        if cand.error_stage in ("refusal", "extract"):
            # refusal: загвар SQL бичихээс татгалзсан (status "refused", TrustSQL-д хариулаагүй) — SQL бичүүлэх гэж
            # шахахгүй. extract: SQL олдоогүй форматын алдаа (тасарсан г.м.) — үүнийг E1-ийн засвар хариуцна.
            text = ("The model declined to write SQL (refusal; not revised)." if cand.error_stage == "refusal" else
                    "No SQL was found in the output (format error; not revised by the agent).")
            return [Finding("no_sql", text, hard=False)]
        if not cand.ok:
            out.append(Finding("exec_error", f"The query failed: {cand.error}"))
            return out
        res = cand.result
        if res is not None and res.rowcount == 0:
            # Зөөлөн олдвор: зөв хариу хоосон байж болно (жишээ: өгөгдлийн зөрчил хайх асуулт) — critic-д л дамжуулна
            out.append(Finding("empty", "The query returned 0 rows (correct only if the question expects none).",
                               hard=False))
        elif res is not None and res.rows and all(all(v is None for v in r) for r in res.rows):
            out.append(Finding("all_null", "Every value in the result is NULL (filter or join may be wrong).",
                               hard=False))
        if self.opts.agent_literal_check and cand.sql:
            out += self.check_literals(cand.sql, has_rows=_meaningful_rows(res))
        return out

    def _types(self) -> dict[tuple[str, str], str]:
        if self._col_types is None:
            schema = self.pipe.model.schema.replace("'", "''")
            r = self.pipe.executor.run("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                       f"WHERE table_schema = '{schema}'", max_rows=20000)
            self._col_types = {(t, c): dt for t, c, dt in r.rows} if r.ok else {}
        return self._col_types

    def _literal_pairs(self, sql: str) -> list[tuple[str, str, list[str], str, bool]]:
        """(хүснэгт, багана, утгууд, оператор, ci) — багана = 'текст', IN ('..', ..), LIKE/ILIKE '..'.
        ci: lower()/upper()/trim()-ээр ороосон эсвэл ILIKE бол үсгийн том жижгийг үл харгалзан шалгана."""
        try:
            tree = sqlglot.parse_one(sql, read="postgres")
        except Exception:
            return []
        model = self.pipe.model
        alias: dict[str, str] = {}
        tables = []
        for t in tree.find_all(exp.Table):
            if t.name in model.tables:
                alias[t.alias_or_name] = t.name
                alias[t.name] = t.name
                tables.append(t.name)

        def resolve(col: exp.Column) -> str | None:
            if col.table:
                return alias.get(col.table)
            owners = [t for t in dict.fromkeys(tables) if col.name in model.tables[t].columns]
            return owners[0] if len(owners) == 1 else None

        pairs = []
        for node in tree.find_all(exp.EQ, exp.In, exp.Like, exp.ILike):
            col, wrapped = node.this, False
            while isinstance(col, exp.Lower | exp.Upper | exp.Trim):
                col, wrapped = col.this, True
            if not isinstance(col, exp.Column):
                continue
            t = resolve(col)
            if not t or col.name not in model.tables[t].columns:
                continue
            if isinstance(node, exp.In):
                lits = [e.this for e in node.expressions if isinstance(e, exp.Literal) and e.is_string]
            else:
                e = node.expression
                lits = [e.this] if isinstance(e, exp.Literal) and e.is_string else []
            if lits:
                op = "like" if isinstance(node, exp.Like | exp.ILike) else ("in" if isinstance(node, exp.In) else "eq")
                pairs.append((t, col.name, lits, op, wrapped or isinstance(node, exp.ILike)))
        return pairs[:8]

    def check_literals(self, sql: str, has_rows: bool = False) -> list[Finding]:
        """Текст баганын утга DB-д байгаа эсэх. Хатуу олдвор зөвхөн үр дүн хоосон үед (үр дүнтэй бол critic-д л);
        IN жагсаалтын аль нэг утга байвал бусдыг зөөлөн гэж үзнэ (хамгаалалтын хувилбар бичсэн байж болно)."""
        ex = self.pipe.executor
        model = self.pipe.model
        schema = model.schema
        types = self._types()
        out = []
        seen = set()

        def exists(t, c, v, op, ci) -> bool | None:
            lit = v.replace("'", "''")
            lhs = f'lower("{c}"::text)' if ci else f'"{c}"::text'
            rhs = f"lower('{lit}')" if ci else f"'{lit}'"
            cond = f"{lhs} LIKE {rhs}" if op == "like" else f"{lhs} = {rhs}"
            r = ex.run(f'SELECT 1 FROM {schema}."{t}" WHERE {cond} LIMIT 1', max_rows=1)
            return (r.rowcount > 0) if r.ok else None

        for t, c, vals, op, ci in self._literal_pairs(sql):
            if types and types.get((t, c)) not in TEXT_TYPES:
                continue          # boolean/огноо/тоон багана ('t', '2025-01-01') — текст утгын шалгалт хамаарахгүй
            todo = [v for v in vals if (t, c, v) not in seen and not re.fullmatch(r"[\d\-: .%_]*", v or "")]
            seen.update((t, c, v) for v in todo)
            res = {v: exists(t, c, v, op, ci) for v in todo}
            missing = [v for v, ok in res.items() if ok is False]
            if not missing:
                continue
            any_present = any(ok for ok in res.values())
            hard = not has_rows and not (op == "in" and any_present)
            if model.tables[t].columns[c].pii:
                sugg_txt = "(stored values not shown: personal data column)"
            else:
                core = re.sub(r"[%_]", "", missing[0])[:4].replace("'", "''")
                sugg = ex.run(f'SELECT "{c}"::text, count(*) FROM {schema}."{t}" WHERE "{c}"::text ILIKE \'%{core}%\' '
                              f'GROUP BY 1 ORDER BY 2 DESC LIMIT 6', max_rows=6) if core else None
                if not sugg or not sugg.ok or not sugg.rows:
                    sugg = ex.run(f'SELECT "{c}"::text, count(*) FROM {schema}."{t}" WHERE "{c}" IS NOT NULL '
                                  f'GROUP BY 1 ORDER BY 2 DESC LIMIT 8', max_rows=8)
                vals_txt = ", ".join(f"'{row[0]}'" for row in (sugg.rows if sugg and sugg.ok else []))
                sugg_txt = f"Stored values of {t}.{c} include: {vals_txt or '(none)'}."
            shown = ", ".join(f"'{v}'" for v in missing)
            out.append(Finding("literal_missing", f"No row has {t}.{c} {'LIKE' if op == 'like' else '='} {shown}. "
                                                  + sugg_txt, hard=hard))
        return out

    # ------------------------------------------------------------------ 4. critic
    def critic(self, ctx: PromptContext, cand, findings: list[Finding]) -> tuple[str, str, int, int]:
        """→ (OK | REVISE | UNPARSED | ERROR, тайлбар, токен). UNPARSED/ERROR нь OK шиг (SQL-ийг өөрчлөхгүй) боловч
        бичлэгт тусад нь тэмдэглэгдэнэ — critic-ийн форматын дагалтыг хэмжинэ."""
        res = cand.result
        if res is not None and res.ok:
            result = f"{res.rowcount} row(s)\n" + res.preview(n=8, width=50)
        else:
            result = f"ERROR: {cand.error}"
        tables = list(dict.fromkeys(cand.tables + ctx.relevant_tables[:3]))
        try:
            text, ti, to = self._ask(CRITIC_PROMPT.format(
                engine=_engine(self.opts.dialect), today=self.pipe.cfg["project"]["as_of_date"],
                schema=self._schema(tables), notes=self._notes(ctx), question=ctx.question,
                sql=cand.sql or cand.raw_sql, result=result,
                findings="\n".join(f"- {f.text}" for f in findings) or "- none"))
        except RuntimeError as e:
            log.warning("agent critic: %s", e)
            return "ERROR", str(e)[:200], 0, 0
        last = None
        for last in VERDICT.finditer(text):
            pass
        if last is None:
            return "UNPARSED", text[-200:], ti, to
        return last.group(1).upper(), last.group(2).strip()[:400], ti, to

    # ------------------------------------------------------------------ 5. revise
    def _revise(self, cand, feedback: str, seed: int | None):
        p = self.pipe
        conv = list(cand.messages or [])
        raw = list(cand.raw_turns or [])
        tin = tout = 0
        if p.family == "sqrl":
            # cand.messages нь sqrl-ийн бүрэн яриа (сүүлийн assistant хариу орсон)
            conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=feedback) + SQRL_REVISE})
            sql, content = None, ""
            for _ in range(3):        # ≤ 2 судлах алхам, дараа нь <answer>
                texts, ti, to = p._chat(conv, None, 1, p._decoding(False), seed)
                tin, tout = tin + ti, tout + to
                raw.append(texts[0])
                content = strip_think(texts[0]).strip()
                conv.append({"role": "assistant", "content": content})
                kind, body = parse_sqrl_action(content)
                if kind == "answer":
                    sql = body
                    break
                if kind == "sql":
                    conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=p._explore(body))})
                    continue
                break                 # хариугүй — SQL-гүй (шинэ хувилбар үгүйсгэгдэнэ)
            prefill = None
        else:
            conv += [{"role": "assistant", "content": strip_think(cand.text or "")},
                     {"role": "user", "content": REVISE_MSG.format(feedback=feedback)}]
            prefill = cand.prefill if p.family == "omnisql" else None
            texts, ti, to = p._chat(conv, prefill, 1, p._decoding(False), seed)
            tin, tout = ti, to
            raw.append(texts[0])
            content = texts[0]
            sql = extract_sql(content)
        new = p._mark_refusal(p.process_sql(sql), content)
        new.text, new.messages, new.prefill, new.raw_turns = content, conv, prefill, raw
        new.tokens_in, new.tokens_out = tin, tout
        return new

    # ------------------------------------------------------------------ гол давталт
    def refine(self, ctx: PromptContext, cand, seed: int | None):
        o = self.opts
        t0 = time.perf_counter()
        log_steps: list[AgentStep] = []
        tin = tout = 0
        for rnd in range(o.agent_rounds + 1):
            findings = self.observe(cand)
            hard = [f for f in findings if f.hard]
            verdict, issue = None, None
            last = rnd == o.agent_rounds
            # Сүүлийн тойрогт critic-ийн дүгнэлт юуг ч өөрчлөхгүй — дуудахгүй
            if o.agent_critic and cand.sql and not last:
                verdict, issue, ti, to = self.critic(ctx, cand, findings)
                tin, tout = tin + ti, tout + to
            log_steps.append(AgentStep(rnd, cand.sql or cand.raw_sql, cand.ok, cand.rows, [f.text for f in findings],
                                       verdict, issue))
            if last or (not hard and verdict != "REVISE"):
                break
            feedback = "\n".join(f"- {f.text}" for f in (findings if verdict == "REVISE" else hard))
            if verdict == "REVISE" and issue:
                feedback += f"\n- Reviewer: {issue}"
            try:
                new = self._revise(cand, feedback.strip(), seed)
            except RuntimeError as e:      # LLM тасалдал — одоогийн хувилбараар үлдэнэ
                log.warning("agent revise: %s", e)
                break
            tin, tout = tin + new.tokens_in, tout + new.tokens_out
            worse = (not new.ok and cand.ok) or (cand.ok and cand.rows > 0 and new.ok and new.rows == 0)
            if worse:
                log_steps.append(AgentStep(rnd + 1, new.sql or new.raw_sql, new.ok, new.rows,
                                           ["rejected: revision is worse (failed or emptied the result)"],
                                           accepted=False))
                break
            new.repairs, new.agent_steps = cand.repairs, cand.agent_steps
            new.tokens_in, new.tokens_out = cand.tokens_in, cand.tokens_out
            new.latency_ms = cand.latency_ms
            cand = new
        cand.tokens_in += tin
        cand.tokens_out += tout
        cand.agent_log = [s.__dict__ for s in log_steps]
        cand.latency_ms += (time.perf_counter() - t0) * 1000
        return cand
