"""Hidden-state probe (pipeline/probe.py) ба extract_hidden.py-ийн numpy хэсэг — синтетик массив, загвар/GPU/DB-гүй."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from mn_text2sql.pipeline import abstain as AB
from mn_text2sql.pipeline import probe as P

ROOT = Path(__file__).resolve().parents[1]


def _rows(n_fam=60, seed=0):
    """abstain.load_dev_table-ийн хэлбэртэй мөр: family бүрт 1 ангилал (dev шиг), 70/15/15%."""
    rng = np.random.default_rng(seed)
    rows = []
    for f in range(n_fam):
        cat = "unanswerable" if f % 7 == 0 else "ambiguous" if f % 7 == 1 else "answerable"
        for j in range(5):
            rows.append({"id": f"Q{f:03d}_{j}", "family": f"F{f:03d}", "cat": cat, "hard": False, "rule": "answer",
                         "x": {k: 0.0 for k in AB.DEFAULT_FEATURES}, "ans_ok": bool(rng.random() < 0.5)})
    return rows


def _hidden(rows, d=200, sep=3.0, seed=1):
    """Ангиллаар шилжсэн далд төлөв (sep=0 бол санамсаргүй)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(len(rows), d))
    dirs = {"unanswerable": rng.normal(size=d), "ambiguous": rng.normal(size=d)}
    for i, r in enumerate(rows):
        if r["cat"] in dirs:
            X[i] += sep * dirs[r["cat"]] / np.linalg.norm(dirs[r["cat"]]) * np.sqrt(d) / 4
    return X


def test_newton_logreg_separates_and_matches_direction():
    rng = np.random.default_rng(0)
    A = rng.normal(size=(200, 5))
    t = (A[:, 0] + 0.5 * A[:, 1] > 0).astype(float)
    w, b = P.fit_logreg_newton(A, t, l2=1.0)
    p = P._sigmoid(A @ w + b)
    assert ((p > 0.5) == (t > 0)).mean() > 0.95
    assert w[0] > abs(w[2]) and w[0] > 0
    # L2 их бол жин багасна
    w2, _ = P.fit_logreg_newton(A, t, l2=1e4)
    assert np.linalg.norm(w2) < np.linalg.norm(w)


def test_decide_order_and_threshold_tuning():
    assert P.decide(0.9, 0.9, 0.5, 0.5) == "refuse"          # татгалзал тодруулгаас өмнө
    assert P.decide(0.1, 0.9, 0.5, 0.5) == "clarify"
    assert P.decide(0.1, 0.1, 0.5, 0.5) == "answer"
    cats = ["unanswerable"] * 5 + ["ambiguous"] * 5 + ["answerable"] * 10
    oks = [False] * 10 + [True] * 10
    pu = np.array([0.9] * 5 + [0.1] * 5 + [0.3] * 10)
    pa = np.array([0.1] * 5 + [0.8] * 5 + [0.4] * 10)
    tu, ta = P.tune_thresholds(pu, pa, cats, oks)
    assert 0.3 < tu <= 0.9 and 0.4 < ta <= 0.8
    acts = [P.decide(u, a, tu, ta) for u, a in zip(pu, pa, strict=False)]
    assert sum(AB.utility(x, c, ok) for x, c, ok in zip(acts, cats, oks, strict=False)) == 20


def test_cv_separable_beats_rules_and_random_does_not_crash():
    rows = _rows()
    n_pos = sum(r["cat"] != "answerable" for r in rows)
    oracle = sum(r["ans_ok"] for r in rows if r["cat"] == "answerable") + n_pos
    cv = P.cross_validate(rows, _hidden(rows), k=5, repeats=2, pca=16, with_v2cat=False)
    rules = AB.rules_baseline(rows)["rules_E4"]["ex_mean"]   # дүрэм бүгд «answer»
    assert cv["probe"]["ex_mean"] >= oracle - 5          # бараг төгс (oracle = хариулбал зөв + бүх татгалзах/тодруулах)
    assert cv["probe"]["ex_mean"] > rules + 0.4 * n_pos
    assert set(cv) == {"v2", "rules+v2", "probe", "rules+probe"}
    assert len(cv["probe"]["per_repeat"]) == 2
    assert cv["rules+probe"]["ex_mean"] == cv["probe"]["ex_mean"]   # дүрэм идэвхгүй үед ижил
    # санамсаргүй шинж: ажиллана, хязгаарт
    cvr = P.cross_validate(rows, _hidden(rows, sep=0.0), k=5, repeats=1, pca=8, with_v2cat=True)
    for s in cvr.values():
        assert 0 <= s["ex_mean"] <= len(rows)
    assert "rules+probe+v2f" in cvr


def test_cv_hard_and_rules_take_precedence():
    rows = _rows(n_fam=35)
    for r in rows[:5]:
        r["hard"], r["rule"] = True, "refuse"
    for r in rows[5:10]:
        r["rule"] = "clarify"
    cv = P.cross_validate(rows, _hidden(rows, sep=0.0), k=5, repeats=1, pca=4, with_v2cat=False)
    conf = cv["rules+probe"]["confusion"]
    want = {"refuse": 0, "clarify": 0}
    for r in rows[:10]:
        want["refuse" if r["hard"] else "clarify"] += 1
    got_ref = sum(v for k, v in conf.items() if k.endswith("->refuse"))
    got_cla = sum(v for k, v in conf.items() if k.endswith("->clarify"))
    assert got_ref >= want["refuse"] and got_cla >= want["clarify"]


