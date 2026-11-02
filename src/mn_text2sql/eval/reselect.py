"""E4 (офлайн): өмнөх run-ий хувилбаруудыг (cand_sqls) дахин гүйцэтгэж шинэ сонголтын дүрмээр дахин сонгоно.

    python -m mn_text2sql reselect --from E2 --to E4 --tag h_H4 --split dev [--no-filter] [--no-refuse]
        [--no-clarify] [--clarify-threshold 0.5] [--abstain-v2] [--trunc-date] [--refix afx|none]

Загварыг дахин ажиллуулахгүй (ижил үүсгэлт) — зөвхөн: '%q' улирлын засвар, хоосон/0/NULL ба DB-д байхгүй утгатай
хувилбарыг санал хураалтаас хасах (vote_filter), дүрмийн татгалзал/тодруулга (rule_refuse/rule_clarify), хүснэгтгүй SQL;
--trunc-date үед postproc R7 (гаралтын DATE_TRUNC → DATE; RunOptions.trunc_date-тэй ижил, guard-аас өмнө).
--refix afx үед хувилбарыг хадгалсан SQL-ээс биш raw_output-оос M1afx-ийн 4 засвартай дахин гаргана (make_refixer);
--refix none — засваргүй тулгалт (хадгалсан SQL-ийг яг давтах ёстой).
Pipeline-ийн онлайн горимтой (RunOptions.vote_filter г.м.) ижил шийдвэр гаргана; оноог runner.score_answer-ээр.
"""
from __future__ import annotations

import json
import logging
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
    rule_decision_for,
)
from .compare import result_hash
from .dataset import load_items
from .goldcheck import make_guard
from .metrics import load_records, summarize

log = logging.getLogger(__name__)


def _split_file(cfg: Config, split: str) -> Path:
    if split not in ("dev", "test") and cfg["eval"].get(f"{split}_file"):   # 2026-11-02: "fresh" (evalset/fresh.jsonl)
        return resolve_path(cfg["eval"][f"{split}_file"])
    return resolve_path(cfg["eval"]["dev_file"] if split == "dev" else cfg["eval"]["test_file"])


def merge_candidates(base: list[str], extra: list[str], weight: int = 1) -> list[str]:
    """E5: нэмэлт run-уудын хувилбарыг weight удаа давтаж ЭХЭНД нь тавина (санал тэнцвэл Pipeline.vote эхний
    хувилбарыг сонгодог тул нэмэлт run давуу), дараа нь суурь run-ийн хувилбарууд."""
    return [s for s in extra for _ in range(max(1, int(weight)))] + list(base)


def _extra_files(root: Path, runs: list[str], model_variant_seed: str = "") -> list[Path]:
    """Нэмэлт run бүрийн `<model>__<variant>__s0.jsonl` (tag-гүй) файл."""
    out = []
    for rid in runs:
        files = sorted((root / rid).glob(f"{model_variant_seed}*__s0.jsonl"))
        if not files:
            raise FileNotFoundError(f"E5: нэмэлт run олдсонгүй: {root / rid}")
        out.append(files[0])
    return out


def load_extra_sqls(root: Path, runs: list[str], model_variant_seed: str = "",
                    refixer_for=None) -> dict[str, list[str]]:
    """{question_id: [sql, ...]} — run бүрийн `<model>__<variant>__s0.jsonl` (tag-гүй) файлаас (cand_sqls эсвэл sql;
    refixer_for(файл) өгвөл raw_output-оос дахин гаргасан SQL)."""
    out: dict[str, list[str]] = {}
    for f in _extra_files(root, runs, model_variant_seed):
        fn = refixer_for(f) if refixer_for else None
        for r in load_records(f):
            sqls = fn(r) if fn else (r.get("cand_sqls") or ([r["sql"]] if r.get("sql") else []))
            out.setdefault(r["question_id"], []).extend(sqls)
    return out


COST_FIELDS = ("latency_ms", "gen_ms", "prompt_tokens", "completion_tokens")
REFIX_FIXES = ("guard_scope", "transpile_fix", "extract_fallback", "literal_fix")


