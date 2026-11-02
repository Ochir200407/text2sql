"""Нэр томьёоны хуваарилагч (reranker): bge-reranker-v2-m3-ийг өөрийн жишээний «хэрэгтэй нэр томьёо» шошгоор нэмж сургаж,
eval асуулт бүрт сонголтын кэш үүсгэнэ (Arctic-тай GPU булаалдахгүй — орчуулгын кэштэй ижил арга).

    python scripts/rerank/rr.py train  --out <dir> [--data v2]        # knowledge/examples*.jsonl (eval-тэй давхцахгүй)
    python scripts/rerank/rr.py select --model <dir> --split dev --tag bge_ft [--translation m1_madlad_improved]
    python scripts/rerank/rr.py select --split dev --tag bge_zero       # --model-гүй = сургаагүй загвар (харьцуулалт)

Нэр дэвшигч = pipeline-ийн нэр томьёоны хайлт (Retriever, hybrid) top-12; индексгүй бол BM25. Асуулт = MADLAD-ийн англи
орчуулга (кэш байвал) эсвэл монгол. Сонголт: оноо ≥ босго (сургалтын holdout дээр сонгосон), дээд тал нь 6.
Кэш: build/rerank/<tag>.json = {item_id: [term_id, ...]} → RunOptions.glossary_select_cache. dev/test-ийн шошгыг зөвхөн
хэмжихэд ашиглана (босго сонголт, сургалтад орохгүй).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

BASE = "BAAI/bge-reranker-v2-m3"
POOL, MAX_SEL = 12, 6


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def doc_text(g) -> str:
    return f"{g.term} / {g.en or ''}: {(g.definition_en or g.definition_mn or '')[:220]} SQL: {(g.sql or '')[:200]}"


class Candidates:
    """Pipeline-тэй ижил нэр томьёоны хайлт (индекс байвал hybrid, үгүй бол BM25)."""

    def __init__(self):
        from mn_text2sql.config import load_config
        from mn_text2sql.knowledge.semantic import load_glossary
        cfg = load_config()
        self.terms = {g.id: g for g in load_glossary(cfg["knowledge"]["glossary"])}
        self.ret = None
        try:
            from mn_text2sql.retrieval.retriever import get_retriever
            self.ret = get_retriever(cfg)
        except Exception as e:   # локал: индексгүй
            from mn_text2sql.retrieval.indexes import GlossaryIndex
            print(f"[rr] Retriever алга ({e}) — BM25", flush=True)
            self.bm = GlossaryIndex(list(self.terms.values()), None)

    @staticmethod
    def terms_only() -> dict:
        from mn_text2sql.config import load_config
        from mn_text2sql.knowledge.semantic import load_glossary
        return {g.id: g for g in load_glossary(load_config()["knowledge"]["glossary"])}

    def __call__(self, question_mn: str) -> list[str]:
        if self.ret is not None:
            r = self.ret.retrieve(question_mn, "mn", glossary_k=POOL, want_values=False, want_glossary=True,
                                  want_examples=False)
            return [g.id for g in r.glossary]
        return [g.id for g in self.bm.search(question_mn, None, k=POOL, alpha=0.5)]


def load_model(path: str, device):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(path)
    m = AutoModelForSequenceClassification.from_pretrained(path).to(device)
    return tok, m


def score(tok, m, device, query: str, docs: list[str]) -> list[float]:
    import torch
    with torch.inference_mode(), torch.autocast(device.type, enabled=device.type == "cuda"):
        enc = tok([[query, d] for d in docs], padding=True, truncation=True, max_length=320, return_tensors="pt").to(device)
        return torch.sigmoid(m(**enc).logits.view(-1).float()).tolist()


def select(scored: list[tuple[str, float]], thr: float) -> list[str]:
    ranked = sorted(scored, key=lambda x: -x[1])
    return [c for c, s in ranked[:MAX_SEL] if s >= thr]


def metrics(rows: list[tuple[set, list[str]]]) -> dict:
    hit = tot = full = ns = 0
    for need, sel in rows:
        hit += len(need & set(sel)); tot += len(need); full += need <= set(sel); ns += len(sel)
    n = max(1, len(rows))
    return {"recall": hit / max(1, tot), "all_found": full / n, "avg_selected": ns / n, "n": len(rows)}


def pick_threshold(rows_scored: list[tuple[set, list[tuple[str, float]]]]) -> float:
    """Нэр томьёо шаарддаг асуултад «бүгд олдсон» хамгийн өндөр, хэрэггүйд цөөн сонголт (F2-тэй төстэй)."""
    best = (-1.0, 0.5)
    for t in [i / 100 for i in range(2, 96, 2)]:
        m = metrics([(need, select(s, t)) for need, s in rows_scored if need])
        extra = metrics([(need, select(s, t)) for need, s in rows_scored if not need])["avg_selected"]
        val = m["all_found"] + 0.5 * m["recall"] - 0.05 * m["avg_selected"] - 0.05 * extra
        best = max(best, (val, t))
    return best[1]


EXAMPLE_FILES = ("knowledge/examples.jsonl", "knowledge/examples_plus_all.jsonl")
# Сургалтын өгөгдлийн хувилбар (B8, FULL_REVIEW_2026-10-30):
#   v1 — V25-д сургасан (M1rr-ийн одоогийн загвар), байт бүрээрээ хуучнаараа: эерэг нэр томьёог holdout-ын нэр дэвшигчдэд ч
#        оруулдаг (holdout recall/«бүгд олдсон» хайлтын дээд хязгаараас давж хөөрөгддөг, босго ч тэр бохир дээр сонгогдоно);
#        pool дүүрэн үед c[-1]-ийг солдог — тэр нь өөрөө эерэг (саяхан оруулсан ч) байвал алга болно.
#   v2 — эерэгийг зөвхөн сургалтын хэсэгт, хамгийн доод рангийн СӨРӨГ нэр дэвшигчийг сольж оруулна; holdout = бодит хайлт.
DATA_VERSIONS = ("v1", "v2")


def inject_positives(cands: list[str], need: list[str], pool: int = POOL, mode: str = "v1") -> list[str]:
    """Хэрэгтэй (эерэг) нэр томьёог нэр дэвшигчдэд заавал оруулна. v1 = хуучин (алдаатай) зан төлөв, хуулбарлахад."""
    c = list(cands)
    if mode == "v1":
        for t in need:
            if t not in c:
                c = c[:-1] + [t] if len(c) >= pool else c + [t]
        return c
    pos = set(need)
    for t in dict.fromkeys(need):
        if t in c:
            continue
        if len(c) >= pool:
            for i in range(len(c) - 1, -1, -1):    # хамгийн доод рангийн сөрөг нэр дэвшигч
                if c[i] not in pos:
                    del c[i]
                    break
        c.append(t)
    return c


def load_items(terms: dict, limit: int = 0) -> list[dict]:
    items = []
    for f in EXAMPLE_FILES:
        for r in read_jsonl(ROOT / f):
            if r.get("category") not in (None, "answerable"):
                continue
            need = [t for t in (r.get("glossary_terms") or []) if t in terms]
            items.append({"id": r["id"], "family": r.get("family") or r["id"], "q_mn": r["question_mn"],
                          "q_en": r.get("question_en") or r["question_mn"], "need": need})
    items = items[:limit or None]
    seen, uniq = set(), []
    for it in items:                       # examples_plus_all нь examples-ийн давхардалгүй, гэхдээ id-аар баталгаажуулна
        if it["id"] not in seen:
            seen.add(it["id"]); uniq.append(it)
    return uniq


def split_holdout(uniq: list[dict]) -> set:
    """Family-аар 15% holdout (random.seed(13)-ийн дараах эхний random дуудлага — v1/v2 ижил хуваалт)."""
    fams = sorted({it["family"] for it in uniq}); random.shuffle(fams)
    return set(fams[: max(1, len(fams) * 15 // 100)])


def build_candidates(uniq: list[dict], hold: set, cand, mode: str = "v1") -> None:
    """it["cands"] (сургалт/holdout), it["retrieved"] (жинхэнэ хайлт) — v2-д holdout-д эерэг оруулахгүй."""
    if mode not in DATA_VERSIONS:
        raise ValueError(f"data version {mode!r} not in {DATA_VERSIONS}")
    for it in uniq:
        c = cand(it["q_mn"])
        it["retrieved"] = list(c)
        if mode == "v1" or it["family"] not in hold:
            c = inject_positives(c, it["need"], POOL, mode)
        it["cands"] = c


def _norm(s) -> str:
    return " ".join(str(s or "").lower().split())


def eval_overlap(items: list[dict], eval_rows: list[dict], raw_examples: list[dict] | None = None) -> dict:
    """Сургалтын жишээ ↔ eval: id, family, асуулт (mn/en/latin/mixed, нормчилсон), gold SQL-ийн яг давхцал."""
    ids = {it["id"] for it in items}; fams = {it["family"] for it in items}
    qs = {_norm(it["q_mn"]) for it in items} | {_norm(it["q_en"]) for it in items}
    sqls = set()
    for r in raw_examples or []:
        qs |= {_norm(r.get(k)) for k in ("question_latin", "question_mixed") if r.get(k)}
        if r.get("gold_sql"):
            sqls.add(_norm(r["gold_sql"]))
    qs.discard("")
    out = {"id": 0, "family": 0, "question": 0, "gold_sql": 0}
    for e in eval_rows:
        out["id"] += e["id"] in ids
        out["family"] += (e.get("family") or e["id"]) in fams
        out["question"] += any(_norm(e.get(k)) in qs for k in ("question_mn", "question_en", "question_latin",
                                                                "question_mixed") if e.get(k))
        out["gold_sql"] += bool(e.get("gold_sql")) and _norm(e["gold_sql"]) in sqls
    return out


def check_no_eval_leak(items: list[dict]) -> dict:
    """dev/test-ийн асуулт сургалтад орсон бол зогсооно (id/family/асуулт). gold SQL давхцал = мэдээлэл (загвар ижил байж болно)."""
    raw = [r for f in EXAMPLE_FILES for r in read_jsonl(ROOT / f)]
    res = {}
    for sp in ("dev", "test"):
        p = ROOT / f"evalset/{sp}.jsonl"
        if not p.exists():
            continue
        res[sp] = o = eval_overlap(items, read_jsonl(p), raw)
        if o["id"] or o["family"] or o["question"]:
            raise SystemExit(f"[rr] {sp}-ийн асуулт reranker-ийн сургалтад орсон: {o}")
    return res


def cmd_train(a) -> None:
    import torch
    random.seed(13); torch.manual_seed(13)
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cand = Candidates()
    uniq = load_items(cand.terms, a.limit)
    leak = check_no_eval_leak(uniq)
    hold = split_holdout(uniq)
    build_candidates(uniq, hold, cand, a.data)
    train = [it for it in uniq if it["family"] not in hold]
    held = [it for it in uniq if it["family"] in hold]
    ceil = metrics([(set(it["need"]), it["retrieved"]) for it in held if it["need"]])
    print(f"[rr] өгөгдөл {a.data}: жишээ {len(uniq)} (сургалт {len(train)}, holdout {len(held)}), төхөөрөмж {dev}; "
          f"eval давхцал {leak}; holdout-ын хайлтын дээд хязгаар {ceil}", flush=True)
    del cand                               # хайлтын загварыг санах ойгоос чөлөөлнө
    tok, m = load_model(BASE, dev)
    # T4 (15 GB): 568M параметрийн AdamW төлөв + идэвхжүүлэлт багтахгүй байсан (Version 24 OOM) → word embedding (≈250M)
    # хөлдөөж, gradient checkpointing
    for p_ in m.roberta.embeddings.parameters():
        p_.requires_grad = False
    m.gradient_checkpointing_enable()
    docs = {k: doc_text(g) for k, g in Candidates.terms_only().items()}
    pairs = []
    for it in train:
        for q in (it["q_en"], it["q_mn"]):     # англи (MADLAD-ийн гаралттай төстэй) ба монгол асуулт
            pairs += [(q, docs[c], 1.0 if c in it["need"] else 0.0) for c in it["cands"]]
    pos = sum(p[2] for p in pairs)
    print(f"[rr] хос {len(pairs)} (эерэг {int(pos)})", flush=True)
    opt = torch.optim.AdamW([p_ for p_ in m.parameters() if p_.requires_grad], lr=a.lr, weight_decay=0.01)
    steps = a.epochs * ((len(pairs) + a.batch - 1) // a.batch)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, steps // 10)) * max(0.0, (steps - s) / steps))
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor((len(pairs) - pos) / max(1, pos)).clamp(max=8).to(dev))
    scaler = torch.amp.GradScaler(enabled=dev.type == "cuda")
    m.train(); step = 0; t0 = time.time()
    for ep in range(a.epochs):
        random.shuffle(pairs)
        for i in range(0, len(pairs), a.batch):
            b = pairs[i:i + a.batch]
            enc = tok([[q, d] for q, d, _ in b], padding=True, truncation=True, max_length=320, return_tensors="pt").to(dev)
            y = torch.tensor([y for *_, y in b], device=dev)
            with torch.autocast(dev.type, enabled=dev.type == "cuda"):
                loss = lossf(m(**enc).logits.view(-1).float(), y)
            opt.zero_grad(); scaler.scale(loss).backward(); scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(m.parameters(), 1.0); scaler.step(opt); scaler.update(); sched.step(); step += 1
            if step % 50 == 0:
                print(f"[rr] эпох {ep + 1} алхам {step}/{steps} loss {loss.item():.4f} ({time.time() - t0:.0f} с)", flush=True)
            if a.max_minutes and time.time() - t0 > a.max_minutes * 60:
                break
    m.eval()
    held_scored = [(set(it["need"]), list(zip(it["cands"], score(tok, m, dev, it["q_en"], [docs[c] for c in it["cands"]]))))
                   for it in held]
    thr = pick_threshold(held_scored)
    hm = metrics([(n, select(s, thr)) for n, s in held_scored if n])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    m.save_pretrained(out); tok.save_pretrained(out)
    meta = {"base": BASE, "threshold": thr, "holdout": hm, "train_items": len(train), "pairs": len(pairs),
            "epochs": a.epochs, "lr": a.lr, "seconds": round(time.time() - t0),
            "data": a.data, "holdout_retrieval_ceiling": ceil, "eval_overlap": leak}
    (out / "rr_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[rr] хадгалав {out}: босго {thr:.2f}, holdout {hm}", flush=True)


def translations(tag: str | None) -> dict:
    if not tag:
        return {}
    p = ROOT / "build/translations" / f"{tag}.jsonl"
    if not p.exists():
        print(f"[rr] орчуулгын кэш алга ({p}) — монгол асуултаар", flush=True)
        return {}
    return {r["id"]: r["text"] for r in read_jsonl(p) if r.get("variant") == "mn"}


def cmd_select(a) -> None:
    import torch
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    path = a.model or BASE
    meta = json.loads((Path(path) / "rr_meta.json").read_text(encoding="utf-8")) if a.model else {}
    thr = a.threshold if a.threshold is not None else meta.get("threshold", 0.5)
    tok, m = load_model(path, dev); m.eval()
    cand = Candidates(); tr = translations(a.translation)
    docs = {k: doc_text(g) for k, g in cand.terms.items()}
    cache, rows, base, t0 = {}, [], [], time.time()
    items = read_jsonl(ROOT / f"evalset/{a.split}.jsonl")
    for r in items[:a.limit or None]:
        c = cand(r["question_mn"])
        q = tr.get(r["id"]) or r["question_mn"]
        sel = select(list(zip(c, score(tok, m, dev, q, [docs[x] for x in c]))), thr)
        cache[r["id"]] = sel
        if r.get("category") == "answerable" and r.get("glossary_terms"):
            rows.append((set(r["glossary_terms"]), sel))
            base.append((set(r["glossary_terms"]), c[:4]))
    out = ROOT / "build/rerank" / f"{a.tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"[rr] {a.split}: {len(cache)} асуулт → {out} ({time.time() - t0:.0f} с), босго {thr:.2f}", flush=True)
    print(f"[rr] {a.split} хэмжүүр (нэр томьёо шаарддаг {len(rows)}): reranker {metrics(rows)} | хайлтын top4 {metrics(base)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--out", required=True)
    t.add_argument("--epochs", type=int, default=3)
    t.add_argument("--lr", type=float, default=2e-5)
    t.add_argument("--batch", type=int, default=16)
    t.add_argument("--max-minutes", type=float, default=0)
    t.add_argument("--limit", type=int, default=0, help="туршилт: эхний N жишээ")
    t.add_argument("--data", default="v1", choices=DATA_VERSIONS,
                   help="сургалтын өгөгдлийн хувилбар: v1 = V25/M1rr (хуулбарлах), v2 = B8 засвар (holdout-д эерэг оруулахгүй)")
    s = sub.add_parser("select")
    s.add_argument("--model", default="")
    s.add_argument("--split", default="dev", choices=["dev", "test", "fresh"])
    s.add_argument("--tag", required=True)
    s.add_argument("--translation", default="m1_madlad_improved")
    s.add_argument("--threshold", type=float, default=None)
    s.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    {"train": cmd_train, "select": cmd_select}[a.cmd](a)


if __name__ == "__main__":
    main()
