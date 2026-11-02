"""Туршилтын тохиргоо (config/experiments.yaml) → RunOptions. base-ийн гинж ба ${vars} орлуулалт."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, fields

import yaml

from ..config import resolve_path


@dataclass
class RunOptions:
    run_id: str = "custom"
    desc: str = ""
    group: str = ""
    variant: str = "mn"
    translate: bool = False
    dialect: str = "sqlite_native"
    describe: bool = False
    knowledge_notes: bool = False
    values: bool = False
    value_mode: str = "basic"
    glossary: bool = False
    fewshot_k: int = 0
    schema_linking: bool = False
    top_tables: int = 6
    agent_max_steps: int = 5
    repair_rounds: int = 0
    repair_on_empty: bool = False
    repair_workers: int = 1             # засварын гинжийг зэрэг ажиллуулах thread-ийн тоо (1 = дараалсан, анхдагч; гаралт ижил)
    repair_strict: bool = False         # C4: засварын LLM алдаа → infra (llm, хадгалахгүй); засварын дараах татгалзлыг үлдээх; bool_cols алдааг кэшлэхгүй; токен бүрэн тоолох
    candidates: int = 1
    sampling: bool = False
    seeds: list[int] = field(default_factory=lambda: [0])
    abstain_threshold: float | None = None
    low_conf_action: str = "abstain"     # I: санал нийлэлт < abstain_threshold үед "abstain" эсвэл "clarify" (тодруулга)
    vote_filter: bool = False           # E4: хоосон/0/NULL ба DB-д байхгүй утгатай хувилбарыг санал хураалтаас хасах
    rule_refuse: bool = False           # E4: AUTHORING-ийн хариулах боломжгүй ангиллын дүрэм + хүснэгтгүй SQL → татгалзах
    rule_clarify: bool = False          # E4: бүдэг хугацаа/хэмжүүрийн дүрэм → тодруулга
    rules_version: str = "v1"           # 2026-11-02: "v2" = AUTHORING-ийн 5 ангилал, үйл үгийн хэлбэр + англи орчуулга (selection.rule_decision_v2)
    kpi_fix: bool = False               # 2026-11-02: KPI/схемийн детерминист засвар (NPL-ийг үлдэгдлээр, POSTED, ID багана, идэвхтэй багц; pipeline/kpi_fix.py)
    vote_lint: bool = False             # 2026-11-02: санал хураалтаас fan/chasm ба KPI-ийн lint-д унасан хувилбарыг хасах (pipeline/sql_lint.py)
    value_list_max: int = 0             # 2026-11-02: >0 бол утгын индексийн ≤N утгатай текст баганын БҮХ утгыг DDL-д + «утгыг орчуулахгүй» заавар
    translation_cache: str | None = None  # I: build/translations/<нэр>.jsonl (анхдагч: models.yaml translator; «qwen3.5-9b.fix» = masking засвар)
    glossary_en: bool = False           # J: en_prompt үед нэр томьёог англиар (definition_en) + «Use exactly: SQL»
    glossary_file: str | None = None    # J: өөр нэр томьёоны файл (knowledge/glossary_plus.yaml — 7 шинэ нэр томьёо); анхдагч knowledge.glossary
    glossary_k: int = 4                 # prompt-д орох нэр томьёоны тоо (хайлтын top-k)
    knowledge_select: bool = False      # S: хуваарилагч — select_pool нэр томьёог хайгаад SQL загвар өөрөө хэрэгтэйг сонгоно
    select_pool: int = 12               # S: сонголтын нэр дэвшигч (BM25 recall: k=4 79%, k=12 96%; dev glossary_terms)
    glossary_select_cache: str | None = None  # R: сургасан reranker-ийн урьдчилсан сонголт build/rerank/<tag>.json (scripts/rerank/rr.py)
    select_parse: str = "legacy"  # C8: хуваарилагчийн хариу уншилт — "strict" = <answer>/сүүлийн мөрийн бие даасан дугаар (PAR30, 3.5 үгүй), "none"-ийг хүндэтгэнэ; анхдагч legacy = бүх бүхэл тоо (M1ks)
    adaptive_stop: str | None = None  # эрт зогсох self-consistency: "beta:0.8:w4" | "g+beta:0.8:w2" | "agree:2" (eval/adaptive.py; анхдагч унтраалттай)
    pick_pairs: bool = False  # P: санал зөрвөл шилдэг 2 бүлгийг Arctic-аар хос харьцуулах (офлайн: reselect --pick, eval/pick.py)
    abstain_v2: bool = False  # Abstain v2: асуултын шинж + LR-ээр татгалзах/тодруулах (офлайн: reselect --abstain-v2, pipeline/abstain.py)
    fk_bridge: bool = False   # FK-гүүр: салангид linked хүснэгтийн завсрын + 1 алхмын FK хүснэгт (≤2; retrieval/fk_bridge.py)
    gen_style: str = "default"  # чуулгын 2-р үүсгэгч: "cte" | "decompose" — CTE/дэд алхмын заавар (llm/prompts.py GEN_STYLE_HINTS)
    table_hints: bool = False  # M1th: төстэй хүснэгтийн хосыг ялгах ≤5 англи мөр evidence-д (knowledge/table_hints.py)
    implied_preds: bool = False  # M1ip (T1): хүснэгтийн далд нөхцөл (POSTED, is_active_portfolio ...) ≤4 англи мөр evidence-д
    pick_diff: bool = False   # T2: pick-ийн хос харьцуулалтыг зөвхөн ялгаатай нөхцөлөөр асуух (офлайн: reselect --pick --pick-diff)
    fewshot_rerank: str | None = None  # M1fs (R5 З1): "table_jaccard" = жишээний pool 20-ийг SQL-ийн хүснэгт ↔ linked[:3]-ийн Jaccard-аар дахин эрэмбэлэх (retrieval/fewshot_rerank.py)
    rescue_truncated: bool = False  # R7 #5: max_tokens-д тасарсан SQL-гүй гаралтыг «</think><answer>```sql»-ээр нэг удаа үргэлжлүүлэх (budget forcing)
    # M1vf (2026-10-30 шалгалтын F1/F3/F5/L6; бүгд анхдагчаар хуучин зан төлөв — prompt, fingerprint байт-байтаар ижил):
    value_match: str = "legacy"   # F1: "strict" = кирилл түлхүүрийг зөвхөн кириллээр + дагавар/угтвар (сул латин түлхүүрийн «байсан → Баасан» үгүй); linking-ийн өсгөлт зөвхөн баттай тааралтад
    mask_mode: str = "legacy"     # F3: "values" = few-shot хайлтад зөвхөн утга/оноосон нэр/regex-ийг маскжуулна (эхний үгийг биш); embedding-ийн асуулт ч ижил маскаар
    enum_codes: str = "first9"    # F5: "all" = DDL-ийн тайлбарт enum-ийн бүх код (gl_entries.account_code 20; анхдагч YAML-ийн эхний 9)
    translit_vocab: str = "pool"  # L6: "no_eval" = галиг хувиргагчийн үгийн сангаас жишээний санд орсон eval (dev) асуултыг хасна (зөвхөн латин/холимог асуултад нөлөөтэй)
    # 2026-11-01 алдааны шинжилгээ (dev-ийн guard/exec/хоосон үр дүнгээс; бүгд анхдагч off = хуучин гаралт, fingerprint ижил):
    guard_scope: bool = False      # guard-ийн баганын шалгалтад alias-ийг SELECT/CTE-ийн хүрээгээр тайлна (CTE дотор `loan_payments AS lp`, гадна `loan_portfolio AS lp` — хүчинтэй SQL-ийг буруу татгалздаг байв: dev X013/X032/X044)
    transpile_fix: bool = False    # SQLite→PG: WHERE/HAVING-ийн alias, strftime/substr-ийн текст арифметикт, substr(огноо), boolean SUM/AVG/MAX, strftime-ийн modifier (pipeline/dialect.py F1–F5; зөвхөн PG-д алдаа өгөх SQL-ийг өөрчилнө)
    extract_fallback: bool = False  # алдаатай (guard/exec) хувилбарын сүүлийн ```sql блок нь өмнөх блокийн тасарсан хуулбар бол бүтэн блокийг ажиллуулна (sql_guard.extended_block; dev C042/P047)
    literal_fix: bool = False      # postproc R6: DB-д байхгүй текст утга («Darkhan», «Term Deposit») → тухайн асуултын утгын хайлтын ижил баганын хадгалагдсан утга («Дархан салбар»); postproc=true үед
    # V27 dev-ийн алдааны шинжилгээ (2026-11-01; анхдагч off = хуучин гаралт, fingerprint ижил):
    trunc_date: bool = False       # postproc R7: гаралтын DATE_TRUNC('month'|…, x) → CAST(… AS DATE) (date_trunc(DATE) = timestamptz → UTC сесст UB-ийн 08:00, gold ::date-тэй таарахгүй; dev-ийн 115 ийм хариулт, зөв 0; K036/K040/P040); postproc=true үед
    pivot: bool = False
    intent: bool = False
    semantic_router: bool = False
    search_values_tool: bool = False
    agent: bool = False                 # G: agentic давталт (tool-ийн шалгалт → critic → засвар; pipeline/agent.py)
    agent_rounds: int = 2               # засварын дээд тоо
    agent_plan: bool = False            # G3: SQL-ээс өмнө англи төлөвлөгөө
    agent_critic: bool = True           # LLM critic (false = зөвхөн детерминист tool)
    agent_literal_check: bool = True    # WHERE-ийн текст утга баганад хадгалагдсан эсэх
    agent_llm: str | None = None        # planner/critic-д өөр загвар (models.yaml; T2S_AGENT_BASE_URL)
    adapter: str | None = None
    index_dir: str | None = None
    dataset: str = "nbfi"
    models: list[str] = field(default_factory=list)
    intent_check: bool = False          # production: хариулах боломжгүй/хоёрдмол асуултыг тодруулах
    intent_refuse_only: bool = False    # H: intent_check-ээс зөвхөн "refuse" (PII, бичих хүсэлт) — тодруулга асуухгүй
    intent_rules: str = "legacy"        # H1 (2026-10-31): "v2" = мэдлэгийн сангийн шошгыг маскжуулж, бичих үйл үгийн хүсэлтийн хэлбэр, англид тодорхой танигч («Бүтэц өөрчилсөн зээл», «registered» татгалзахгүй; pipeline/intent_check.py)
    abstain_on_error: bool = False      # H: эцсийн SQL алдаатай бол хариулахаас татгалзах (TrustSQL)
    postproc: bool = False              # H: схем/хадгалагдсан утгаас гаргасан SQL засвар (pipeline/postproc.py)
    translate_mode: str = "replace"     # A4: "replace" = англи орчуулгаар солих (retrieval ч англиар); "bilingual" = монгол + англи мөр; "en_prompt" = загварт англи, retrieval монголоор
    translate_check: str = "off"        # R1.2 (M1tc): "bilingual" = translation_check тэмдэглэсэн асуултад л монгол + англи мөр (pipeline/translate_check.py)
    base: str | None = None             # өвлөсөн тохиргоо (тайланд харьцуулах суурь)
    compare_to: str | None = None       # base-ээс өөр суурьтай харьцуулах бол

    def needs_retrieval(self) -> bool:
        return any([self.values, self.glossary, self.fewshot_k > 0, self.schema_linking, self.search_values_tool])

    def validate(self) -> RunOptions:
        """M3: тогтсон утгатай (ENUM_OPTIONS) ба bool талбарын утгыг шалгана — буруу бол ValueError (resolve_run дуудна)."""
        bad = [f"{k}={getattr(self, k)!r} (боломжит: {' | '.join(map(str, allowed))})"
               for k, allowed in ENUM_OPTIONS.items() if getattr(self, k) not in allowed]
        bad += [f"{f.name}={getattr(self, f.name)!r} (true/false байх ёстой)" for f in fields(self)
                if isinstance(f.default, bool) and not isinstance(getattr(self, f.name), bool)]
        if bad:
            raise ValueError(f"{self.run_id}: тохиргооны буруу утга — " + "; ".join(bad))
        return self

    def baseline(self) -> str | None:
        return self.compare_to or self.base

    def fingerprint(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        # repair_workers — зөвхөн гүйцэтгэлийн хурд (үр дүнд нөлөөгүй) тул hash-д оруулахгүй
        for k in ("run_id", "desc", "group", "models", "base", "compare_to", "repair_workers"):
            d.pop(k, None)
        # Хожим нэмэгдсэн талбар анхдагч утгатай бол hash-д оруулахгүй (өмнөх run-уудаас үргэлжлүүлэх боломж)
        for k, default in _LATE_DEFAULTS.items():
            if d.get(k) == default:
                d.pop(k, None)
        return d


_LATE_DEFAULTS = {"intent_refuse_only": False, "abstain_on_error": False, "postproc": False,
                  "translate_mode": "replace", "low_conf_action": "abstain", "vote_filter": False,
                  "rule_refuse": False, "rule_clarify": False, "translation_cache": None,
                  "glossary_en": False, "glossary_file": None, "glossary_k": 4, "knowledge_select": False,
                  "select_pool": 12, "glossary_select_cache": None, "pick_pairs": False,
                  "fk_bridge": False, "abstain_v2": False, "gen_style": "default",
                  "table_hints": False, "adaptive_stop": None, "repair_workers": 1,
                  "implied_preds": False, "pick_diff": False, "rescue_truncated": False,
                  "fewshot_rerank": None,
                  "translate_check": "off",
                  "value_match": "legacy", "mask_mode": "legacy", "enum_codes": "first9", "translit_vocab": "pool",
                  "select_parse": "legacy", "repair_strict": False, "intent_rules": "legacy",
                  "guard_scope": False, "transpile_fix": False, "extract_fallback": False, "literal_fix": False,
                  "trunc_date": False, "rules_version": "v1", "value_list_max": 0, "vote_lint": False, "kpi_fix": False}

# M3 (2026-10-31): тогтсон утгатай талбар ба зөвшөөрөгдөх утга. Хэрэглэгч модулийн жагсаалттай ижил (prompts.ENUM_CODE_MODES,
# retriever.TRANSLIT_VOCABS, indexes.MASK_MODES/VALUE_MATCH_MODES, fewshot_rerank.MODES, intent_check.INTENT_RULES,
# GEN_STYLE_HINTS — tests/test_options_validation.py тулгана). Энд импортлохгүй: options.py хөнгөн хэвээр.
ENUM_OPTIONS: dict[str, tuple] = {
    "variant": ("mn", "latin", "en", "mixed"),
    "dialect": ("sqlite_native", "postgres_prompt", "sqlite_transpile"),
    "dataset": ("nbfi", "bird_mn"),
    "value_mode": ("basic", "rule", "learned"),
    "translate_mode": ("replace", "bilingual", "en_prompt"),
    "translate_check": ("off", "bilingual"),
    "low_conf_action": ("abstain", "clarify"),
    "select_parse": ("legacy", "strict"),
    "gen_style": ("default", "cte", "decompose"),
    "value_match": ("legacy", "strict"),
    "mask_mode": ("legacy", "values"),
    "enum_codes": ("first9", "all"),
    "translit_vocab": ("pool", "no_eval"),
    "fewshot_rerank": (None, "table_jaccard"),
    "intent_rules": ("legacy", "v2"),
    "rules_version": ("v1", "v2"),
}

_VAR = re.compile(r"\$\{(\w+)\}")


def load_experiments(path: str = "config/experiments.yaml") -> dict:
    with open(resolve_path(path), encoding="utf-8") as fh:
        exp = yaml.safe_load(fh)
    flows = resolve_path("config/flows.yaml")   # вэб flow builder-ээр угсарсан туршилтууд (service/flow.py)
    if path == "config/experiments.yaml" and flows.exists():
        extra = (yaml.safe_load(flows.read_text(encoding="utf-8")) or {}).get("runs") or {}
        exp["runs"] = {**{k: v for k, v in extra.items() if k not in exp["runs"]}, **exp["runs"]}
    return exp


def resolve_run(run_id: str, exp: dict | None = None, overrides: dict | None = None,
                var_overrides: dict | None = None) -> RunOptions:
    exp = exp or load_experiments()
    runs = exp["runs"]
    if var_overrides:
        # M3: үсгийн алдаатай --var (best_aa=A7) өмнө нь чимээгүй алгасагдаж анхдагч vars-аар ажилладаг байв
        known_vars = set(exp.get("vars") or {}) | {m for spec in runs.values() for v in (spec or {}).values()
                                                   if isinstance(v, str) for m in _VAR.findall(v)}
        bad = sorted(set(var_overrides) - known_vars)
        if bad:
            raise ValueError(f"Үл мэдэгдэх --var: {', '.join(bad)} (experiments.yaml-ийн vars: "
                             f"{', '.join(sorted(known_vars))})")
    vars_ = dict(exp.get("vars") or {})
    vars_.update(var_overrides or {})

    def subst(v):
        if isinstance(v, str):
            m = _VAR.fullmatch(v.strip())
            if m:
                return vars_.get(m.group(1), v)
            return _VAR.sub(lambda mm: str(vars_.get(mm.group(1), mm.group(0))), v)
        return v

    def chain(rid: str, seen: tuple = ()) -> dict:
        rid = subst(rid)
        if rid in seen:
            raise ValueError(f"experiments.yaml-д давталттай base: {' → '.join(seen + (rid,))}")
        if rid not in runs:
            raise KeyError(f"Туршилтын ID олдсонгүй: {rid}")
        spec = {k: subst(v) for k, v in (runs[rid] or {}).items()}
        base = spec.pop("base", None)
        merged = chain(base, seen + (rid,)) if base else copy.deepcopy(exp.get("defaults") or {})
        # Удамшдаггүй талбарууд: тайлбар, харьцуулах суурь
        for k in ("desc", "compare_to"):
            merged.pop(k, None)
        merged.update(spec)
        merged["base"] = base
        return merged

    spec = chain(run_id)
    spec.update(overrides or {})
    known = {f.name for f in fields(RunOptions)}
    unknown = sorted(set(spec) - known)
    if unknown:
        # M3: `--set repair_strct=true` өмнө нь чимээгүй алгасагдаж (M1a-ийн файлыг үргэлжлүүлж) хувилбар гэж мэдээлэгддэг байв
        src = "--set" if set(unknown) & set(overrides or {}) else "experiments.yaml/flows.yaml"
        raise ValueError(f"{run_id}: үл мэдэгдэх тохиргоо ({src}): {', '.join(unknown)} — RunOptions-ийн талбар биш")
    opts = RunOptions(run_id=run_id, **{k: v for k, v in spec.items() if k in known})
    if not opts.models:
        opts.models = list((exp.get("defaults") or {}).get("models") or [])
    return opts.validate()


def parse_set(pairs: list[str] | None) -> dict:
    """CLI --set key=value → dict (true/false/тоо автоматаар). «=»-гүй бол алдаа (өмнө нь хоосон мөр = False болдог байв);
    түлхүүрийг resolve_run шалгана (M3)."""
    out = {}
    for p in pairs or []:
        k, eq, v = p.partition("=")
        if not eq or not k.strip():
            raise ValueError(f"--set/--var «түлхүүр=утга» хэлбэртэй байх ёстой: {p!r}")
        val: object = v
        if v.lower() in ("true", "false"):
            val = v.lower() == "true"
        else:
            try:
                val = int(v)
            except ValueError:
                try:
                    val = float(v)
                except ValueError:
                    val = v
        out[k.strip()] = val
    return out
