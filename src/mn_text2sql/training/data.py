"""Сургалтын өгөгдөл: нийтийн (BIRD train, Spider) асуултыг монгол руу орчуулах, галиг, өөрийн DB-ийн жишээ,
dev/test-тэй давхардал шүүх (туршилтын төлөвлөгөө: "Сургалтын өгөгдлийг цэвэрлэ. Dev, test асуулт болон тэдгээрийн
өөрчилсөн хувилбар сургалтад орохгүй; embedding-ийн төстэй байдлаар давхардлыг шүү").

TrainItem JSONL (data/train/*.jsonl):
  id, source (bird | spider | own_synth | human), engine (sqlite | postgres), db_id, db_path,
  question_en, question_mn, question_latin, evidence_en, evidence_mn, gold_sql, difficulty
"""

from __future__ import annotations

import json
import logging
import random
import threading
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from ..config import Config, load_models, resolve_path

log = logging.getLogger(__name__)


@dataclass
class TrainItem:
    id: str
    source: str
    engine: str
    gold_sql: str
    question_en: str = ""
    question_mn: str = ""
    question_latin: str = ""
    evidence_en: str = ""
    evidence_mn: str = ""
    db_id: str = ""
    db_path: str = ""
    difficulty: str = ""
    extra: dict = field(default_factory=dict)

    def question(self, variant: str) -> str:
        return {"latin": self.question_latin, "en": self.question_en}.get(variant) or self.question_mn

    def evidence(self, variant: str) -> str:
        return self.evidence_en if variant == "en" else (self.evidence_mn or self.evidence_en)


