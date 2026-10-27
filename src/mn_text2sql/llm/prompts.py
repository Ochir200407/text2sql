"""Загвар тус бүрийн prompt протокол ба схемийн формат.

Туршилтын төлөвлөгөө ("Хоёр загварын онцлог"): загвар бүрийг ЯГ өөрийн протоколоор ажиллуулж, RAG-ийн
агуулгыг тухайн протоколын схем, evidence хэсэгт оруулна.
  * omnisql (Arctic-Text2SQL-R1-7B, OmniSQL-7B): OmniSQL маягийн user prompt, assistant «Let me solve this step by
    step.\\n<think>» prefill; SQL нь <answer> доторх ```sql``` блок. RAG: DDL-ийн comment; evidence асуултын өмнө.
  * sqrl (feyninc/sqrl-*): agentic протокол <sql> (судлах) / <answer> (эцсийн), ≤ max_steps; RAG: <schema>, <evidence>.
  * xiyan (XiYanSQL-QwenCoder): M-Schema + 【参考信息】.
  * generic (Qwen3.5, Gemma 4, Kwai-AutoSQL ...): энгийн заавар + ```sql``` блок.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..knowledge.semantic import SemanticModel

ENGINE_LABEL = {"sqlite_native": "SQLite", "postgres_prompt": "PostgreSQL", "sqlite_transpile": "SQLite"}
PG_NOTE = ("Use PostgreSQL syntax: date_trunc('month', d), EXTRACT(YEAR FROM d), d::date, INTERVAL '30 days', "
           "COUNT(*) FILTER (WHERE ...), ROUND(x::numeric, 2), || for string concatenation.")


@dataclass
class PromptContext:
    """Prompt үүсгэхэд хэрэгтэй бүх зүйл (туршилтын тохиргоо + retrieval-ийн үр дүн)."""
    question: str
    tables: list[str]
    columns: dict[str, list[str]] | None = None      # schema linking хийсэн бол баганын дэд олонлог
    describe: bool = False                            # B1: монгол/англи тайлбар, синоним, утга
    evidence_lines: list[str] = field(default_factory=list)   # B2/B3 (+ огноо, тэмдэглэл)
    examples: list[tuple[str, str]] = field(default_factory=list)  # B4: (асуулт, SQL)
    dialect: str = "sqlite_native"
    pivot: bool = False                               # F5: англи нэг өгүүлбэрийн дахин томьёолол
    intent_json: str | None = None                    # F7
    max_steps: int = 5                                # sqrl
    search_values_tool: bool = False                  # F10
    relevant_tables: list[str] = field(default_factory=list)  # тайлбарын эрэмбэ (токены төсөвт багтаах)
    # schema linking (H4s): хүснэгт бүрийн БҮХ багана DDL-д байна; тайлбар (comment)-ыг зөвхөн эдгээр баганад өгнө
    describe_columns: dict[str, set[str]] | None = None


# ---------------------------------------------------------------------------
# Схемийн формат
# ---------------------------------------------------------------------------

def _pg_type(t: str) -> str:
    return {"character varying": "TEXT", "character": "TEXT", "text": "TEXT", "uuid": "TEXT", "numeric": "NUMERIC",
            "integer": "INTEGER", "smallint": "INTEGER", "bigint": "INTEGER", "boolean": "BOOLEAN",
            "date": "DATE", "double precision": "REAL"}.get(t, (t or "TEXT").upper())


TRIVIAL_COLS = {"currency_code", "created_at", "updated_at"}
_EN_STOP = {"the", "of", "a", "an", "in", "on", "for", "to", "and", "or", "is", "by", "at", "id", "code", "name",
            "date", "amount", "number", "flag", "type", "status"}


def est_tokens(text: str) -> int:
    """Токены ойролцоо тоо: ASCII ~4 тэмдэгт/токен, кирилл ~2.6 тэмдэгт/токен (Qwen/Gemma BPE)."""
    ascii_n = sum(1 for ch in text if ord(ch) < 128)
    return int(ascii_n / 4 + (len(text) - ascii_n) / 2.6) + 1


def _col_comment(model: SemanticModel, table: str, col: str, examples: dict, typ: str = "TEXT") -> str:
    """Товч тайлбар: монгол тайлбар; enum утга (КОД=нэр) эсвэл синоним (≤2); enum-гүй текст баганад жишээ утга."""
    c = model.tables[table].columns[col]
    text = c.mn or c.en
    if c.mn and c.en:
        # Англи тайлбарыг баганын нэрээс илүү мэдээлэл өгөх үед л нэмнэ (токен хэмнэх)
        new_words = set(re.findall(r"[a-z]+", c.en.lower())) - set(col.lower().split("_")) - _EN_STOP
        if len(new_words) >= 2:
            text += f" ({c.en})"
    if c.values:
        text += "; " + ", ".join(f"{k}={v}" for k, v in list(c.values.items())[:9])
    else:
        syn = [s for s in c.syn if s.lower() not in text.lower()][:2]
        if syn:
            text += "; " + ", ".join(syn)
    ex = (examples.get(table) or {}).get(col)
    if ex and typ == "TEXT" and not c.pii and not c.values and not col.endswith("_id") and col not in TRIVIAL_COLS:
        text += "; e.g. " + ", ".join(str(x) for x in ex[:2])
    return text.replace("\n", " ")


def render_ddl(model: SemanticModel, tables: list[str], types: dict, examples: dict, describe: bool,
               columns: dict[str, list[str]] | None = None, describe_tables: set[str] | None = None,
               describe_columns: dict[str, set[str]] | None = None) -> str:
    """OmniSQL/sqrl-д: CREATE TABLE жагсаалт. describe=True бол багана бүрт SQL comment (B1).

    describe_tables өгвөл зөвхөн тэдгээр хүснэгтийн баганыг тайлбарлаж, бусад хүснэгтэд зөвхөн нэг мөр гарчиг
    үлдээнэ (токены төсөв — Pipeline.schema_text). describe_columns өгвөл (schema linking) тухайн хүснэгтийн бүх
    багана DDL-д байх боловч тайлбарыг зөвхөн жагсаасан баганад өгнө. FK багана, техникийн багана тайлбаргүй."""
    out = []
    tset = set(tables)
    for t in tables:
        tab = model.tables[t]
        detailed = describe and (describe_tables is None or t in describe_tables)
        only = describe_columns.get(t) if describe_columns else None
        keep = list(tab.columns)
        if columns and t in columns:
            must = set(tab.pk) | {c for c, *_ in tab.fks()}
            keep = [c for c in tab.columns if c in set(columns[t]) | must]
        decls = []
        for c in keep:
            typ = _pg_type(types.get(f"{t}.{c}", ""))
            comment = ""
            if detailed and not tab.columns[c].fk and c not in TRIVIAL_COLS and (only is None or c in only):
                comment = _col_comment(model, t, c, examples, typ)
            decls.append((f"  {c} {typ}", comment))
        tail = []
        if tab.pk:
            tail.append(f"  PRIMARY KEY ({', '.join(tab.pk)})")
        for col, target, tcol in tab.fks():
            if target in tset and col in keep:
                tail.append(f"  FOREIGN KEY ({col}) REFERENCES {target}({tcol})")
        total = len(decls) + len(tail)
        body = []
        for i, (decl, comment) in enumerate(decls):
            body.append(decl + ("," if i < total - 1 else "") + (f"  -- {comment}" if comment else ""))
        for j, line in enumerate(tail):
            body.append(line + ("," if len(decls) + j < total - 1 else ""))
        header = f"-- {tab.mn} / {tab.en}\n" if describe else ""
        out.append(header + f"CREATE TABLE {t} (\n" + "\n".join(body) + "\n);")
    return "\n\n".join(out)


def render_mschema(model: SemanticModel, tables: list[str], types: dict, examples: dict, describe: bool,
                   columns: dict[str, list[str]] | None = None, db_id: str = "nbfi") -> str:
    out = [f"【DB_ID】 {db_id}", "【Schema】"]
    fks = []
    for t in tables:
        tab = model.tables[t]
        out.append(f"# Table: {t}" + (f", {tab.mn}" if describe else ""))
        out.append("[")
        keep = list(tab.columns)
        if columns and t in columns:
            must = set(tab.pk) | {c for c, *_ in tab.fks()}
            keep = [c for c in tab.columns if c in set(columns[t]) | must]
        rows = []
        for c in keep:
            col = tab.columns[c]
            parts = [f"{c}:{_pg_type(types.get(f'{t}.{c}', '')).upper()}"]
            if c in tab.pk:
                parts.append("Primary Key")
            if describe and col.mn:
                parts.append(col.mn)
            ex = (examples.get(t) or {}).get(c)
            if describe and ex and not col.pii:
                parts.append("Examples: [" + ", ".join(str(x) for x in ex[:3]) + "]")
            rows.append("(" + ", ".join(parts) + ")")
        out.append(",\n".join(rows))
        out.append("]")
        for col, target, tcol in tab.fks():
            if target in tables and col in keep:
                fks.append(f"{t}.{col}={target}.{tcol}")
    if fks:
        out.append("【Foreign keys】")
        out.extend(fks)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Prompt гэр бүлүүд
# ---------------------------------------------------------------------------

OMNISQL_TEMPLATE = """Task Overview:
You are a data science expert. Below, you are provided with a database schema and a natural language question. Your task is to understand the schema and generate a valid SQL query to answer the question.

