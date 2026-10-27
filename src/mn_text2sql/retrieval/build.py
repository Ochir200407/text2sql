"""index-build: схем, утга, нэр томьёо, жишээний индекс + баганын жишээ утгыг build/index/-д хадгална."""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np

from ..config import Config, resolve_path
from ..db.conn import bot_dsn
from ..db.executor import Executor
from ..knowledge.semantic import load_glossary, load_semantic_model
from .embed import get_embedder
from .indexes import Example, SchemaIndex, ValueEntry, mask_question, save_json, to_dicts

log = logging.getLogger(__name__)

SKIP_SUFFIXES = ("_id", "_no", "_number")
SKIP_COLUMNS = {"description", "notes"}
TEXT_TYPES = {"character varying", "text", "character"}


def _fmt(v) -> str:
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(round(v, 2))
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v)


def collect_values(cfg: Config, model) -> tuple[list[ValueEntry], dict]:
    """Текст баганын давтагдашгүй утга (хувийн мэдээлэлгүй) ба бүх баганын 2–3 жишээ утга."""
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=60000, max_rows=5000)
    max_distinct = int(cfg["knowledge"]["value_index_max_distinct"])
    types = {}
    res = ex.run("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = 't2s'")
    for t, c, dt in res.rows:
        types[(t, c)] = dt
    entries: list[ValueEntry] = []
    col_examples: dict[str, dict[str, list[str]]] = {}
    for t in model.tables.values():
        col_examples[t.name] = {}
        for c in t.columns.values():
            dt = types.get((t.name, c.name), "")
            if c.pii:
                continue
            if dt in TEXT_TYPES:
                r = ex.run(f'SELECT "{c.name}", count(*) FROM t2s."{t.name}" WHERE "{c.name}" IS NOT NULL '
                           f'GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {max_distinct + 1}')
                if not r.ok:
                    log.warning("утга уншиж чадсангүй %s.%s: %s", t.name, c.name, r.error)
                    continue
                col_examples[t.name][c.name] = [_fmt(v) for v, _ in r.rows[:3]]
                skip = c.name.endswith(SKIP_SUFFIXES) or c.name in SKIP_COLUMNS or len(r.rows) > max_distinct
                if not skip:
                    entries.extend(ValueEntry(t.name, c.name, str(v), int(n)) for v, n in r.rows)
            elif dt in ("date",):
                r = ex.run(f'SELECT min("{c.name}"), max("{c.name}") FROM t2s."{t.name}"')
                if r.ok and r.rows and r.rows[0][0] is not None:
                    col_examples[t.name][c.name] = [_fmt(r.rows[0][0]), _fmt(r.rows[0][1])]
            elif dt in ("numeric", "integer", "smallint", "bigint", "double precision"):
                if c.name.endswith("_id"):
                    continue
                r = ex.run(f'SELECT "{c.name}" FROM t2s."{t.name}" WHERE "{c.name}" IS NOT NULL '
                           f'GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 2')
                if r.ok:
                    col_examples[t.name][c.name] = [_fmt(v[0]) for v in r.rows]
            elif dt == "boolean":
                col_examples[t.name][c.name] = ["true", "false"]
    ex.close()
    col_examples["__types__"] = {f"{t}.{c}": dt for (t, c), dt in types.items()}
    return entries, col_examples


def load_example_pool(cfg: Config, extra: str | None = None, include_dev: bool = False) -> list[Example]:
    """knowledge/examples.jsonl (+ F9: тэмдэглэсэн нэмэлт асуултууд). Eval set-ийн асуулт энд орохгүй.
    include_dev (I бүлэг): dev-ийн хариулах боломжтой асуултыг (gold SQL) жишээ болгон нэмнэ — test-ийн асуулт
    хэзээ ч орохгүй; dev дээр үнэлэхэд асуулт бүр өөрийн family-ийн жишээг татахгүй (Pipeline.item_families)."""
    out = []
    paths = [resolve_path(cfg["knowledge"]["examples"])] + ([resolve_path(extra)] if extra else [])
    lines = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Жишээний сан алга: {path}. Эхлээд: python -m mn_text2sql evalset-build "
                                    "(B4/B5 few-shot чимээгүй хоосон болохоос сэргийлнэ)")
        lines += path.read_text(encoding="utf-8").splitlines()
    eval_ids = _eval_ids(cfg)
    test_ids = _eval_ids(cfg, ("test_file",))
    if include_dev:
        dev = resolve_path(cfg["eval"]["dev_file"])
        lines += [x for x in dev.read_text(encoding="utf-8").splitlines() if x.strip()
                  and json.loads(x).get("category") == "answerable"]
    for line in lines:
        if not line.strip():
            continue
        d = json.loads(line)
        if not d.get("gold_sql") or d.get("id") in test_ids or (d.get("id") in eval_ids and not include_dev):
            continue
        out.append(Example(id=d["id"], question_mn=d["question_mn"], question_latin=d.get("question_latin", ""),
                           question_en=d.get("question_en", ""), sql=d["gold_sql"], values=d.get("values") or [],
                           family=d.get("family", "")))
    return out


def _eval_ids(cfg: Config, keys: tuple[str, ...] = ("dev_file", "test_file")) -> set[str]:
    ids = set()
    for key in keys:
        path = resolve_path(cfg["eval"][key])
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ids.add(json.loads(line).get("id"))
    return ids


def build_indexes(cfg: Config, use_embeddings: bool = True, extra_examples: str | None = None,
                  include_dev: bool = False) -> dict:
    t0 = time.time()
    out_dir = resolve_path(cfg["knowledge"]["index_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    model = load_semantic_model(cfg["knowledge"]["semantic_model"])
    glossary = load_glossary(cfg["knowledge"]["glossary"])
    entries, col_examples = collect_values(cfg, model)
    docs = SchemaIndex.build_docs(model)
    examples = load_example_pool(cfg, extra_examples, include_dev)
    save_json(out_dir / "values.json", to_dicts(entries))
    save_json(out_dir / "column_examples.json", col_examples)
    save_json(out_dir / "schema_docs.json", to_dicts(docs))
    save_json(out_dir / "examples.json", to_dicts(examples))
    meta = {"created": datetime.now().isoformat(timespec="seconds"), "values": len(entries), "schema_docs": len(docs),
            "glossary_terms": len(glossary), "examples": len(examples), "embedding_model": None,
            "include_dev": include_dev, "extra_examples": extra_examples}
    if use_embeddings:
        emb = get_embedder(cfg["retrieval"]["embedding_model"], cfg["retrieval"]["embedding_device"],
                           int(cfg["retrieval"]["embedding_batch_size"]))
        if emb is not None:
            np.save(out_dir / "schema_emb.npy", emb.encode([d.text for d in docs]))
            np.save(out_dir / "glossary_emb.npy", emb.encode([t.text() for t in glossary]))
            if examples:
                np.save(out_dir / "examples_emb.npy",
                        emb.encode([mask_question(e.question_mn, e.values) for e in examples]))
            meta["embedding_model"] = emb.model_name
    save_json(out_dir / "meta.json", meta)
    meta["seconds"] = round(time.time() - t0, 1)
    meta["index_dir"] = str(out_dir)
    return meta


def index_dir(cfg: Config) -> Path:
    return resolve_path(cfg["knowledge"]["index_dir"])
