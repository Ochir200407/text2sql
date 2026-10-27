"""Үнэлгээний хэмжүүр (туршилтын төлөвлөгөө "Хэмжих үзүүлэлт", зааврын 5-р хэсэг).

Гол үзүүлэлт: кирилл асуултын dev EX (зөвхөн хариулах боломжтой асуулт дээр).
Нэмэлт: латин/англи EX ба зөрүү, хүндрэл/асуултын төрөл/утга агуулсан эсэхээр, TS-EX, гүйцэтгэгдэх SQL %,
хоосон үр дүн %, татгалзалтын coverage ба selective accuracy, TrustSQL (c = 1, 5, 10), санал нэгдлийн хувь ба
түүний нарийвчлал, p50/p95 хугацаа, токен. Харьцуулалт: McNemar (exact binomial), bootstrap 95% CI.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

NO_ANSWER = {"abstained", "refused", "clarify"}


def load_records(path: str | Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _mean(xs) -> float | None:
    xs = [float(x) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _pct(x: float | None) -> float | None:
    return None if x is None else round(100.0 * x, 2)


def percentile(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def bootstrap_ci(values: list[bool], n: int = 1000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    m = len(values)
    stats = sorted(sum(values[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return (round(100 * stats[int(n * alpha / 2)], 2), round(100 * stats[int(n * (1 - alpha / 2)) - 1], 2))


def trust_score(r: dict, c: float) -> float:
    """TrustSQL (Lee et al., 2024): зөв хариулт эсвэл зөв татгалзалт +1, татгалзсан хариулах-боломжтой асуулт 0,
    буруу хариулт эсвэл хариулах боломжгүй асуултад хариулсан −c. Хоёрдмол: тодруулсан/аль нэг тайлбартай таарвал +1."""
    answered = r.get("status") not in NO_ANSWER
    cat = r.get("category")
    if cat == "unanswerable":
        return 1.0 if not answered else -c
    if cat == "ambiguous":
        if r.get("status") == "clarify" or (answered and r.get("correct")):
            return 1.0
        return 0.0 if not answered else -c
    if not answered:
        return 0.0
    return 1.0 if r.get("correct") else -c


def _ex(records: list[dict]) -> float | None:
    return _mean([bool(r.get("correct")) for r in records])


def group_ex(records: list[dict], key) -> dict:
    groups = defaultdict(list)
    for r in records:
        k = key(r)
        if isinstance(k, (list, tuple, set)):
            for kk in k:
                groups[str(kk)].append(r)
        else:
            groups[str(k)].append(r)
    return {k: {"n": len(v), "ex": _pct(_ex(v))} for k, v in sorted(groups.items())}


def abstention_curve(records: list[dict], steps: int = 8) -> list[dict]:
    """Санал нэгдлийн итгэлээр (confidence) босго тавихад coverage–selective accuracy (E3). TrustSQL-ийг БҮХ
    ангиллаар (хариулах боломжгүй/хоёрдмол асуултаас татгалзвал +1) тооцно."""
    withc = [r for r in records if r.get("confidence") is not None]
    ans = [r for r in withc if r.get("category") == "answerable"]
    if not ans:
        return []
    out = []
    for i in range(steps + 1):
        t = i / steps
        kept = [r for r in ans if r["confidence"] >= t - 1e-9]
        sim = []
        for r in withc:
            rr = dict(r)
            if r["confidence"] < t - 1e-9:
                rr["status"] = "abstained"
            elif rr.get("category") == "answerable":
                rr["correct"] = bool(r.get("raw_correct"))
            sim.append(rr)
        out.append({"threshold": round(t, 3), "coverage": _pct(len(kept) / len(ans)),
                    "selective_accuracy": _pct(_mean([bool(r.get("raw_correct")) for r in kept])),
                    "trust_c10": round(sum(trust_score(r, 10) for r in sim) / len(sim), 4)})
    return out


def summarize(records: list[dict], penalty_c=(1, 5, 10)) -> dict:
    ans = [r for r in records if r.get("category") == "answerable"]
    unans = [r for r in records if r.get("category") == "unanswerable"]
    amb = [r for r in records if r.get("category") == "ambiguous"]
    produced = [r for r in records if r.get("sql")]
    out: dict = {"n": len(records), "n_answerable": len(ans), "n_unanswerable": len(unans), "n_ambiguous": len(amb)}
    out["ex"] = _pct(_ex(ans))
    out["ex_ci95"] = bootstrap_ci([bool(r.get("correct")) for r in ans])
    out["raw_ex"] = _pct(_mean([bool(r.get("raw_correct")) for r in ans]))
    out["ex_strict"] = _pct(_mean([bool(r.get("strict_correct", r.get("correct"))) for r in ans]))
    # TS-EX: EX-ээр зөв бүх бичлэг test-suite-ээр шалгагдсан үед л тооцно (eval ts-score)
    correct_recs = [r for r in ans if r.get("correct")]
    if correct_recs and all(r.get("ts_correct") is not None for r in correct_recs):
        out["ts_ex"] = _pct(_mean([bool(r.get("correct")) and bool(r.get("ts_correct")) for r in ans]))
    else:
        out["ts_ex"] = None
    out["soft_f1"] = _pct(_mean([r.get("soft_f1") for r in ans]))
    out["by_difficulty"] = group_ex(ans, lambda r: r.get("difficulty"))
    out["by_domain"] = group_ex(ans, lambda r: r.get("domain"))
    out["by_contains_value"] = group_ex(ans, lambda r: bool(r.get("contains_value")))
    out["by_tag"] = group_ex(ans, lambda r: r.get("tags") or ["(none)"])
    # найдвартай байдал
    attempted = [r for r in records if r.get("status") not in NO_ANSWER]
    out["executable_pct"] = _pct(_mean([bool(r.get("exec_ok")) for r in attempted])) if attempted else None
    out["empty_pct"] = _pct(_mean([bool(r.get("empty")) for r in produced if r.get("exec_ok")]))
    answered = [r for r in ans if r.get("status") not in NO_ANSWER]
    out["coverage_pct"] = _pct(len(answered) / len(ans)) if ans else None
    out["selective_accuracy"] = _pct(_mean([bool(r.get("correct")) for r in answered]))
    out["trust"] = {f"c{c}": round(sum(trust_score(r, c) for r in records) / len(records), 4) if records else None
                    for c in penalty_c}
    out["unanswerable_detected_pct"] = _pct(_mean([r.get("status") in NO_ANSWER for r in unans]))
    out["ambiguous"] = {"clarify_pct": _pct(_mean([r.get("status") == "clarify" for r in amb])),
                        "interp_match_pct": _pct(_mean([bool(r.get("correct")) for r in amb]))}
    # санал хураалт
    multi = [r for r in ans if (r.get("n_candidates") or 1) > 1]
    if multi:
        una = [r for r in multi if r.get("unanimous")]
        out["voting"] = {"unanimity_pct": _pct(len(una) / len(multi)),
                         "unanimous_accuracy": _pct(_ex(una)),
                         "non_unanimous_accuracy": _pct(_ex([r for r in multi if not r.get("unanimous")])),
                         "oracle_at_k": _pct(_mean([any(r.get("cand_correct") or []) for r in multi])),
                         "abstention_curve": abstention_curve(multi)}
    # зардал
    lat = [r.get("latency_ms") for r in records if r.get("latency_ms") is not None]
    out["latency_ms"] = {"p50": _r(percentile(lat, 0.5)), "p95": _r(percentile(lat, 0.95)), "mean": _r(_mean(lat))}
    out["tokens"] = {"in_mean": _r(_mean([r.get("prompt_tokens") for r in records])),
                     "out_mean": _r(_mean([r.get("completion_tokens") for r in records]))}
    out["gen_seconds_per_q"] = _r(_mean([(r.get("gen_ms") or 0) / 1000 for r in records]), 3)
    out["repairs_mean"] = _r(_mean([r.get("repairs") for r in records]), 3)
    out["table_recall"] = _pct(_mean([r.get("table_recall") for r in ans]))
    out["value_recall"] = _pct(_mean([r.get("value_recall") for r in ans]))
    if any(r.get("routed") for r in records):
        routed = [r for r in ans if r.get("routed")]
        out["routed_pct"] = _pct(len([r for r in records if r.get("routed")]) / len(records))
        out["ex_routed"] = _pct(_ex(routed))
        out["ex_not_routed"] = _pct(_ex([r for r in ans if not r.get("routed")]))
    out["agent_steps_mean"] = _r(_mean([r.get("agent_steps") for r in records]), 3)
    wrong = [r for r in ans if not r.get("correct")]
    out["error_stages"] = dict(Counter(r.get("error_stage") or ("abstained" if r.get("status") in NO_ANSWER
                                                                else "wrong_result") for r in wrong))
    return out


def _r(x, nd: int = 1):
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------------------
# Харьцуулалт
# ---------------------------------------------------------------------------

def _key(r: dict) -> tuple:
    return (r.get("question_id"), r.get("variant"))


def mcnemar(a: list[dict], b: list[dict], only_answerable: bool = True) -> dict:
    """Хос асуулт (question_id, variant) дээр exact McNemar (binomial). b нь шинэ тохиргоо."""
    from scipy.stats import binomtest
    fa = {_key(r): bool(r.get("correct")) for r in a if not only_answerable or r.get("category") == "answerable"}
    fb = {_key(r): bool(r.get("correct")) for r in b if not only_answerable or r.get("category") == "answerable"}
    keys = sorted(set(fa) & set(fb), key=str)
    b01 = sum(1 for k in keys if not fa[k] and fb[k])   # шинэ нь ялсан
    b10 = sum(1 for k in keys if fa[k] and not fb[k])   # шинэ нь ялагдсан
    n = b01 + b10
    p = binomtest(min(b01, b10), n, 0.5).pvalue if n else 1.0
    ex_a = _mean([fa[k] for k in keys])
    ex_b = _mean([fb[k] for k in keys])
    return {"n_pairs": len(keys), "wins": b01, "losses": b10, "p_value": round(float(p), 5),
            "delta_ex": _pct((ex_b or 0) - (ex_a or 0)), "significant": bool(n and p < 0.05)}


def compare_runs(a_file: str, b_file: str, penalty_c=(1, 5, 10)) -> dict:
    """Хоёр run-ийг (жишээ нь одоогийн систем EXT-* ба шинэ систем) ижил асуулт дээр харьцуулна: McNemar, ΔEX,
    TrustSQL. Туршилтын төлөвлөгөөний орлуулах шийдвэрт (test 200 дээр)."""
    a, b = load_records(a_file), load_records(b_file)
    sa, sb = summarize(a, penalty_c), summarize(b, penalty_c)
    return {"a": {"file": a_file, "ex": sa["ex"], "ex_ci95": sa["ex_ci95"], "trust": sa["trust"], "n": sa["n"]},
            "b": {"file": b_file, "ex": sb["ex"], "ex_ci95": sb["ex_ci95"], "trust": sb["trust"], "n": sb["n"]},
            "mcnemar_answerable": mcnemar(a, b), "mcnemar_all": mcnemar(a, b, only_answerable=False)}


def seed_stats(values: list[float | None]) -> dict:
    xs = [v for v in values if v is not None]
    if not xs:
        return {"mean": None, "std": None, "n": 0}
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
    return {"mean": round(m, 2), "std": round(sd, 2), "n": len(xs)}