Database Engine:
{engine}

Database Schema:
{schema}
This schema describes the database's structure, including tables, columns, primary keys, foreign keys, and any relevant relationships or constraints.
{examples}
Question:
{question}

Instructions:
- Make sure you only output the information that is asked in the question. If the question asks for a specific column, make sure to only include that column in the SELECT clause, nothing more.
- The generated query should return all of the information asked in the question without any missing or extra information.
- Before generating the final SQL query, please think through the steps of how to write the query.{extra_instructions}

Output Format:
In your answer, please enclose the generated SQL query in a code block:
```sql
-- Your SQL query
```

Take a deep breath and think step by step to find the correct SQL query."""

# Arctic-Text2SQL-R1 (Snowflake) — сургалт/үнэлгээний prompt, arXiv:2505.20315 Figure C.1 (CC BY 4.0)-ээс яг хуулбар:
# system = "data science expert", user = Engine/Schema/Question(evidence+question)/Instructions/Output Format,
# assistant нь «Let me solve this step by step.\n<think>»-ээр эхэлнэ.
ARCTIC_SYSTEM = ("You are a data science expert. Below, you are provided with a database schema and a natural language "
                 "question. Your task is to understand the schema and generate a valid SQL query to answer the question.")
ARCTIC_USER_TEMPLATE = """Database Engine:
{engine}