def load_training_cfg(path: str = "config/training.yaml") -> dict:
    with open(resolve_path(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def train_dir(tcfg: dict) -> Path:
    d = resolve_path(tcfg["data"]["dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_items(items: list[TrainItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")


SAMPLED_SOURCES = ("bird_mn.jsonl", "own_synth_clean.jsonl", "human.jsonl")


def load_item_index(tcfg: dict) -> dict[str, "TrainItem"]:
    """Sample хийсэн эх файлуудын бичлэг (id → TrainItem). bird_selected.jsonl (зөвхөн англи) зэргийг оруулахгүй;
    ижил id давхардвал монгол асуулттайг нь сонгоно."""
    d = train_dir(tcfg)
    out: dict[str, TrainItem] = {}
    for name in SAMPLED_SOURCES:
        for it in read_items(d / name):
            if it.id not in out or (not out[it.id].question_mn and it.question_mn):
                out[it.id] = it
    return out


def read_items(path: Path) -> list[TrainItem]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(TrainItem(**{k: v for k, v in d.items() if k in TrainItem.__dataclass_fields__}))
    return out


# ---------------------------------------------------------------------------
# BIRD train татах
# ---------------------------------------------------------------------------

def fetch_bird_train(cfg: Config, tcfg: dict, n: int | None = None, seed: int = 42) -> dict:
    """BIRD train (CC BY-SA 4.0) татаж задлах, DB-ээр давхаргалж n асуулт сонгох → data/train/bird_selected.jsonl."""
    import httpx
    raw = resolve_path("data/train_raw/bird")
    raw.mkdir(parents=True, exist_ok=True)
    zpath = raw / "train.zip"
    if not any(raw.glob("**/train.json")):
        if not zpath.exists():
            url = tcfg["data"]["sources"]["bird_train"]["url"]
            log.info("BIRD train татаж байна (~ хэдэн GB): %s", url)
            with httpx.stream("GET", url, follow_redirects=True, timeout=None) as r:
                r.raise_for_status()
                with open(zpath, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(raw)
    tj = next(raw.glob("**/train.json"))
    base = tj.parent
    dbzip = next(base.glob("train_databases.zip"), None)
    if dbzip and not (base / "train_databases").exists():
        with zipfile.ZipFile(dbzip) as z:
            z.extractall(base)
    data = json.loads(tj.read_text(encoding="utf-8"))
    n = int(n or tcfg["data"]["n_questions"])
    rng = random.Random(seed)
    by_db = defaultdict(list)
    for i, d in enumerate(data):
        by_db[d["db_id"]].append((i, d))
    chosen = []
    for db, rows in by_db.items():
        k = max(1, round(n * len(rows) / len(data)))
        chosen += rng.sample(rows, min(k, len(rows)))
    chosen = sorted(chosen)[:n]
    items = [TrainItem(id=f"bird-{i}", source="bird", engine="sqlite", gold_sql=d["SQL"], question_en=d["question"],
                       evidence_en=d.get("evidence", ""), db_id=d["db_id"],
                       db_path=str(base / "train_databases" / d["db_id"] / f"{d['db_id']}.sqlite"))
             for i, d in chosen]
    out = train_dir(tcfg) / "bird_selected.jsonl"
    write_items(items, out)
    return {"train_total": len(data), "selected": len(items), "dbs": len(by_db), "file": str(out)}


# ---------------------------------------------------------------------------
# BIRDTurk маягийн орчуулга (англи → монгол), галиг
# ---------------------------------------------------------------------------

def translate_items(cfg: Config, tcfg: dict, src: str = "bird_selected.jsonl", model_name: str | None = None,
                    base_url: str | None = None, workers: int = 16, seed: int = 42) -> dict:
    from ..bird.data import MN_FEWSHOT, MN_SYSTEM, _protect, _restore, latin_variant
    from ..llm.client import make_llm
    registry = load_models()
    model_name = model_name or registry.get("translator")
    mspec = registry["models"].get(model_name, {})
    llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
    if base_url:
        llm_cfg["base_url"] = base_url
    if mspec.get("extra_body"):
        llm_cfg["extra_body"] = mspec["extra_body"]
    llm = make_llm(llm_cfg)
    d = train_dir(tcfg)
    items = read_items(d / src)
    out = d / src.replace("_selected", "_mn")
    done = {it.id for it in read_items(out)}
    lock = threading.Lock()
    latin_share = float(tcfg["data"].get("latin_share", 0.3))

    def tr(text: str, sql: str) -> str:
        if not text.strip():
            return ""
        masked, mapping = _protect(text, sql)
        msgs = [{"role": "system", "content": MN_SYSTEM}]
        for s, t in MN_FEWSHOT:
            msgs += [{"role": "user", "content": s}, {"role": "assistant", "content": t}]
        msgs.append({"role": "user", "content": masked})
        resp = llm.chat(msgs, temperature=0.0, max_tokens=512)
        return _restore(resp.texts[0].split("</think>")[-1].strip(), mapping)[0]

    def work(it: TrainItem):
        if it.id in done:
            return
        it.question_mn = tr(it.question_en, it.gold_sql)
        it.evidence_mn = tr(it.evidence_en, it.gold_sql)
        rng = random.Random(f"{seed}:{it.id}")
        it.question_latin = latin_variant(it.question_mn, rng)
        it.extra["train_variant"] = "latin" if rng.random() < latin_share else "mn"
        with lock, open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, items))
    return {"file": str(out), "total": len(items), "translated": len(read_items(out))}


# ---------------------------------------------------------------------------
# Жишээний сан (knowledge/examples.jsonl) — C3
# ---------------------------------------------------------------------------

def human_items(cfg: Config) -> list[TrainItem]:
    path = resolve_path(cfg["knowledge"]["examples"])
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if not d.get("gold_sql"):
            continue
        out.append(TrainItem(id=f"human-{d['id']}", source="human", engine="postgres", gold_sql=d["gold_sql"],
                             question_en=d.get("question_en", ""), question_mn=d["question_mn"],
                             question_latin=d.get("question_latin", ""), difficulty=d.get("difficulty", ""),
                             extra={"family": d.get("family"), "values": d.get("values") or []}))
    return out


# ---------------------------------------------------------------------------
# dev/test-тэй давхардал шүүх
# ---------------------------------------------------------------------------

def dedup_against_eval(cfg: Config, items: list[TrainItem], threshold: float = 0.8,
                       use_embeddings: bool = True) -> tuple[list[TrainItem], list[dict]]:
    """Eval-ийн асуулттай (бүх хувилбар) ойролцоо сургалтын асуултыг хасна. Embedding (bge-m3) байвал cosine ≥ 0.9,
    үгүй бол тэмдэгтийн 3-gram cosine ≥ threshold. Өөрийн DB-ийн мөрийг SQL үр дүнгийн hash-аар мөн шалгана."""
    from ..eval.dataset import load_items
    from ..eval.split import _cos, _vec
    evals = load_items([p for p in (resolve_path(cfg["eval"]["dev_file"]), resolve_path(cfg["eval"]["test_file"]))
                        if p.exists()])
    eval_texts = []
    for it in evals:
        for v in ("mn", "latin", "en", "mixed"):
            q = it.question(v)
            if q:
                eval_texts.append((it.id, q))
    dropped = []
    keep = [True] * len(items)
    emb = None
    if use_embeddings:
        try:
            from ..retrieval.embed import get_embedder
            emb = get_embedder(cfg["retrieval"]["embedding_model"], cfg["retrieval"]["embedding_device"],
                               int(cfg["retrieval"]["embedding_batch_size"]))
        except Exception as e:  # sentence-transformers байхгүй
            log.warning("embedding-гүй давхардлын шүүлт: %s", e)
    if emb is not None:
        import numpy as np
        ev = emb.encode([q for _, q in eval_texts])
        for i, it in enumerate(items):
            qs = [q for q in (it.question_mn, it.question_latin, it.question_en) if q]
            if not qs:
                continue
            tv = emb.encode(qs)
            sims = tv @ ev.T
            j = np.unravel_index(int(np.argmax(sims)), sims.shape)
            if sims[j] >= 0.9:
                keep[i] = False
                dropped.append({"id": it.id, "eval_id": eval_texts[j[1]][0], "sim": round(float(sims[j]), 3)})
    else:
        ev = [(eid, _vec(q)) for eid, q in eval_texts]
        for i, it in enumerate(items):
            for q in (it.question_mn, it.question_latin, it.question_en):
                if not q:
                    continue
                vq = _vec(q)
                best = max(((eid, _cos(vq, v)) for eid, v in ev), key=lambda x: x[1], default=(None, 0))
                if best[1] >= threshold:
                    keep[i] = False
                    dropped.append({"id": it.id, "eval_id": best[0], "sim": round(best[1], 3)})
                    break
    # өөрийн DB: eval-ийн gold-той ижил үр дүнтэй (ижил асуултын өөр хэлбэр) мөрийг хасна
    own = [i for i, it in enumerate(items) if keep[i] and it.engine == "postgres"]
    if own:
        from ..db.conn import bot_dsn
        from ..db.executor import Executor
        from ..eval.compare import result_hash
        from ..eval.goldcheck import make_guard
        guard, ex = make_guard(cfg), Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"])
        eval_hashes = {}
        for it in evals:
            for s in it.gold_sqls():
                g = guard.check(s)
                r = ex.run(g.sql) if g.ok else None
                if r is not None and r.ok and r.rowcount > 0:
                    eval_hashes[result_hash(r.columns, r.rows)] = it.id
        for i in own:
            g = guard.check(items[i].gold_sql)
            r = ex.run(g.sql) if g.ok else None
            if r is not None and r.ok:
                h = result_hash(r.columns, r.rows)
                if h in eval_hashes:
                    keep[i] = False
                    dropped.append({"id": items[i].id, "eval_id": eval_hashes[h], "reason": "same_result"})
        ex.close()
    return [it for i, it in enumerate(items) if keep[i]], dropped
