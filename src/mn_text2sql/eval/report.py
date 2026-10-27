"""Туршилтын тайлан: results/runs/** → results/REPORT.md (+ report.json).

Хүснэгтийн формат туршилтын төлөвлөгөөний дагуу: ID | Тохиргоо | Arctic-R1-7B EX % | sqrl-9b EX % | Төлөв | Тэмдэглэл.
Хүлээн авах дүрэм (төлөвлөгөө): dev дээрх өсөлт статистикийн хувьд ач холбогдолтой (McNemar p < 0.05), англи ба
латин хувилбар 2 пунктээс илүү буураагүй, p95 хугацаа төсөвтөө багтсан.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..config import ROOT, Config, resolve_path
from ..pipeline.options import load_experiments, resolve_run
from .metrics import load_records, mcnemar, seed_stats

log = logging.getLogger(__name__)

MODEL_LABEL = {"arctic-r1-7b": "Arctic-R1-7B", "sqrl-9b": "sqrl-9b"}
RUN_FILE = re.compile(r"(.+?)__(mn|latin|en|mixed)__s(\d+)\.jsonl")
MAIN_MODELS = ["arctic-r1-7b", "sqrl-9b"]
GROUP_TITLE = {
    "A": "A. Суурь загвар, шууд (сургалтгүй, RAG-гүй)",
    "B": "B. Суурь загвар + RAG ба few-shot",
    "C": "C. Зөвхөн fine-tune",
    "D": "D. Fine-tune + RAG",
    "E": "E. Засвар, санал хураалт, татгалзалт",
    "F": "F. Монгол хэлэнд чиглэсэн санаа",
    "G": "G. Agentic систем (төлөвлөгөөнөөс гадуурх нэмэлт; docs/AGENT.md)",
    "H": "H. Ерөнхий засвар (dev-ийн алдааны ангиллаас; docs/IMPROVE_40_PLAN.md — test-ээр баталгаажуулна)",
    "I": "I. Жишээний сан өргөтгөх (dev асуулт, шинэ жишээ; docs/IMPROVE_80_PLAN.md — test-ээр баталгаажуулна)",
    "J": "J. Мэдлэгийн сангийн засвар (англи нэр томьёо, шинэ KPI; docs/IMPROVE_80_PLAN.md)",
}


class RunSet:
    """Нэг (config, model, variant)-ийн бүх seed-ийн run."""

    def __init__(self):
        self.files: dict[int, Path] = {}
        self.summaries: dict[int, dict] = {}
        self.metas: dict[int, dict] = {}
        self._records: dict[int, list[dict]] = {}

    def meta(self) -> dict:
        return self.metas[min(self.metas)] if self.metas else {}

    def records(self, seed: int) -> list[dict]:
        if seed not in self._records:
            self._records[seed] = load_records(self.files[seed])
        return self._records[seed]

    def ex(self) -> dict:
        return seed_stats([s.get("ex") for s in self.summaries.values()])

    def first(self) -> dict:
        return self.summaries[min(self.summaries)]

    def majority_records(self) -> list[dict]:
        """Олон seed бол асуулт бүрийг seed-үүдийн олонхоор зөв гэж тооцсон бичлэг (McNemar-д)."""
        seeds = sorted(self.files)
        if len(seeds) == 1:
            return self.records(seeds[0])
        votes = defaultdict(list)
        base = {}
        for s in seeds:
            for r in self.records(s):
                key = (r["question_id"], r["variant"])
                votes[key].append(bool(r.get("correct")))
                base.setdefault(key, r)
        out = []
        for key, v in votes.items():
            r = dict(base[key])
            r["correct"] = sum(v) * 2 > len(v)
            out.append(r)
        return out


def collect(cfg: Config, split: str = "dev") -> dict[tuple[str, str, str], RunSet]:
    root = resolve_path(cfg["eval"]["results_dir"]) / "runs" / split
    runs: dict[tuple[str, str, str], RunSet] = defaultdict(RunSet)
    if not root.exists():
        return runs
    for f in sorted(root.glob("*/*.jsonl")):
        m = RUN_FILE.fullmatch(f.name)
        if not m:   # tag-тай run, judge/туслах файл үндсэн хүснэгтэд орохгүй
            continue
        model, variant, seed = m.group(1), m.group(2), int(m.group(3))
        sp = f.with_suffix(".summary.json")
        if not sp.exists():
            continue
        rs = runs[(f.parent.name, model, variant)]
        rs.files[seed] = f
        rs.summaries[seed] = json.loads(sp.read_text(encoding="utf-8"))
        mp = f.with_suffix(".meta.json")
        if mp.exists():
            rs.metas[seed] = json.loads(mp.read_text(encoding="utf-8"))
    return runs


def _fmt_ex(st: dict) -> str:
    if not st or st.get("mean") is None:
        return "—"
    if st["n"] > 1:
        return f"{st['mean']:.1f} ± {st['std']:.1f}"
    return f"{st['mean']:.1f}"


def verdict(cfg: Config, runs: dict, config_id: str, baseline: str | None, model: str) -> tuple[str, str]:
    """(Төлөв, тэмдэглэл) — хүлээн авах дүрмээр."""
    cur = runs.get((config_id, model, "mn"))
    if cur is None or not cur.summaries:
        return "⏳ хийгдээгүй", ""
    baseline = cur.meta().get("baseline", baseline)   # run хийх үед тогтсон суурь (vars өөрчлөгдсөн ч зөв)
    if not baseline:
        return "суурь", ""
    base = runs.get((baseline, model, "mn"))
    if base is None or not base.summaries:
        return "—", f"суурь {baseline} хийгдээгүй"
    try:
        opts = resolve_run(config_id)
    except KeyError:
        opts = None
    if opts is not None and opts.abstain_threshold is not None:
        # E3: татгалзалт EX-ийг бууруулдаг тул TrustSQL (c = 10) ба coverage-оор дүгнэнэ
        tc, tb = (cur.first().get("trust") or {}).get("c10"), (base.first().get("trust") or {}).get("c10")
        cov = cur.first().get("coverage_pct")
        note = f"TrustSQL c10 {tb} → {tc}, coverage {cov}%, selective acc {cur.first().get('selective_accuracy')}%"
        if tc is None or tb is None:
            return "—", note
        return ("✅ хүлээн авна" if tc > tb else ("➖ ялгаагүй" if tc == tb else "❌ буурсан")), note
    mc = mcnemar(base.majority_records(), cur.majority_records())
    note = f"Δ{mc['delta_ex']:+.1f} vs {baseline} (+{mc['wins']}/−{mc['losses']}, p={mc['p_value']:.3f})"
    warn = []
    for v in ("en", "latin"):
        c, b = runs.get((config_id, model, v)), runs.get((baseline, model, v))
        if c and b and c.summaries and b.summaries:
            d = (c.ex()["mean"] or 0) - (b.ex()["mean"] or 0)
            if d < -2.0:
                warn.append(f"{v} {d:+.1f}")
    budget = cfg["eval"].get("latency_budget_p95_ms")
    p95 = cur.first().get("latency_ms", {}).get("p95")
    if budget and p95 and p95 > float(budget):
        warn.append(f"p95 {p95 / 1000:.1f}с > {float(budget) / 1000:.0f}с")
    if mc["significant"] and mc["delta_ex"] > 0:
        status = "⚠️ " + ", ".join(warn) if warn else "✅ хүлээн авна"
    elif mc["significant"] and mc["delta_ex"] < 0:
        status = "❌ буурсан"
    else:
        status = "➖ ялгаагүй"
    if warn and not status.startswith("⚠️"):
        note += "; " + ", ".join(warn)
    return status, note


def build_report(cfg: Config, runs_args: list[str] | None = None, out: str = "results/REPORT.md",
                 split: str = "dev") -> dict:
    exp = load_experiments()
    runs = collect(cfg, split)
    test_runs = collect(cfg, "test")
    bird_runs = collect(cfg, "bird_mn")      # F1: монгол BIRD (SQLite) — тусдаа split
    lines = [f"# Туршилтын тайлан ({split})", "",
             f"_Үүсгэсэн: {datetime.now().strftime('%Y-%m-%d %H:%M')}. Гол үзүүлэлт: кирилл асуултын EX (хариулах "
             f"боломжтой асуулт дээр). Олон seed бол дундаж ± std. Төлөв: McNemar p < 0.05 өсөлт, англи/латин "
             f"−2 пунктээс ихгүй, p95 төсөвтөө._", ""]
    report_json: dict = {"split": split, "groups": {}}
    by_group = defaultdict(list)
    for rid, spec in exp["runs"].items():
        by_group[(spec or {}).get("group", "?")].append(rid)
    for g in sorted(by_group):
        lines += [f"## {GROUP_TITLE.get(g, g)}", "",
                  "| ID | Тохиргоо | Arctic-R1-7B EX % | sqrl-9b EX % | Төлөв | Тэмдэглэл |",
                  "|---|---|---|---|---|---|"]
        for rid in by_group[g]:
            opts = resolve_run(rid, exp)
            src_runs = bird_runs if opts.dataset != "nbfi" else runs
            cells, statuses, notes = [], [], []
            for m in MAIN_MODELS:
                if m not in opts.models:
                    cells.append("n/a")
                    continue
                short = MODEL_LABEL[m].split("-")[0]
                rs = src_runs.get((rid, m, opts.variant))
                if (rs is None or not rs.summaries) and opts.variant != "mn" and opts.base:
                    # A2/A3: A1-ийг --variants mn,latin,en-ээр ажиллуулсан бол түүний хувилбарыг ашиглана
                    rs = src_runs.get((opts.base, m, opts.variant))
                cells.append(_fmt_ex(rs.ex()) if rs and rs.summaries else "—")
                if opts.variant != "mn":
                    base_mn = runs.get((opts.base, m, "mn")) if opts.base else None
                    if rs and rs.summaries and base_mn and base_mn.summaries:
                        st = "хэмжилт"
                        cur_ex, base_ex = rs.ex()["mean"] or 0, base_mn.ex()["mean"] or 0
                        if opts.variant == "latin":
                            note = f"галигийн зөрүү {opts.base} − {rid} = {base_ex - cur_ex:+.1f}"
                        else:
                            note = f"хэлний зөрүү {rid} − {opts.base} = {cur_ex - base_ex:+.1f}"
                    else:
                        st, note = ("⏳ хийгдээгүй" if not (rs and rs.summaries) else "хэмжилт"), ""
                else:
                    st, note = verdict(cfg, src_runs, rid, opts.baseline(), m)
                statuses.append(f"{short}: {st}")
                if note:
                    notes.append(f"{short}: {note}")
                report_json["groups"].setdefault(g, {}).setdefault(rid, {})[m] = {
                    "ex": rs.ex() if rs and rs.summaries else None, "status": st, "note": note}
            desc = (exp["runs"][rid] or {}).get("desc", "")
            lines.append(f"| {rid} | {desc} | {cells[0]} | {cells[1]} | {'<br>'.join(statuses)} | "
                         f"{'<br>'.join(notes)} |")
        lines.append("")
    lines += _script_table(runs) + _reliability_table(runs, cfg) + _cost_table(runs) + _breakdown(runs) + \
        _voting_table(runs) + _retrieval_table(runs) + _forgetting(cfg, bird_runs)
    if test_runs:
        lines += ["## Test (200 асуулт) — бүлэг бүрийн эцэст", "", "| ID | Загвар | Хувилбар | EX % | TS-EX % |",
                  "|---|---|---|---|---|"]
        for (rid, m, v), rs in sorted(test_runs.items()):
            s = rs.first()
            lines.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {v} | {_fmt_ex(rs.ex())} | {s.get('ts_ex') or '—'} |")
        lines.append("")
    path = resolve_path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    jpath = path.with_suffix(".json")
    jpath.write_text(json.dumps(report_json, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"report": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "runs_found": len(runs), "test_runs": len(test_runs)}


def _script_table(runs) -> list[str]:
    def f(x, signed=False):
        return "—" if x is None else (f"{x:+.1f}" if signed else f"{x:.1f}")

    rows = []
    for rid in sorted({rid for (rid, _, _) in runs}):
        for m in sorted({m for (r, m, _) in runs if r == rid}):
            ex = {}
            for v in ("mn", "latin", "en", "mixed"):
                rs = runs.get((rid, m, v))
                if rs and rs.summaries:
                    ex[v] = rs.ex()["mean"]
            if len(ex) < 2:
                continue
            gap_en = ex["en"] - ex["mn"] if ex.get("en") is not None and ex.get("mn") is not None else None
            gap_lat = ex["mn"] - ex["latin"] if ex.get("latin") is not None and ex.get("mn") is not None else None
            rows.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {f(ex.get('mn'))} | {f(ex.get('latin'))} | "
                        f"{f(ex.get('en'))} | {f(ex.get('mixed'))} | {f(gap_en, True)} | {f(gap_lat, True)} |")
    if not rows:
        return []
    return ["## Бичиг ба хэлний зөрүү", "", "| ID | Загвар | Кирилл | Латин | Англи | Холимог | Англи − кирилл | "
            "Кирилл − латин |", "|---|---|---|---|---|---|---|---|", *rows, ""]


def _reliability_table(runs, cfg) -> list[str]:
    rows = []
    for (rid, m, v), rs in sorted(runs.items()):
        if v != "mn":
            continue
        s = rs.first()
        t = s.get("trust") or {}
        rows.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {s.get('ts_ex') or '—'} | {s.get('executable_pct')} | "
                    f"{s.get('empty_pct')} | {s.get('coverage_pct')} | {s.get('selective_accuracy')} | "
                    f"{t.get('c1')} / {t.get('c5')} / {t.get('c10')} | {s.get('unanswerable_detected_pct')} | "
                    f"{(s.get('ambiguous') or {}).get('interp_match_pct')} |")
    if not rows:
        return []
    return ["## Найдвартай байдал (кирилл)", "", "| ID | Загвар | TS-EX % | Гүйцэтгэгдэх % | Хоосон % | Coverage % | "
            "Selective acc % | TrustSQL c=1/5/10 | Хариулах боломжгүйг илрүүлсэн % | Хоёрдмол: тайлбартай таарсан % |",
            "|---|---|---|---|---|---|---|---|---|---|", *rows, ""]


def _cost_table(runs) -> list[str]:
    rows = []
    for (rid, m, v), rs in sorted(runs.items()):
        if v != "mn":
            continue
        s = rs.first()
        lat, tok = s.get("latency_ms") or {}, s.get("tokens") or {}
        rows.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {_sec(lat.get('p50'))} | {_sec(lat.get('p95'))} | "
                    f"{tok.get('in_mean')} | {tok.get('out_mean')} | {s.get('gpu_seconds_per_q', '—')} | "
                    f"{s.get('repairs_mean')} | {s.get('agent_steps_mean')} |")
    if not rows:
        return []
    return ["## Зардал", "", "| ID | Загвар | p50 (с) | p95 (с) | Оролтын токен | Гаралтын токен | GPU-с/асуулт* | "
            "Засвар (дундаж) | DB судлах алхам |", "|---|---|---|---|---|---|---|---|---|", *rows,
            "", "_* GPU-с/асуулт = run-ийн ханын цаг / асуултын тоо (нэг GPU дээр vLLM-ийг бүрэн ашигласан үеийн "
                "үнэ). p50/p95 нь зэрэг ажиллуулсан (workers) үеийн нэг асуултын хугацаа — төсвийн шалгалтыг "
                "workers=1-ээр хэмжвэл илүү нарийн._", ""]


def _retrieval_table(runs) -> list[str]:
    rows = []
    for (rid, m, v), rs in sorted(runs.items()):
        if v != "mn":
            continue
        s = rs.first()
        if s.get("table_recall") is None and s.get("value_recall") is None and not s.get("routed_pct"):
            continue
        rows.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {s.get('table_recall', '—')} | {s.get('value_recall', '—')} | "
                    f"{s.get('routed_pct', '—')} | {s.get('ex_routed', '—')} | {s.get('ex_not_routed', '—')} |")
    if not rows:
        return []
    return ["## Retrieval ба F8 хамрах хувь (кирилл)", "",
            "| ID | Загвар | Хүснэгтийн recall % | Утгын recall % | Router-ээр шийдсэн % | EX (router) | EX (LLM) |",
            "|---|---|---|---|---|---|---|", *rows, ""]


def _forgetting(cfg, bird_runs) -> list[str]:
    """Fine-tune-ийн дараах мартагдал: BIRD-dev (англи, F1 + tag) ба Belebele khk_Cyrl."""
    rows = []
    for (rid, m, v), rs in sorted(bird_runs.items()):
        if rid.startswith("F1") and v == "en":
            rows.append(f"| BIRD-dev EX % ({rid}) | {MODEL_LABEL.get(m, m)} | {_fmt_ex(rs.ex())} |")
    gdir = resolve_path(cfg["eval"]["results_dir"]) / "general"
    if gdir.exists():
        for f in sorted(gdir.glob("belebele__*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            rows.append(f"| Belebele khk_Cyrl % ({d.get('served')}) | {d.get('model')} | {d.get('accuracy')} |")
    if not rows:
        return []
    return ["## Мартагдал ба ерөнхий чадвар (fine-tune-ийн өмнө/дараа)", "", "| Хэмжүүр | Загвар | Утга |",
            "|---|---|---|", *rows, ""]


def _sec(ms):
    return "—" if ms is None else f"{ms / 1000:.1f}"


def _breakdown(runs) -> list[str]:
    rows = []
    for (rid, m, v), rs in sorted(runs.items()):
        if v != "mn":
            continue
        s = rs.first()
        d = s.get("by_difficulty") or {}
        cv = s.get("by_contains_value") or {}
        rows.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | " + " | ".join(
            str((d.get(k) or {}).get("ex", "—")) for k in ("easy", "medium", "hard")) + " | " +
            " | ".join(str((cv.get(k) or {}).get("ex", "—")) for k in ("True", "False")) + " |")
    if not rows:
        return []
    return ["## Задаргаа (кирилл, EX %)", "", "| ID | Загвар | Хялбар | Дунд | Хүнд | Утгатай | Утгагүй |",
            "|---|---|---|---|---|---|---|", *rows, ""]


def _voting_table(runs) -> list[str]:
    rows, curves = [], []
    for (rid, m, v), rs in sorted(runs.items()):
        if v != "mn":
            continue
        s = rs.first()
        vt = s.get("voting")
        if not vt:
            continue
        rows.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {vt.get('unanimity_pct')} | {vt.get('unanimous_accuracy')} | "
                    f"{vt.get('non_unanimous_accuracy')} | {vt.get('oracle_at_k')} |")
        for p in vt.get("abstention_curve") or []:
            curves.append(f"| {rid} | {MODEL_LABEL.get(m, m)} | {p['threshold']} | {p['coverage']} | "
                          f"{p['selective_accuracy']} | {p['trust_c10']} |")
    if not rows:
        return []
    return ["## Санал хураалт (E2) ба татгалзалтын муруй (E3)", "",
            "| ID | Загвар | Санал нэгдсэн % | Нэгдсэн үеийн EX % | Нэгдээгүй үеийн EX % | Oracle@8 % |",
            "|---|---|---|---|---|---|", *rows, "",
            "| ID | Загвар | Босго | Coverage % | Selective acc % | TrustSQL c=10 |", "|---|---|---|---|---|---|",
            *curves, ""]


def derive_abstention(cfg: Config, src_config: str, dst_config: str, threshold: float, split: str = "dev",
                      action: str = "abstain", tag: str = "") -> list[dict]:
    """E3-ийг E2-ийн бичлэгээс гаргана (ижил үүсгэлт, зөвхөн босго): confidence < босго → abstained.
    action="clarify" (I бүлэг, Pipeline-тай ижил): татгалзаагүй (refused биш) бүх хариулт → тодруулга; хоёрдмол асуултад
    тодруулга зөв (GUIDE.md), хариулах боломжгүйд хариулаагүй тул зөв, хариулах боломжтойд буруу."""
    from .metrics import summarize
    root = resolve_path(cfg["eval"]["results_dir"]) / "runs" / split
    out = []
    pattern = re.compile(r"(.+?)__(mn|latin|en|mixed)__s(\d+)__" + re.escape(tag) + r"\.jsonl") if tag else RUN_FILE
    for f in sorted((root / src_config).glob("*.jsonl")):
        if not pattern.fullmatch(f.name):
            continue
        recs = load_records(f)
        for r in recs:
            r["config_id"] = dst_config
            low = r.get("confidence") is not None and r["confidence"] < threshold
            if action == "clarify" and low and r.get("status") in ("answered", "error", "abstained"):
                r["status"] = "clarify"
                r["correct"] = r.get("category") in ("unanswerable", "ambiguous")
            elif action != "clarify" and low and r.get("status") in ("answered", "error"):
                r["status"] = "abstained"
                r["correct"] = r.get("category") == "unanswerable"
            elif r.get("category") == "unanswerable":
                r["correct"] = r.get("status") in ("abstained", "refused", "clarify")
        dst = root / dst_config / f.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        summ = summarize(recs, cfg["eval"]["penalty_c"])
        summ.update({"config_id": dst_config, "derived_from": src_config, "abstain_threshold": threshold,
                     "low_conf_action": action})
        dst.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append({"file": str(dst), "ex": summ["ex"], "coverage": summ["coverage_pct"],
                    "selective_accuracy": summ["selective_accuracy"]})
    return out