Database Schema:
{schema}
This schema describes the database's structure, including tables, columns, primary keys, foreign keys, and any relevant relationships or constraints.
{examples}
Question:
{question}

Instructions:
- Make sure you only output the information that is asked in the question. If the question asks for a specific column, make sure to only include that column in the SELECT clause, nothing more.
- The generated query should return all of the information asked in the question without any missing or extra information.
- Before generating the final SQL query, please think through the steps of how to write the query.{extra_instructions}

Output Format:
Please provide a detailed chain-of-thought reasoning process and include your thought process within `<think>` tags. Your final answer should be enclosed within `<answer>` tags.

Ensure that your SQL query follows the correct syntax and is formatted as follows:

```sql
-- Your SQL query here
```

Example format:
<think> Step-by-step reasoning, including self-reflection and corrections if necessary. [Limited by 4K tokens] </think>
<answer> Summary of the thought process leading to the final SQL query. [Limited by 1K tokens]

```sql
Correct SQL query here
```
</answer>"""
ARCTIC_PREFILL = "Let me solve this step by step.\n<think>"

SQRL_SYSTEM = """<role>
You are an expert data analyst, fluent in SQL, with a meticulous eye for
matching a question's intent to the exact tables, columns, and stored value formats of
a database.
</role>

<task>
Translate the user's natural-language question into a SQL query that answers
it, using the database schema in <schema> and any domain hints in <evidence>. You can
run read-only queries against the database to inspect it before giving your final
answer.
</task>

<database_engine>
{engine}
</database_engine>

<schema>
{schema}
</schema>

<evidence>
{evidence}
</evidence>

<protocol>
Think through the problem internally first. Then, in your response, write a
BRIEF summary of your reasoning — 2-4 sentences stating which tables and columns are
relevant, the joins and filters, and any exact value-format detail. Be decisive: state
the plan once, do not second-guess or restate. End your message with EXACTLY ONE action
block (and nothing after it):

  <sql> a read-only query to inspect the database </sql>
     Use this when uncertain and you want to see real data before answering — e.g.
     confirm a value's exact stored format, check a filter actually matches rows,
     sanity-check an intermediate result, or verify a join. You will see the result
     rows and then continue.
{tool_block}
  <answer> your final SQL query </answer>
     Use this once you are confident. It is executed and scored, and the task ends.

