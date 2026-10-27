"""E4 (офлайн): өмнөх run-ий хувилбаруудыг (cand_sqls) дахин гүйцэтгэж шинэ сонголтын дүрмээр дахин сонгоно.

    python -m mn_text2sql reselect --from E2 --to E4 --tag h_H4 --split dev [--no-filter] [--no-refuse]
        [--no-clarify] [--clarify-threshold 0.5]

Загварыг дахин ажиллуулахгүй (ижил үүсгэлт) — зөвхөн: '%q' улирлын засвар, хоосон/0/NULL ба DB-д байхгүй утгатай
хувилбарыг санал хураалтаас хасах (vote_filter), дүрмийн татгалзал/тодруулга (rule_refuse/rule_clarify), хүснэгтгүй SQL.
Pipeline-ийн онлайн горимтой (RunOptions.vote_filter г.м.) ижил шийдвэр гаргана; оноог runner.score_answer-ээр.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import Config, resolve_path
from ..db.conn import bot_dsn
from ..db.executor import Executor
from ..knowledge.semantic import cached_model
from ..pipeline.orchestrator import Answer, Candidate, Pipeline
from ..pipeline.selection import (
    LiteralChecker,
    enum_codes,
    fix_quarter_pg,
    is_degenerate,
    is_fromless,
    rule_decision,
)
from .compare import result_hash
from .dataset import load_items
from .goldcheck import make_guard
from .metrics import load_records, summarize


def _split_file(cfg: Config, split: str) -> Path:
    return resolve_path(cfg["eval"]["dev_file"] if split == "dev" else cfg["eval"]["test_file"])


def merge_candidates(base: list[str], extra: list[str], weight: int = 1) -> list[str]:
    """E5: нэмэлт run-уудын хувилбарыг weight удаа давтаж ЭХЭНД нь тавина (санал тэнцвэл Pipeline.vote эхний
    хувилбарыг сонгодог тул нэмэлт run давуу), дараа нь суурь run-ийн хувилбарууд."""
    return [s for s in extra for _ in range(max(1, int(weight)))] + list(base)


def load_extra_sqls(root: Path, runs: list[str], model_variant_seed: str = "") -> dict[str, list[str]]:
    """{question_id: [sql, ...]} — run бүрийн `<model>__<variant>__s0.jsonl` (tag-гүй) файлаас (cand_sqls эсвэл sql)."""
    out: dict[str, list[str]] = {}
    for rid in runs:
        files = sorted((root / rid).glob(f"{model_variant_seed}*__s0.jsonl"))
        if not files:
            raise FileNotFoundError(f"E5: нэмэлт run олдсонгүй: {root / rid}")
        for r in load_records(files[0]):
            sqls = r.get("cand_sqls") or ([r["sql"]] if r.get("sql") else [])
            out.setdefault(r["question_id"], []).extend(sqls)
    return out


def reselect_run(cfg: Config, src: str, dst: str, split: str = "dev", tag: str = "", vote_filter: bool = True,
                 rule_refuse: bool = True, rule_clarify: bool = True, clarify_threshold: float | None = None,
                 extra: list[str] | None = None, extra_weight: int = 1) -> list:
    from .runner import GoldCache, init_value_maps, score_answer
    root = resolve_path(cfg["eval"]["results_dir"]) / "runs" / split
    pat = re.compile(r"(.+?)__(mn|latin|en|mixed)__s(\d+)" + (r"__" + re.escape(tag) if tag else "") + r"\.jsonl")
    items = {it.id: it for it in load_items([_split_file(cfg, split)])}
    guard = make_guard(cfg)
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                  max_rows=int(cfg["pipeline"]["max_result_rows"]) * 100, max_cost=float(cfg["db"]["max_explain_cost"]))
    gold = GoldCache(guard, ex)
    init_value_maps(ex)
    lits = LiteralChecker(ex, enums=enum_codes(cached_model(cfg["knowledge"]["semantic_model"])))
    out = []
    for f in sorted((root / src).glob("*.jsonl")):
        if not pat.fullmatch(f.name):
            continue
        m = pat.fullmatch(f.name)
        extra_sqls = load_extra_sqls(root, extra, f"{m.group(1)}__{m.group(2)}") if extra else {}
        meta_p = f.with_suffix(".meta.json")
        opts = (json.loads(meta_p.read_text(encoding="utf-8")).get("options") or {}) if meta_p.exists() else {}
        abstain_on_error = bool(opts.get("abstain_on_error"))
        recs = load_records(f)
        new = []
        for r in recs:
            it = items[r["question_id"]]
            ans = Answer(question=r.get("question") or it.question(r.get("variant", "mn")),
                         variant=r.get("variant", "mn"), run_id=dst, model=r.get("model", ""))
            ans.retrieval = r.get("retrieval") or {}
            conf, unanimous = r.get("confidence"), r.get("unanimous")
            rd = rule_decision(ans.question, rule_refuse, rule_clarify) if (rule_refuse or rule_clarify) else None
            sqls = r.get("cand_sqls") or ([r["sql"]] if r.get("sql") else [])
            if extra:
                sqls = merge_candidates(sqls, extra_sqls.get(r["question_id"], []), extra_weight)
            if r.get("status") == "refused" and not r.get("n_candidates"):
                ans.status = "refused"                       # intent_check-ийн татгалзал (үүсгэлтээс өмнө)
            elif rd is not None:                             # онлайнд дүрэм үүсгэлтээс өмнө — загварын татгалзлаас түрүүлнэ
                ans.status = "refused" if rd[0] == "refuse" else "clarify"
            elif not sqls:
                ans.status = r.get("status") or "abstained"
            else:
                cands = []
                for s in sqls:
                    c = Candidate(raw_sql=s)
                    s2 = fix_quarter_pg(s)
                    g = guard.check(s2)
                    c.sql, c.tables = (g.sql if g.ok else s2), g.tables
                    if not g.ok:
                        c.error, c.error_stage = g.error, "guard"
                    else:
                        res = ex.run(g.sql)
                        c.result = res
                        if res.ok:
                            c.ok, c.rows, c.result_hash = True, res.rowcount, result_hash(res.columns, res.rows)
                            if vote_filter:
                                c.degenerate = is_degenerate(res)
                                c.bad_literals = lits.bad_literals(c.sql)
                        else:
                            c.error, c.error_stage = res.error, "exec"
                    cands.append(c)
                chosen, conf, unanimous = Pipeline.vote(cands, vote_filter) if len(cands) > 1 else (cands[0], 1.0, True)
                ans.candidates, ans.chosen_index = cands, cands.index(chosen)
                ans.sql, ans.result = chosen.sql, chosen.result
                ans.status = "answered" if chosen.ok else ("abstained" if abstain_on_error else "error")
                if not chosen.ok and r.get("status") == "refused":
                    ans.status = "refused"                   # онлайн pipeline-тай ижил (tester L5)
                if vote_filter and ans.status == "answered" and (chosen.degenerate or chosen.bad_literals):
                    ans.status = "abstained"
                if rule_refuse and ans.status == "answered" and is_fromless(chosen.sql):
                    ans.status = "refused"
                if clarify_threshold is not None and conf < clarify_threshold and ans.status != "refused":
                    ans.status = "clarify"
            ans.confidence, ans.unanimous = conf, unanimous
            sc = score_answer(cfg, it, ans, gold)
            if not ans.candidates:
                sc["cand_correct"] = r.get("cand_correct")
            r2 = dict(r)
            r2.update({"config_id": dst, "sql": ans.sql if ans.candidates else r.get("sql"), "status": ans.status,
                       "confidence": conf, "unanimous": unanimous, **sc,
                       "reselect": {"from": src, "vote_filter": vote_filter, "rule_refuse": rule_refuse,
                                    "rule_clarify": rule_clarify, "clarify_threshold": clarify_threshold,
                                    "extra": extra, "extra_weight": extra_weight if extra else None,
                                    "rule": rd[1] if rd else None}})
            if ans.candidates:
                ch = ans.candidates[ans.chosen_index]
                r2.update({"result_hash": ch.result_hash, "row_count": ch.result.rowcount if ch.ok else None,
                           "cand_hashes": [c.result_hash for c in ans.candidates] if len(ans.candidates) > 1 else None,
                           "cand_sqls": [c.sql for c in ans.candidates] if len(ans.candidates) > 1 else None,
                           "error_stage": ch.error_stage, "error": (ch.error or "")[:500] or None})
            new.append(r2)
        dst_f = root / dst / f.name
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        with open(dst_f, "w", encoding="utf-8", newline="\n") as fh:
            for r2 in new:
                fh.write(json.dumps(r2, ensure_ascii=False, default=str) + "\n")
        summ = summarize(new, cfg["eval"]["penalty_c"])
        summ.update({"config_id": dst, "derived_from": src, "vote_filter": vote_filter, "rule_refuse": rule_refuse,
                     "rule_clarify": rule_clarify, "clarify_threshold": clarify_threshold, "extra": extra,
                     "extra_weight": extra_weight if extra else None})
        dst_f.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
        if meta_p.exists():
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            m.update({"config_id": dst, "derived_from": src})
            dst_f.with_suffix(".meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        cats = {}
        for r2 in new:
            k = r2["category"]
            cats.setdefault(k, [0, 0])
            cats[k][0] += bool(r2["correct"])
            cats[k][1] += 1
        out.append({"file": str(dst_f), "correct": sum(bool(x["correct"]) for x in new), "n": len(new),
                    "by_category": {k: f"{a}/{b}" for k, (a, b) in sorted(cats.items())}})
    ex.close()
    return out