class _ColsOnly:
    """refix: Pipeline-д зөвхөн баганын төрөл/жишээ (transpile_fix-ийн F1–F5) — хайлтын индекс, embedding ачаалахгүй."""

    def __init__(self, cfg: Config):
        from ..retrieval.build import index_dir
        from ..retrieval.indexes import load_json
        p = index_dir(cfg) / "column_examples.json"
        self.col_examples = load_json(p) if p.exists() else {}


def make_refixer(cfg: Config, run_file: Path, ex: Executor, mode: str, model_name: str = "arctic-r1-7b"):
    """Хадгалсан raw_output-оос SQL-ийг pipeline-ийн замаар дахин гаргана (extract → SQLite→PG → postproc → guard →
    exec; extract_fallback): mode "afx" — M1afx-ийн 4 засвартай (guard_scope, transpile_fix, extract_fallback,
    literal_fix), "none" — засваргүй (тулгалт). Prompt ба үүсгэлт өөрчлөгдөхгүй тул онлайн M1afx-ийн шийдвэртэй ижил,
    vLLM-ийн шуугиангүй. runner raw_output-ийн СҮҮЛИЙН 4000 тэмдэгтийг л хадгалдаг тул урт (давталттай) гаралтын эхлэл
    тасарч болно: afx-д засваргүй дахин гаргалт хадгалсан SQL-ийг яг давтсан бичлэгт л засвар хэрэглэж, бусдад хадгалсан
    SQL-ийг үлдээнэ (dev-ийн 5 гишүүнд тасраагүй бүх бичлэг яг давтагдсан). Тохиргоо нь run-ий .meta.json (алга бол
    experiments.yaml). → (record → [sql], хувилбарын guard); refix.stats — kept/trunc/derived/changed тоо."""
    from collections import Counter
    from dataclasses import asdict, fields, replace

    from ..llm.client import MockLLM
    from ..pipeline.options import RunOptions, resolve_run
    from ..security.sql_guard import extended_block, extract_sql
    if mode not in ("afx", "none"):
        raise ValueError(f"refix: mode afx|none, {mode!r} биш")
    meta_p = run_file.with_suffix(".meta.json")
    raw = ((json.loads(meta_p.read_text(encoding="utf-8")).get("options") or {}) if meta_p.exists()
           else asdict(resolve_run(run_file.parent.name)))
    names = {f.name for f in fields(RunOptions)}
    base = RunOptions(**{k: v for k, v in raw.items() if k in names and v is not None})
    cols = _ColsOnly(cfg)
    pipes = {m: Pipeline(cfg, replace(base, **dict.fromkeys(REFIX_FIXES, m == "afx")), model_name,
                         llm=MockLLM(lambda _m: ""), retriever=cols, executor=ex) for m in {"none", mode}}

    def derive(pipe: Pipeline, r: dict, text: str) -> list[str]:
        hits = [tuple(v) for v in ((r.get("retrieval") or {}).get("values") or [])] or None
        cand = pipe._mark_refusal(pipe.process_sql(extract_sql(text), hits), text)
        if pipe.opts.extract_fallback and not cand.ok and cand.error_stage in ("guard", "exec"):
            alt = extended_block(text, cand.raw_sql)      # orchestrator._single_turn-тэй ижил
            if alt:
                alt_cand = pipe.process_sql(alt, hits)
                if alt_cand.ok:
                    cand = alt_cand
        return [cand.sql] if cand.sql else []

    def refix(r: dict) -> list[str]:
        stored = r.get("cand_sqls") or ([r["sql"]] if r.get("sql") else [])
        text = r.get("raw_output")
        if not text or r.get("cand_sqls") or (r.get("n_candidates") or 0) > 1:
            # үүсгэлтээс өмнөх татгалзал, олон хувилбартай run (E2: raw_output нь нэг л хувилбарынх) — хадгалсан хэвээр
            refix.stats["kept"] += 1
            return stored
        same = derive(pipes["none"], r, text)
        if mode == "none":
            refix.stats["derived"] += 1
            refix.stats["changed"] += same != stored
            return same
        if same != stored:          # тасархай raw_output — засваргүйгээр давтагдахгүй тул хадгалсан SQL
            refix.stats["trunc"] += 1
            return stored
        fixed = derive(pipes["afx"], r, text)
        refix.stats["derived"] += 1
        refix.stats["changed"] += fixed != stored
        return fixed
    refix.stats = Counter()
    return refix, pipes[mode].guard