Examples:
  I'll check the exact county value before filtering, since the format may vary.
  <sql> SELECT DISTINCT `County Name` FROM frpm LIMIT 10 </sql>

  The schools are in the frpm table; I'll select `School Name` and filter `County Name`
  to 'Alameda'. The schema is clear, so I can answer directly.
  <answer> SELECT `School Name` FROM frpm WHERE `County Name` = 'Alameda' </answer>
</protocol>

<rules>
- Use only the tables and columns defined in <schema>.
- Quote identifiers containing spaces or special characters with backticks.
- Return exactly the columns the question asks for — no more, no fewer.
- Use the hints in <evidence> to resolve ambiguous terms and value encodings.
- If you already know the correct query, go straight to <answer> — investigating is
  optional; only run <sql> when seeing the data would actually change your answer.
- The action block must be the LAST thing in your message; do not discuss the tags
  themselves in your reasoning.
- You have at most {max_steps} <sql> steps; after that you must give <answer>.{extra_rules}
</rules>"""

SQRL_TOOL_BLOCK = """
  <search_values> a keyword (any script: Cyrillic or Latin) </search_values>
     Look up exact stored values that match a name or term from the question (branch,
     product, district ...). You will see table.column = value candidates.
"""
SQRL_FIRST_USER = "Question: {question}\n\nReason about it, then give <sql> to investigate or <answer> to finish."
SQRL_OBSERVATION = "<observation>\n{obs}\n</observation>\nContinue with <sql> or <answer>."
SQRL_FINISH = "You must finish now. Give your <answer>."

XIYAN_TEMPLATE = """你是一名{dialect}专家，现在需要阅读并理解下面的【数据库schema】描述，以及可能用到的【参考信息】，并运用{dialect}知识生成sql语句回答【用户问题】。
【用户问题】
{question}

【数据库schema】
{db_schema}

【参考信息】
{evidence}

【用户问题】
{question}

