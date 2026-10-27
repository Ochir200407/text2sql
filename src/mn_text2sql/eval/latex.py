"""Төгсөлтийн ажлын тайлангийн (report/, ШУТИС MUSTThesis LaTeX) хүснэгтүүдийг results/-аас үүсгэнэ.

  t2s report-latex [--out report/generated]

Үүсгэх файлууд (report/Chapters/*.tex эдгээрийг \\input хийнэ):
  status.tex          \\resultsavailable{0|1}, хамгийн сүүлийн run-ийн огноо
  evalset.tex         eval set-ийн статистик (evalset/MANIFEST.json)
  results_dev.tex     туршилтын хүснэгт A–G (dev, кирилл, хоёр загвар) — REPORT.md-ийн ижил өгөгдөл
  results_test.tex    test-ийн run-ууд
  scripts_dev.tex     бичгийн зөрүү (A1: кирилл / латин / англи / холимог)
  thesis_compare.tex  text2sql-thesis-ийн 150 кейс дээрх харьцуулалт (results/thesis)
Сервер дээр туршилт хийгдээгүй бол хүснэгтэд «—» гарч, бүлгийн текст үүнийг тэмдэглэнэ.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from ..config import Config, load_config, resolve_path
from ..pipeline.options import load_experiments

TEX_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
           "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex(s) -> str:
    return "".join(TEX_ESC.get(ch, ch) for ch in str(s if s is not None else "—"))


def _fmt(st) -> str:
    if not st or st.get("mean") is None:
        return "—"
    return f"{st['mean']:.1f}" + (f" $\\pm$ {st['std']:.1f}" if st.get("n", 1) > 1 else "")


def _report_json(cfg: Config, split: str) -> dict:
    from .report import build_report
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "REPORT.md"
        build_report(cfg, out=str(out), split=split)
        return json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def evalset_table(out: Path) -> None:
    man = json.loads(resolve_path("evalset/MANIFEST.json").read_text(encoding="utf-8"))
    f = man["files"]
    rows = []
    for key, label in (("dev", "dev"), ("test", "test")):
        s = f[key]
        c, d = s["category"], s["difficulty_answerable"]
        rows.append(f"{label} & {s['n']} & {c['answerable']} & {c['unanswerable']} & {c['ambiguous']} & "
                    f"{d['easy']}/{d['medium']}/{d['hard']} & {s['mixed_variant']} & {s['families']} \\\\")
    domains = ", ".join(f"{tex(k)} ({v} / {f['test']['domain'].get(k, 0)})" for k, v in f["dev"]["domain"].items())
    _write(out / "evalset.tex", [
        "\\begin{table}[H]\\centering\\small",
        "\\caption{Үнэлгээний асуултын сан (eval set) --- хуваалт ба ангилал}\\label{tab:evalset}",
        "\\begin{adjustbox}{max width=\\textwidth}", "\\begin{tabular}{lrrrrrrr}\\toprule",
        "Хуваалт & Нийт & Хариулах & Хариулах & Хоёрдмол & Хүндрэл & Холимог & Загварын \\\\",
        " & & боломжтой & боломжгүй & & хялбар/дунд/хүнд & хувилбар & гэр бүл \\\\\\midrule",
        *rows,
        "\\bottomrule\\end{tabular}", "\\end{adjustbox}",
        f"\\par\\smallskip\\footnotesize Домэйн (dev / test): {domains}. Жишээний сан (few-shot): "
        f"{f['examples']['n']} асуулт. Ойролцоо давхардал (dev--test, жишээ--eval): 0.",
        "\\end{table}"])


def results_table(cfg: Config, out: Path, split: str) -> bool:
    exp = load_experiments()
    rj = _report_json(cfg, split)
    groups = rj.get("groups", {})
    any_result = False
    lines = ["\\begin{small}", "\\begin{longtable}{p{1.0cm}p{7.6cm}rr}",
             f"\\caption{{Туршилтын үр дүн ({split}, кирилл асуулт, EX \\%)}}\\label{{tab:results-{split}}}\\\\",
             "\\toprule ID & Тохиргоо & Arctic-R1-7B & sqrl-9b \\\\\\midrule\\endfirsthead",
             "\\toprule ID & Тохиргоо & Arctic-R1-7B & sqrl-9b \\\\\\midrule\\endhead"]
    from .report import GROUP_TITLE
    for g in sorted(groups):
        lines.append(f"\\multicolumn{{4}}{{l}}{{\\textbf{{{tex(GROUP_TITLE.get(g, g))}}}}}\\\\")
        for rid, per in groups[g].items():
            cells = []
            for m in ("arctic-r1-7b", "sqrl-9b"):
                if m not in per:
                    cells.append("n/a")
                    continue
                v = _fmt(per[m].get("ex"))
                any_result |= v != "—"
                cells.append(v)
            desc = (exp["runs"].get(rid) or {}).get("desc", "")
            lines.append(f"{rid} & {tex(desc)} & {cells[0]} & {cells[1]} \\\\")
    lines += ["\\bottomrule",
              "\\multicolumn{4}{p{13.4cm}}{\\footnotesize F1, F1b нь монгол BIRD dev дээр (ББСБ-ийн eval set биш).}\\\\",
              "\\end{longtable}", "\\end{small}"]
    _write(out / f"results_{split}.tex", lines)
    return any_result


def scripts_table(cfg: Config, out: Path) -> None:
    from .report import collect
    runs = collect(cfg, "dev")
    lines = ["\\begin{table}[H]\\centering\\small",
             "\\caption{Асуултын хувилбарын (бичиг, хэл) нөлөө (A1, dev, EX \\%)}\\label{tab:scripts}",
             "\\begin{tabular}{lrrrr}\\toprule Загвар & Кирилл & Латин галиг & Англи & Холимог \\\\\\midrule"]
    for m, label in (("arctic-r1-7b", "Arctic-R1-7B"), ("sqrl-9b", "sqrl-9b")):
        cells = []
        for v in ("mn", "latin", "en", "mixed"):
            rs = runs.get(("A1", m, v))
            cells.append(_fmt(rs.ex()) if rs and rs.summaries else "—")
        lines.append(f"{label} & " + " & ".join(cells) + " \\\\")
    lines += ["\\bottomrule\\end{tabular}\\end{table}"]
    _write(out / "scripts_dev.tex", lines)


def thesis_table(out: Path) -> bool:
    from ..thesis.bench import RUN_FILE_TAGGED
    cfg = load_config("config/thesis.yaml")
    data = {}
    root = resolve_path(cfg["eval"]["results_dir"]) / "runs"
    lines = []
    found = False
    for split in ("dev", "test"):
        sdir = root / split
        rows = []
        if sdir.exists():
            for f in sorted(sdir.glob("*/*.summary.json")):
                m = RUN_FILE_TAGGED.fullmatch(f.name.replace(".summary.json", ".jsonl"))
                if not m or m.group("seed") != "0":
                    continue
                s = json.loads(f.read_text(encoding="utf-8"))
                name = f.parent.name + (f"-{m.group('tag')}" if m.group("tag") else "")
                model = m.group("model")
                if name.startswith("EXT-"):      # thesis-ийн систем: файлын нэр биш, ашигласан LLM-ийг харуулна
                    meta = f.with_name(f.name.replace(".summary.json", ".meta.json"))
                    llm = json.loads(meta.read_text(encoding="utf-8")).get("llm") if meta.exists() else None
                    model = llm or "—"
                data.setdefault((split, name, model), {})[m.group("variant")] = s.get("ex")
        for (sp, name, model), per in sorted(data.items()):
            if sp != split:
                continue
            found = True
            rows.append(f"{tex(name)} & {tex(model)} & {per.get('mn', '—')} & {per.get('en', '—')} & "
                        f"{per.get('latin', '—')} \\\\")
        if rows:
            lines += [f"\\multicolumn{{5}}{{l}}{{\\textbf{{{split}}}}}\\\\", *rows]
    if not lines:
        lines = ["\\multicolumn{5}{c}{\\emph{Сервер дээр ажиллуулсны дараа бөглөгдөнө}}\\\\"]
    _write(out / "thesis_compare.tex", [
        "\\begin{table}[H]\\centering\\small",
        "\\caption{text2sql-thesis-ийн кейс дээрх харьцуулалт (EX \\%, ижил шалгуур)}\\label{tab:thesis-compare}",
        "\\begin{tabular}{llrrr}\\toprule Систем & Загвар & Кирилл & Англи & Латин \\\\\\midrule",
        *lines, "\\bottomrule\\end{tabular}",
        "\\par\\smallskip\\footnotesize EXT-* = thesis-ийн agent систем (ижил шалгуураар дахин оноолсон). McNemar ба "
        "хос асуултын зөрүүг \\texttt{results/thesis/COMPARE.md}-ээс үзнэ.",
        "\\end{table}"])
    return found


def experiments_list(out: Path) -> None:
    """Хавсралт: config/experiments.yaml-ийн бүх ID, суурь, тайлбар."""
    exp = load_experiments()
    lines = ["\\begin{small}", "\\begin{longtable}{p{1.0cm}p{2.6cm}p{9.6cm}}",
             "\\caption{Туршилтын тохиргооны бүрэн жагсаалт (config/experiments.yaml)}\\label{tab:all-experiments}\\\\",
             "\\toprule ID & Суурь & Тайлбар \\\\\\midrule\\endfirsthead",
             "\\toprule ID & Суурь & Тайлбар \\\\\\midrule\\endhead"]
    for rid, spec in exp["runs"].items():
        spec = spec or {}
        lines.append(f"{tex(rid)} & {tex(spec.get('base') or '—')} & {tex(spec.get('desc', ''))} \\\\")
    lines += ["\\bottomrule", "\\end{longtable}", "\\end{small}"]
    _write(out / "experiments.tex", lines)


def build_latex(cfg: Config, out_dir: str = "report/generated") -> dict:
    out = resolve_path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    evalset_table(out)
    experiments_list(out)
    have_dev = results_table(cfg, out, "dev")
    have_test = results_table(cfg, out, "test")
    scripts_table(cfg, out)
    have_thesis = thesis_table(out)
    _write(out / "status.tex", [
        f"\\newcommand{{\\resultsavailable}}{{{1 if have_dev else 0}}}",
        f"\\newcommand{{\\testresultsavailable}}{{{1 if have_test else 0}}}",
        f"\\newcommand{{\\thesisresultsavailable}}{{{1 if have_thesis else 0}}}",
        f"\\newcommand{{\\generatedon}}{{{datetime.now().strftime('%Y-%m-%d %H:%M')}}}"])
    return {"out": str(out), "dev_results": have_dev, "test_results": have_test, "thesis_results": have_thesis}
