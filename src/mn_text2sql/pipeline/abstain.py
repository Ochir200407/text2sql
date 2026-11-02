"""Abstain v2: GPU-гүй, тайлбарлагдах «хариулах / татгалзах / тодруулах» шийдвэр (E4-ийн rule_refuse/rule_clarify-ийн орлуулга).

E4-ийн дүрэм dev-ийн асуултын яг хэллэгээс бичигдсэн тул хэврэг. Энд асуултыг цөөн ЕРӨНХИЙ шинжээр илэрхийлнэ:

* хэллэгийн бүлэг (AUTHORING.md-ийн ангиллаар): хувийн мэдээлэл, бичих үйлдэл, SQL биш даалгавар, ирээдүй
  (as_of = 2026-06-30-аас хойш), гадны мэдлэг, схемд байхгүй бизнесийн ойлголт;
* мэдлэгийн сангийн хамрах хүрээ: асуултын үг semantic_model/glossary/metrics ба жишээний санд (examples*.jsonl —
  eval-аас тусдаа) огт тохиолддоггүй эсэх (OOV);
* хоёрдмол байдал: glossary-ийн `ambiguous` нэр томьёо тодотголгүй, бүдэг хугацаа, хэмжүүргүй «хамгийн», бүдэг тэмдэг нэр;
* санал хураалтын тархалт: итгэл (confidence), ялгаатай үр дүнгийн тоо, алдаатай хувилбарын хувь, хоосон үр дүн,
  хүснэгтгүй SQL. (dev CV-д итгэл/тархалт нэмэр өгөөгүй тул DEFAULT_FEATURES-д зөвхөн хоосон ба хүснэгтгүй.)

Хоёр жижиг логистик регресс (P(хариулах боломжгүй), P(хоёрдмол)) — numpy-аар, sklearn шаардахгүй. Жин ба босгыг
dev дээр family-аар хуваасан CV-ээр үнэлсэн (scripts-гүй: `python -m mn_text2sql.pipeline.abstain --cv`,
docs/ACCURACY_PLAN.md «Abstain v2»). DEFAULT_WEIGHTS нь бүх dev дээр сургасан жин — test-ээр л баталгаажина.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..text.normalize import cyr_key

AS_OF = (2026, 6)   # өгөгдлийн «өнөөдөр» = 2026-06-30

# ---------------------------------------------------------------- хэллэгийн бүлгүүд (ерөнхий, AUTHORING-ийн ангиллаар)
CUES: dict[str, list[str]] = {
    "pii": [r"регистр", r"\bрд\b", r"утас(ны|аа|ыг)?\b", r"гэрийн хаяг", r"оршин суугаа хаяг", r"и-?мэйл",
            r"нууц үг", r"паспорт", r"дансны дугаар", r"төрсөн өдөр"],
    # өөрчлөх үйл үгийн тушаах/дараалсан хэлбэр
    "write": [r"устга", r"шинэчил", r"өөрчил", r"засварла", r"бүртгэж оруул", r"нэмж бүртгэ",
              r"\w+(аад|ээд|оод|өөд) .*(болго|тавь|өөрчил)", r"болго(оройж|ороч|оорой|ж өг)?[.!]?$",
              r"\bтавь\b", r"сунга(ад|ж өг|аарай)", r"шилжүүл(ж өг|ээд|ээрэй)", r"хаа(ж өг|гаад|аарай)"],
    "non_sql": [r"бич(иж|ээд) өг", r"зохио(ж|од)", r"боловсруул(ж|аад) өг", r"зөвлөмж", r"зөвлөгөө",
                r"стратеги", r"санал (бич|боловсруул)", r"текст", r"мессеж", r"захидал", r"илтгэл", r"танилцуулга бэлд"],
    "future": [r"ирэх (сар|он|жил|улирал|долоо хоног|7 хоног)", r"дараа(гийн|чийн) (сар|он|жил|улирал)", r"хойшид",
               r"болох (вэ|бол|бэ)", r"\bбол\?", r"таамагла", r"урьдчилан (хэл|тооц|таам)", r"төсөөл"],
    # хуваарьт/гэрээт ирээдүйн дүн DB-д бий (хариулж болно) — future-ийн эсрэг
    "schedule": [r"хуваар", r"ёстой", r"дуусах", r"төлөгдөх", r"хугацаа нь дуус"],
    "external": [r"хууль", r"инфляц", r"бодлогын хүү", r"монголбанк", r"зах зээл", r"салбарын дундаж",
                 r"арилжааны банк", r"бусад (банк|ббсб|байгууллага)", r"өрсөлдөгч", r"сзх", r"дэлхийн",
                 r"олон улсын", r"статистикийн хороо", r"\bддн\b"],
    "missing": [r"(?<!\S)цалин(?!гийн зээл|тай)", r"урамшуул", r"сэтгэл ханамж", r"\bnps\b", r"төлөвлөгөө",
                r"зорилт", r"түрээс", r"\broe\b", r"\broa\b", r"ажлын цаг", r"ирц"],
    "vague_time": [r"сүүлийн (\d+|хоёр|гурав|гурван|зургаа|зургаан|хэдэн) (сар|улирал|жил|долоо хоног|7 хоног)",
                   r"сүүлийн улирал", r"сүүлийн үед", r"саяхан", r"ойрын үед", r"сүүлийн хугацаанд"],
    "superl": [r"хамгийн (том|сайн|муу|ашигтай|чухал|идэвхтэй|эрсдэлтэй|их|бага)"],
    "vague_adj": [r"асуудалтай", r"эрсдэлтэй", r"(?<!\S)(өндөр|бага|том|жижиг)(тэй)?\b", r"(сайн|муу) ажилла"],
}
# хэмжүүр/хугацааг тодорхой заасан бол бүдэг биш
_RESOLVED = [r"\d+[- ]?р сар", r"\d{4}-\d{2}", r"\w+(аар|ээр|оор|өөр) (нь )?хамгийн", r"(дүнгээр|тоогоор|үлдэгдлээр)",
             r"\d+ хоног"]
_STOP = {"хэлээч", "хэлээрэй", "өгнө", "өгөөч", "бөгөөд", "хийж", "хийнэ", "нэгэн", "байвал", "бүгдийг", "гарга",
         "гаргаж", "харуул", "жагсаа", "тоолж", "байгаа", "байна", "байсан", "хэдэн", "ямар", "манай"}
_WORD = re.compile(r"[а-яёөү]+")

FEATURES = ["pii", "write", "non_sql", "future", "schedule", "external", "missing", "oov",
            "gloss_amb", "vague_time", "superl", "vague_adj", "specific",
            "low_conf", "dispersion", "err_frac", "empty", "fromless"]
TEXT_ONLY = FEATURES[:13]
# Сонгосон багц (family CV): асуултын шинж + хүснэгтгүй/хоосон үр дүн. Итгэл/тархалт dev CV-д нэмэр өгөөгүй.
DEFAULT_FEATURES = TEXT_ONLY + ["fromless", "empty"]


# ---------------------------------------------------------------- мэдлэгийн сангийн толь (OOV)
def _walk(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


@dataclass
class Knowledge:
    prefixes: set[str] = field(default_factory=set)       # үгийн эхний 5 үсэг (залгавраас үл хамаарах)
    amb_terms: list[dict] = field(default_factory=list)   # glossary.ambiguous: {triggers, qualifiers}


@lru_cache(maxsize=2)
def load_knowledge(semantic: str = "knowledge/semantic_model.yaml", glossary: str = "knowledge/glossary.yaml",
                   metrics: str = "knowledge/metrics.yaml",
                   examples: tuple = ("knowledge/examples.jsonl", "knowledge/examples_plus_all.jsonl")) -> Knowledge:
    """Толь: semantic_model + glossary + metrics + жишээний сангийн асуулт (eval set ОРОХГҮЙ)."""
    import yaml
    from ..config import resolve_path
    kn = Knowledge()
    texts: list[str] = []
    for p in (semantic, glossary, metrics):
        rp = resolve_path(p)
        if rp.exists():
            data = yaml.safe_load(rp.read_text(encoding="utf-8"))
            texts += list(_walk(data))
            if p == glossary:
                for t in data.get("terms") or []:
                    amb = t.get("ambiguous")
                    if amb and amb.get("alternatives"):
                        kn.amb_terms.append({"triggers": [cyr_key(str(x)) for x in (amb.get("triggers") or [t["term"]])],
                                             "qualifiers": [cyr_key(str(x)) for x in (amb.get("qualifiers") or [])]})
    for p in examples:
        rp = resolve_path(p)
        if rp.exists():
            for line in rp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    texts.append(json.loads(line).get("question_mn") or "")
    for t in texts:
        kn.prefixes.update(w[:5] for w in _WORD.findall(cyr_key(t)))
    return kn


# ---------------------------------------------------------------- шинж
def _any(pats, q) -> bool:
    return any(re.search(p, q) for p in pats)


def text_features(question: str, kn: Knowledge | None = None) -> dict[str, float]:
    q = cyr_key(question or "")
    f = {k: float(_any(CUES[k], q)) for k in ("pii", "write", "non_sql", "future", "schedule", "external", "missing")}
    for y, mth in re.findall(r"(20\d\d) о[нй]\w*(?: (\d{1,2})[- ]?р сар)?", q):   # as_of-оос хойшхи тодорхой огноо
        if int(y) > AS_OF[0] or (int(y) == AS_OF[0] and mth and int(mth) > AS_OF[1]):
            f["future"] = 1.0
    resolved = _any(_RESOLVED, q)
    f["vague_time"] = float(_any(CUES["vague_time"], q) and not resolved)
    f["superl"] = float(_any(CUES["superl"], q) and not resolved)
    f["vague_adj"] = float(_any(CUES["vague_adj"], q) and not re.search(r"\d", q))
    words = _WORD.findall(q)
    f["specific"] = float(bool(re.search(r"\d|«|\"|'|[a-z]{2,}-\d", q)))
    f["oov"] = f["gloss_amb"] = 0.0
    if kn is not None:
        oov = [w for w in words if len(w) >= 4 and w not in _STOP and w[:5] not in kn.prefixes]
        f["oov"] = min(len(oov), 2) / 2
        for t in kn.amb_terms:
            if any(x and x in q for x in t["triggers"]) and not any(x and x in q for x in t["qualifiers"]):
                f["gloss_amb"] = 1.0
                break
    return f


def vote_features(confidence: float | None, cand_hashes: list | None, row_count: int | None,
                  n_candidates: int | None = None, sql: str | None = None) -> dict[str, float]:
    """Санал хураалтын тархалт: run-ий бичлэг эсвэл reselect-ийн хувилбаруудаас."""
    hs = list(cand_hashes or [])
    n = len(hs) or int(n_candidates or 1)
    ok = [h for h in hs if h]
    distinct = len(set(ok))
    from .selection import is_fromless
    return {"low_conf": 1.0 - float(confidence if confidence is not None else 1.0),
            "dispersion": (max(distinct, 1) - 1) / (n - 1) if n > 1 else 0.0,
            "err_frac": (1 - len(ok) / n) if hs else 0.0,
            "empty": float(row_count == 0),
            "fromless": float(is_fromless(sql))}


def featurize(question: str, kn: Knowledge | None = None, confidence: float | None = None, cand_hashes=None,
              row_count: int | None = None, n_candidates: int | None = None, sql: str | None = None) -> dict[str, float]:
    f = text_features(question, kn)
    f.update(vote_features(confidence, cand_hashes, row_count, n_candidates, sql))
    return f


# ---------------------------------------------------------------- загвар
def _sig(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


@dataclass
class AbstainModel:
    w_u: list[float]          # P(хариулах боломжгүй)
    b_u: float
    w_a: list[float]          # P(хоёрдмол)
    b_a: float
    t_u: float = 0.5          # татгалзах босго
    t_a: float = 0.5          # тодруулах босго
    features: list[str] = field(default_factory=lambda: list(DEFAULT_FEATURES))

    def vec(self, f: dict | list) -> list[float]:
        return [float(f.get(k, 0.0)) for k in self.features] if isinstance(f, dict) else list(f)

    def probs(self, x: dict | list) -> tuple[float, float]:
        x = self.vec(x)
        pu = _sig(self.b_u + sum(w * v for w, v in zip(self.w_u, x)))
        pa = _sig(self.b_a + sum(w * v for w, v in zip(self.w_a, x)))
        return pu, pa

    def decide(self, x: dict | list) -> tuple[str, str, float, float]:
        """(action, reason, p_u, p_a): action ∈ answer | refuse | clarify; reason = хамгийн их нөлөөлсөн шинж."""
        x = self.vec(x)
        pu, pa = self.probs(x)
        if pu >= self.t_u:
            contrib = {k: w * v for k, w, v in zip(self.features, self.w_u, x) if v}
            return "refuse", max(contrib, key=contrib.get) if contrib else "score", pu, pa
        if pa >= self.t_a:
            contrib = {k: w * v for k, w, v in zip(self.features, self.w_a, x) if v}
            return "clarify", max(contrib, key=contrib.get) if contrib else "score", pu, pa
        return "answer", "", pu, pa

    def to_dict(self) -> dict:
        return {"features": self.features, "w_u": [round(w, 4) for w in self.w_u], "b_u": round(self.b_u, 4),
                "w_a": [round(w, 4) for w in self.w_a], "b_a": round(self.b_a, 4), "t_u": self.t_u, "t_a": self.t_a}


def fit_logreg(X: list[list[float]], y: list[int], l2: float = 1.0, iters: int = 400, lr: float = 0.5,
               balanced: bool = True) -> tuple[list[float], float]:
    """L2-тэй логистик регресс (numpy, бүтэн градиент). balanced — цөөн ангиллыг жигнэнэ."""
    import numpy as np
    A, t = np.asarray(X, float), np.asarray(y, float)
    n, d = A.shape
    pos = max(t.sum(), 1.0)
    sw = np.where(t > 0, n / (2 * pos), n / (2 * max(n - pos, 1.0))) if balanced else np.ones(n)
    w, b = np.zeros(d), 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(A @ w + b)))
        g = sw * (p - t)
        w -= lr * (A.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return w.tolist(), float(b)


def utility(action: str, category: str, answer_correct: bool) -> int:
    """Үнэлгээтэй ижил (runner.score_answer): хариулах боломжгүйд аль ч татгалзал зөв, хоёрдмолд тодруулга зөв."""
    if action == "answer":
        return int(answer_correct)
    if action == "refuse":
        return int(category == "unanswerable")
    return int(category in ("unanswerable", "ambiguous"))


_GRID = [round(0.05 * i, 2) for i in range(4, 20)]


def train(X: list[list[float]], cats: list[str], ans_ok: list[bool], l2: float = 1.0,
          features: list[str] | None = None) -> AbstainModel:
    # X — features-ийн дарааллаарх вектор
    """Жин (2 LR) + босго (сургалтын EX-ийг хамгийн их болгох; тэнцвэл өндөр/болгоомжтой босго)."""
    w_u, b_u = fit_logreg(X, [int(c == "unanswerable") for c in cats], l2)
    w_a, b_a = fit_logreg(X, [int(c == "ambiguous") for c in cats], l2)
    m = AbstainModel(w_u, b_u, w_a, b_a, features=list(features or DEFAULT_FEATURES))
    P = [m.probs(x) for x in X]
    best = None
    for tu in _GRID:
        for ta in _GRID:
            s = sum(utility("refuse" if pu >= tu else "clarify" if pa >= ta else "answer", c, ok)
                    for (pu, pa), c, ok in zip(P, cats, ans_ok))
            key = (s, tu + ta)
            if best is None or key > best[0]:
                best = (key, tu, ta)
    m.t_u, m.t_a = best[1], best[2]
    return m


def model_from_dict(d: dict) -> AbstainModel:
    return AbstainModel(d["w_u"], d["b_u"], d["w_a"], d["b_a"], d["t_u"], d["t_a"], list(d["features"]))


# Бүх dev (300, E4f суурь) дээр сургасан жин (`python -m mn_text2sql.pipeline.abstain --fit` хэвлэнэ); test-д тааруулаагүй.
DEFAULT_WEIGHTS: dict = {
    "features": ["pii", "write", "non_sql", "future", "schedule", "external", "missing", "oov", "gloss_amb",
                 "vague_time", "superl", "vague_adj", "specific", "fromless", "empty"],
    "w_u": [2.7743, 3.0072, 0.9023, 1.3101, -0.098, 0.7372, 2.1604, 2.3697, -0.283, -0.1985, -0.8203, 0.4349,
            -1.2171, 0.6794, 0.649],
    "b_u": -0.9909,
    "w_a": [-0.8204, -0.3872, -0.2502, 0.1452, 0.1694, -0.0785, -0.5586, -0.4853, 1.6346, 2.2394, 1.668, 1.1611,
            -0.9863, -0.1545, -0.6843],
    "b_a": -0.0581, "t_u": 0.55, "t_a": 0.65}


def default_model() -> AbstainModel:
    return model_from_dict(DEFAULT_WEIGHTS)


def abstain_decision(question: str, confidence: float | None = None, cand_hashes=None, row_count: int | None = None,
                     n_candidates: int | None = None, sql: str | None = None,
                     model: AbstainModel | None = None, kn: Knowledge | None = None) -> tuple[str, str] | None:
    """rule_decision-тэй ижил гэрээ: ("refuse"|"clarify", reason) эсвэл None (хариулна)."""
    model = model or default_model()
    kn = kn or load_knowledge()
    x = featurize(question, kn, confidence, cand_hashes, row_count, n_candidates, sql)
    act, reason, _, _ = model.decide(x)
    return None if act == "answer" else (act, f"v2:{reason}")


# ---------------------------------------------------------------- офлайн CV (dev)
def _family_folds(fams: list[str], k: int, seed: int) -> list[int]:
    import random
    uniq = sorted(set(fams))
    random.Random(seed).shuffle(uniq)
    fold = {f: i % k for i, f in enumerate(uniq)}
    return [fold[f] for f in fams]


def load_dev_table(run_file: str, items_file: str = "evalset/dev.jsonl", kn: Knowledge | None = None) -> list[dict]:
    """run-ий бичлэг (жишээ нь E4f: vote_filter, дүрэмгүй) + dev асуулт → шинж, «хариулбал зөв эсэх», E4 дүрмийн шийдвэр."""
    from ..config import resolve_path
    from .selection import is_fromless, rule_decision
    kn = kn or load_knowledge()
    items = {}
    for line in resolve_path(items_file).read_text(encoding="utf-8").splitlines():
        if line.strip():
            it = json.loads(line)
            items[it["id"]] = it
    rows = []
    for line in resolve_path(run_file).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        it = items[r["question_id"]]
        q = r.get("question") or it["question_mn"]
        hard = r.get("status") == "refused" and not r.get("n_candidates")   # intent_check (үүсгэлтээс өмнө)
        rd = rule_decision(q)
        rule = "refuse" if hard else rd[0] if rd else ("refuse" if r.get("status") == "answered" and is_fromless(r.get("sql"))
                                                       else "answer")
        rows.append({"id": it["id"], "family": it["family"], "cat": it["category"], "hard": hard, "rule": rule,
                     "x": featurize(q, kn, r.get("confidence"), r.get("cand_hashes"), r.get("row_count"),
                                    r.get("n_candidates"), r.get("sql")),
                     "ans_ok": bool(r.get("correct")), "q": it["question_mn"]})
    return rows


def _tally(rows: list[dict], decs: list[list[str]]) -> dict:
    from collections import Counter
    totals, acc, conf = [], Counter(), Counter()
    for dec in decs:
        tot = 0
        for r, d in zip(rows, dec):
            u = utility(d, r["cat"], r["ans_ok"])
            tot += u
            acc[r["cat"]] += u
            conf[(r["cat"], d)] += 1
        totals.append(tot)
    k = len(decs)
    mean = sum(totals) / k
    sd = (sum((t - mean) ** 2 for t in totals) / max(k - 1, 1)) ** 0.5
    cnt = Counter(r["cat"] for r in rows)
    return {"ex_mean": round(mean, 2), "ex_sd": round(sd, 2), "n": len(rows),
            "by_cat": {c: round(acc[c] / k / cnt[c], 3) for c in sorted(cnt)},
            "confusion": {f"{c}->{d}": round(v / k, 1) for (c, d), v in sorted(conf.items())}}


def rules_baseline(rows: list[dict]) -> dict:
    """E4-ийн дүрэм (тогтмол, dev-ээс бичсэн тул in-sample) ба дүрэмгүй суурь (E4f)."""
    return {"rules_E4": _tally(rows, [[r["rule"] for r in rows]]),
            "no_rules_E4f": _tally(rows, [["refuse" if r["hard"] else "answer" for r in rows]])}


def cross_validate(rows: list[dict], k: int = 5, repeats: int = 10, l2: float = 1.0,
                   feats: list[str] | None = None, with_rules: bool = False) -> dict:
    """Family-аар k-fold (давтан, seed = давталт); шийдвэр бүр тухайн асуултын family-г хассан загвараас.
    with_rules — эхлээд E4 дүрэм, дүрэм хариулна гэвэл v2 (дүрмүүд dev-ээс бичигдсэн тул in-sample хэсэгтэй)."""
    feats = list(feats or DEFAULT_FEATURES)
    X = [[r["x"][f] for f in feats] for r in rows]
    decs = []
    for rep in range(repeats):
        folds = _family_folds([r["family"] for r in rows], k, rep)
        dec = [""] * len(rows)
        for f in range(k):
            tr = [i for i in range(len(rows)) if folds[i] != f]
            m = train([X[i] for i in tr], [rows[i]["cat"] for i in tr], [rows[i]["ans_ok"] for i in tr], l2, feats)
            for i in range(len(rows)):
                if folds[i] == f:
                    if rows[i]["hard"]:
                        dec[i] = "refuse"
                    elif with_rules and rows[i]["rule"] != "answer":
                        dec[i] = rows[i]["rule"]
                    else:
                        dec[i] = m.decide(X[i])[0]
        decs.append(dec)
    return _tally(rows, decs)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m mn_text2sql.pipeline.abstain")
    ap.add_argument("--run", default="results/runs/dev/E4f/arctic-r1-7b__mn__s1__h_H4.jsonl",
                    help="суурь run (vote_filter, дүрэмгүй) — хариулбал зөв эсэх эндээс")
    ap.add_argument("--cv", action="store_true")
    ap.add_argument("--fit", action="store_true", help="бүх dev дээр сургаж жинг хэвлэнэ (DEFAULT_WEIGHTS)")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--l2", type=float, default=1.0)
    a = ap.parse_args(argv)
    rows = load_dev_table(a.run)
    if a.cv:
        out = rules_baseline(rows)
        out.update({"v2": cross_validate(rows, a.k, a.repeats, a.l2),
                    "v2_all_feats": cross_validate(rows, a.k, a.repeats, a.l2, FEATURES),
                    "rules+v2": cross_validate(rows, a.k, a.repeats, a.l2, with_rules=True)})
        print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.fit:
        m = train([[r["x"][f] for f in DEFAULT_FEATURES] for r in rows], [r["cat"] for r in rows],
                  [r["ans_ok"] for r in rows], a.l2)
        print(json.dumps(m.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