```sql"""

GENERIC_SYSTEM = ("You are an expert SQL analyst for a Mongolian non-bank financial institution. Questions may be in "
                  "Mongolian (Cyrillic or Latin transliteration), English or mixed. Write ONE {engine} SELECT query "
                  "that answers the question using only the given schema. Return exactly the requested columns.")
GENERIC_USER = """Database schema:
{schema}
{evidence}{examples}
Question: {question}

{extra}Think briefly, then give the final SQL in a ```sql code block."""


def _evidence_text(ctx: PromptContext) -> str:
    lines = list(ctx.evidence_lines)
    if ctx.intent_json:
        lines.append("Structured intent from a Mongolian language model (English): " + ctx.intent_json)
    return "\n".join(f"- {x}" if not x.startswith("- ") else x for x in lines)


def _examples_text(ctx: PromptContext, style: str) -> str:
    if not ctx.examples:
        return ""
    if style == "omnisql":
        body = "\n\n".join(f"Question: {q}\n```sql\n{s}\n```" for q, s in ctx.examples)
        return f"\nExamples of similar questions with correct SQL for this database:\n{body}\n"
    return "\n".join(f"Q: {q}\nSQL: {s}" for q, s in ctx.examples)


def _pivot_instruction() -> str:
    return ("\n- First restate the question as ONE English sentence on a line starting with 'Restated:'; keep "
            "database values (names, codes) exactly as written in the question.")


def build_omnisql(ctx: PromptContext, schema_text: str) -> tuple[list[dict], str | None]:
    """Arctic-Text2SQL-R1 (family omnisql): system + user (Figure C.1) + prefill. Evidence нь асуултын өмнө
    ({evidence + question}); few-shot (B4) нь Question-ийн өмнөх тусдаа хэсэг; dialect/pivot заавар Instructions-д."""
    evidence = _evidence_text(ctx)
    question = (evidence + "\n" if evidence else "") + ctx.question
    extra = ""
    if ctx.dialect == "postgres_prompt":
        extra += "\n- " + PG_NOTE
    if ctx.pivot:
        extra += _pivot_instruction()
    user = ARCTIC_USER_TEMPLATE.format(engine=ENGINE_LABEL[ctx.dialect], schema=schema_text,
                                       examples=_examples_text(ctx, "omnisql"), question=question,
                                       extra_instructions=extra)
    return [{"role": "system", "content": ARCTIC_SYSTEM}, {"role": "user", "content": user}], ARCTIC_PREFILL


def build_omnisql_plain(ctx: PromptContext, schema_text: str) -> tuple[list[dict], str | None]:
    """OmniSQL-7B (family omnisql_plain): OmniSQL-ийн нэг user prompt, prefill-гүй."""
    evidence = _evidence_text(ctx)
    question = (evidence + "\n" if evidence else "") + ctx.question
    extra = ""
    if ctx.dialect == "postgres_prompt":
        extra += "\n- " + PG_NOTE
    if ctx.pivot:
        extra += _pivot_instruction()
    user = OMNISQL_TEMPLATE.format(engine=ENGINE_LABEL[ctx.dialect], schema=schema_text,
                                   examples=_examples_text(ctx, "omnisql"), question=question,
                                   extra_instructions=extra)
    return [{"role": "user", "content": user}], None


def build_sqrl_system(ctx: PromptContext, schema_text: str) -> str:
    ev = _evidence_text(ctx)
    if ctx.examples:
        ev = (ev + "\n" if ev else "") + "Similar solved questions:\n" + _examples_text(ctx, "plain")
    extra_rules = ""
    if ctx.dialect == "postgres_prompt":
        extra_rules += "\n- " + PG_NOTE
    if ctx.pivot:
        extra_rules += ("\n- Start your summary with one line 'Restated: <the question as one English sentence>' "
                        "keeping database values exactly as written.")
    return SQRL_SYSTEM.format(engine=ENGINE_LABEL[ctx.dialect], schema=schema_text, evidence=ev or "(none provided)",
                              max_steps=ctx.max_steps, tool_block=SQRL_TOOL_BLOCK if ctx.search_values_tool else "",
                              extra_rules=extra_rules)


def build_xiyan(ctx: PromptContext, schema_text: str) -> tuple[list[dict], str | None]:
    ev = _evidence_text(ctx)
    if ctx.examples:
        ev = (ev + "\n" if ev else "") + _examples_text(ctx, "plain")
    dialect = "PostgreSQL" if ctx.dialect == "postgres_prompt" else ENGINE_LABEL[ctx.dialect]
    user = XIYAN_TEMPLATE.format(dialect=dialect, question=ctx.question, db_schema=schema_text, evidence=ev or "")
    return [{"role": "user", "content": user}], None


def build_generic(ctx: PromptContext, schema_text: str) -> tuple[list[dict], str | None]:
    ev = _evidence_text(ctx)
    evidence = f"\nHints:\n{ev}\n" if ev else ""
    examples = ("\nExamples:\n" + _examples_text(ctx, "plain") + "\n") if ctx.examples else ""
    extra = (PG_NOTE + "\n") if ctx.dialect == "postgres_prompt" else ""
    if ctx.pivot:
        extra += "First write 'Restated:' and the question as one English sentence.\n"
    engine = "PostgreSQL" if ctx.dialect == "postgres_prompt" else ENGINE_LABEL[ctx.dialect]
    return ([{"role": "system", "content": GENERIC_SYSTEM.format(engine=engine)},
             {"role": "user", "content": GENERIC_USER.format(schema=schema_text, evidence=evidence, examples=examples,
                                                              question=ctx.question, extra=extra)}], None)


ACTION_RE = re.compile(r"<(sql|answer|search_values)>(.*?)</\1>", re.S | re.I)


_FENCE = re.compile(r"```(?:sql|postgresql|postgres|sqlite)?\s*(.*?)```", re.S | re.I)


def strip_code_fences(text: str) -> str:
    """<answer>/<sql> доторх ```sql ... ``` блокийг задална (загвар заримдаа хашилтаар бичдэг)."""
    m = _FENCE.findall(text or "")
    return (m[-1] if m else text or "").strip()


def parse_sqrl_action(text: str) -> tuple[str | None, str]:
    """sqrl хариуны СҮҮЛИЙН action блок: ('sql'|'answer'|'search_values', агуулга) эсвэл (None, '')."""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    found = ACTION_RE.findall(text)
    if not found:
        return None, ""
    kind, body = found[-1]
    kind = kind.lower()
    return kind, (strip_code_fences(body) if kind in ("sql", "answer") else body.strip())


def strip_think(text: str) -> str:
    return text.split("</think>")[-1] if "</think>" in text else text
