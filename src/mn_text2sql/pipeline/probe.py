"""Hidden-state probe (LatentRefusal маягийн): SQL загварын (Arctic-R1-7B + D2) prompt-ын далд төлөвөөс
P(хариулах боломжгүй), P(хоёрдмол)-ыг таамаглах жижиг логистик регресс. docs/PROBE_PLAN.md, docs/RESEARCH_NEXT.md (зөвлөмж 1).

Шинж (scripts/probe/extract_hidden.py, Kaggle):  <dir>/ids.json + <dir>/<блок>.npy (float16, мөр = ids-ийн дараалал)
  L<i>_mean / L<i>_last — i-р давхаргын гаралтын (hidden_states[i]) prompt-ын дундаж ба сүүлийн токен
  logit                 — prefill-ийн дараах эхний токены энтропи, top-1 магадлал, зөрүү (+ сонголтоор эхний G токены энтропи)

Загвар: стандартчилах → (сонголтоор) PCA (n ≪ d тул; fold дотор л тооцно) → дахин стандартчилах → 2 L2 LR
(Newton, numpy; sklearn шаардахгүй) → босго. Босгыг сургалтын fold-ын ДОТООД family CV-ийн out-of-fold магадлалаар
сонгоно (өндөр хэмжээст probe-ийн in-sample магадлал хэт итгэлтэй — abstain.py-ийн in-sample босгоос ялгаа).

Үнэлгээ abstain.py-тэй ЯГ ижил: dev, family-аар 5-fold × 10 давталт (seed = давталт → v2-тэй ижил fold), utility =
runner.score_answer-тэй ижил тооцоо, «хариулбал зөв» нь суурь run-ий бичлэгээс (анхдагч E4f). Нэг дуудалтад v2 ба
«дүрэм + v2»-ийг ч ижил fold дээр дахин тооцож хос (paired) зөрүүг гаргана.

    python -m mn_text2sql.pipeline.probe --cv    --features build/probe/m1a_dev
    python -m mn_text2sql.pipeline.probe --train --features build/probe/m1a_dev --save build/probe/probe_m1a.npz
    python -m mn_text2sql.pipeline.probe --apply --model build/probe/probe_m1a.npz --features build/probe/m1a_test \
        --run results/runs/test/E4f/<файл>.jsonl --out build/probe/decisions_test.jsonl
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import abstain as AB

DEFAULT_BLOCKS = ["L20_mean", "L20_last", "logit"]     # урьдчилан бүртгэсэн (pre-registered) үндсэн багц
DEFAULT_PCA = 32
DEFAULT_L2 = 1.0
INNER_K = 3
GRID = AB._GRID                                         # босгын тор abstain.py-тэй ижил (0.20 … 0.95)


# ---------------------------------------------------------------- шинжийн хавтас
def read_ids(feat_dir: str | Path) -> dict:
    from ..config import resolve_path
    return json.loads((resolve_path(feat_dir) / "ids.json").read_text(encoding="utf-8"))


SIDE_MAX_DIM = 64      # үүнээс жижиг блок (logit) PCA-д орохгүй, шууд залгагдана


def order_blocks(blocks: list[str]) -> list[str]:
    """Далд төлөвийн (L*) блок эхэнд, жижиг (logit г.м.) блок сүүлд — n_side нь X-ийн сүүлийн баганууд."""
    return [b for b in blocks if b.startswith("L")] + [b for b in blocks if not b.startswith("L")]


def load_feature_dir(feat_dir: str | Path, blocks: list[str] | None = None) -> tuple[list[str], np.ndarray, dict]:
    """(ids, X float32 [n, Σd], ids.json-ийн мета). Блокуудыг order_blocks-ийн дарааллаар залгана;
    meta["_n_side"] = PCA-гүй (≤ SIDE_MAX_DIM хэмжээстэй) сүүлийн блокуудын нийт багана."""
    from ..config import resolve_path
    d = resolve_path(feat_dir)
    meta = read_ids(d)
    blocks = order_blocks(list(blocks or DEFAULT_BLOCKS))
    mats = []
    for b in blocks:
        p = d / f"{b}.npy"
        if not p.exists():
            have = sorted(x.stem for x in d.glob("*.npy"))
            raise FileNotFoundError(f"{p} алга (байгаа блок: {have})")
        a = np.load(p).astype(np.float32)
        if a.ndim == 1:
            a = a[:, None]
        if len(a) != len(meta["ids"]):
            raise ValueError(f"{b}: {len(a)} мөр, ids {len(meta['ids'])}")
        mats.append(a)
    X = np.concatenate(mats, axis=1)
    X[~np.isfinite(X)] = 0.0                            # fp16 overflow (inf) хамгаална
    n_side = 0
    for m in reversed(mats):
        if m.shape[1] > SIDE_MAX_DIM:
            break
        n_side += m.shape[1]
    meta = dict(meta, _blocks=blocks, _n_side=n_side if n_side < X.shape[1] else 0)
    return list(meta["ids"]), X, meta


def align(ids: list[str], X: np.ndarray, want: list[str]) -> np.ndarray:
    """want-ийн дарааллаар мөр сонгоно; дутуу id бол алдаа (тоо v2-тэй харьцуулагдахын тулд бүх dev хэрэгтэй)."""
    pos = {i: k for k, i in enumerate(ids)}
    miss = [w for w in want if w not in pos]
    if miss:
        raise ValueError(f"шинжгүй {len(miss)} асуулт (жишээ {miss[:5]}) — extract_hidden.py-г дуусгах")
    return X[[pos[w] for w in want]]


# ---------------------------------------------------------------- LR (Newton, L2) — жижиг хэмжээст (PCA-ийн дараа)
def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logreg_newton(A: np.ndarray, t: np.ndarray, l2: float = DEFAULT_L2, balanced: bool = True,
                      iters: int = 60, tol: float = 1e-7) -> tuple[np.ndarray, float]:
    """Σ s_i·logloss_i + l2/2·‖w‖² (bias-д торгуульгүй) — abstain.fit_logreg-тэй ижил параметрчлэл, гэхдээ Newton
    (детерминист, хурдан нийлнэ). balanced: цөөн ангиллыг n/(2·pos) жингээр."""
    A = np.asarray(A, np.float64)
    t = np.asarray(t, np.float64)
    n, d = A.shape
    if d > 2048:
        raise ValueError(f"{d} хэмжээс Newton-д хэт их — --pca ашигла")
    pos = max(t.sum(), 1.0)
    sw = np.where(t > 0, n / (2 * pos), n / (2 * max(n - pos, 1.0))) if balanced else np.ones(n)
    Ab = np.hstack([A, np.ones((n, 1))])
    R = np.full(d + 1, float(l2))
    R[-1] = 1e-6
    w = np.zeros(d + 1)
    for _ in range(iters):
        p = _sigmoid(Ab @ w)
        g = Ab.T @ (sw * (p - t)) + R * w
        H = (Ab * (sw * p * (1 - p))[:, None]).T @ Ab + np.diag(R)
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < tol:
            break
    return w[:-1], float(w[-1])


# ---------------------------------------------------------------- probe загвар
@dataclass
class ProbeModel:
    mu: np.ndarray                       # түүхий шинжийн дундаж / стандарт хазайлт
    sd: np.ndarray
    V: np.ndarray | None                 # PCA-ийн тэнхлэг [d, k] эсвэл None
    zmu: np.ndarray                      # (PCA [+ v2]) дараах стандартчилал
    zsd: np.ndarray
    w_u: np.ndarray
    b_u: float
    w_a: np.ndarray
    b_a: float
    t_u: float = 0.5
    t_a: float = 0.5
    meta: dict = field(default_factory=dict)   # blocks, pca, l2, v2_feats, mode, ...

    @property
    def v2_feats(self) -> list[str]:
        return list(self.meta.get("v2_feats") or [])

    def transform(self, X: np.ndarray, V2: np.ndarray | None = None) -> np.ndarray:
        Z = (np.atleast_2d(np.asarray(X, np.float64)) - self.mu) / self.sd
        ns = int(self.meta.get("n_side") or 0)
        if self.V is not None:
            Z = np.hstack([Z[:, :Z.shape[1] - ns] @ self.V, Z[:, Z.shape[1] - ns:]])
        if int(self.meta.get("n_v2") or 0):
            if V2 is None:
                raise ValueError("энэ probe v2-ийн шинж шаардана (V2)")
            Z = np.hstack([Z, np.atleast_2d(np.asarray(V2, np.float64))])
        return (Z - self.zmu) / self.zsd

    def probs(self, X: np.ndarray, V2: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        Z = self.transform(X, V2)
        return _sigmoid(Z @ self.w_u + self.b_u), _sigmoid(Z @ self.w_a + self.b_a)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = dict(self.meta, b_u=self.b_u, b_a=self.b_a, t_u=self.t_u, t_a=self.t_a)
        np.savez_compressed(p, mu=self.mu, sd=self.sd, V=self.V if self.V is not None else np.zeros((0, 0)),
                            zmu=self.zmu, zsd=self.zsd, w_u=self.w_u, w_a=self.w_a,
                            meta=np.array(json.dumps(meta, ensure_ascii=False)))
        return p

    @classmethod
    def load(cls, path: str | Path) -> ProbeModel:
        from ..config import resolve_path
        z = np.load(resolve_path(path), allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        V = z["V"] if z["V"].size else None
        return cls(z["mu"], z["sd"], V, z["zmu"], z["zsd"], z["w_u"], float(meta["b_u"]), z["w_a"],
                   float(meta["b_a"]), float(meta["t_u"]), float(meta["t_a"]), meta)


def decide(pu: float, pa: float, t_u: float, t_a: float) -> str:
    """abstain.AbstainModel.decide-тэй ижил дараалал: татгалзал → тодруулга → хариулт."""
    return "refuse" if pu >= t_u else "clarify" if pa >= t_a else "answer"


def fit_probe(X: np.ndarray, cats: list[str], pca: int = DEFAULT_PCA, l2: float = DEFAULT_L2,
              V2: np.ndarray | None = None, meta: dict | None = None, n_side: int = 0) -> ProbeModel:
    """Жин (босгогүй, 0.5). PCA нь Gram матрицын eigh-ээр (n ≪ d үед хурдан); сүүлийн n_side багана PCA-гүй."""
    X = np.asarray(X, np.float64)
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd < 1e-6] = 1.0
    Z = (X - mu) / sd
    V = None
    S = Z[:, Z.shape[1] - n_side:]
    Z = Z[:, :Z.shape[1] - n_side]
    if pca and pca < min(Z.shape):
        G = Z @ Z.T
        ev, U = np.linalg.eigh(G)
        top = np.argsort(ev)[::-1][:pca]
        s = np.sqrt(np.maximum(ev[top], 1e-12))
        V = (Z.T @ U[:, top]) / s                       # [d, k] — нэгж урттай тэнхлэг
        Z = Z @ V
    Z = np.hstack([Z, S])
    if V2 is not None:
        Z = np.hstack([Z, np.asarray(V2, np.float64)])
    zmu = Z.mean(0)
    zsd = Z.std(0)
    zsd[zsd < 1e-6] = 1.0
    Zs = (Z - zmu) / zsd
    w_u, b_u = fit_logreg_newton(Zs, np.array([c == "unanswerable" for c in cats], float), l2)
    w_a, b_a = fit_logreg_newton(Zs, np.array([c == "ambiguous" for c in cats], float), l2)
    return ProbeModel(mu, sd, V, zmu, zsd, w_u, b_u, w_a, b_a, meta=dict(meta or {}, pca=pca, l2=l2, n_side=n_side,
                                                                     n_v2=0 if V2 is None else int(np.shape(V2)[1])))


def tune_thresholds(pu: np.ndarray, pa: np.ndarray, cats: list[str], ans_ok: list[bool]) -> tuple[float, float]:
    """EX-ийг хамгийн их болгох (t_u, t_a); тэнцвэл өндөр (болгоомжтой) босго — abstain.train-тэй ижил."""
    best = None
    for tu in GRID:
        for ta in GRID:
            s = sum(AB.utility(decide(u, a, tu, ta), c, ok) for u, a, c, ok in zip(pu, pa, cats, ans_ok, strict=False))
            key = (s, tu + ta)
            if best is None or key > best[0]:
                best = (key, tu, ta)
    return best[1], best[2]


def _oof_probs(X, cats, fams, V2, Xe, ce, V2e, pca, l2, k, seed, n_side=0) -> tuple[np.ndarray, np.ndarray]:
    """Дотоод family CV-ийн out-of-fold магадлал (нэмэлт мөр Xe үргэлж сургалтад)."""
    folds = AB._family_folds(fams, k, seed)
    pu, pa = np.zeros(len(X)), np.zeros(len(X))
    for f in range(k):
        te = np.array([x == f for x in folds])
        if not te.any() or te.all():
            continue
        Xtr, ctr = np.vstack([X[~te], Xe]), [c for c, m in zip(cats, te, strict=False) if not m] + list(ce)
        Vtr = None if V2 is None else np.vstack([V2[~te], V2e])
        m = fit_probe(Xtr, ctr, pca, l2, Vtr, n_side=n_side)
        pu[te], pa[te] = m.probs(X[te], None if V2 is None else V2[te])
    return pu, pa


def train(X, cats, ans_ok, fams, mask=None, Xe=None, ce=(), pca=DEFAULT_PCA, l2=DEFAULT_L2, V2=None, V2e=None,
          inner_k=INNER_K, seed=12345, meta=None, n_side=0) -> ProbeModel:
    """Бүх (X + нэмэлт) дээр жин; босгыг X-ийн дотоод CV-ийн OOF магадлалаар, зөвхөн mask=True мөрөөр
    (probe-ийн шийдвэрлэх мөр: hard биш, дүрэмтэй горимд дүрэм «answer» гэсэн)."""
    X = np.asarray(X, np.float64)
    Xe = np.zeros((0, X.shape[1])) if Xe is None else np.asarray(Xe, np.float64)
    if V2 is not None and V2e is None:
        V2e = np.zeros((0, V2.shape[1]))
    m = fit_probe(np.vstack([X, Xe]), list(cats) + list(ce), pca, l2,
                  None if V2 is None else np.vstack([V2, V2e]), meta, n_side)
    pu, pa = _oof_probs(X, list(cats), list(fams), V2, Xe, list(ce), V2e, pca, l2, inner_k, seed, n_side)
    mask = np.ones(len(X), bool) if mask is None else np.asarray(mask, bool)
    idx = np.flatnonzero(mask)
    m.t_u, m.t_a = tune_thresholds(pu[idx], pa[idx], [cats[i] for i in idx], [ans_ok[i] for i in idx])
    return m


# ---------------------------------------------------------------- шийдвэр (online / reselect-ийн гэрээ)
def probe_decision(x: np.ndarray, model: ProbeModel, v2: dict | None = None,
                   rule: str | None = None) -> tuple[str, str] | None:
    """abstain_decision / rule_decision-тэй ижил гэрээ: ("refuse"|"clarify", reason) эсвэл None (хариулна).
    rule: E4-ийн дүрмийн шийдвэр («answer» биш бол дүрэм ялна — «эхлээд дүрэм, дараа нь probe»)."""
    if rule and rule != "answer":
        return rule, "rule"
    V2 = np.array([[float((v2 or {}).get(f, 0.0)) for f in model.v2_feats]]) if model.v2_feats else None
    pu, pa = model.probs(np.asarray(x)[None, :] if np.ndim(x) == 1 else x, V2)
    act = decide(float(pu[0]), float(pa[0]), model.t_u, model.t_a)
    return None if act == "answer" else (act, f"probe:p_u={pu[0]:.2f},p_a={pa[0]:.2f}")


# ---------------------------------------------------------------- CV (dev; abstain.py-ийн harness)
def _v2_matrix(rows: list[dict], feats: list[str]) -> np.ndarray:
    return np.array([[float(r["x"].get(f, 0.0)) for f in feats] for r in rows])


def cross_validate(rows: list[dict], X: np.ndarray, k: int = 5, repeats: int = 10, pca: int = DEFAULT_PCA,
                   l2: float = DEFAULT_L2, Xe: np.ndarray | None = None, ce: list[str] | tuple = (),
                   v2_feats: list[str] | None = None, with_v2cat: bool = True, inner_k: int = INNER_K,
                   v2_l2: float = 1.0, n_side: int = 0) -> dict:
    """Family-аар k-fold × repeats (seed = давталт → abstain.cross_validate-тэй ижил fold). Систем бүрийн шийдвэр:
      v2, rules+v2              — abstain.py-ийн v2 (ижил fold дээр дахин; лавлах 154.5 / 160.9)
      probe, rules+probe        — зөвхөн далд төлөвийн probe
      probe+v2f, rules+probe+v2f — далд төлөв + v2-ийн 15 шинж нэг LR-д (судалгааны)
    hard (intent_check-ийн татгалзал) бүх системд «refuse». Буцаах: {систем: _tally + per-repeat нийлбэр},
    «paired» = rules+v2-оос давталт бүрийн зөрүү."""
    v2_feats = list(v2_feats or AB.DEFAULT_FEATURES)
    n = len(rows)
    X = np.asarray(X, np.float64)
    Xe = np.zeros((0, X.shape[1])) if Xe is None else np.asarray(Xe, np.float64)
    V2 = _v2_matrix(rows, v2_feats)
    V2e = np.zeros((len(Xe), len(v2_feats)))           # нэмэлт мөрийн v2 шинж: run-гүй тул 0 (зөвхөн probe-д)
    cats = [r["cat"] for r in rows]
    oks = [r["ans_ok"] for r in rows]
    fams = [r["family"] for r in rows]
    hard = np.array([r["hard"] for r in rows])
    ruled = np.array([r["rule"] != "answer" for r in rows])
    systems = ["v2", "rules+v2", "probe", "rules+probe"] + (["probe+v2f", "rules+probe+v2f"] if with_v2cat else [])
    decs: dict[str, list[list[str]]] = {s: [] for s in systems}
    for rep in range(repeats):
        folds = np.array(AB._family_folds(fams, k, rep))
        cur = {s: [""] * n for s in systems}
        for f in range(k):
            tr, te = np.flatnonzero(folds != f), np.flatnonzero(folds == f)
            # v2 (abstain.train-тэй яг ижил: dev-ийн сургалтын мөр, in-sample босго)
            mv2 = AB.train(V2[tr].tolist(), [cats[i] for i in tr], [oks[i] for i in tr], v2_l2, v2_feats)
            ctr, otr, ftr = [cats[i] for i in tr], [oks[i] for i in tr], [fams[i] for i in tr]
            variants = [("probe", None)] + ([("probe+v2f", V2)] if with_v2cat else [])
            fitted = {}
            for name, V in variants:
                Vtr, Vte = (None, None) if V is None else (V[tr], V[te])
                m = fit_probe(np.vstack([X[tr], Xe]), ctr + list(ce), pca, l2,
                              None if V is None else np.vstack([Vtr, V2e]), n_side=n_side)
                pu_o, pa_o = _oof_probs(X[tr], ctr, ftr, Vtr, Xe, list(ce), None if V is None else V2e, pca, l2,
                                        inner_k, 1000 + rep * k + f, n_side)
                th = {}
                for mode, msk in (("", ~hard[tr]), ("rules+", ~hard[tr] & ~ruled[tr])):
                    idx = np.flatnonzero(msk)
                    th[mode] = tune_thresholds(pu_o[idx], pa_o[idx], [ctr[i] for i in idx], [otr[i] for i in idx])
                pu, pa = m.probs(X[te], Vte)
                fitted[name] = (pu, pa, th)
            for j, i in enumerate(te):
                if hard[i]:
                    for s in systems:
                        cur[s][i] = "refuse"
                    continue
                a_v2 = mv2.decide(V2[i].tolist())[0]
                cur["v2"][i] = a_v2
                cur["rules+v2"][i] = rows[i]["rule"] if ruled[i] else a_v2
                for name, (pu, pa, th) in fitted.items():
                    cur[name][i] = decide(pu[j], pa[j], *th[""])
                    cur["rules+" + name][i] = rows[i]["rule"] if ruled[i] else decide(pu[j], pa[j], *th["rules+"])
        for s in systems:
            decs[s].append(cur[s])
    out = {}
    tot = {s: [sum(AB.utility(d, r["cat"], r["ans_ok"]) for r, d in zip(rows, dec, strict=False)) for dec in decs[s]]
           for s in systems}
    for s in systems:
        out[s] = AB._tally(rows, decs[s])
        out[s]["per_repeat"] = tot[s]
        diff = np.array(tot[s], float) - np.array(tot["rules+v2"], float)
        out[s]["paired_vs_rules+v2"] = {"mean": round(float(diff.mean()), 2),
                                        "sd": round(float(diff.std(ddof=1)) if len(diff) > 1 else 0.0, 2),
                                        "n_better": int((diff > 0).sum()), "n_worse": int((diff < 0).sum())}
    return out


ADOPT_REF = 160.9      # docs/ACCURACY_PLAN.md: E4 дүрэм + v2 (CV) — урьдчилан бүртгэсэн босго
PRIMARY = "rules+probe"


def adopt_verdict(cv: dict, ref: float = ADOPT_REF, system: str = PRIMARY, min_better: int = 8) -> dict:
    """Шийдвэрийн дүрэм (docs/PROBE_PLAN.md §5): system-ийн CV дундаж > ref БА ижил fold дээрх rules+v2-оос
    ≥ min_better/10 давталтад илүү. Хоёул биелбэл adopt."""
    s = cv[system]
    p = s["paired_vs_rules+v2"]
    ok_ref = s["ex_mean"] > ref
    ok_pair = p["n_better"] >= min(min_better, len(s["per_repeat"]))
    return {"system": system, "ex_mean": s["ex_mean"], "ref": ref, "beats_ref": ok_ref, "paired": p,
            "paired_ok": ok_pair, "adopt": bool(ok_ref and ok_pair)}


# ---------------------------------------------------------------- run-ий бичлэгээс (test-д: items/шошго уншихгүй)
def rows_from_run(run_file: str | Path) -> list[dict]:
    """--apply-д: run-ий бичлэгээс асуулт, v2 шинж, E4 дүрэм, hard. evalset-ийн шошгыг (category) УНШИХГҮЙ."""
    from ..config import resolve_path
    from .selection import is_fromless, rule_decision
    kn = AB.load_knowledge()
    rows = []
    for line in resolve_path(run_file).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        q = r.get("question") or ""
        hard = r.get("status") == "refused" and not r.get("n_candidates")
        rd = rule_decision(q)
        rule = "refuse" if hard else rd[0] if rd else ("refuse" if r.get("status") == "answered" and
                                                       is_fromless(r.get("sql")) else "answer")
        rows.append({"id": r["question_id"], "hard": hard, "rule": rule,
                     "x": AB.featurize(q, kn, r.get("confidence"), r.get("cand_hashes"), r.get("row_count"),
                                       r.get("n_candidates"), r.get("sql"))})
    return rows


def _extra(dirs: list[str], blocks: list[str]) -> tuple[np.ndarray | None, list[str]]:
    """Нэмэлт сургалтын мөр (knowledge-ийн 160 жишээ, синтетик unanswerable): ids.json-ийн items[].category."""
    Xs, cs = [], []
    for d in dirs or []:
        ids, X, meta = load_feature_dir(d, blocks)
        cat = {it["id"]: it.get("category") for it in meta.get("items") or []}
        keep = [i for i, x in enumerate(ids) if cat.get(x) in ("answerable", "unanswerable", "ambiguous")]
        if meta.get("split") == "test":
            raise SystemExit(f"{d}: test-ийн шинжийг сургалтад ашиглахгүй")
        Xs.append(X[keep])
        cs += [cat[ids[i]] for i in keep]
    return (np.vstack(Xs) if Xs else None), cs


def _main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m mn_text2sql.pipeline.probe")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cv", action="store_true", help="dev дээр family 5×10 CV (abstain --cv-тэй ижил хэмжүүр)")
    g.add_argument("--train", action="store_true", help="бүх dev (+ --extra) дээр сургаж --save руу хадгална")
    g.add_argument("--apply", action="store_true", help="хадгалсан probe-оор шийдвэр (jsonl) — test-д ч (шошгогүй)")
    ap.add_argument("--features", default="build/probe/m1a_dev", help="extract_hidden.py-ийн гаралтын хавтас")
    ap.add_argument("--run", default="results/runs/dev/E4f/arctic-r1-7b__mn__s1__h_H4.jsonl",
                    help="суурь run (abstain --cv-тэй ижил): «хариулбал зөв», v2 шинж, E4 дүрэм")
    ap.add_argument("--blocks", default=",".join(DEFAULT_BLOCKS), help="жишээ L16_mean,L20_last,L24_mean,logit")
    ap.add_argument("--pca", type=int, default=DEFAULT_PCA, help="0 = PCA-гүй (зөвхөн жижиг блокт)")
    ap.add_argument("--l2", type=float, default=DEFAULT_L2)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--extra", nargs="*", default=[], help="нэмэлт сургалтын шинжийн хавтас (knowledge, синтетик)")
    ap.add_argument("--no-v2cat", action="store_true", help="probe+v2f хувилбарыг алгасах (хурдан)")
    ap.add_argument("--sweep", action="store_true", help="судалгааны: давхарга/pca-ийн хэд хэдэн хослол (dev-д "
                                                          "тааруулах тул үр дүн өөдрөг — шийдвэрт ашиглахгүй)")
    ap.add_argument("--mode", default="rules+probe", choices=["probe", "rules+probe", "probe+v2f", "rules+probe+v2f"])
    ap.add_argument("--save", default="build/probe/probe_m1a.npz")
    ap.add_argument("--model", default="build/probe/probe_m1a.npz")
    ap.add_argument("--out", default="build/probe/decisions.jsonl")
    a = ap.parse_args(argv)
    blocks = order_blocks([b for b in a.blocks.split(",") if b])

    if a.apply:
        m = ProbeModel.load(a.model)
        ids, X, meta = load_feature_dir(a.features, m.meta["blocks"])
        rows = rows_from_run(a.run)
        X = align(ids, X, [r["id"] for r in rows])
        rules = m.meta.get("mode", "").startswith("rules+")
        V2 = _v2_matrix(rows, m.v2_feats) if m.v2_feats else None
        pu, pa = m.probs(X, V2)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        from collections import Counter
        cnt = Counter()
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            for r, u, v in zip(rows, pu, pa, strict=False):
                act = "refuse" if r["hard"] else (r["rule"] if rules and r["rule"] != "answer"
                                                  else decide(u, v, m.t_u, m.t_a))
                cnt[act] += 1
                fh.write(json.dumps({"id": r["id"], "action": act, "p_u": round(float(u), 4),
                                     "p_a": round(float(v), 4), "rule": r["rule"], "hard": r["hard"]},
                                    ensure_ascii=False) + "\n")
        print(json.dumps({"out": str(out), "n": len(rows), "actions": dict(cnt), "t_u": m.t_u, "t_a": m.t_a}))
        return 0

    rows = AB.load_dev_table(a.run)
    ids, X, meta = load_feature_dir(a.features, blocks)
    if meta.get("split") == "test":
        raise SystemExit("--cv/--train зөвхөн dev-ийн шинжээр")
    X = align(ids, X, [r["id"] for r in rows])
    Xe, ce = _extra(a.extra, blocks)

    if a.train:
        hard = np.array([r["hard"] for r in rows])
        ruled = np.array([r["rule"] != "answer" for r in rows])
        mask = ~hard & (~ruled if a.mode.startswith("rules+") else True)
        v2f = list(AB.DEFAULT_FEATURES) if a.mode.endswith("+v2f") else []
        V2 = _v2_matrix(rows, v2f) if v2f else None
        m = train(X, [r["cat"] for r in rows], [r["ans_ok"] for r in rows], [r["family"] for r in rows], mask,
                  Xe, ce, a.pca, a.l2, V2, None if V2 is None or Xe is None else np.zeros((len(Xe), len(v2f))),
                  inner_k=a.k, n_side=meta["_n_side"], meta={"blocks": blocks, "mode": a.mode, "v2_feats": v2f, "features": str(a.features),
                                     "run": a.run, "n_dev": len(rows), "n_extra": len(ce),
                                     "layers": meta.get("layers"), "model_path": meta.get("model_path")})
        p = m.save(a.save)
        print(json.dumps({"saved": str(p), "t_u": m.t_u, "t_a": m.t_a, "mode": a.mode, "blocks": blocks}))
        return 0

    out = {"rules": AB.rules_baseline(rows), "config": {"blocks": blocks, "pca": a.pca, "l2": a.l2, "k": a.k,
                                                          "repeats": a.repeats, "n_extra": len(ce), "run": a.run}}
    cv = cross_validate(rows, X, a.k, a.repeats, a.pca, a.l2, Xe, ce, with_v2cat=not a.no_v2cat,
                        n_side=meta["_n_side"])
    out["cv"] = cv
    out["verdict"] = adopt_verdict(cv)
    if a.sweep:
        sw = {}
        layers = meta.get("layers") or []
        combos = [[f"L{L}_{p}"] for L in layers for p in ("mean", "last")] + [["logit"]] + \
                 [[f"L{L}_mean", f"L{L}_last", "logit"] for L in layers]
        for bl in combos:
            for pc in (16, 32, 64):
                try:
                    _, Xs, ms = load_feature_dir(a.features, bl)
                except FileNotFoundError:
                    continue
                Xs = align(ids, Xs, [r["id"] for r in rows])
                if bl == ["logit"] and pc != 16:
                    continue
                r_ = cross_validate(rows, Xs, a.k, a.repeats, pc, a.l2, with_v2cat=False, n_side=ms["_n_side"])
                sw[f"{'+'.join(bl)}|pca{pc}"] = {s: r_[s]["ex_mean"] for s in ("probe", "rules+probe")}
        out["sweep_exploratory"] = sw
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
