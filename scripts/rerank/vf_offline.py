"""M1vf офлайн хэмжилт (2026-10-30 шалгалтын F1/F3/F5 ба F2): ЗӨВХӨН dev (evalset/test.jsonl-ийг огт уншихгүй), CPU,
DB/LLM/embedding-гүй (BM25; Kaggle дээрх bge-m3-ын хагасыг тооцохгүй). Орчуулга = локал Qwen кэш (en_prompt-д зөвхөн
загварт харуулах асуулт; хайлт монголоор тул M1a ↔ M1vf-ийн ялгаанд нөлөөгүй).

    PYTHONIOENCODING=utf-8 python scripts/rerank/vf_offline.py                 # build/index-ийн утга/схем, qwen3.5-9b.fix
    PYTHONIOENCODING=utf-8 python scripts/rerank/vf_offline.py --json build/rerank/vf_offline.json

Хэсгүүд:
  F1  «Stored value» мөр: legacy ↔ strict (value_match), сул латин түлхүүрийн хуурамч мөр, gold SQL-ийн литералаар
      баталгаажсан утгын алдагдал/нэмэгдэл; linked_tables-ийн ялгаа ба gold хүснэгтийн бүрэн хамралт.
  F3  few-shot (index_plus_dev-тэй ижил сан: examples + examples_plus + dev; өөрийн family хасна, k=4, pool 20):
      gold хүснэгтийн cover (scripts/rerank/fewshot_cover.py-ийн тодорхойлолт) — legacy маск ↔ mask_mode=values.
  F5  enum: dev gold SQL-д эхний 9-өөс хойших код (gl_entries.account_code 4100–5400 г.м.).
  P   M1a ↔ M1vf-ийн бүрэн prompt (Pipeline.prompt_for, en_prompt) — хэдэн dev prompt өөрчлөгдөх, шалтгаанаар.
  F2  орчуулагчийн нэр томьёоны зөвлөмж: term_hints lexicon ↔ lexicon2.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rapidfuzz import fuzz  # noqa: E402

from mn_text2sql.config import _to_config, load_config  # noqa: E402
from mn_text2sql.retrieval import fewshot_rerank as fr  # noqa: E402
from mn_text2sql.retrieval.indexes import (  # noqa: E402
    Example, ExampleIndex, ValueEntry, ValueIndex, mask_values, stem_forms, strict_match,
)
from mn_text2sql.retrieval.retriever import _quoted  # noqa: E402
from mn_text2sql.text.normalize import cyr_key, script_of, tokens, weak_latin_keys  # noqa: E402


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def literals(d: dict) -> set[str]:
    out = {v.lower() for v in d.get("values") or []}
    for s in [d.get("gold_sql") or ""] + list(d.get("alt_sql") or []):
        out |= {m.replace("''", "'").lower() for m in re.findall(r"'((?:[^']|'')*)'", s or "")}
    return out


def pool_rows(dev: list[dict], extra: str) -> list[dict]:
    """build/index_plus_dev-ийн жишээний сантай ижил (build.load_example_pool(include_dev=True)); test-ийг уншихгүй —
    жишээний сангийн id (AX01, DY001) eval-ийн id (A001)-тай давхцдаггүйг шалгана."""
    ids = {d["id"] for d in dev}
    rows = [r for p in ("knowledge/examples.jsonl", extra) for r in read_jsonl(ROOT / p)]
    assert not ids & {r["id"] for r in rows}
    rows = [r for r in rows if r.get("gold_sql")] + [d for d in dev if d.get("category") == "answerable"]
    return [{"id": r["id"], "question_mn": r["question_mn"], "question_latin": r.get("question_latin", ""),
             "question_en": r.get("question_en", ""), "sql": r["gold_sql"], "values": r.get("values") or [],
             "family": r.get("family", "")} for r in rows]


# --------------------------------------------------------------------------- F1
def f1_values(dev, entries, rc) -> dict:
    vi = ValueIndex(entries)
    pk, tt = int(rc["value_candidates_per_keyword"]), int(rc["value_top_total"])
    st = Counter()
    lost, gained, spurious = [], [], Counter()
    for d in dev:
        q, lits = d["question_mn"], literals(d)
        L = vi.search(q, per_keyword=pk, top_total=tt)
        S = vi.search(q, per_keyword=pk, top_total=tt, match="strict")
        kl, ks = {(h.table, h.column, h.value) for h in L}, {(h.table, h.column, h.value) for h in S}
        st["q_lines_legacy"] += bool(L)
        st["q_lines_strict"] += bool(S)
        st["lines_legacy"] += len(L)
        st["lines_strict"] += len(S)
        q_sp = False
        for h in L:
            if (h.table, h.column, h.value) in ks:
                continue
            kw_c = cyr_key(h.keyword)
            m = strict_match(kw_c, weak_latin_keys(h.keyword), stem_forms(kw_c), script_of(h.keyword) == "cyrillic",
                             cyr_key(h.value), vi.keys[[e.value for e in entries].index(h.value)][1])
            if m is None:                                  # сул латин түлхүүр / дунд хэсгийн тааралт — хуурамч
                st["spurious_removed"] += 1
                spurious[f"{h.keyword.lower()} → {h.value}"] += 1
                q_sp = True
            else:
                st["removed_by_ranking"] += 1
        st["q_spurious_removed"] += q_sp
        st["added_strict"] += len(ks - kl)
        st["gold_legacy"] += sum(h.value.lower() in lits for h in L)
        st["gold_strict"] += sum(h.value.lower() in lits for h in S)
        lost += [f"{d['id']}: {h.keyword} → {h.value}" for h in L
                 if h.value.lower() in lits and (h.table, h.column, h.value) not in ks]
        gained += [f"{d['id']}: {h.keyword} → {h.value}" for h in S
                   if h.value.lower() in lits and (h.table, h.column, h.value) not in kl]
        st["q_gold_any_legacy"] += any(h.value.lower() in lits for h in L)
        st["q_gold_any_strict"] += any(h.value.lower() in lits for h in S)
    return {"stats": dict(st), "gold_lost": lost, "gold_gained": gained, "top_spurious": spurious.most_common(25)}


# --------------------------------------------------------------------------- F3
def f3_fewshot(dev, entries, examples, retr) -> dict:
    vi = ValueIndex(entries)
    idx = ExampleIndex([Example(**e) for e in examples], None)
    pos = {e["id"]: i for i, e in enumerate(examples)}
    etab = [idx.example_tables(i) for i in range(len(examples))]
    keys = retr.entity_keys()
    st, n = Counter(), 0
    for d in dev:
        q = d["question_mn"]
        L = vi.search(q, per_keyword=10, top_total=16)
        S = vi.search(q, per_keyword=10, top_total=16, match="strict")
        legacy_masks = [h.value for h in L] + _quoted(q)
        first = (tokens(q) or [""])[0]
        st["q_first_word_masked_legacy"] += first in _quoted(q)
        st["q_first_word_masked_values"] += any(first.lower() == m.lower() or first.lower().startswith(m.lower())
                                                for m in mask_values(q, S, keys))
        if d.get("category") != "answerable":
            continue
        gold = fr.sql_tables(d["gold_sql"])
        if not gold:
            continue
        n += 1
        fam = {d["family"]} if d.get("family") else None
        variants = {"legacy": legacy_masks, "values (legacy hits)": mask_values(q, L, keys),
                    "values + strict (M1vf)": mask_values(q, S, keys)}
        sel = {}
        for name, masks in variants.items():
            sel[name] = [pos[e.id] for e in idx.search(q, masks, None, k=4, pool=20, exclude_families=fam)]
            ts = [etab[i] for i in sel[name]]
            st[f"cover|{name}"] += any(gold <= t for t in ts)
            st[f"union|{name}"] += gold <= (frozenset().union(*ts) if ts else frozenset())
        st["changed_sets_M1vf"] += sel["legacy"] != sel["values + strict (M1vf)"]
    return {"n_answerable": n, "stats": dict(st)}


# --------------------------------------------------------------------------- F1 linking
def f1_linking(dev, retr) -> dict:
    st = Counter()
    for d in dev:
        a = retr.retrieve(d["question_mn"], fewshot_k=0, want_glossary=False)
        b = retr.retrieve(d["question_mn"], fewshot_k=0, want_glossary=False, value_match="strict")
        st["q_linked_changed"] += a.linked_tables != b.linked_tables
        if d.get("category") == "answerable" and d.get("gold_sql"):
            gold = fr.sql_tables(d["gold_sql"])
            st["n_answerable"] += 1
            st["gold_full_legacy"] += gold <= set(a.linked_tables)
            st["gold_full_strict"] += gold <= set(b.linked_tables)
    return dict(st)


# --------------------------------------------------------------------------- F5
def f5_enum(dev) -> dict:
    from mn_text2sql.knowledge.semantic import cached_model
    model = cached_model("knowledge/semantic_model.yaml")
    cut = {(t.name, c.name): set(list(c.values)[9:]) for t in model.tables.values() for c in t.columns.values()
           if len(c.values) > 9}
    hits = Counter()
    for d in dev:
        sql = d.get("gold_sql") or ""
        for (t, c), codes in cut.items():
            if re.search(rf"\b{t}\b", sql) and any(re.search(rf"'{re.escape(k)}'", sql) for k in codes):
                hits[f"{t}.{c}"] += 1
    return {"columns_cut": {f"{t}.{c}": sorted(v) for (t, c), v in cut.items()}, "dev_gold_using_cut_codes": dict(hits)}


# --------------------------------------------------------------------------- P: бүрэн prompt
def prompts_diff(dev, cfg, retr, translations) -> dict:
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.options import load_experiments, resolve_run
    from mn_text2sql.pipeline.orchestrator import Pipeline
    exp = load_experiments()
    pipes = {}
    for rid in ("M1a", "M1vf"):
        p = Pipeline(cfg, resolve_run(rid, exp), "arctic-r1-7b", MockLLM(lambda m: "<answer>1</answer>"),
                     retriever=retr, executor=object(), guard=object(), translations=translations)
        p.item_families = {d["id"]: d["family"] for d in dev if d.get("family")}
        pipes[rid] = p
    st = Counter()
    for d in dev:
        out = {}
        for rid, p in pipes.items():
            msgs, pre, ctx, _, _ = p.prompt_for(d["question_mn"], "mn", d["id"])
            out[rid] = (msgs, ctx, p.schema_text(ctx))
        (ma, ca, sa), (mb, cb, sb) = out["M1a"], out["M1vf"]
        st["n"] += 1
        st["prompt_changed"] += ma != mb
        st["evidence_changed"] += ca.evidence_lines != cb.evidence_lines
        st["examples_changed"] += ca.examples != cb.examples
        st["schema_changed"] += sa != sb
        st["enum_line_added"] += sa != sb and "5400=" in sb and "5400=" not in sa
        st["chars_delta"] += len(json.dumps(mb, ensure_ascii=False)) - len(json.dumps(ma, ensure_ascii=False))
    return dict(st)


# --------------------------------------------------------------------------- F2
def _misleading(question: str, line: str, lex) -> str | None:
    """lexicon-ийн мөр яагаад төөрөгдүүлэх вэ: negation (асуултын «-гүй» үгэнд эерэг нэр томьёо), merged (эсрэг туйлын
    шошгыг нэгтгэсэн), digits (тоог хассан түлхүүр)."""
    from mn_text2sql.pipeline.translate_terms import _WORD, _polarity
    words = _WORD.findall(cyr_key(question))
    stems = next(s for s, ln in lex if ln == line)
    mn, en = line.split(" = ", 1)
    labels = re.findall(r'label "([^"]+)"', en)
    for i in range(len(words) - len(stems) + 1):
        if all(words[i + j].startswith(stems[j]) for j in range(len(stems))):
            if any(_polarity(w) == "гүй" for w in words[i:i + len(stems)]) and "гүй" not in mn:
                return "negation"
            break
    if len({"гүй" in x.lower() for x in labels}) > 1:
        return "merged"
    if any(re.search(r"\d", x) for x in labels) and not re.search(r"\d", mn):
        return "digits"
    return None


def f2_hints(dev) -> dict:
    """Анхаар: "lexicon"-ийн эрэмбэ PYTHONHASHSEED-ээс хамаардаг (set-ийн давталт) — dev-ийн 8 асуултад seed бүрт өөр;
    lexicon2 тогтмол. Тоо нь энэ процессын seed-ээр (тогтвортой харьцуулахад PYTHONHASHSEED=0)."""
    from mn_text2sql.pipeline.translate_terms import load_lexicon, term_hints
    lex = load_lexicon()
    st, removed, added = Counter(), Counter(), Counter()
    for d in dev:
        a, b = term_hints(d["question_mn"]), term_hints(d["question_mn"], mode="lexicon2")
        st["n"] += 1
        st["q_with_hints_lexicon"] += bool(a)
        st["q_with_hints_lexicon2"] += bool(b)
        if a == b:
            continue
        st["q_changed_any"] += 1
        st["q_changed_set"] += set(a) != set(b)
        kinds = {_misleading(d["question_mn"], h, lex) for h in a if h not in b} - {None}
        st["q_misleading_removed"] += bool(kinds)
        for k in kinds:
            st[f"q_misleading|{k}"] += 1
        removed.update(h[:110] for h in a if h not in b)
        added.update(h[:110] for h in b if h not in a)
    return {"stats": dict(st), "top_removed": removed.most_common(15), "top_added": added.most_common(10)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--index", default="build/index", help="утга/схемийн индекс (values.json, schema_docs.json)")
    ap.add_argument("--extra", default="knowledge/examples_plus.jsonl", help="жишээний сангийн нэмэлт (index_plus_dev)")
    ap.add_argument("--cache", default="qwen3.5-9b.fix", help="build/translations/<cache>.jsonl (dev мөрийг л)")
    ap.add_argument("--json", default="", help="бүх тоог JSON-оор хадгалах")
    args = ap.parse_args()

    dev = read_jsonl(ROOT / "evalset/dev.jsonl")
    assert all(d.get("split") == "dev" for d in dev), "зөвхөн dev"
    ids = {d["id"] for d in dev}
    cfg = load_config()
    entries = [ValueEntry(**x) for x in json.loads((ROOT / args.index / "values.json").read_text(encoding="utf-8"))]
    examples = pool_rows(dev, args.extra)
    tr = {}
    cache = ROOT / "build/translations" / f"{args.cache}.jsonl"
    if cache.exists():
        tr = {f"{r['id']}:{r['variant']}": r["text"] for r in read_jsonl(cache) if r["id"] in ids}
    out: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        for f in ("values.json", "column_examples.json", "schema_docs.json"):
            shutil.copyfile(ROOT / args.index / f, Path(tmp) / f)
        (Path(tmp) / "examples.json").write_text(json.dumps(examples, ensure_ascii=False), encoding="utf-8")
        (Path(tmp) / "meta.json").write_text(json.dumps({"created": "vf_offline", "embedding_model": None}),
                                             encoding="utf-8")
        cfg = _to_config(json.loads(json.dumps(cfg, default=str)))
        cfg["knowledge"]["index_dir"] = tmp
        from mn_text2sql.retrieval.retriever import Retriever
        retr = Retriever(cfg)
        out["F1"] = f1_values(dev, entries, cfg["retrieval"])
        out["F1_linking"] = f1_linking(dev, retr)
        out["F3"] = f3_fewshot(dev, entries, examples, retr)
        out["F5"] = f5_enum(dev)
        out["prompts"] = prompts_diff(dev, cfg, retr, tr)
    out["F2"] = f2_hints(dev)
    out["setup"] = {"dev": len(dev), "values": len(entries), "pool": len(examples), "index": args.index,
                    "translations": args.cache if tr else None}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
