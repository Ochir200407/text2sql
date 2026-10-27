"""Semantic model (knowledge/semantic_model.yaml) ба нэр томьёоны толь (glossary.yaml) ачаалах."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import resolve_path


@dataclass
class Column:
    table: str
    name: str
    mn: str = ""
    en: str = ""
    syn: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)
    pii: bool = False
    fk: str | None = None
    type: str = ""
    examples: list[str] = field(default_factory=list)


@dataclass
class Table:
    name: str
    mn: str = ""
    en: str = ""
    syn: list[str] = field(default_factory=list)
    pk: list[str] = field(default_factory=list)
    columns: dict[str, Column] = field(default_factory=dict)

    def fks(self) -> list[tuple[str, str, str]]:
        """[(багана, зорилтот хүснэгт, зорилтот багана)]"""
        out = []
        for c in self.columns.values():
            if c.fk:
                t, col = c.fk.split(".")
                out.append((c.name, t, col))
        return out


@dataclass
class SemanticModel:
    schema: str
    tables: dict[str, Table]
    notes_mn: list[str] = field(default_factory=list)

    def allowed(self) -> dict[str, set[str]]:
        return {t: set(tab.columns) for t, tab in self.tables.items()}

    def neighbors(self, table: str) -> set[str]:
        out = set()
        for t, tab in self.tables.items():
            for _, target, _ in tab.fks():
                if t == table:
                    out.add(target)
                if target == table:
                    out.add(t)
        out.discard(table)
        return out


@dataclass
class GlossaryTerm:
    id: str
    term: str
    aliases: list[str]
    en: str
    definition_mn: str
    sql: str
    tables: list[str]
    related: list[str]
    ambiguous: dict | None = None
    definition_en: str = ""            # J: англи prompt-д (translate_mode=en_prompt, glossary_en)

    def text(self) -> str:
        return " ".join([self.term, *self.aliases, self.en, self.definition_mn])


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def load_semantic_model(path: str | Path = "knowledge/semantic_model.yaml") -> SemanticModel:
    data = yaml.safe_load(resolve_path(path).read_text(encoding="utf-8"))
    tables = {}
    for tname, t in data["tables"].items():
        cols = {}
        for cname, spec in (t.get("columns") or {}).items():
            spec = spec or {}
            cols[cname] = Column(table=tname, name=cname, mn=spec.get("mn", ""), en=spec.get("en", ""),
                                 syn=[str(s) for s in _as_list(spec.get("syn"))],
                                 values={str(k): str(v) for k, v in (spec.get("values") or {}).items()},
                                 pii=bool(spec.get("pii", False)), fk=spec.get("fk"), type=spec.get("type", ""),
                                 examples=[str(x) for x in _as_list(spec.get("examples"))])
        tables[tname] = Table(name=tname, mn=t.get("mn", ""), en=t.get("en", ""),
                              syn=[str(s) for s in _as_list(t.get("syn"))], pk=_as_list(t.get("pk")), columns=cols)
    return SemanticModel(schema=data.get("schema", "t2s"), tables=tables, notes_mn=data.get("notes_mn", []))


def load_glossary(path: str | Path = "knowledge/glossary.yaml") -> list[GlossaryTerm]:
    data = yaml.safe_load(resolve_path(path).read_text(encoding="utf-8"))
    out = []
    for t in data["terms"]:
        out.append(GlossaryTerm(id=t["id"], term=t["term"], aliases=[str(a) for a in _as_list(t.get("aliases"))],
                                en=t.get("en", ""), definition_mn=t.get("definition_mn", ""), sql=t.get("sql", ""),
                                tables=_as_list(t.get("tables")), related=_as_list(t.get("related")),
                                ambiguous=t.get("ambiguous"), definition_en=t.get("definition_en", "")))
    return out


@lru_cache(maxsize=4)
def cached_model(path: str = "knowledge/semantic_model.yaml") -> SemanticModel:
    return load_semantic_model(path)