def load_extra_costs(root: Path, runs: list[str], model_variant_seed: str = "") -> dict[str, dict]:
    """L11: {question_id: {COST_FIELDS: нэмэлт run-уудын нийлбэр}} — аль нэг run-ий бичлэгт талбар None бол None."""
    out: dict[str, dict] = {}
    for f in _extra_files(root, runs, model_variant_seed):
        for r in load_records(f):
            acc = out.setdefault(r["question_id"], dict.fromkeys(COST_FIELDS, 0))
            for k in COST_FIELDS:
                v = r.get(k)
                acc[k] = None if acc[k] is None or v is None else acc[k] + v
    return out


def ensemble_cost(base: dict, extra: dict | None) -> dict:
    """L11: чуулгын (E5h, M1ens) бичлэгийн зардал = суурь run + нэмэлт run-уудын нийлбэр (үүсгэлт тус тусдаа хийгдсэн;
    latency нь дараалан ажиллуулсан хугацаа). Суурийнхыг дангаар нь үлдээвэл зардлыг ~(1 + нэмэлт run-ийн тоо) дахин
    дутуу харуулна. Аль нэг хэсэг нь мэдэгдэхгүй (None) бол None; нэмэлт run-д асуулт байхгүй бол суурийнх хэвээр."""
    if extra is None:
        return {k: base.get(k) for k in COST_FIELDS}
    out = {}
    for k in COST_FIELDS:
        a, b = base.get(k), extra.get(k)
        out[k] = None if a is None or b is None else (round(a + b, 1) if isinstance(a + b, float) else a + b)
    return out


