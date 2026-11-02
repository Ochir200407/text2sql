"""scripts/ingest/ingest_results.py — синтетик fixture дээр (бүтэн JSONL ба товч *_kaggle_q.txt).

Мөн ingest-тэй хамт засварласан eval/metrics.py (McNemar-ийн p бүрэн нарийвчлалтай) ба eval/report.py
(derive_abstention-ийн strict_correct) — файлын төгсгөлд."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ingest_results", ROOT / "scripts" / "ingest" / "ingest_results.py")
ing = importlib.util.module_from_spec(_spec)
sys.modules["ingest_results"] = ing     # dataclass-д модуль бүртгэлтэй байх ёстой
_spec.loader.exec_module(ing)

M = "arctic-r1-7b"


def _questions(split):
    n_ans, n_un, n_amb = (151, 24, 25) if split == "test" else (224, 38, 38)
    cats = ["answerable"] * n_ans + ["unanswerable"] * n_un + ["ambiguous"] * n_amb
    p = "T" if split == "test" else "D"
    return [(f"{p}{i:03d}", c, ("easy", "medium", "hard")[i % 3], ("loans", "cards")[i % 2])
            for i, c in enumerate(cats)]


def _rows(split, cid, correct):
    return [{"question_id": q, "config_id": cid, "split": split, "variant": "mn", "category": c, "difficulty": d,
             "domain": dm, "status": "answered", "correct": bool(correct(i, q, c)), "confidence": 1.0,
             "cand_correct": None} for i, (q, c, d, dm) in enumerate(_questions(split))]


def _write_jsonl(d: Path, rows):
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{M}__mn__s0.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (d / f"{M}__mn__s0.summary.json").write_text("{}", encoding="utf-8")
    (d / f"{M}__mn__s0.meta.json").write_text("{}", encoding="utf-8")
    return f


def _q_lines(rows, prefix=""):
    return [f"{prefix}Q|{r['question_id']}|{r['category']}|{r['status']}|{int(r['correct'])}|1.0|" for r in rows]


# dev хариулах 224: M1ens зөв 0..99; M1ensp: 0..83 зөв (16 буурсан), 100..141 зөв (42 ахисан) → b=42, c=16
def _ens(i, q, c):
    return c == "answerable" and i < 100 or c == "unanswerable" and i % 2 == 0


def _ensp(i, q, c):
    return c == "answerable" and (i < 84 or 100 <= i < 142) or c == "unanswerable" and i % 2 == 0


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "kaggle_out"
    # test — бүтэн JSONL
    _write_jsonl(src / "results" / "test" / "M1ens", _rows("test", "M1ens", lambda i, q, c: i % 2 == 0))
    _write_jsonl(src / "results" / "test" / "M1ensr", _rows("test", "M1ensr", lambda i, q, c: i % 3 != 2))
    _write_jsonl(src / "results" / "test" / "M1r", _rows("test", "M1r", lambda i, q, c: i % 4 != 3))
    # test — M1a товч хэлбэр (*_kaggle_q.txt, test хавтсанд)
    m1a = _rows("test", "M1a", lambda i, q, c: i % 2 == 1)
    (src / "test").mkdir(parents=True)
    (src / "test" / "M1a_kaggle_q.txt").write_text("\n".join(_q_lines(m1a)) + "\n", encoding="utf-8")
    # dev — M1ens бүтэн JSONL (split талбараас), M1ensp notebook-ийн лог дахь [compact] толгойтой
    _write_jsonl(src / "results" / "M1ens", _rows("dev", "M1ens", _ens))
    ensp = _rows("dev", "M1ensp", _ensp)
    log = [f"[compact] results/runs/dev/M1ensp/{M}__mn__s0.jsonl n=300"] + _q_lines(ensp, "12:00:01 ") + \
          ["S|total|x/300"]
    (src / "notebook.log").write_text("noise line\n" + "\n".join(log) + "\n", encoding="utf-8")
    return src


def _latex(out: str) -> str:
    return out.split("=== LaTeX (Ch6) ===")[1].split("=== Слайд")[0]


def test_mcnemar_known_value():
    p = ing.mcnemar_exact(42, 16)
    assert p == pytest.approx(0.000862, abs=1e-5) and round(p, 4) == 0.0009
    assert ing.mcnemar_exact(0, 0) == 1.0
    assert ing.mcnemar_exact(5, 5) == 1.0
    assert ing.mcnemar_exact(11, 1) == pytest.approx(0.00635, abs=1e-4)
    lo, hi = ing.wilson(81, 151)
    assert 0.45 < lo < 0.54 < hi < 0.62


def test_fmt_p_significant_digits():
    # 3 тэмдэгт цифр; жижиг p «0.0» болохгүй (metrics.py-ийн хуучин round(p, 5) алдаа)
    assert ing.fmt_p(ing.mcnemar_exact(42, 16)) == "8.62e-04"
    assert ing.fmt_p(ing.mcnemar_exact(26, 15)) == "0.117"
    assert ing.fmt_p(ing.mcnemar_exact(77, 14)) == "1.05e-11"
    assert ing.fmt_p(0.0496) == "0.0496" and ing.fmt_p(1.0) == "1"
    assert ing.tex_p(ing.mcnemar_exact(42, 16)) == r"$8.62 \times 10^{-4}$" and ing.tex_p(0.117) == "0.117"


def test_parse_compact_with_log_prefix_and_headers():
    text = ("[compact] results/runs/test/M1ks/x.jsonl n=2\n"
            "10:00 Q|A001|answerable|answered|1|0.9|10\nQ|A002|unanswerable|refused|1||\n"
            "[compact] results/runs/dev/M1m/x.jsonl n=1\nQ|D1|ambiguous|clarify|0|0.4|0\n")
    blocks = ing.parse_compact_text(text)
    assert [(b[0], b[1], len(b[2])) for b in blocks] == [("M1ks", "test", 2), ("M1m", "dev", 1)]


def test_ingest_end_to_end(tmp_path, capsys):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    assert ing.main(["--src", str(src), "--root", str(root)]) == 0
    out = capsys.readouterr().out
    runs = root / "results" / "runs"
    for rid in ("M1ens", "M1ensr", "M1r"):
        for suf in (".jsonl", ".summary.json", ".meta.json"):
            assert (runs / "test" / rid / f"{M}__mn__s0{suf}").exists()
    assert (runs / "dev" / "M1ens" / f"{M}__mn__s0.jsonl").exists()
    q = (runs / "dev" / "M1ensp" / "M1ensp_kaggle_q.txt").read_text(encoding="utf-8").splitlines()
    assert len(q) == 300 and q[0].startswith("Q|D000|")          # лог-ийн угтвар хасагдсан
    assert len((runs / "test" / "M1a" / "M1a_kaggle_q.txt").read_text(encoding="utf-8").splitlines()) == 200

    md = (root / "docs" / "RESULTS_V26.md").read_text(encoding="utf-8")
    assert "PICK_TEST=1" in md and "PICK_TEST=1" in out
    assert "| dev | M1ens → M1ensp | хариулах | 100/224 | 126/224 | +42 / −16 | 8.62e-04 |" in md
    assert "| M1ens | 119/300 (39.7%)" in md                       # 100 хариулах + 19 боломжгүй (тэгш i)
    assert r"\begin{tabular}" in out and "Слайд 16" in out
    # Wilson CI бүх EX-д, аргын нэртэй
    assert "Wilson score 95% интервал" in md and "| ID | Нийт | Wilson 95% CI | Хариулах | Wilson 95% CI |" in md
    assert ("| M1ens | 100/200 (50.0%) | 43.1–56.9 | 76/151 (50.3%) | 42.4–58.2 | 12/24 (50.0%) [31.4–68.6] | "
            "12/25 (48.0%) [30.0–66.5] | jsonl | бүрэн |") in md
    assert "Слайд 11 (test-ийн үр дүн; [ ] = Wilson 95% CI):" in out
    assert "  - M1ens: нийт 100/200 (50.0%) [43.1–56.9], хариулах 76/151 (50.3%) [42.4–58.2]" in out
    # товч хэлбэрт түвшин/домэйн бүтэн JSONL-ээс нөхөгдөнө
    assert "**Түвшин" in md and "easy" in md and "?" not in md.split("**Түвшин")[1].split("|\n")[0]
    # test M1a → M1ens: нийт ба хариулах хоёулаа + ангилал бүрийн тоо (M1a сондгой, M1ens тэгш → b = c)
    assert ("test M1a → M1ens  нийт +100/−100 (n=200) p=1 | хариулах +76/−75 (n=151) p=1 | "
            "боломжгүй +12/−12 (n=24) | хоёрдмол +12/−13 (n=25)") in out
    assert "I3t" not in out                                       # I3t байхгүй үед хос алгасна


def test_dry_run_writes_nothing(tmp_path):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    assert ing.main(["--src", str(src), "--root", str(root), "--dry-run"]) == 0
    assert not (root / "results").exists() and not (root / "docs").exists()


def test_no_overwrite_without_force(tmp_path):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    dest = root / "results" / "runs" / "test" / "M1r" / f"{M}__mn__s0.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text("OLD\n", encoding="utf-8")
    ing.main(["--src", str(src), "--root", str(root)])
    assert dest.read_text(encoding="utf-8") == "OLD\n"
    ing.main(["--src", str(src), "--root", str(root), "--force"])
    assert dest.read_text(encoding="utf-8") != "OLD\n"


def test_bad_row_count_not_copied_and_no_pick(tmp_path, capsys):
    src = tmp_path / "kaggle_out"
    _write_jsonl(src / "results" / "test" / "M1ens", _rows("test", "M1ens", lambda i, q, c: True)[:150])
    _write_jsonl(src / "results" / "M1ens", _rows("dev", "M1ens", _ens))
    _write_jsonl(src / "results" / "M1ensp", _rows("dev", "M1ensp", _ens))   # ялгаагүй → PICK_TEST=0
    root = tmp_path / "proj"
    ing.main(["--src", str(src), "--root", str(root)])
    out = capsys.readouterr().out
    assert "мөр 150 ≠ 200" in out
    assert not (root / "results" / "runs" / "test" / "M1ens").exists()
    assert "PICK_TEST=0" in out


# --------------------------------------------------------------------------- M2a: дутуу run хасагдана
def test_partial_run_excluded_from_stats_mcnemar_latex_unless_force(tmp_path, capsys):
    src = _make_src(tmp_path)
    _write_jsonl(src / "results" / "test" / "M1ens", _rows("test", "M1ens", lambda i, q, c: i % 2 == 0)[:180])
    root = tmp_path / "proj"
    ing.main(["--src", str(src), "--root", str(root), "--dry-run"])
    out = capsys.readouterr().out
    assert "test/M1ens [jsonl]" in out and "мөр 180 ≠ 200" in out and "ХАСАВ" in out    # яагаад хассан
    assert "test M1a → M1ens: хасагдсан M1ens (мөр 180 ≠ 200)" in out                   # McNemar-гүй
    assert "test M1ens → M1ensr: хасагдсан M1ens (мөр 180 ≠ 200)" in out
    tex = _latex(out)
    assert "\nM1ens & " not in tex and "\nM1ens$" not in tex and "test M1a → M1ens & " not in tex  # LaTeX-д үгүй
    assert "test M1ens " not in out.split("=== EX")[1].split("=== McNemar")[0]          # статистикт ороогүй
    assert "Headline: урьдчилан зарласан M1ens — хасагдсан (мөр 180 ≠ 200); headline хэвлэхгүй" in out

    ing.main(["--src", str(src), "--root", str(root), "--dry-run", "--force"])
    out = capsys.readouterr().out
    tex = _latex(out)
    assert "M1ens$^{*}$ & 90/180 (50.0\\%)" in tex and "\\multicolumn{7}{l}{\\footnotesize $^{*}$" in tex
    assert "test M1a → M1ens* " in out and "(n=180)" in out and "зөвхөн давхцсан 180 асуулт" in out
    assert "M1ens* нийт 90/180" in out                                                  # headline-д ч «*»


def test_validate_category_counts():
    rows = _rows("test", "X", lambda i, q, c: True)
    for r in rows[:10]:
        r["category"] = "ambiguous"                                                     # 141/24/35
    run = ing.Run("X", "test", "jsonl", Path("x.jsonl"), "src", rows={r["question_id"]: r for r in rows})
    oc = ing.outcomes(run, {})
    assert ing.validate(run, oc) == "ангилал 141/24/35 ≠ 151/24/25 (хариулах/боломжгүй/хоёрдмол)"


# --------------------------------------------------------------------------- M2b: зөв id-ийн жагсаалт
def test_answerable_correct_list_never_total_and_labelled(tmp_path, capsys):
    src = _make_src(tmp_path)
    (src / "test" / "M1a_kaggle_q.txt").unlink()
    root = tmp_path / "proj"
    local = root / "results" / "runs" / "test"
    local.mkdir(parents=True)
    # дутуу жагсаалт (30 id) — дутуу асуулт харагдахгүй тул шалгалт давна, гэхдээ «†»
    (local / "M1a_answerable_correct.txt").write_text("\n".join(f"T{i:03d}" for i in range(30)) + "\n",
                                                      encoding="utf-8")
    ing.main(["--src", str(src), "--root", str(root), "--dry-run"])
    out = capsys.readouterr().out
    ex = out.split("=== EX")[1].split("=== McNemar")[0]
    assert "M1a†" in ex and "бүрэн эсэх шалгагдахгүй" in ex and "30/151 (19.9%)" in ex
    line = next(x for x in out.splitlines() if x.startswith("  test M1a† → M1ens"))
    assert "хариулах" in line and "нийт" not in line.split("[")[0]                     # нийт McNemar-гүй
    assert "† M1a: зөв id-ийн жагсаалт" in line
    tex = _latex(out)
    assert "M1a$^{\\dagger}$ & --- & --- & 30/151 (19.9\\%)" in tex
    assert "test M1a$^{\\dagger}$ → M1ens & хариулах боломжтой" in tex and "test M1a$^{\\dagger}$ → M1ens & нийт" not in tex
    assert "$^{\\dagger}$ зөв id-ийн жагсаалт" in tex

    # мэдэгдэхгүй id-тай жагсаалт → буруу, хасагдана
    (local / "M1a_answerable_correct.txt").write_text("T000\nZZZ999\n", encoding="utf-8")
    ing.main(["--src", str(src), "--root", str(root), "--dry-run"])
    out = capsys.readouterr().out
    assert "1 id хариулах асуултын мета-д алга (ZZZ999)" in out and "test M1a → M1ens: хасагдсан M1a" in out


# --------------------------------------------------------------------------- M2c: src нь хуучин локал JSONL-ийг давна
def test_fresh_src_compact_outranks_stale_local_jsonl(tmp_path, capsys):
    src = _make_src(tmp_path)
    root = tmp_path / "proj"
    _write_jsonl(root / "results" / "runs" / "test" / "M1a", _rows("test", "M1a", lambda i, q, c: False))  # хуучин
    ing.main(["--src", str(src), "--root", str(root), "--dry-run"])
    out = capsys.readouterr().out
    assert "test/M1a [compact] test/M1a_kaggle_q.txt n=200" in out and "src давуу" in out
    assert "\nM1a & 100/200 (50.0\\%)" in _latex(out)                                   # src-ийн дүн, 0/200 биш

    runs = ing.discover(src, "src") + ing.discover(root / "results" / "runs", "local")
    ing.prepare(runs, ing.build_meta(runs))
    best = ing.choose(runs)
    assert (best[("test", "M1a")].kind, best[("test", "M1a")].origin) == ("compact", "src")


# --------------------------------------------------------------------------- L3: headline урьдчилан зарласан
def test_headline_is_predeclared_not_max(tmp_path, capsys):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    ing.main(["--src", str(src), "--root", str(root), "--dry-run"])
    out = capsys.readouterr().out
    head = out.split("=== Headline ===\n")[1].splitlines()[0]
    # M1r (150/200) хамгийн өндөр ч headline нь урьдчилан зарласан M1ens (100/200)
    assert head.startswith("Headline (урьдчилан зарласан, --headline M1ens): M1ens нийт 100/200 (50.0%; "
                           "Wilson 95% CI 43.1–56.9)")
    assert "post hoc (test дээр хамгийн өндрийг сонгосон — headline биш, нотолгоо биш): M1r нийт 150/200" in out
    assert "Хамгийн өндөр" not in out

    ing.main(["--src", str(src), "--root", str(root), "--dry-run", "--headline", "M1zz"])
    out = capsys.readouterr().out
    slides = out.split("=== Слайд 11/16 ===")[1]
    assert "Headline: урьдчилан зарласан M1zz — test run алга; headline хэвлэхгүй (хамгийн өндрөөр орлуулахгүй)." in slides
    assert "M1r" not in slides.split("Слайд 16")[0].split("Headline:")[1].splitlines()[0]


# --------------------------------------------------------------------------- H2, H3
def test_every_total_mcnemar_has_answerable_and_category_counts(tmp_path, capsys):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    ing.main(["--src", str(src), "--root", str(root), "--dry-run"])
    out = capsys.readouterr().out
    tex_rows = _latex(out).split("tab:mcnemar")[1].splitlines()
    totals = [r.split(" & ")[0] for r in tex_rows if " & нийт & " in r]
    assert totals
    for name in totals:
        kinds = [r.split(" & ")[1] for r in tex_rows if r.startswith(name + " & ")]
        assert kinds == ["нийт", "хариулах боломжтой", "боломжгүй (тоо)", "хоёрдмол (тоо)"], name
    for line in (x for x in out.splitlines() if " нийт +" in x and "(n=" in x):
        assert " | хариулах +" in line and " | боломжгүй +" in line and " | хоёрдмол +" in line
    slide16 = out.split("Слайд 16")[1]
    assert "M1a → M1r: нийт +100/−50, p = 5.45e-05 (ач холбогдолтой); хариулах +76/−37" in slide16


def test_always_clarify_baseline_row(tmp_path, capsys):
    b = ing.clarify_baseline("test")
    assert (b["total"]["k"], b["total"]["n"]) == (49, 200) and (b["cat"]["answerable"]["k"], b["cat"]["answerable"]["n"]) == (0, 151)
    assert ing.clarify_baseline("dev")["total"]["k"] == 76
    src, root = _make_src(tmp_path), tmp_path / "proj"
    ing.main(["--src", str(src), "--root", str(root)])
    out = capsys.readouterr().out
    assert "Үргэлж тодруулах (суурь) & 49/200 (24.5\\%) & 19.1--30.9 & 0/151 (0.0\\%) & 0.0--2.5 & 24 & 25 \\\\" in _latex(out)
    md = (root / "docs" / "RESULTS_V26.md").read_text(encoding="utf-8")
    assert "| Үргэлж тодруулах (суурь) | 49/200 (24.5%) | 19.1–30.9 | 0/151 (0.0%) |" in md
    assert "| Үргэлж тодруулах (суурь) | 76/300 (25.3%) |" in md


# --------------------------------------------------------------------------- sensitivity
def test_load_ids_comments_and_dedup(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text("﻿# толгой тайлбар\nT000\n\nT001  # мөрийн тайлбар\nT000\n", encoding="utf-8")
    assert ing.load_ids(f) == ["T000", "T001"]


def test_exclude_ids_sensitivity_with_and_without(tmp_path, capsys):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    a, b = tmp_path / "setA.txt", tmp_path / "setB.txt"
    a.write_text("# A: хариулах 2 + боломжгүй 1\nT000\nT001\nT151\n", encoding="utf-8")
    b.write_text("# B\nT002\nZZZ999\n", encoding="utf-8")
    ing.main(["--src", str(src), "--root", str(root), "--exclude-ids", str(a), "--exclude-ids", str(b)])
    out = capsys.readouterr().out
    assert "=== Sensitivity: setA (3 id; test: 3)" in out and "=== Sensitivity: setB (2 id; test: 1)" in out
    assert "=== Sensitivity: setA+setB (5 id; test: 4)" in out
    sa = out.split("=== Sensitivity: setA (")[1].split("=== Sensitivity")[0]
    # M1ens (тэгш i зөв): T000 зөв, T001 буруу, T151 буруу → нийт 100/200 → 99/197, хариулах 76/151 → 75/149
    m1ens = next(x for x in sa.splitlines() if x.startswith("  test M1ens "))
    assert "нийт 100/200 (50.0%) [43.1–56.9] → 99/197 (50.3%)" in m1ens and "хариулах 76/151 (50.3%)" in m1ens
    assert "→ 75/149 (50.3%)" in m1ens
    # M1a → M1ens: T000 (b), T001 (c), T151 (c) хасагдана → нийт +100/−100 → +99/−98
    assert "test M1a → M1ens  нийт +100/−100 p=1 → +99/−98 p=1 | хариулах +76/−75 p=1 → +75/−74 p=1" in sa
    assert "анхаар: 1 id аль ч оруулсан run-д алга: ZZZ999" in out
    md = (root / "docs" / "RESULTS_V26.md").read_text(encoding="utf-8")
    assert "## Sensitivity: setA (3 id; test: 3)" in md
    assert "| test | M1a → M1ens | нийт | +100/−100 p=1 | +99/−98 p=1 |" in md


def test_missing_exclude_file_is_an_error(tmp_path, capsys):
    src, root = _make_src(tmp_path), tmp_path / "proj"
    assert ing.main(["--src", str(src), "--root", str(root), "--exclude-ids", str(tmp_path / "nope.txt")]) == 2
    assert "--exclude-ids файл алга" in capsys.readouterr().out


SENS = ROOT / "results" / "sensitivity"


@pytest.mark.skipif(not (SENS / "touched13.txt").exists(), reason="results/sensitivity алга (git-д ороогүй)")
def test_repo_sensitivity_id_files():
    t13, t6 = ing.load_ids(SENS / "touched13.txt"), ing.load_ids(SENS / "template6.txt")
    assert t13 == "A008 C012 G014 G018 G021 G022 K008 K011 K026 L039 P010 P011 P029".split()
    assert t6 == "P030 A040 C036 A015 L024 L020".split()
    assert not set(t13) & set(t6)


LOCAL = ROOT / "results" / "runs" / "test"


@pytest.mark.skipif(not all((LOCAL / x).exists() for x in ("D2", "H4", "E2_kaggle_q.txt", "E4_kaggle_q.txt")),
                    reason="хадгалсан test run алга (results/ git-д ороогүй)")
def test_stored_local_test_runs_d2_h4_e2_e4():
    runs = ing.discover(LOCAL.parent, "local")
    ing.prepare(runs, ing.build_meta(runs))
    best = ing.choose(runs)
    get = lambda rid: best[("test", rid)]                                      # noqa: E731
    assert all(get(r).problem is None for r in ("D2", "H4", "E2", "E4"))
    t = ing.pair_block(get("D2"), get("H4"))
    assert (t["all"]["b"], t["all"]["c"]) == (42, 16) and ing.fmt_p(t["all"]["p"]) == "8.62e-04"
    assert (t["ans"]["b"], t["ans"]["c"]) == (26, 15) and t["ans"]["p"] == pytest.approx(0.117, abs=5e-4)
    assert (t["cats"]["unanswerable"]["b"], t["cats"]["ambiguous"]["b"], t["cats"]["ambiguous"]["c"]) == (9, 7, 1)
    t = ing.pair_block(get("E2"), get("E4"))
    assert (t["all"]["b"], t["all"]["c"], t["ans"]["b"], t["ans"]["c"]) == (14, 1, 3, 1)


# --------------------------------------------------------------------------- eval/metrics.py, eval/report.py
def test_metrics_mcnemar_p_keeps_full_precision():
    from mn_text2sql.eval.metrics import fmt_p, mcnemar

    def rec(i, ok):
        return {"question_id": str(i), "variant": "mn", "category": "answerable", "correct": ok}

    a = [rec(i, i >= 77) for i in range(91)]          # 77 ахисан, 14 буурсан (dev D2 → H4-ийн нийт)
    b = [rec(i, i < 77) for i in range(91)]
    m = mcnemar(a, b)
    assert (m["wins"], m["losses"]) == (77, 14) and m["p_value"] > 0          # хуучин round(p, 5) → 0.0
    assert m["p_value"] == pytest.approx(ing.mcnemar_exact(77, 14), rel=1e-9)
    assert fmt_p(m["p_value"]) == "1.05e-11" and fmt_p(0.11727) == "0.117" and fmt_p(1.0) == "1"
    assert fmt_p(None) == "—"


def test_derive_abstention_recomputes_strict_correct(tmp_path):
    from mn_text2sql.config import load_config
    from mn_text2sql.eval import report
    cfg = load_config()
    cfg["eval"]["results_dir"] = str(tmp_path)
    src = tmp_path / "runs" / "dev" / "E2"
    src.mkdir(parents=True)

    def rec(qid, cat, correct, strict, conf):
        return {"question_id": qid, "variant": "mn", "category": cat, "status": "answered", "correct": correct,
                "raw_correct": correct, "strict_correct": strict, "confidence": conf}

    recs = [rec("A1", "answerable", True, True, 0.25),      # бага итгэл → тодруулга → буруу, strict ч буруу
            rec("A4", "answerable", True, True, 0.375),     # мөн адил
            rec("A2", "answerable", True, True, 1.0),       # хэвээр (strict зөв)
            rec("A3", "answerable", True, False, 1.0),      # хэвээр (зөвхөн зөөлөн таарал)
            rec("U1", "unanswerable", False, False, 0.25),  # тодруулга → зөв; strict = correct
            rec("M1", "ambiguous", False, False, 0.25),     # тодруулга → зөв; үр дүн хатуу тааралгүй
            rec("M2", "ambiguous", True, True, 0.25)]       # тодруулга → зөв; хатуу таарал хадгалагдана
    plain = {"question_id": "A5", "variant": "mn", "category": "answerable", "status": "answered", "correct": True,
             "confidence": 0.25}                             # strict_correct талбаргүй (хуучин run) → нэмэхгүй
    (src / f"{M}__mn__s0.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs + [plain]), encoding="utf-8")
    report.derive_abstention(cfg, "E2", "E3c", 0.5, "dev", action="clarify")
    dst = tmp_path / "runs" / "dev" / "E3c" / f"{M}__mn__s0.jsonl"
    got = {r["question_id"]: r for r in map(json.loads, dst.read_text(encoding="utf-8").splitlines())}
    assert [got[q]["strict_correct"] for q in ("A1", "A4", "A2", "A3", "U1", "M1", "M2")] == \
        [False, False, True, False, True, False, True]
    assert "strict_correct" not in got["A5"]
    summ = json.loads(dst.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summ["ex"] == 40.0 and summ["ex_strict"] == 20.0 and summ["ex_strict"] <= summ["ex"]   # хуучин: 60.0

    report.derive_abstention(cfg, "E2", "E3", 0.5, "dev", action="abstain")
    got = {r["question_id"]: r for r in map(json.loads, (tmp_path / "runs" / "dev" / "E3" / f"{M}__mn__s0.jsonl")
                                            .read_text(encoding="utf-8").splitlines())}
    assert not got["A1"]["strict_correct"] and got["U1"]["strict_correct"] and not got["M2"]["strict_correct"]
