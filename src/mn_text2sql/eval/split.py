"""Eval set-ийн эцсийн хувилбар: evalset/drafts/*.jsonl → evalset/dev.jsonl (300) + evalset/test.jsonl (200),
жишээний сан → knowledge/examples.jsonl, evalset/MANIFEST.json.

Туршилтын төлөвлөгөөний дүрэм:
  * 500 асуулт → 300 dev / 200 test; шийдвэрийг dev дээр, test-ийг бүлэг бүрийн эцэст;
  * нэг загвараас (family) гаргасан асуултууд нэг талд (family бүтнээрээ dev эсвэл test-д);
  * давхаргалсан хуваалт: домэйн × ангилал (answerable/unanswerable/ambiguous) × хүндрэл;
  * dev/test асуулт ба тэдгээрийн хувилбар сургалт/жишээний санд орохгүй — жишээний сан ба eval-ийн хооронд
    тэмдэгтийн n-gram cosine ойролцоо байдлыг шалгаж тайлагнана (training/dedup.py нь сургалтын өгөгдлийг шүүнэ).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from ..config import Config, resolve_path
from ..text.normalize import cyr_key
from .dataset import load_items, write_items

DRAFTS = "evalset/drafts"


def _vec(text: str, n: int = 3) -> Counter:
    t = f" {cyr_key(text)} "
    return Counter(t[i:i + n] for i in range(max(0, len(t) - n + 1)))


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


def near_duplicates(a_items, b_items, threshold: float = 0.8, same_family_ok: bool = True) -> list[dict]:
    bv = [(it, _vec(it.question_mn)) for it in b_items]
    out = []
    for it in a_items:
        va = _vec(it.question_mn)
        for jt, vb in bv:
            if it.id == jt.id or (same_family_ok and it.family == jt.family):
                continue
            s = _cos(va, vb)
            if s >= threshold:
                out.append({"a": it.id, "b": jt.id, "sim": round(s, 3), "a_q": it.question_mn, "b_q": jt.question_mn})
    return sorted(out, key=lambda x: -x["sim"])


_GROUPINGS = (
    lambda it: ("domain", it.domain),
    lambda it: ("category", it.category),
    lambda it: ("difficulty", it.difficulty),
    lambda it: ("dom×cat", it.domain, it.category),
    lambda it: ("cat×diff", it.category, it.difficulty),
    lambda it: ("mixed", bool(it.question_mixed)),
)


def assign_splits(items, n_dev: int = 300, seed: int = 42, iters: int = 40000) -> dict[str, str]:
    """Family-г бүтнээр нь dev/test-д хуваарилна. Эхлээд санамсаргүй, дараа нь ижил хэмжээтэй хоёр family-г
    солих орон нутгийн хайлтаар домэйн, ангилал, хүндрэл (ба хослолуудын) dev-ийн хувийг n_dev/N-д ойртуулна."""
    rng = random.Random(seed)
    fams: dict[str, list] = defaultdict(list)
    for it in items:
        fams[it.family].append(it)
    target = n_dev / len(items)
    totals = Counter(g(it) for it in items for g in _GROUPINGS)
    fam_keys = {f: Counter(g(it) for it in members for g in _GROUPINGS) for f, members in fams.items()}
    order = list(fams)
    rng.shuffle(order)
    in_dev: set[str] = set()
    n = 0
    # санамсаргүй дарааллаар дүүргэнэ (олон бичлэгтэй family хоёр талд тархана), дараа нь дан family-гаар яг n_dev
    for f in order:
        if n + len(fams[f]) <= n_dev and rng.random() < n_dev / len(items) + 0.05:
            in_dev.add(f)
            n += len(fams[f])
    for f in order:
        if n >= n_dev:
            break
        if f not in in_dev and n + len(fams[f]) <= n_dev:
            in_dev.add(f)
            n += len(fams[f])
    dev_counts = Counter()
    for f in in_dev:
        dev_counts.update(fam_keys[f])

    def cost(dc: Counter) -> float:
        return sum((dc[k] - target * t) ** 2 / max(1.0, t) for k, t in totals.items())

    cur = cost(dev_counts)
    by_size: dict[int, list[str]] = defaultdict(list)
    for f in order:
        by_size[len(fams[f])].append(f)
    sizes = [s for s, fs in by_size.items() if len(fs) > 1]
    for _ in range(iters if sizes else 0):
        s = rng.choice(sizes)
        a = rng.choice(by_size[s])
        b = rng.choice(by_size[s])
        if (a in in_dev) == (b in in_dev):
            continue
        out_f, in_f = (a, b) if a in in_dev else (b, a)
        trial = dev_counts.copy()
        trial.subtract(fam_keys[out_f])
        trial.update(fam_keys[in_f])
        c = cost(trial)
        if c < cur:
            cur, dev_counts = c, trial
            in_dev.discard(out_f)
            in_dev.add(in_f)
    return {it.id: ("dev" if it.family in in_dev else "test") for it in items}


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def build_evalset(cfg: Config, n_dev: int = 300, seed: int = 42, drafts: str = DRAFTS, version: str = "v1",
                  overwrite: bool = False) -> dict:
    ddir = resolve_path(drafts)
    eval_files = sorted(p for p in ddir.glob("*.jsonl") if not p.name.startswith("_")
                        and not p.stem.endswith("_examples"))
    ex_files = sorted(ddir.glob("*_examples.jsonl"))
    items = load_items(eval_files)
    examples = load_items(ex_files)
    ids = Counter(it.id for it in items + examples)
    dups = [i for i, n in ids.items() if n > 1]
    if dups:
        raise ValueError(f"давхардсан id: {dups[:10]}")
    dev_path, test_path = resolve_path(cfg["eval"]["dev_file"]), resolve_path(cfg["eval"]["test_file"])
    if (dev_path.exists() or test_path.exists()) and not overwrite:
        raise FileExistsError("evalset/dev.jsonl эсвэл test.jsonl аль хэдийн бий. Хуваалтыг өөрчлөх нь өмнөх "
                              "үр дүнг хүчингүй болгоно — зориуд бол --overwrite.")
    split = assign_splits(items, n_dev, seed)
    dev, test = [], []
    for it in items:
        d = dict(it.raw)
        d["split"] = split[it.id]
        (dev if d["split"] == "dev" else test).append(d)
    key = lambda d: d["id"]  # noqa: E731
    write_items(sorted(dev, key=key), dev_path)
    write_items(sorted(test, key=key), test_path)
    ex_path = resolve_path(cfg["knowledge"]["examples"])
    write_items(sorted((dict(e.raw, pool="examples") for e in examples), key=key), ex_path)
    # ойролцоо давхардал: жишээний сан ↔ eval, dev ↔ test (өөр family)
    dev_items = [it for it in items if split[it.id] == "dev"]
    test_items = [it for it in items if split[it.id] == "test"]
    dup_ex = near_duplicates(examples, items, 0.8, same_family_ok=False)
    dup_dt = near_duplicates(dev_items, test_items, 0.85)

    def stats(rows):
        return {"n": len(rows), "category": dict(Counter(r["category"] for r in rows)),
                "difficulty_answerable": dict(Counter(r["difficulty"] for r in rows if r["category"] == "answerable")),
                "domain": dict(Counter(r.get("domain") for r in rows)),
                "mixed_variant": sum(1 for r in rows if r.get("question_mixed")),
                "families": len({r.get("family") for r in rows})}

    manifest = {"version": version, "created": datetime.now().isoformat(timespec="seconds"), "seed": seed,
                "as_of_date": str(cfg["project"]["as_of_date"]), "db": {"name": cfg["db"]["name"],
                                                                     "seed": cfg["project"]["seed"],
                                                                     "scale": cfg["data"]["scale"]},
                "files": {"dev": {"path": cfg["eval"]["dev_file"], "sha1": _sha1(dev_path), **stats(dev)},
                          "test": {"path": cfg["eval"]["test_file"], "sha1": _sha1(test_path), **stats(test)},
                          "examples": {"path": cfg["knowledge"]["examples"], "sha1": _sha1(ex_path),
                                       "n": len(examples)}},
                "sources": [p.name for p in eval_files + ex_files],
                "near_duplicates": {"examples_vs_eval": dup_ex[:50], "dev_vs_test": dup_dt[:50]}}
    mpath = resolve_path("evalset/MANIFEST.json")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dev": manifest["files"]["dev"], "test": manifest["files"]["test"], "examples": len(examples),
            "near_dup_examples_vs_eval": len(dup_ex), "near_dup_dev_vs_test": len(dup_dt), "manifest": str(mpath)}