def reselect_run(cfg: Config, src: str, dst: str, split: str = "dev", tag: str = "", vote_filter: bool = True,
                 rule_refuse: bool = True, rule_clarify: bool = True, clarify_threshold: float | None = None,
                 extra: list[str] | None = None, extra_weight: int = 1, pick_model: str | None = None,
                 pick_max_conf: float = 1.0, abstain_v2: bool = False, pick_diff: bool = False,
                 trunc_date: bool = False, refix: str | None = None, rules: str = "v1",
                 lint: bool = False, kpi: bool = False) -> list:
    from .runner import GoldCache, init_value_maps, score_answer
    if kpi:   # 2026-11-02: KPI/схемийн детерминист засвар (pipeline/kpi_fix.py) — хадгалсан хувилбарт, guard-аас өмнө
        from ..pipeline.kpi_fix import kpi_fix
        kpi_model = cached_model(cfg["knowledge"]["semantic_model"])
    if lint:   # 2026-11-02: санал хураалтын өмнөх SQL lint — semantic_model-ийн FK-ээс хүүхэд→эцэг
        from ..pipeline.sql_lint import child_map, label_codes, lint_sql
        lint_children = child_map(cached_model(cfg["knowledge"]["semantic_model"]))
        lint_labels = label_codes(cached_model(cfg["knowledge"]["semantic_model"]))
    if trunc_date:   # R7 (2026-11-01 V27 dev): pipeline-ийн trunc_date-тэй ижил — хадгалсан хувилбарт GPU-гүй хэрэглэнэ
        from ..pipeline.postproc import cast_date_trunc
    if abstain_v2:   # v2: асуултын шинж + санал хураалтаас LR-ээр татгалзах/тодруулах (pipeline/abstain.py)
        from ..pipeline.abstain import abstain_decision
    pick_llm = None
    if pick_model:   # P: санал зөрсөн үед Arctic-аар хос харьцуулах (eval/pick.py)
        from ..llm.client import make_llm
        pick_llm = make_llm(dict(cfg["llm"], model=pick_model))
    root = resolve_path(cfg["eval"]["results_dir"]) / "runs" / split
    pat = re.compile(r"(.+?)__(mn|latin|en|mixed)__s(\d+)" + (r"__" + re.escape(tag) if tag else "") + r"\.jsonl")
    items = {it.id: it for it in load_items([_split_file(cfg, split)])}
    guard = make_guard(cfg)
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                  max_rows=int(cfg["pipeline"]["max_result_rows"]) * 100, max_cost=float(cfg["db"]["max_explain_cost"]))
    gold = GoldCache(guard, ex)
    init_value_maps(ex)
    lits = LiteralChecker(ex, enums=enum_codes(cached_model(cfg["knowledge"]["semantic_model"])))
    cguard, refixers = guard, {}
    if refix:   # M1afx-ийн засварыг хадгалсан raw_output-д (make_refixer); gold-ийн guard өөрчлөгдөхгүй
        def refixer_for(path: Path):
            if path not in refixers:
                refixers[path] = make_refixer(cfg, path, ex, refix)
            return refixers[path][0]
        if refix == "afx":
            cguard = make_guard(cfg, scoped_columns=True)
    out = []
    for f in sorted((root / src).glob("*.jsonl")):
        if not pat.fullmatch(f.name):
            continue
        m = pat.fullmatch(f.name)
        extra_sqls = (load_extra_sqls(root, extra, f"{m.group(1)}__{m.group(2)}", refixer_for if refix else None)
                      if extra else {})
        base_fix = refixer_for(f) if refix else None
        n_changed = 0
        extra_costs = load_extra_costs(root, extra, f"{m.group(1)}__{m.group(2)}") if extra else {}
        meta_p = f.with_suffix(".meta.json")
        opts = (json.loads(meta_p.read_text(encoding="utf-8")).get("options") or {}) if meta_p.exists() else {}
        opts_source = "meta" if meta_p.exists() else None   # L11: derived summary/meta-д бичнэ
        if not meta_p.exists():   # input-ээс зөвхөн .jsonl сэргээгдсэн бол abstain_on_error/translation_cache-ийг config-оос
            try:
                from dataclasses import asdict

                from ..pipeline.options import resolve_run
                opts, opts_source = asdict(resolve_run(src)), "experiments.yaml"
                log.warning("reselect: %s алга — %s-ийн тохиргоог experiments.yaml-аас авав", meta_p.name, src)
            except Exception as e:  # noqa: BLE001 — config-д байхгүй run (ж: flow) бол өмнөх зан төлөв
                log.warning("reselect: %s алга, %s config-д олдсонгүй (%s) — abstain_on_error=False", meta_p.name, src, e)
        abstain_on_error = bool(opts.get("abstain_on_error"))
        if pick_llm is not None:
            from .pick import load_translations, pick_pair, top_two
            tr = load_translations(opts.get("translation_cache"))
        recs = load_records(f)
        new = []
        for r in recs:
            it = items[r["question_id"]]
            ans = Answer(question=r.get("question") or it.question(r.get("variant", "mn")),
                         variant=r.get("variant", "mn"), run_id=dst, model=r.get("model", ""))
            ans.retrieval = r.get("retrieval") or {}
            conf, unanimous = r.get("confidence"), r.get("unanimous")
            # rules v2: хадгалсан question_used (загварт өгсөн англи орчуулга) — онлайн pipeline-тэй ижил эх
            rd = (rule_decision_for(rules, ans.question, r.get("question_used"), rule_refuse, rule_clarify)
                  if (rule_refuse or rule_clarify) else None)
            picked = None
            pick_votes: list[str] = []   # pick_pair-ийн хоёр дарааллын хариу (dev-ийн pick-ийг оношлох)
            sqls = r.get("cand_sqls") or ([r["sql"]] if r.get("sql") else [])
            if base_fix is not None:
                fixed = base_fix(r)
                n_changed += fixed != sqls
                sqls = fixed
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
                    if trunc_date:
                        s2 = cast_date_trunc(s2)[0]
                    if kpi:
                        s2 = kpi_fix(ans.question, s2, kpi_model)[0]
                    g = cguard.check(s2)
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
                if lint and len(cands) > 1:   # vote_lint (sql_lint.py): fan/chasm ба KPI-ийн шалгалт
                    for c in cands:
                        c.lint_flags = sorted(lint_sql(ans.question, c.sql, lint_children, lint_labels)) or None
                chosen, conf, unanimous = (Pipeline.vote(cands, vote_filter, lint) if len(cands) > 1
                                           else (cands[0], 1.0, True))
                if pick_llm is not None and chosen.ok and not unanimous and conf < pick_max_conf:
                    pair = top_two(cands, chosen)
                    if pair:
                        q_en = (tr.get(f"{r['question_id']}:{r.get('variant', 'mn')}") or r.get("question_used")
                                or ans.question)   # кэш алга бол run-д ашигласан (англи) асуулт — монголоор биш
                        pick_votes = []
                        chosen, picked = pick_pair(pick_llm, q_en, *pair, diff=pick_diff,   # T2: --pick-diff
                                                   trace=pick_votes)
                ans.candidates, ans.chosen_index = cands, cands.index(chosen)
                ans.sql, ans.result = chosen.sql, chosen.result
                ans.status = "answered" if chosen.ok else ("abstained" if abstain_on_error else "error")
                if not chosen.ok and r.get("status") == "refused":
                    ans.status = "refused"                   # онлайн pipeline-тай ижил (tester L5)
                if vote_filter and ans.status == "answered" and (chosen.degenerate or chosen.bad_literals):
                    ans.status = "abstained"
                if rule_refuse and ans.status == "answered" and is_fromless(chosen.sql):
                    ans.status = "refused"
                if abstain_v2 and ans.status not in ("refused", "clarify"):
                    v2 = abstain_decision(ans.question, conf, [c.result_hash for c in cands] if len(cands) > 1 else None,
                                          chosen.result.rowcount if chosen.ok else None, len(cands), chosen.sql)
                    if v2 is not None:
                        ans.status, rd = ("refused" if v2[0] == "refuse" else "clarify"), v2
                if clarify_threshold is not None and conf < clarify_threshold and ans.status != "refused":
                    ans.status = "clarify"
            ans.confidence, ans.unanimous = conf, unanimous
            sc = score_answer(cfg, it, ans, gold)
            if not ans.candidates:
                sc["cand_correct"] = r.get("cand_correct")
            r2 = dict(r)
            if extra:   # L11: суурь run-ий latency/токеныг дангаар нь үлдээхгүй — нэмэлт run-уудынхыг нэмнэ
                r2.update(ensemble_cost(r, extra_costs.get(r["question_id"])))
            r2.update({"config_id": dst, "sql": ans.sql if ans.candidates else r.get("sql"), "status": ans.status,
                       "confidence": conf, "unanimous": unanimous, **sc,
                       "reselect": {"from": src, "vote_filter": vote_filter, "rule_refuse": rule_refuse,
                                    "rule_clarify": rule_clarify, "clarify_threshold": clarify_threshold,
                                    "extra": extra, "extra_weight": extra_weight if extra else None,
                                    "rule": rd[1] if rd else None, "pick": picked, "pick_votes": pick_votes[0] if pick_votes else None,
                                    "abstain_v2": abstain_v2,
                                    **({"pick_diff": True} if pick_diff else {}),
                                    **({"trunc_date": True} if trunc_date else {}),
                                    **({"rules": rules} if rules != "v1" else {}),
                                    **({"lint": True} if lint else {}),
                                    **({"kpi_fix": True} if kpi else {}),
                                    **({"refix": refix} if refix else {})}})
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
                     "extra_weight": extra_weight if extra else None, "abstain_v2": abstain_v2,
                     "options_source": opts_source, **({"cost_summed_from": [src, *extra]} if extra else {}),
                     **({"trunc_date": True} if trunc_date else {}),
                     **({"rules": rules} if rules != "v1" else {}),
                     **({"lint": True} if lint else {}),
                     **({"kpi_fix": True} if kpi else {}),
                     **({"refix": refix, "refix_changed": n_changed, "refix_stats": dict(base_fix.stats)}
                        if refix else {})})
        dst_f.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
        if meta_p.exists():
            m = json.loads(meta_p.read_text(encoding="utf-8"))
            m.update({"config_id": dst, "derived_from": src, "options_source": opts_source})
            dst_f.with_suffix(".meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        cats = {}
        for r2 in new:
            k = r2["category"]
            cats.setdefault(k, [0, 0])
            cats[k][0] += bool(r2["correct"])
            cats[k][1] += 1
        out.append({"file": str(dst_f), "correct": sum(bool(x["correct"]) for x in new), "n": len(new),
                    **({"refix_changed": n_changed, "refix_stats": dict(base_fix.stats)} if refix else {}),
                    "by_category": {k: f"{a}/{b}" for k, (a, b) in sorted(cats.items())}})
    ex.close()
    return out