def test_cv_deterministic_and_paired_fields():
    rows = _rows(n_fam=30)
    X = _hidden(rows, d=50)
    a = P.cross_validate(rows, X, k=5, repeats=2, pca=8, with_v2cat=False)
    b = P.cross_validate(rows, X, k=5, repeats=2, pca=8, with_v2cat=False)
    assert a["probe"]["per_repeat"] == b["probe"]["per_repeat"]
    assert a["rules+v2"]["paired_vs_rules+v2"]["mean"] == 0
    v = P.adopt_verdict(a, ref=0.0, min_better=0)
    assert v["beats_ref"] and v["adopt"]
    v = P.adopt_verdict(a, ref=1e9)
    assert not v["adopt"]


def test_extra_rows_used_for_training_only():
    rows = _rows(n_fam=30)
    X = _hidden(rows, d=40)
    Xe = _hidden(rows[:20], d=40, seed=5)
    cv = P.cross_validate(rows, X, k=5, repeats=1, pca=8, Xe=Xe, ce=["answerable"] * 20, with_v2cat=False)
    assert cv["probe"]["n"] == len(rows)


def test_side_columns_bypass_pca_and_save_load_roundtrip(tmp_path):
    rows = _rows(n_fam=30)
    H = _hidden(rows, d=120)
    side = np.array([[1.0 if r["cat"] == "unanswerable" else 0.0, 0.5] for r in rows])
    X = np.hstack([H, side])
    cats = [r["cat"] for r in rows]
    m = P.train(X, cats, [r["ans_ok"] for r in rows], [r["family"] for r in rows], pca=8, n_side=2,
                meta={"blocks": ["L20_mean", "logit"], "mode": "rules+probe", "v2_feats": []})
    assert m.V.shape == (120, 8) and m.meta["n_side"] == 2
    assert m.transform(X).shape == (len(rows), 10)
    p = m.save(tmp_path / "probe.npz")
    m2 = P.ProbeModel.load(p)
    pu1, pa1 = m.probs(X)
    pu2, pa2 = m2.probs(X)
    assert np.allclose(pu1, pu2) and np.allclose(pa1, pa2)
    assert (m2.t_u, m2.t_a) == (m.t_u, m.t_a)
    # online гэрээ: дүрэм ялна; эс бөгөөс probe
    assert P.probe_decision(X[0], m2, rule="clarify") == ("clarify", "rule")
    i_u = next(i for i, c in enumerate(cats) if c == "unanswerable")
    d = P.probe_decision(X[i_u], m2)
    assert d is None or d[0] in ("refuse", "clarify")


def test_load_feature_dir_orders_blocks_and_aligns(tmp_path):
    ids = ["A", "B", "C"]
    (tmp_path / "ids.json").write_text(json.dumps({"ids": ids, "layers": [20]}), encoding="utf-8")
    np.save(tmp_path / "L20_mean.npy", np.arange(3 * 100, dtype=np.float16).reshape(3, 100))
    np.save(tmp_path / "logit.npy", np.array([[1, 2, 3]] * 3, np.float16))
    got_ids, X, meta = P.load_feature_dir(tmp_path, ["logit", "L20_mean"])
    assert X.shape == (3, 103) and X.dtype == np.float32
    assert meta["_blocks"] == ["L20_mean", "logit"] and meta["_n_side"] == 3
    assert np.allclose(X[:, -3:], [1, 2, 3])
    Y = P.align(got_ids, X, ["C", "A"])
    assert np.allclose(Y[0, :100], X[2, :100])
    with pytest.raises(ValueError):
        P.align(got_ids, X, ["Z"])
    with pytest.raises(FileNotFoundError):
        P.load_feature_dir(tmp_path, ["L16_mean"])


def _extract_module():
    spec = importlib.util.spec_from_file_location("extract_hidden", ROOT / "scripts/probe/extract_hidden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)    # torch/transformers-ийг дээд түвшинд импортлохгүй
    return mod


def test_extract_test_split_requires_final_test_flag():
    mod = _extract_module()
    with pytest.raises(SystemExit) as e:
        mod.main(["--split", "test", "--out", "unused"])
    assert "--final-test" in str(e.value)


def test_extract_consolidate_resumable_parts(tmp_path):
    mod = _extract_module()
    items = [{"id": "A001", "family": "f1", "category": "answerable"},
             {"id": "U/02", "family": "f2", "category": "unanswerable"},
             {"id": "X003", "family": "f3", "category": "ambiguous"}]
    (tmp_path / "parts").mkdir()
    for it in items[:2]:   # 3 дахь нь дуусаагүй (тасарсан)
        np.savez(tmp_path / "parts" / f"{mod.safe_name(it['id'])}.npz",
                 L20_mean=np.ones(8, np.float16), L20_last=np.full(8, 7e4, np.float32),
                 logit=np.array([1.0, 0.5, 0.1], np.float16), n_tokens=np.asarray(100), truncated=np.asarray(False))
    r = mod.consolidate(tmp_path, items, {"split": "dev", "layers": [20]})
    assert r == {"n": 2, "missing": 1, "blocks": ["L20_last", "L20_mean", "logit"]}
    meta = json.loads((tmp_path / "ids.json").read_text(encoding="utf-8"))
    assert meta["ids"] == ["A001", "U/02"] and meta["items"][1]["category"] == "unanswerable"
    a = np.load(tmp_path / "L20_last.npy")
    assert a.dtype == np.float16 and np.isfinite(a).all()            # 7e4 → fp16-д clip
    # test: category хадгалахгүй
    mod.consolidate(tmp_path, items, {"split": "test"})
    meta = json.loads((tmp_path / "ids.json").read_text(encoding="utf-8"))
    assert "category" not in meta["items"][0]
