"""scripts/ingest/ingest_results.py --tag/--out (V27 preset): dev-only тайлан, M1ensx ↔ M1ensx0 шийдвэр, M1ensra in-sample."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ingest_results_presets", ROOT / "scripts" / "ingest" / "ingest_results.py")
ing = importlib.util.module_from_spec(_spec)
sys.modules["ingest_results_presets"] = ing     # dataclass-д модуль бүртгэлтэй байх ёстой
_spec.loader.exec_module(ing)

M = "arctic-r1-7b"


def _questions(split):
    n_ans, n_un, n_amb = (151, 24, 25) if split == "test" else (224, 38, 38)
    cats = ["answerable"] * n_ans + ["unanswerable"] * n_un + ["ambiguous"] * n_amb
    p = "T" if split == "test" else "D"
    return [(f"{p}{i:03d}", c) for i, c in enumerate(cats)]


def _write(d: Path, split: str, cid: str, correct) -> None:
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"question_id": q, "config_id": cid, "split": split, "variant": "mn", "category": c, "difficulty": "easy",
             "domain": "loans", "status": "answered", "correct": bool(correct(i, c))}
            for i, (q, c) in enumerate(_questions(split))]
    (d / f"{M}__mn__s0.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _src(tmp_path: Path, gain: int) -> Path:
    """dev: M1ensx0 хариулах 0..99 зөв; M1ensx = 0..99 + 100..100+gain (зөвхөн ахиц); M1ensra хамгийн өндөр (in-sample)."""
    src = tmp_path / "v27_out"
    res = src / "mt" / "results"
    _write(res / "M1a", "dev", "M1a", lambda i, c: c == "answerable" and i < 90)
    _write(res / "M1ip", "dev", "M1ip", lambda i, c: c == "answerable" and i < 95)
    _write(res / "M1ens", "dev", "M1ens", lambda i, c: c == "answerable" and i < 100)
    _write(res / "M1ensx0", "dev", "M1ensx0", lambda i, c: c == "answerable" and i < 100)
    _write(res / "M1ensx", "dev", "M1ensx", lambda i, c: c == "answerable" and i < 100 + gain)
    _write(res / "M1ensra", "dev", "M1ensra", lambda i, c: c == "answerable" and i < 150 or c != "answerable")
    _write(res / "test" / "M1ens", "test", "M1ens", lambda i, c: i % 2 == 0)     # V26-ийн test (хуримтлагдсан архив)
    return src


def test_v27_preset_decision_in_sample_and_dev_only(tmp_path, capsys):
    src, root = _src(tmp_path, gain=20), tmp_path / "proj"
    assert ing.main(["--src", str(src), "--root", str(root), "--tag", "v27"]) == 0
    out = capsys.readouterr().out
    assert "M1ensx → V28 test: ТИЙМ" in out and "+20/−0" in out               # M1ensx0 → M1ensx шийдвэр
    mcn = out.split("=== McNemar")[1].split("=== Headline")[0]
    assert "M1ensra" not in mcn                                                # in-sample: хосгүй
    assert "M1ensra§" in out and "in-sample" in out
    assert "dev M1a → M1ip" in mcn and "dev M1ens → M1ensx0" in mcn
    assert "PICK_TEST" not in out.split("=== Шийдвэр ===")[1]
    md = (root / "docs" / "RESULTS_V27.md").read_text(encoding="utf-8")
    assert md.startswith("# V27-ийн үр дүн") and "## Dev (300)" in md and "## Test (200)" not in md
    assert "## Ch6-д (LaTeX" not in md and "M1ensra§" in md
    assert not (root / "docs" / "RESULTS_V26.md").exists()
    assert (root / "results" / "runs" / "test" / "M1ens" / f"{M}__mn__s0.jsonl").exists()   # хуулагдсан, тайлагнаагүй


def test_v27_no_gain_keeps_m1ens_and_out_path(tmp_path, capsys):
    src, root, out_md = _src(tmp_path, gain=0), tmp_path / "proj", tmp_path / "rep" / "v27.md"
    assert ing.main(["--src", str(src), "--root", str(root), "--tag", "V27", "--out", str(out_md)]) == 0
    out = capsys.readouterr().out
    assert "M1ensx → V28 test: үгүй" in out and "M1ens хэвээр" in out
    assert out_md.exists() and not (root / "docs" / "RESULTS_V27.md").exists()


def test_v27_missing_baseline_never_decides(tmp_path, capsys):
    src, root = _src(tmp_path, gain=30), tmp_path / "proj"
    for f in (src / "mt" / "results" / "M1ensx0").iterdir():
        f.unlink()
    ing.main(["--src", str(src), "--root", str(root), "--tag", "V27", "--dry-run"])
    out = capsys.readouterr().out
    assert "M1ensx → V28 test: үгүй" in out and "run алга: M1ensx0" in out


def test_default_tag_is_v26_and_presets_consistent():
    assert ing.PRESETS["V26"]["test_pairs"] == ing.TEST_PAIRS and ing.PRESETS["V26"]["headline"] == "M1ens"
    v27 = ing.PRESETS["V27"]
    assert ("M1ensx0", "M1ensx") in v27["dev_pairs"] and v27["splits"] == ("dev",)
    assert all("M1ensra" not in p for p in v27["dev_pairs"] + v27["test_pairs"])   # in-sample хэзээ ч хосод орохгүй
    assert set(v27["in_sample"]) == {"M1ensra"}
