"""Eval set-ийн бичлэгийн бүтэц, ачаалах, шалгах (evalset/README.md).

Бичлэг (JSONL):
  id, family, split, domain, category (answerable|unanswerable|ambiguous), difficulty (easy|medium|hard),
  question_mn, question_latin, question_en, question_mixed?, gold_sql, alt_sql[], order_matters,
  interpretations[{label_mn, sql}], clarification_mn, unanswerable_reason, tags[], contains_value, values[],
  glossary_terms[], notes
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import resolve_path

CATEGORIES = {"answerable", "unanswerable", "ambiguous"}
DIFFICULTIES = {"easy", "medium", "hard"}
UNANSWERABLE_REASONS = {"pii", "missing_data", "non_sql", "external_knowledge", "future", "write_operation"}
VARIANT_FIELDS = {"mn": "question_mn", "latin": "question_latin", "en": "question_en", "mixed": "question_mixed"}


@dataclass
class EvalItem:
    id: str
    family: str
    category: str
    difficulty: str
    question_mn: str
    question_latin: str = ""
    question_en: str = ""
    question_mixed: str | None = None
    gold_sql: str | None = None
    alt_sql: list[str] = field(default_factory=list)
    order_matters: bool = False
    interpretations: list[dict] = field(default_factory=list)
    clarification_mn: str | None = None
    unanswerable_reason: str | None = None
    domain: str = ""
    split: str = ""
    tags: list[str] = field(default_factory=list)
    contains_value: bool = False
    values: list[str] = field(default_factory=list)
    glossary_terms: list[str] = field(default_factory=list)
    notes: str = ""
    raw: dict = field(default_factory=dict)

    def question(self, variant: str) -> str | None:
        return getattr(self, VARIANT_FIELDS.get(variant, "question_mn"), None) or None

    def gold_sqls(self) -> list[str]:
        out = [self.gold_sql] if self.gold_sql else []
        out += [s for s in self.alt_sql if s]
        if self.category == "ambiguous":
            out += [i["sql"] for i in self.interpretations if i.get("sql")]
        return out


def item_from_dict(d: dict) -> EvalItem:
    known = {f for f in EvalItem.__dataclass_fields__ if f != "raw"}
    kwargs = {k: v for k, v in d.items() if k in known}
    kwargs.setdefault("family", d.get("id", ""))
    for key in ("alt_sql", "interpretations", "tags", "values", "glossary_terms"):
        if kwargs.get(key) is None:
            kwargs[key] = []
    return EvalItem(**kwargs, raw=d)


def load_items(paths: list[str | Path]) -> list[EvalItem]:
    items = []
    for p in paths:
        path = resolve_path(p)
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("//"):
                    continue
                try:
                    items.append(item_from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError) as e:
                    raise ValueError(f"{path}:{n}: {e}") from e
    return items


def validate_item(it: EvalItem) -> list[str]:
    """Бүтцийн алдаа (SQL гүйцэтгэхээс өмнө)."""
    errs = []
    if not it.id:
        errs.append("id хоосон")
    if it.category not in CATEGORIES:
        errs.append(f"category буруу: {it.category}")
    if it.difficulty not in DIFFICULTIES:
        errs.append(f"difficulty буруу: {it.difficulty}")
    if not it.question_mn or not it.question_mn.strip():
        errs.append("question_mn хоосон")
    if not it.question_latin:
        errs.append("question_latin хоосон")
    if not it.question_en:
        errs.append("question_en хоосон")
    if it.category == "answerable" and not it.gold_sql:
        errs.append("answerable боловч gold_sql байхгүй")
    if it.category == "unanswerable":
        if it.gold_sql:
            errs.append("unanswerable боловч gold_sql байна")
        if it.unanswerable_reason not in UNANSWERABLE_REASONS:
            errs.append(f"unanswerable_reason буруу: {it.unanswerable_reason}")
    if it.category == "ambiguous":
        if len(it.interpretations) < 2:
            errs.append("ambiguous асуултад 2+ interpretations хэрэгтэй")
        if not it.clarification_mn:
            errs.append("ambiguous асуултад clarification_mn хэрэгтэй")
    if any(ch in it.question_latin for ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюяөү"):
        errs.append("question_latin-д кирилл үсэг байна")
    return errs


def write_items(items: list[dict], path: str | Path) -> None:
    path = resolve_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for d in items:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")


def load_all_eval_items(cfg) -> list[EvalItem]:
    """dev + test (байхгүй бол evalset/drafts/*.jsonl — жишээний санг оруулахгүй). ts-score, errors, judge-д."""
    files = [resolve_path(cfg["eval"][k]) for k in ("dev_file", "test_file")]
    files = [f for f in files if f.exists()]
    if not files:
        drafts = resolve_path("evalset/drafts")
        files = sorted(f for f in drafts.glob("*.jsonl") if not f.name.startswith("_") and "_examples" not in f.name)
    return load_items(files)

