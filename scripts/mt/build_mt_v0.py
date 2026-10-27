"""MT (mn↔en) fine-tune өгөгдлийн v0: репо доторх одоо байгаа хос мэдлэгээс (гаднаас юу ч татахгүй) цуглуулна.

Эх сурвалж → гаралт (data/mt/v0/):
  knowledge/examples*.jsonl + knowledge/thesis/examples.jsonl  → pairs.jsonl    (асуултын хос: кирилл / латин / холимог → англи)
  knowledge/semantic_model.yaml, glossary*.yaml, translation_terms.yaml → terms.jsonl (нэр томьёоны хос)
  glossary_plus definition_mn/en, semantic_model-ийн тайлбар          → reference_loose.jsonl (СУРГАЛТАД ОРУУЛАХГҮЙ:
                                                                          англи тал нь орчуулга биш, нэмэлт дүрэмтэй)
  + MANIFEST.json (тоо, SHA256, шударга байдлын шалгалт), README.md

Шударга байдлын дүрэм (docs/MT_INVARIANT_PLAN.md §6):
  * dev/test (evalset/, data/thesis/{dev,test}) текст сургалтын өгөгдөлд ОРОХГҮЙ. Яг давхцал эсвэл тэмдэгтийн
    3-gram cosine ≥ 0.8 бүхий мөрийг хасаж, хэдийг хассаныг MANIFEST-д тэмдэглэнэ.
  * val тусгаарлалт нь family-аар (ижил загварын асуулт нэг талд) — hash(family) % 10 == 0.

Ажиллуулах:  PYTHONPATH=src python scripts/mt/build_mt_v0.py
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.pipeline.translate_terms import _col_label, _en_label  # noqa: E402
from mn_text2sql.text.normalize import STYLE_UOV, STYLE_VUW, cyr_key, translit  # noqa: E402

OUT = ROOT / "data" / "mt" / "v0"
EXAMPLE_FILES = ["knowledge/examples.jsonl", "knowledge/examples_plus.jsonl", "knowledge/examples_plus2.jsonl",
                 "knowledge/thesis/examples.jsonl"]
EVAL_FILES = ["evalset/dev.jsonl", "evalset/test.jsonl", "data/thesis/dev.jsonl", "data/thesis/test.jsonl"]
SEED = 20261023
CYR_RE = re.compile(r"[а-яёөү]", re.I)


# ----------------------------------------------------------------------------- туслах
def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def grams(s: str) -> dict[str, int]:
    s = norm(s)
    c: Counter = Counter(s[i:i + 3] for i in range(len(s) - 2))
    return c


def vnorm(a: dict) -> float:
    return math.sqrt(sum(v * v for v in a.values()))


def cos(a: dict, b: dict, na: float, nb: float) -> float:
    """Тэмдэгтийн n-gram векторуудын cosine; na/nb = тус бүрийн урьдчилан бодсон норм."""
    if not a or not b or na == 0 or nb == 0:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return sum(v * big.get(k, 0) for k, v in small.items()) / (na * nb)


class EvalGuard:
    """dev/test-ийн текстүүдтэй давхцлыг шалгана (хэл тус бүрээр)."""

    def __init__(self) -> None:
        self.by_lang: dict[str, list[tuple[str, dict, float]]] = defaultdict(list)
        self.exact: dict[str, set[str]] = defaultdict(set)
        self.strings: list[tuple[str, str, str]] = []   # (eval id, хэл, норм. текст) — дэд мөрийн шалгалтад
        n = 0
        for f in EVAL_FILES:
            p = ROOT / f
            if not p.exists():
                continue
            for d in read_jsonl(p):
                n += 1
                for lang, key in (("mn", "question_mn"), ("latin", "question_latin"), ("en", "question_en"),
                                  ("mixed", "question_mixed")):
                    t = d.get(key)
                    if t:
                        g = grams(t)
                        self.by_lang[lang].append((t, g, vnorm(g)))
                        self.exact[lang].add(norm(t))
                        self.strings.append((str(d.get("id")), lang, norm(t)))
        self.n_eval_items = n

    def hit(self, lang: str, text: str, thr: float = 0.8) -> bool:
        if not text:
            return False
        if norm(text) in self.exact[lang]:
            return True
        g = grams(text)
        ng = vnorm(g)
        return any(cos(g, eg, ng, en) >= thr for _, eg, en in self.by_lang[lang])


    def substring_hits(self, texts: dict[str, list[str]], min_len: int = 13) -> list[dict]:
        """Eval текст сургалтын текстийн ДОТОР бүтнээрээ орсон тохиолдол (cosine-оор илрэхгүй: богино eval асуулт урт текстийн
        төгсгөлд). {eval_id, pair_id, ratio = len(eval)/len(text)}. ratio өндөр (≥ 0.6) бол хасах ёстой; бага бол хүний шалгалтад."""
        out = []
        for pid, forms in texts.items():
            for t in forms:
                nt = norm(t)
                for eid, _lang, es in self.strings:
                    if len(es) >= min_len and es in nt:
                        out.append({"eval_id": eid, "pair_id": pid, "ratio": round(len(es) / max(1, len(nt)), 2)})
        return out


# ----------------------------------------------------------------------------- латин галиг (синтетик)
# Хувилбар бүрийн давтамж (давхардуулсан жин): хэрэглэгчид «ий» → "ii", «х» → "h"/"kh", «ү» → "v"/"u" гэж ихэвчлэн бичдэг
LAT_OPTS = {"в": ["w", "w", "v"], "ө": ["u", "o"], "ү": ["v", "u"], "х": ["h", "h", "kh"], "ц": ["ts", "ts", "c"],
            "ч": ["ch", "ch", "c"], "ш": ["sh", "sh", "s"], "ж": ["j", "j", "zh"], "й": ["i", "i", "i", "y"],
            "ы": ["ii", "i", "y"], "я": ["ya", "ya", "ia"], "ю": ["yu", "yu", "iu"], "ё": ["yo", "yo", "io"],
            "ь": ["i", "i", "'", ""], "ъ": [""], "у": ["u"], "э": ["e"]}


def noisy_latin(cyr: str, rng: random.Random) -> str:
    """Кирилл → хэрэглэгчийн бодит бичдэг латин (нэг өгүүлбэр дотор тогтвортой «хэв маяг», 10% хазайлт, давхар
    эгшиг хураах, төгсгөлийн тэмдэгт алгасах). Тогтмол STYLE_VUW / STYLE_UOV-оос ялгаатай хувилбарыг өгнө."""
    persona = {k: rng.choice(v) for k, v in LAT_OPTS.items()}
    from mn_text2sql.text.normalize import CYR2LAT
    collapse = rng.random() < 0.2
    out = []
    for ch in cyr:
        low = ch.lower()
        if low in LAT_OPTS:
            opts = LAT_OPTS[low]
            t = rng.choice(opts) if rng.random() < 0.1 else persona[low]
        elif low in CYR2LAT:
            t = CYR2LAT[low]
        else:
            t = ch
        out.append(t.capitalize() if ch.isupper() and t else t)
    s = "".join(out)
    if collapse:
        s = re.sub(r"([aeiou])\1+", r"\1", s)
    if rng.random() < 0.3:
        s = s.rstrip("?.! ")
    return s


# ----------------------------------------------------------------------------- асуултын хос
def build_pairs(guard: EvalGuard, rng: random.Random) -> tuple[list[dict], dict]:
    seen, rows, stats = set(), [], Counter()
    for f in EXAMPLE_FILES:
        p = ROOT / f
        if not p.exists():
            continue
        for d in read_jsonl(p):
            if d["id"] in seen:
                stats["dup_id"] += 1
                continue
            seen.add(d["id"])
            mn, lat, en, mixed = d["question_mn"], d.get("question_latin") or "", d["question_en"], d.get("question_mixed")
            drop = (guard.hit("mn", mn) or guard.hit("en", en) or (lat and guard.hit("latin", lat))
                    or (mixed and guard.hit("mixed", mixed)))
            if drop:
                stats["eval_overlap_dropped"] += 1
                continue
            fam = d.get("family") or d["id"]
            val = int(hashlib.sha1(fam.encode()).hexdigest(), 16) % 10 == 0
            synth = []
            for style in (STYLE_VUW, STYLE_UOV):
                s = translit(mn, style)
                if s.lower() != lat.lower() and s not in synth:
                    synth.append(s)
            synth.append(noisy_latin(mn, rng))
            rows.append({"id": d["id"], "kind": "question", "domain": d.get("domain"), "family": fam, "mn": mn,
                         "latin": lat, "mixed": mixed, "en": en, "values": d.get("values") or [],
                         "latin_synth": synth[:3], "source": f, "quality": "authored_llm",
                         "split": "val" if val else "train"})
            stats[f"src:{f}"] += 1
    stats["pairs"] = len(rows)
    stats["val"] = sum(1 for r in rows if r["split"] == "val")
    return rows, dict(stats)


# ----------------------------------------------------------------------------- нэр томьёо
def _head(mn: str) -> str:
    return re.split(r"\s*[(:;]", str(mn or ""))[0].strip()


def build_terms(rng: random.Random) -> tuple[list[dict], list[dict], dict]:
    terms: dict[tuple, dict] = {}
    loose: list[dict] = []

    # quality: curated = хүний/agent-ийн шалгасан нэр томьёо (glossary, lexicon); schema_label = схемийн монгол/англи
    # шошго (зөв боловч өгүүлбэрийн орчуулгад биш, hint-д тохиромжтой); code_derived = enum кодоос гаргасан англи
    # (КОД → "fee charge"), ЗӨВХӨН хүснэгт.баганын контекстэй hint болгон ашигла, дангаар нь сургалтын хос болгохгүй
    QUALITY = {"table": "schema_label", "column": "schema_label", "enum": "code_derived", "glossary": "curated",
               "lexicon": "curated"}

    def add(kind, mn_forms, en, source, table=None, column=None, code=None, en_aliases=(), quality=None):
        quality = quality or QUALITY[kind]
        mn_forms = [m.strip() for m in mn_forms if m and CYR_RE.search(m)]
        en = (en or "").strip()
        if not mn_forms or not en:
            return
        key = (kind, en.lower(), table, column, code)
        t = terms.setdefault(key, {"kind": kind, "en": en, "mn": mn_forms[0], "mn_aliases": [], "en_aliases": [],
                                   "table": table, "column": column, "code": code, "source": source,
                                   "quality": quality})
        for m in mn_forms:
            if m != t["mn"] and m not in t["mn_aliases"]:
                t["mn_aliases"].append(m)
        for e in en_aliases:
            if e and e.lower() != en.lower() and e not in t["en_aliases"]:
                t["en_aliases"].append(e)

    sem = yaml.safe_load((ROOT / "knowledge/semantic_model.yaml").read_text(encoding="utf-8"))
    for tname, spec in (sem.get("tables") or {}).items():
        ten = _en_label(spec.get("en", tname))
        add("table", [_head(spec.get("mn"))] + list(spec.get("syn") or []), ten, "semantic_model", table=tname)
        loose.append({"kind": "description", "scope": f"table:{tname}", "mn": spec.get("mn"), "en": spec.get("en"),
                      "source": "semantic_model", "quality": "loose"})
        for cname, cs in (spec.get("columns") or {}).items():
            if not isinstance(cs, dict):
                continue
            cen = _col_label(cs.get("en", cname))
            add("column", [_head(cs.get("mn"))] + list(cs.get("syn") or []), cen, "semantic_model", table=tname,
                column=cname)
            loose.append({"kind": "description", "scope": f"column:{tname}.{cname}", "mn": cs.get("mn"),
                          "en": cs.get("en"), "source": "semantic_model", "quality": "loose"})
            for code, label in (cs.get("values") or {}).items():
                if not re.search(r"[A-Za-z]", str(code)):
                    continue
                add("enum", [str(label)], str(code).lower().replace("_", " "), "semantic_model", table=tname,
                    column=cname, code=str(code))
    for gf in ("knowledge/glossary.yaml", "knowledge/glossary_plus.yaml"):
        glo = yaml.safe_load((ROOT / gf).read_text(encoding="utf-8")) or {}
        for t in glo.get("terms") or []:
            aliases = list(t.get("aliases") or [])
            cyr_al = [a for a in aliases if CYR_RE.search(a)]
            en_al = [a for a in aliases if not CYR_RE.search(a)]
            add("glossary", [t.get("term")] + cyr_al, _en_label(t.get("en", "")), gf, en_aliases=en_al)
            if t.get("definition_mn") and t.get("definition_en"):
                loose.append({"kind": "definition", "scope": f"glossary:{t.get('id')}", "mn": t["definition_mn"],
                              "en": t["definition_en"], "source": gf, "quality": "loose",
                              "note": "англи тал нь SQL-ийн дүрэм нэмсэн тул шууд орчуулга биш"})
    lex = yaml.safe_load((ROOT / "knowledge/translation_terms.yaml").read_text(encoding="utf-8")) or {}
    for t in lex.get("terms") or []:
        add("lexicon", [t.get("mn")] + list(t.get("aliases") or []), _en_label(t.get("en", "")),
            "translation_terms", quality="curated")
    rows = list(terms.values())
    for i, r in enumerate(sorted(rows, key=lambda x: (x["kind"], x["en"].lower(), x["mn"])), 1):
        r["id"] = f"T{i:05d}"
        r["mn_latin_synth"] = [noisy_latin(r["mn"], rng)]
    rows.sort(key=lambda r: r["id"])
    return rows, loose, dict(Counter(r["kind"] for r in rows))


# ----------------------------------------------------------------------------- DB-ийн оноосон нэрс (PG-гүйгээр)
def build_entities() -> dict:
    """build/index/values.json (index-build-ийн үед DB-ээс цуглуулсан) → {хүснэгт.багана: [утга, ...]}.
    Зохиогч agent-ууд асуулт бичихдээ DB-ийн бодит нэр (салбар, бүтээгдэхүүн, аймаг ...)-ийг ашиглахад зориулсан."""
    p = ROOT / "build/index/values.json"
    if not p.exists():
        return {}
    by: dict[str, list] = defaultdict(list)
    for x in json.loads(p.read_text(encoding="utf-8")):
        by[f"{x['table']}.{x['column']}"].append((int(x.get("freq", 1)), str(x["value"])))
    return {k: [v for _, v in sorted(vs, key=lambda t: (-t[0], t[1]))[:40]] for k, vs in sorted(by.items())}


# ----------------------------------------------------------------------------- гол
def main() -> None:
    rng = random.Random(SEED)
    guard = EvalGuard()
    pairs, pstats = build_pairs(guard, rng)
    terms, loose, tstats = build_terms(rng)
    # terms-ийн mn нь eval-ийн асуулттай давхцаж болохгүй (хэллэг богино тул 3-gram биш, яг тэнцүү эсэхийг л шалгана)
    eval_exact = guard.exact["mn"]
    terms = [t for t in terms if norm(t["mn"]) not in eval_exact]

    sub_hits = guard.substring_hits({p["id"]: [p["mn"], p["latin"], p["en"], p["mixed"] or ""] for p in pairs})
    # ratio ≥ 0.5 → тэр хосыг хасна (ихэвчлэн eval асуулт бараг бүтнээрээ давтагдсан)
    drop_ids = {h["pair_id"] for h in sub_hits if h["ratio"] >= 0.5}
    if drop_ids:
        pairs = [p for p in pairs if p["id"] not in drop_ids]
        pstats["substring_dropped"] = len(drop_ids)
        pstats["pairs"] = len(pairs)
    OUT.mkdir(parents=True, exist_ok=True)
    sha = {
        "pairs.jsonl": write_jsonl(OUT / "pairs.jsonl", pairs),
        "terms.jsonl": write_jsonl(OUT / "terms.jsonl", terms),
        "reference_loose.jsonl": write_jsonl(OUT / "reference_loose.jsonl", loose),
    }
    entities = build_entities()
    (OUT / "entities.json").write_text(json.dumps(entities, ensure_ascii=False, indent=1), encoding="utf-8")
    sha["entities.json"] = hashlib.sha256((OUT / "entities.json").read_bytes()).hexdigest()
    n_scripts = {"cyr→en": len(pairs), "latin→en (зохиосон)": sum(1 for p in pairs if p["latin"]),
                 "latin→en (синтетик нэмэлт)": sum(len(p["latin_synth"]) for p in pairs),
                 "холимог→en": sum(1 for p in pairs if p["mixed"]), "en→cyr": len(pairs)}
    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"), "seed": SEED, "version": "v0",
        "inputs": EXAMPLE_FILES + ["knowledge/semantic_model.yaml", "knowledge/glossary.yaml",
                                   "knowledge/glossary_plus.yaml", "knowledge/translation_terms.yaml"],
        "eval_guard": {"files": EVAL_FILES, "eval_items": guard.n_eval_items, "rule": "яг давхцал эсвэл 3-gram cos ≥ 0.8; дэд мөр ratio ≥ 0.5",
                       "dropped_pairs": pstats.get("eval_overlap_dropped", 0) + pstats.get("substring_dropped", 0),
                       "substring_hits_kept": [h for h in sub_hits if h["ratio"] < 0.5],
                       "note": "substring_hits_kept: богино eval асуулт урт, өөр утгатай жишээний төгсгөлд орсон (ratio < 0.5) — хүний шалгалт"},
        "pairs": pstats, "pair_domains": dict(Counter(p["domain"] for p in pairs)),
        "directions": n_scripts, "terms": {"total": len(terms), "by_kind": tstats},
        "reference_loose": len(loose), "sha256": sha,
        "caveats": ["Англи/латин/кирилл гурвуулаа LLM agent-аар ноороглосон (хүн хянаагүй).",
                    "reference_loose.jsonl-ийг сургалтад ОРУУЛАХГҮЙ.",
                    "Кириллын жишээ ихэвчлэн ББСБ-ийн асуулт (≈14 үг) — албан бичиг, хууль эрхзүйн хэв маяг ороогүй."],
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
