"""Дасан зохицох (эрт зогсох) self-consistency — офлайн симулятор ба онлайн зогсоох дүрэм.

Санаа (Adaptive-Consistency, Aggarwal et al. 2023; ESC "Escape Sky-high Cost", Li et al. 2024):
8 хувилбарыг үргэлж үүсгэхийн оронд хувилбарыг давалгаагаар (wave) үүсгэж, санал хангалттай
нийлсэн бол зогсоно. Хувилбарууд нэг vLLM дуудлагаас (n=8) i.i.d. түүвэрлэгддэг тул тэдгээрийн
аль ч дэд олонлог/дараалал нь n=k үүсгэлттэй статистикийн хувьд ижил — иймээс бэлэн dev run-ын
(E2/E4/E5h) `cand_hashes`, `cand_correct` дээр үүсгэлтгүйгээр симуляци хийж болно.

Ажиллуулах (DB, загвар хэрэггүй):
    PYTHONPATH=src python -m mn_text2sql.eval.adaptive --perms 200

Зөвхөн dev run-ыг уншина (test-ийг хэзээ ч уншихгүй).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

RUN_FILE = "arctic-r1-7b__mn__s1__h_H4.jsonl"
CONST_STATUSES = ("refused", "clarify")   # дүрэм/intent-ээр шийдэгдсэн — симуляцид тогтмол


# ---------------------------------------------------------------------------- зогсоох дүрэм
def beta_leader_prob(a: int, b: int) -> float:
    """P(p1 > p2 | a, b): тэргүүлэгч a, хоёрдугаар b санал — Beta(a+1, b+1)-ийн P(x > 0.5).

    Adaptive-Consistency-ийн Beta шалгуур (шилдэг 2 хариултын хувьд Dirichlet-ийн хялбаршуулга).
    Бүхэл параметртэй үед I_x(α, β) = Σ_{j=α}^{n} C(n, j) x^j (1-x)^{n-j}, n = α+β-1 тул scipy хэрэггүй.
    """
    al, be = a + 1, b + 1
    n = al + be - 1
    cdf_half = sum(math.comb(n, j) for j in range(al, n + 1)) / 2 ** n   # I_{0.5}(α, β)
    return 1.0 - cdf_half


def _votes(hashes: list[str | None], bad: frozenset = frozenset()) -> Counter:
    """Санал тоолох хүчинтэй хувилбарууд: алдаагүй (hash бий) бөгөөд шүүлтэд (degenerate/буруу утга) ороогүй."""
    good = [h for h in hashes if h]
    kept = [h for h in good if h not in bad] or good
    return Counter(kept)


@dataclass(frozen=True)
class Policy:
    """waves — давалгаа бүрийн хувилбарын тоо (нийлбэр ≤ 8); rule — давалгаа бүрийн дараах шалгуур.

    rule: "none" (тогтмол k) | "agree" (одоог хүртэлх бүх хувилбар нэг үр дүнтэй, тоо ≥ param) |
          "beta" (beta_leader_prob ≥ param) | "esc" (сүүлийн давалгааны бүх хувилбар нэг үр дүнтэй).
    greedy — эхэнд нэг greedy (temperature 0) хувилбар (E5h-ийн H4; офлайнд run-ын жингээр, онлайнд 1 санал); дүрмийг түүний дараа ч шалгана.
    """
    name: str
    waves: tuple[int, ...]
    rule: str = "none"
    param: float = 0.0
    greedy: bool = False

    def stop(self, hashes: list[str | None], last_wave: list[str | None], bad: frozenset = frozenset()) -> bool:
        if self.rule == "none":
            return False
        if self.rule == "esc":
            return len(last_wave) >= 2 and all(last_wave) and len(set(last_wave)) == 1 and last_wave[0] not in bad
        votes = _votes(hashes, bad)
        if not votes:
            return False
        top = votes.most_common(2)
        a = top[0][1]
        b = top[1][1] if len(top) > 1 else 0
        if top[0][0] in bad:
            return False
        if self.rule == "agree":
            return all(h == top[0][0] for h in hashes) and a >= self.param
        if self.rule == "beta":
            return beta_leader_prob(a, b) >= self.param
        raise ValueError(f"тодорхойгүй дүрэм: {self.rule}")


def parse_spec(spec: str) -> Policy:
    """Онлайн тохиргооны мөр → Policy. Жишээ: "beta:0.8:w4", "g+beta:0.8:w2", "agree:2", "esc:w4", "fixed:4".

    w<N> — давалгааны хэмжээ (анхдагч 2); дээд тоог `candidates` хязгаарлана.
    """
    text = str(spec).strip()
    greedy = text.lower().startswith("g+")          # эхэнд нэг greedy (temperature 0) хувилбар
    parts = [p.strip() for p in text[2 if greedy else 0:].split(":") if p.strip()]
    if not parts:
        raise ValueError("adaptive_stop хоосон")
    rule, rest = parts[0].lower(), parts[1:]
    wave, param = 2, 0.0
    for p in rest:
        if p.lower().startswith("w"):
            wave = int(p[1:])
        else:
            param = float(p)
    if rule == "fixed":
        k = int(param) or 8
        return Policy(spec, (k,), greedy=greedy)
    if rule not in ("agree", "beta", "esc"):
        raise ValueError(f"adaptive_stop: тодорхойгүй дүрэм {rule!r}")
    if rule == "agree":
        m = int(param) or 2
        # эхний m нийлвэл зогсоно; эс бөгөөс үлдсэнийг нэг давалгаагаар
        return Policy(spec, (m, 8 - m), "agree", m, greedy)
    if wave < 1:
        raise ValueError("давалгааны хэмжээ ≥ 1")
    waves = tuple([wave] * (8 // wave) + ([8 % wave] if 8 % wave else []))
    return Policy(spec, waves, rule, param or (0.95 if rule == "beta" else 0.0), greedy)


def schedule(policy: Policy, max_n: int) -> list[int]:
    """Давалгааны хэмжээг max_n-д тааруулна (онлайнд candidates < 8 байж болно)."""
    out, tot = [], 0
    for w in policy.waves:
        w = min(w, max_n - tot)
        if w <= 0:
            break
        out.append(w)
        tot += w
    if tot < max_n and policy.rule != "none":
        out.append(max_n - tot)
    return out


class AdaptiveStopper:
    """Онлайн hook (pipeline/orchestrator.py): давалгаа бүрийн дараа `done(cands)`.

    cands — pipeline.Candidate (ok, result_hash, degenerate, bad_literals); use_filter=True бол
    Pipeline.vote-ийн E4 шүүлттэй адилаар degenerate/буруу утгатайг санал тоололтоос хасна.
    """

    def __init__(self, spec: str, max_n: int, use_filter: bool = False):
        self.policy = parse_spec(spec)
        self.waves = schedule(self.policy, max_n)
        self.use_filter = use_filter

    def done(self, cands: list, last_wave: list) -> bool:
        def h(c):
            return c.result_hash if (c.ok and c.result_hash) else None
        bad = frozenset(c.result_hash for c in cands if self.use_filter and c.ok and c.result_hash
                        and (c.degenerate or c.bad_literals))
        return self.policy.stop([h(c) for c in cands], [h(c) for c in last_wave], bad)


# ---------------------------------------------------------------------------- санал хураалт (Pipeline.vote-той ижил)
def vote(hashes: list[str | None], bad: frozenset = frozenset()) -> int | None:
    """Сонгогдсон хувилбарын индекс (hash байхгүй бол None). Тэнцвэл эхэлж гарсан нь."""
    good = [(i, h) for i, h in enumerate(hashes) if h]
    if not good:
        return None
    kept = [(i, h) for i, h in good if h not in bad] or good
    counts = Counter(h for _, h in kept)
    first = {}
    for i, h in enumerate(hashes):
        first.setdefault(h, i)
    best = max(counts, key=lambda k: (counts[k], -first[k]))
    return next(i for i, h in kept if h == best)


# ---------------------------------------------------------------------------- өгөгдөл
@dataclass
class Item:
    qid: str
    category: str
    const: str | None                 # тогтмол статус (refused/clarify) эсвэл None
    hashes: list[str | None]          # түүвэрлэсэн 8 хувилбар
    correct: list[bool]
    bad: frozenset                    # шүүлтэд хасагдсан нь мэдэгдэх hash (доод хязгаар)
    g_hash: str | None = None         # greedy (H4) хувилбар
    g_correct: bool = False
    g_weight: int = 0
    gen_ms: float = 0.0               # E2: n=8 үүсгэлтийн хугацаа (засвар орсон)
    ovh_ms: float = 0.0               # latency - gen (retrieval, prompt г.м.)
    g_ms: float = 0.0                 # H4: n=1 greedy үүсгэлтийн хугацаа
    base_correct: bool = False
    base_status: str = ""


def _load(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as fh:
        return {r["question_id"]: r for r in map(json.loads, fh)}


def _infer_bad(hashes: list[str | None], chosen: str | None, status: str) -> frozenset:
    """Онлайн шүүлтийн (degenerate/буруу утга) хассан hash-ыг run-ын сонголтоос сэргээнэ.

    Шүүлтгүй олонх нь сонгогдсоноос дээгүүр жагссан hash бүр хасагдсан байх ёстой; бүх хувилбар
    шүүгдсэн бол (abstained) хүчинтэй бүх hash. Бага саналтай хасагдсан hash-ыг мэдэх боломжгүй (доод хязгаар).
    """
    valid = [h for h in hashes if h]
    if not valid:
        return frozenset()
    if status == "abstained":
        return frozenset(valid)
    if not chosen or chosen not in valid:
        return frozenset()
    counts = Counter(valid)
    first = {}
    for i, h in enumerate(hashes):
        first.setdefault(h, i)
    key = lambda k: (counts[k], -first[k])   # noqa: E731
    return frozenset(h for h in counts if key(h) > key(chosen))


def load_items(root: Path, run: str, greedy_run: str | None = "H4", e2: str = "E2") -> list[Item]:
    """run — E4 эсвэл E5h_w* (E2-оос дахин сонгосон); E5h бол эхний w хувилбар нь greedy (H4)."""
    rows = _load(root / run / RUN_FILE)
    base = _load(root / e2 / RUN_FILE)
    gfiles = sorted((root / greedy_run).glob("*.jsonl")) if greedy_run else []
    gre = _load(gfiles[0]) if gfiles else {}          # H4 нь seed 0 (s0) — файлын нэр өөр
    items = []
    for qid, r in rows.items():
        hs = list(r.get("cand_hashes") or [])
        cc = [bool(x) for x in (r.get("cand_correct") or [])]
        b = base.get(qid, {})
        n_s = len(b.get("cand_hashes") or [])            # түүвэрлэсэн хувилбарын тоо (8)
        gw = len(hs) - n_s if n_s and len(hs) > n_s else 0
        status = r.get("status") or ""
        it = Item(qid=qid, category=r.get("category", ""),
                  const=status if status in CONST_STATUSES else None,
                  hashes=hs[gw:], correct=cc[gw:], bad=_infer_bad(hs, r.get("result_hash"), status),
                  base_correct=bool(r.get("correct")), base_status=status)
        if gw:
            it.g_hash, it.g_correct, it.g_weight = hs[0], cc[0], gw
        gen = float(b.get("gen_ms") or 0.0)
        it.gen_ms = gen
        it.ovh_ms = max(0.0, float(b.get("latency_ms") or 0.0) - gen)
        it.g_ms = float((gre.get(qid) or {}).get("gen_ms") or 0.0)
        items.append(it)
    return items


# ---------------------------------------------------------------------------- симуляци
@dataclass
class Outcome:
    correct: bool
    answered: bool
    n_gen: int              # үүсгэсэн хувилбар (greedy орно)
    waves: list[int] = field(default_factory=list)


def run_item(it: Item, pol: Policy, order: list[int] | None = None) -> Outcome:
    """Нэг асуулт: давалгаагаар хувилбар нэмж, дүрэм биелбэл зогсоод санал хураана."""
    idx = order if order is not None else list(range(len(it.hashes)))
    hs = [it.hashes[i] for i in idx]
    cc = [it.correct[i] for i in idx]
    cur_h: list[str | None] = []
    cur_c: list[bool] = []
    waves: list[int] = []
    use_greedy = pol.greedy and it.g_weight > 0
    if use_greedy:
        cur_h += [it.g_hash] * it.g_weight
        cur_c += [it.g_correct] * it.g_weight
        waves.append(1)
        stopped = pol.stop(cur_h, [it.g_hash], it.bad)
    else:
        stopped = False
    pos = 0
    if not stopped:
        for w in schedule(pol, len(hs)):
            wave_h = hs[pos:pos + w]
            cur_h += wave_h
            cur_c += cc[pos:pos + w]
            pos += w
            waves.append(w)
            if pol.stop(cur_h, wave_h, it.bad):
                break
    n_gen = pos + (1 if use_greedy else 0)
    if it.const:
        return Outcome(False, False, n_gen, waves)
    k = vote(cur_h, it.bad)
    if k is None or cur_h[k] in it.bad:
        return Outcome(False, False, n_gen, waves)     # abstain_on_error / шүүлтийн дараа ч муу → татгалзана
    return Outcome(cur_c[k], True, n_gen, waves)


def wave_latency_ms(it: Item, waves: list[int], greedy: bool, alpha: float) -> float:
    """B хувилбар (ганц хэрэглэгч, давалгаанууд дараалсан): давалгаа = T1·(1 + α(w−1)).

    T1 — H4 (n=1, 8 зэрэг асуулт = GPU дээр ~8 дараалал зэрэг) gen_ms; энэ нь ганц хэрэглэгчийн n≤8
    batch-ийн нэг дарааллын хугацаатай ойролцоо. α — w дарааллын хамгийн урт нь дунджаас урт байх нэмэгдэл.
    Байхгүй бол E2 gen_ms/8. Хувилбар бүрийн дараалсан засвар (repair) энд ороогүй.
    """
    t1 = it.g_ms or (it.gen_ms / 8.0)
    return it.ovh_ms + sum(t1 * (1 + alpha * (w - 1)) for w in waves)


def load_latency_ms(it: Item, n_gen: int, greedy: bool) -> float:
    """A хувилбар (ачаалалтай сервер, eval шиг 8 зэрэг асуулт — throughput-оор хязгаарлагдана):
    хугацаа ≈ үүсгэсэн хувилбарын (токены) тоотой пропорциональ: ovh + gen_E2 · n_gen/8 (greedy = 1 хувилбар)."""
    return it.ovh_ms + it.gen_ms * n_gen / 8.0


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def credit_other(it: Item, o: Outcome) -> bool:
    """L5: хариулах боломжгүй/хоёрдмол асуултын «зөв» — runner.score_answer-тэй ижил: хариулах боломжгүйд аль ч
    хариулаагүй төлөв (refused/clarify/abstained) зөв; хоёрдмолд зөвхөн тодруулга (clarify) эсвэл аль нэг тайлбартай
    таарсан хариулт зөв (татгалзал/abstain буруу). Өмнө нь хоёрдмолд аль ч хариулаагүй төлөвийг зөв, тайлбартай
    таарсан хариултыг буруу гэж тоолж EX_нийт бодит run-аас доогуур гардаг байв (E4: 49.00 vs 53.00)."""
    if it.category == "unanswerable":
        return not o.answered
    if it.category == "ambiguous":
        return it.const == "clarify" or (o.answered and o.correct)
    return False


def evaluate(items: list[Item], pol: Policy, orders: list[dict[str, list[int]]] | None = None,
             alpha: float = 0.1) -> dict:
    """orders — permutation бүрийн {qid: дараалал}; None бол эх дараалал."""
    orders = orders or [None]     # type: ignore[list-item]
    ex_a, ex_t, mean_n, p95_n, la50, la95, lb50, lb95 = ([] for _ in range(8))
    n_ans = sum(1 for it in items if it.category == "answerable")
    for od in orders:
        corr = det = 0
        ns, la, lb = [], [], []
        for it in items:
            o = run_item(it, pol, (od or {}).get(it.qid))
            if it.category == "answerable":
                corr += o.correct
            else:
                det += credit_other(it, o)
            if it.const is None and it.hashes:            # үүсгэлт хийгдсэн асуултууд (287 − дүрмээр шийдсэн)
                ns.append(o.n_gen)
                la.append(load_latency_ms(it, o.n_gen, pol.greedy))
                lb.append(wave_latency_ms(it, o.waves, pol.greedy, alpha))
        ex_a.append(100 * corr / max(1, n_ans))
        ex_t.append(100 * (corr + det) / max(1, len(items)))
        mean_n.append(st.mean(ns))
        p95_n.append(_pct(ns, 0.95))
        la50.append(_pct(la, 0.5) / 1000)
        la95.append(_pct(la, 0.95) / 1000)
        lb50.append(_pct(lb, 0.5) / 1000)
        lb95.append(_pct(lb, 0.95) / 1000)

    def ms(xs):
        return (st.mean(xs), st.pstdev(xs) if len(xs) > 1 else 0.0)
    return {"policy": pol.name, "ex_ans": ms(ex_a), "ex_total": ms(ex_t), "mean_n": ms(mean_n),
            "p95_n": ms(p95_n), "lat_load_p50": ms(la50), "lat_load_p95": ms(la95),
            "lat_wave_p50": ms(lb50), "lat_wave_p95": ms(lb95)}


def random_orders(items: list[Item], n: int, seed: int = 0) -> list[dict[str, list[int]]]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        d = {}
        for it in items:
            o = list(range(len(it.hashes)))
            rng.shuffle(o)
            d[it.qid] = o
        out.append(d)
    return out


def default_policies(greedy: bool = False) -> list[Policy]:
    g = "g+" if greedy else ""
    pols = [Policy(f"{g}fixed{k}", (k,), greedy=greedy) for k in range(1, 9)] if not greedy else \
        [Policy("g only", (0,), greedy=True)] + [Policy(f"g+fixed{k}", (k,), greedy=True) for k in (2, 4, 8)]
    pols += [Policy(f"{g}agree2 (2→6)", (2, 6), "agree", 2, greedy),
             Policy(f"{g}agree3 (3→5)", (3, 5), "agree", 3, greedy)]
    for c in (0.7, 0.8, 0.9, 0.95):
        for w in (1, 2, 4):
            pols.append(Policy(f"{g}beta{c} w{w}", tuple([w] * (8 // w)), "beta", c, greedy))
    for w in (2, 4):
        pols.append(Policy(f"{g}esc w{w}", tuple([w] * (8 // w)), "esc", 0, greedy))
    pols.append(Policy(f"{g}agree2+beta0.9 (2,2,4)", (2, 2, 4), "beta", 0.9, greedy))
    return pols


def pareto(rows: list[dict], cost: str = "mean_n") -> list[str]:
    """EX_ans-ыг өсгөх, зардлыг бууруулах Pareto фронт (бусдаар давамгайлагдаагүй бодлого)."""
    out = []
    for r in rows:
        e, c = r["ex_ans"][0], r[cost][0]
        dom = any((o["ex_ans"][0] >= e and o[cost][0] <= c) and (o["ex_ans"][0] > e or o[cost][0] < c)
                  for o in rows)
        if not dom:
            out.append(r["policy"])
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="results/runs/dev")
    ap.add_argument("--runs", nargs="+", default=["E4", "E5h_w1", "E5h_w2"])
    ap.add_argument("--perms", type=int, default=200)
    ap.add_argument("--alpha", type=float, default=0.1, help="давалгааны batch-ийн нэмэлт хугацаа (нэг хувилбарт)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    root = Path(a.root)
    if "test" in root.parts:
        raise SystemExit("test split-ийг уншихгүй — зөвхөн dev")
    allres = {}
    for run in a.runs:
        items = load_items(root, run)
        greedy = any(it.g_weight for it in items)
        # Шалгалт: эх дараалал, 8 хувилбар → run-ын өөрийн correct-ыг яг давтах ёстой
        full = Policy("full", (8,), greedy=greedy)
        rep = sum(run_item(it, full).correct == (it.base_correct and it.base_status == "answered") for it in items)
        print(f"\n=== {run}: {len(items)} асуулт, эх сонголтыг давтсан {rep}/{len(items)}")
        orders = random_orders(items, a.perms)
        rows = []
        for pol in default_policies(False) + (default_policies(True) if greedy else []):
            orig = evaluate(items, pol, None, a.alpha)
            r = evaluate(items, pol, orders, a.alpha)
            r["ex_ans_orig"] = orig["ex_ans"][0]
            rows.append(r)
        front = set(pareto(rows))
        print(f"{'бодлого':28s} {'EX_ans(эх)':>10s} {'EX_ans μ±σ':>13s} {'EX_нийт':>8s} {'μN':>5s} {'p95N':>5s} "
              f"{'A p50/p95 с':>13s} {'B p50/p95 с':>13s}")
        for r in rows:
            print(f"{r['policy']:28s} {r['ex_ans_orig']:10.2f} {r['ex_ans'][0]:6.2f}±{r['ex_ans'][1]:4.2f} "
                  f"{r['ex_total'][0]:8.2f} {r['mean_n'][0]:5.2f} {r['p95_n'][0]:5.1f} "
                  f"{r['lat_load_p50'][0]:6.1f}/{r['lat_load_p95'][0]:6.1f} "
                  f"{r['lat_wave_p50'][0]:6.1f}/{r['lat_wave_p95'][0]:6.1f}{'  *' if r['policy'] in front else ''}")
        allres[run] = {"rows": rows, "pareto": sorted(front), "reproduced": rep}
    if a.json:
        Path(a.json).write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
