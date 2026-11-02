"""Сургалтын кодын засвар (2026-10-30 review): B2 (C2 gold-хуулбарын шүүлт), B3/F9 (бүх prompt горимд мөрийн id +
family), B4 (GRPO reward = үнэлгээний process_sql зам), B5 (GRPO init), B9 (h4s sampling, keep policy, sample_prompt,
family-ээр validation), L5 (dedup question_mixed). GPU/torch/DB-гүй — хуурамч pipe/executor-оор."""
import copy
import json
import random
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mn_text2sql.config import ROOT, load_config
from mn_text2sql.db.executor import ExecResult
from mn_text2sql.training import formats, sampling
from mn_text2sql.training.data import TrainItem, load_training_cfg

MODEL = "arctic-r1-7b"
GOLD = "SELECT COUNT(*) FROM t2s.loans WHERE status_code = 'CLOSED'"
PROMPT = [{"role": "system", "content": "You are a data science expert."},
          {"role": "user", "content": "Database Engine:\nSQLite\n\nCREATE TABLE loans (status_code TEXT);\n"
                                      "Question:\nХэдэн зээл хаагдсан бэ?"}]
PREFILL = "Let me solve this step by step.\n<think>"


def _sample(raw: str, **kw) -> dict:
    return dict({"raw_turns": [raw], "train_messages": PROMPT, "prefill": PREFILL, "conversation": None,
                 "final_sql": GOLD, "correct": True}, **kw)


def _old_drop(raw: str, gold: str, family: str, phrases: list[str]) -> bool:
    """Засварын өмнөх cot_file-ийн шүүлт (лавлагаа): зөвхөн <answer>-ийн өмнөх reasoning."""
    reasoning = sampling.reasoning_part(raw, family).lower()
    return sampling._norm_sql(gold) in re.sub(r"\s+", " ", reasoning) or any(p in reasoning for p in phrases)


# --------------------------------------------------------------------------- B2

def test_drop_phrases_cover_hint_wording():
    phrases = load_training_cfg()["cot"]["drop_phrases"]
    for p in ("note", "private", "correct query", "provided query", "given query", "gold", "the hint"):
        assert p in phrases
    # HINT_TEMPLATE-ийн үг бүрийг дурдвал (hint-ийг иш татвал) хасагдана
    assert "private note" in sampling.HINT_TEMPLATE.lower() and "a correct query is" in sampling.HINT_TEMPLATE.lower()


def test_copy_reason_checks_whole_completion_including_summary():
    phrases = load_training_cfg()["cot"]["drop_phrases"]
    old_phrases = ["given sql", "provided sql", "reference sql", "the hint", "өгөгдсөн sql", "gold"]
    # хураангуй (<answer>) дотор hint-ийг иш татсан — хуучин шүүлт зөвхөн reasoning-ийг харж алгасдаг байв
    raw = (PREFILL + " The loans table has status_code; closed loans have status 'CLOSED'. </think>\n<answer>As the "
           "private note says, a correct query counts CLOSED loans.\n```sql\n" + GOLD + "\n```\n</answer>")
    assert not _old_drop(raw, GOLD, "omnisql", old_phrases)
    assert sampling.copy_reason(_sample(raw), GOLD, "omnisql", phrases) == "phrase:note"
    # reasoning дотор: "the provided query" (хуучин жагсаалтад алга)
    raw2 = (PREFILL + " Looking at the provided query, it filters status_code. </think>\n<answer>Count closed loans."
            "\n```sql\n" + GOLD + "\n```\n</answer>")
    assert not _old_drop(raw2, GOLD, "omnisql", old_phrases)
    assert sampling.copy_reason(_sample(raw2), GOLD, "omnisql", phrases) == "phrase:provided query"
    # цэвэр гаргалт: эцсийн SQL нь gold-той үгчлэн ижил байх нь зөв — хасахгүй
    clean = (PREFILL + " We count rows in loans whose status_code is CLOSED. </think>\n<answer>Count closed loans.\n"
             "```sql\n" + GOLD + "\n```\n</answer>")
    assert sampling.copy_reason(_sample(clean), GOLD, "omnisql", phrases) is None


def test_copy_reason_gold_verbatim_outside_final_sql():
    phrases = load_training_cfg()["cot"]["drop_phrases"]
    in_think = (PREFILL + " I will write:\n```sql\n" + GOLD.lower() + ";\n```\nThat answers it. </think>\n<answer>Done."
                "\n```sql\n" + GOLD + "\n```\n</answer>")
    assert sampling.copy_reason(_sample(in_think), GOLD, "omnisql", phrases) == "gold_verbatim"
    # хураангуй дотор (код блокгүй) gold-ыг үгчлэн бичсэн
    in_summary = (PREFILL + " Count closed loans. </think>\n<answer>Use " + GOLD + " directly.\n```sql\n" + GOLD +
                  "\n```\n</answer>")
    assert sampling.copy_reason(_sample(in_summary), GOLD, "omnisql", phrases) == "gold_verbatim"
    # sqrl: эцсийн <answer> SQL нь хариулт; судлах <sql> алхамд gold-ыг ажиллуулсан бол хуулбар
    sq_ok = ["<think>check statuses</think>\n<sql>SELECT DISTINCT status_code FROM loans</sql>",
             "<think>CLOSED exists</think>\n<answer> " + GOLD + " </answer>"]
    assert sampling.copy_reason(_sample("", raw_turns=sq_ok), GOLD, "sqrl", phrases) is None
    sq_copy = ["<think>try it</think>\n<sql>" + GOLD + "</sql>", "<think>ok</think>\n<answer> " + GOLD + " </answer>"]
    assert sampling.copy_reason(_sample("", raw_turns=sq_copy), GOLD, "sqrl", phrases) == "gold_verbatim"


def test_copy_reason_word_start_and_prompt_context():
    phrases = ["note", "gold", "private", "the hint"]
    body = PREFILL + " Let x denote the count; the footnote is irrelevant. </think>\n<answer>\n```sql\nSELECT 1\n```"
    assert sampling.copy_reason(_sample(body), GOLD, "omnisql", phrases) is None        # denote/footnote ≠ note
    gold_col = PREFILL + " Compare with gold_sql. </think>"
    assert sampling.copy_reason(_sample(gold_col), GOLD, "omnisql", phrases) == "phrase:gold"
    # асуулт өөрөө "private" гэж агуулсан бол (prompt-д байгаа үг) хуулбарын шинж биш
    q_private = [PROMPT[0], dict(PROMPT[1], content=PROMPT[1]["content"] + "\nHow many private schools?")]
    raw = PREFILL + " Filter private schools. </think>\n<answer>\n```sql\nSELECT 1\n```\n</answer>"
    assert sampling.copy_reason(_sample(raw, train_messages=q_private), GOLD, "omnisql", phrases) is None
    assert sampling.copy_reason(_sample(raw), GOLD, "omnisql", phrases) == "phrase:private"
    # sqrl-ийн prompt "Use the hints in <evidence>" — "the hint" тэнд байгаа тул тооцохгүй
    sq_prompt = [{"role": "system", "content": "Use the hints in <evidence> to resolve terms."}]
    sq = ["<think>Using the hints in evidence.</think>\n<answer> SELECT 1 </answer>"]
    assert sampling.copy_reason(_sample("", raw_turns=sq, train_messages=sq_prompt), GOLD, "sqrl", phrases) is None


def test_copy_reason_ignores_leftover_hint_in_context():
    """Хадгалсан ярианд hint үлдсэн ч (жишээ нь хасалт амжилтгүй) түүний үгс «prompt-д байгаа» гэж тооцогдохгүй."""
    phrases = load_training_cfg()["cot"]["drop_phrases"]
    conv = [PROMPT[0], dict(PROMPT[1], content=PROMPT[1]["content"] + sampling.HINT_TEMPLATE.format(sql=GOLD))]
    raw = PREFILL + " ok </think>\n<answer>The private note gives it.\n```sql\n" + GOLD + "\n```\n</answer>"
    assert sampling.copy_reason(_sample(raw, conversation=conv), GOLD, "omnisql", phrases) == "phrase:note"
    assert sampling.copy_reason(_sample(raw, train_messages=conv), GOLD, "omnisql", phrases) == "phrase:note"


class _CotSampler:
    """cot_file-д: hint-тэй үед урьдчилан бэлтгэсэн гаргалт буцаана."""
    family = "omnisql"

    def __init__(self, outputs):
        self.outputs = outputs
        self.hints = []

    def sample(self, item, variant, n, temperature, top_p, hint=None):
        self.hints.append(hint)
        return [dict(_sample(raw), item_id=item.id, variant=variant, correct=ok) for raw, ok in self.outputs[item.id]]


def _tmp_tcfg(tmp_path: Path) -> dict:
    tcfg = copy.deepcopy(load_training_cfg())
    tcfg["data"]["dir"] = str(tmp_path / "train")
    tcfg["sampling"]["workers"] = 2
    return tcfg


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def test_cot_file_counts_drops_by_reason(tmp_path, monkeypatch, caplog):
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    items = [{"id": f"q{i}", "source": "human", "engine": "postgres", "gold_sql": GOLD, "question_mn": f"асуулт {i}"}
             for i in range(3)]
    _write_jsonl(d / "src.jsonl", items)
    _write_jsonl(d / "samples" / f"{MODEL}__plain__src.stats.jsonl",
                 [{"item_id": f"q{i}", "variant": "mn", "n": 8, "n_correct": 0} for i in range(3)])
    leak = PREFILL + " ok </think>\n<answer>Per the note, count CLOSED.\n```sql\n" + GOLD + "\n```\n</answer>"
    verbatim = PREFILL + " " + GOLD + " </think>\n<answer>\n```sql\n" + GOLD + "\n```\n</answer>"
    clean = PREFILL + " count CLOSED loans </think>\n<answer>Count.\n```sql\n" + GOLD + "\n```\n</answer>"
    fake = _CotSampler({"q0": [(leak, True), (clean, True)], "q1": [(verbatim, True), (clean, False)],
                        "q2": [(leak, True)]})
    monkeypatch.setattr(sampling, "Sampler", lambda *a, **k: fake)
    with caplog.at_level("INFO", logger="mn_text2sql.training.sampling"):
        tot = sampling.cot_file(load_config(), tcfg, MODEL, "src.jsonl")
    # q0: leak хасагдаад цэвэр нь сэргэнэ (1); q1: 2 оролдлого × verbatim (2); q2: 2 оролдлого × leak (2)
    assert tot["recovered"] == 1 and tot["dropped_copy"] == 5
    assert tot["dropped_by_reason"] == {"phrase:note": 3, "gold_verbatim": 2}
    rows = [json.loads(x) for x in Path(tot["file"]).read_text(encoding="utf-8").splitlines()]
    assert [r["item_id"] for r in rows] == ["q0"] and rows[0]["cot"] is True
    assert set(fake.hints) == {GOLD}
    assert "gold хуулсан гэж хасагдсан" in caplog.text


# --------------------------------------------------------------------------- B3/F9

class _FakePipe:
    def __init__(self, examples=()):
        self.item_families: dict = {}
        self.translations: dict = {}
        self.retriever = SimpleNamespace(examples=SimpleNamespace(examples=list(examples)))
        self.calls = []

    def prompt_for(self, q, variant, item_id):
        self.calls.append({"item_id": item_id, "family": self.item_families.get(item_id or ""),
                           "en": self.translations.get(f"{item_id}:{variant}")})
        return [{"role": "user", "content": f"Q: {q}"}], PREFILL, None, {}, q


class _NoLLM:
    name = "none"

    def chat(self, *a, **k):  # pragma: no cover
        raise AssertionError("LLM дуудахгүй")


HUMAN = TrainItem(id="human-AX03", source="human", engine="postgres", gold_sql=GOLD, question_mn="хэдэн зээл?",
                  question_en="how many loans?", extra={"family": "AX-f03"})
SYNTH = TrainItem(id="synth-human-AX03-0123abcd", source="own_synth", engine="postgres", gold_sql="SELECT 2",
                  question_mn="синтетик асуулт", question_en="synthetic question", extra={"template": "human-AX03"})


@pytest.mark.parametrize("mode", ["plain", "rag", "h4s"])
def test_every_prompt_mode_passes_item_id_and_family(mode, monkeypatch):
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    s = sampling.Sampler(load_config(), MODEL, mode, llm=_NoLLM())
    pipe = _FakePipe([SimpleNamespace(id="AX03", family="AX-f03"), SimpleNamespace(id="LX19", family="LX-f19")])
    s._pipe = pipe
    for it in (HUMAN, SYNTH):
        msgs, prefill = s.messages_for(it, "mn")
        call = pipe.calls[-1]
        assert call["item_id"] == it.id                     # өмнө plain/rag нь None байсан (B3)
        assert call["family"] == "AX-f03"                    # human: өөрийн, own_synth: эх жишээний family
        assert call["en"] == (it.question_en if mode == "h4s" else None)   # англи асуулт зөвхөн h4s-д
        assert prefill == PREFILL and msgs[-1]["content"] == f"Q: {it.question_mn}"
    hinted, _ = s.messages_for(HUMAN, "mn", hint="SELECT 42")
    assert hinted[-1]["content"].endswith(sampling.HINT_TEMPLATE.format(sql="SELECT 42"))
    assert s.render_stats["family_excluded"] == 3


def test_plain_prompt_has_no_retrieval_so_item_id_cannot_change_it(monkeypatch):
    """B3-ийн засвар plain (A-шилдэг = A7) prompt-ыг өөрчлөхгүй: retrieval/few-shot/орчуулга алга. rag (B5) — few-shot 4."""
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    plain, rag = sampling.run_options("plain"), sampling.run_options("rag")
    assert plain.run_id == "A7" and not plain.needs_retrieval() and plain.fewshot_k == 0 and not plain.translate
    assert rag.run_id == "B5" and rag.fewshot_k == 4


# --------------------------------------------------------------------------- B4

class _FakePG:
    """PostgreSQL-ийн хуурамч executor: SQLite-ийн strftime ба boolean = 1-ийг PG шиг алдаа болгоно."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def run(self, sql, max_rows=None):
        with self.lock:
            self.calls.append(sql)
        if "information_schema.columns" in sql:
            return ExecResult(ok=True, columns=["c"], rows=[("is_active_portfolio",)], rowcount=1)
        up = sql.upper()
        if "STRFTIME" in up:
            return ExecResult(ok=False, error="function strftime(unknown, date) does not exist")
        if re.search(r"IS_ACTIVE_PORTFOLIO\s*=\s*1\b", up):
            return ExecResult(ok=False, error="operator does not exist: boolean = integer")
        if "2025" in sql:
            return ExecResult(ok=True, columns=["count"], rows=[(42,)], rowcount=1)
        return ExecResult(ok=True, columns=["count"], rows=[(7,)], rowcount=1)


@pytest.fixture()
def a7_pipe(monkeypatch):
    from mn_text2sql.eval.goldcheck import make_guard
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.orchestrator import Pipeline
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    cfg = load_config()
    ex = _FakePG()
    pipe = Pipeline(cfg, sampling.run_options("plain"), MODEL, MockLLM(lambda m: ""), executor=ex,
                    guard=make_guard(cfg))
    return pipe, ex


def test_grpo_reward_scores_sqlite_answers_like_evaluation(a7_pipe):
    from mn_text2sql.training.grpo import ExecReward
    pipe, ex = a7_pipe
    assert pipe.opts.dialect == "sqlite_transpile"           # A7: prompt нь SQLite гэж заана
    gold = "SELECT COUNT(*) FROM t2s.loans WHERE EXTRACT(YEAR FROM disbursement_date) = 2025 AND is_active_portfolio"
    sqlite_sql = "SELECT COUNT(*) FROM loans WHERE strftime('%Y', disbursement_date) = '2025' AND is_active_portfolio = 1"
    # хуучин зам (guard + шууд гүйцэтгэл) — зөв SQLite хариулт PG дээр алдаа → reward 0
    assert not ex.run(pipe.guard.check(sqlite_sql).sql).ok
    reward = ExecReward(load_config(), "omnisql", {"correct": 1.0, "executable": 0.1, "error": 0.0}, pipe=pipe)
    comps = [f" ok</think>\n<answer>\n```sql\n{sqlite_sql}\n```\n</answer>",
             " ok</think>\n<answer>\n```sql\nSELECT COUNT(*) FROM loans\n```\n</answer>",     # ажилласан, буруу
             " ok</think>\n<answer>\n```sql\nDELETE FROM loans\n```\n</answer>",               # guard
             " no sql at all"]
    n = len(comps)
    scores = reward(prompts=["p"] * n, completions=comps, engine=["postgres"] * n, db_path=[""] * n,
                    gold_sql=[gold] * n)
    assert scores == [1.0, 0.1, 0.0, 0.0]
    ran = [c for c in ex.calls if "information_schema" not in c]
    assert any("TO_CHAR" in c and "TRUE" in c.upper() for c in ran)      # sqlite_to_postgres + boolean хөрвүүлэлт
    assert not any("STRFTIME" in c.upper() for c in ran[1:])
    # sampling-ийн зөвийн дүрэмтэй нэг (pg_correct): gold нэг л удаа гүйцэтгэгдэнэ (кэш)
    assert sum(1 for c in ran if "EXTRACT" in c.upper()) == 1


def test_sampler_correctness_uses_same_pg_correct(a7_pipe, monkeypatch):
    pipe, ex = a7_pipe
    s = sampling.Sampler(load_config(), MODEL, "plain", llm=_NoLLM())
    s._pipe = pipe
    item = TrainItem(id="x", source="human", engine="postgres", gold_sql="SELECT COUNT(*) FROM t2s.loans WHERE "
                     "EXTRACT(YEAR FROM disbursement_date) = 2025", question_mn="q")
    cand = pipe.process_sql("SELECT COUNT(*) FROM loans WHERE strftime('%Y', disbursement_date) = '2025'")
    assert cand.ok and s._correct_pg(item, cand)
    assert not s._correct_pg(item, pipe.process_sql("SELECT COUNT(*) FROM loans"))
    seen = []
    monkeypatch.setattr(sampling, "pg_correct", lambda *a: seen.append(a) or True)
    s._correct_pg(item, cand)
    assert seen and seen[0][0] is pipe and seen[0][1] == item.gold_sql


# --------------------------------------------------------------------------- B5

def test_grpo_init_honours_recipe():
    from mn_text2sql.training.grpo import resolve_init
    tcfg = load_training_cfg()
    best = tcfg["best_sft"]
    # c6/c7/f4 (init: best_sft): өмнөх анхдагчтай яг ижил зам
    for r in ("c6", "c7", "f4"):
        assert resolve_init(tcfg, r, MODEL) == (str(ROOT / "adapters" / f"{best}__{MODEL}__merged"), "recipe:best_sft")
    # d4 (init: d2) — өмнө үл тоомсорлодог байв
    assert resolve_init(tcfg, "d4", MODEL) == (str(ROOT / "adapters" / f"d2__{MODEL}__merged"), "recipe:d2")
    assert resolve_init(tcfg, "d4", MODEL, "/x/merged") == ("/x/merged", "cli")          # --init давуу
    tcfg2 = copy.deepcopy(tcfg)
    tcfg2["recipes"]["gx"] = {"kind": "grpo", "init": "/kaggle/input/d2-merged16"}
    tcfg2["recipes"]["gy"] = {"kind": "grpo"}
    assert resolve_init(tcfg2, "gx", MODEL) == ("/kaggle/input/d2-merged16", "recipe:/kaggle/input/d2-merged16")
    assert resolve_init(tcfg2, "gy", MODEL)[0] == str(ROOT / "adapters" / f"{best}__{MODEL}__merged")


class _Tok:
    """transformers-гүй tokenizer (ChatML, 4 тэмдэгт ≈ 1 токен)."""
    eos_token = "<|im_end|>"

    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=True):
        return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs) + "<|im_start|>assistant\n"

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(range(len(text) // 4))}


def test_grpo_dataset_prompts_carry_item_id(tmp_path, monkeypatch):
    """B3 нь GRPO-ийн prompt-д ч хүрнэ (build_grpo_dataset → Sampler.messages_for)."""
    from mn_text2sql.training.grpo import build_grpo_dataset
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    _write_jsonl(d / "human.jsonl", [{"id": HUMAN.id, "source": "human", "engine": "postgres", "gold_sql": GOLD,
                                      "question_mn": HUMAN.question_mn, "extra": HUMAN.extra}])
    _write_jsonl(d / "samples" / f"{MODEL}__plain__human.stats.jsonl",
                 [{"item_id": HUMAN.id, "variant": "mn", "n": 8, "n_correct": 3}])
    s = sampling.Sampler(load_config(), MODEL, "rag", llm=_NoLLM())
    s._pipe = _FakePipe()
    rows = build_grpo_dataset(load_config(), tcfg, "d4", MODEL, sampler=s, tokenizer=_Tok())
    assert [r["qid"] for r in rows] == [HUMAN.id]
    assert s._pipe.calls == [{"item_id": HUMAN.id, "family": "AX-f03", "en": None}]


# --------------------------------------------------------------------------- B9: keep policy, h4s sampling

def _old_keep(samples, max_keep=4):
    good, seen = [], set()
    for s in samples:
        key = sampling._norm_sql(s["final_sql"])
        if s["correct"] and key not in seen:
            seen.add(key)
            good.append(s)
    return good[:max_keep]


def test_keep_correct_default_equals_old_rule_and_r2_policy():
    rng = random.Random(0)
    for _ in range(200):
        samples = [{"final_sql": f"SELECT {rng.randint(0, 4)}", "correct": rng.random() < 0.6} for _ in range(8)]
        assert sampling.keep_correct(samples) == _old_keep(samples)
    partial = [{"final_sql": f"SELECT {i % 3}", "correct": i < 5} for i in range(8)]       # 5/8 зөв, 3 ялгаатай
    assert len(sampling.keep_correct(partial, 2, 1)) == 2
    solved = [{"final_sql": f"SELECT {i % 3}", "correct": True} for i in range(8)]           # 8/8
    assert len(sampling.keep_correct(solved)) == 3 and len(sampling.keep_correct(solved, 2, 1)) == 1
    assert sampling.keep_correct(solved, 2, 0) == []
    assert sampling.keep_correct([{"final_sql": "SELECT 1", "correct": False}] * 8, 2, 1) == []


class _FakeSampler:
    instances: list = []

    def __init__(self, cfg, model_name, prompt_mode="plain", pivot=False, tool=False, base_url=None, llm=None,
                 index_dir=None):
        self.args = {"model": model_name, "prompt_mode": prompt_mode, "index_dir": index_dir}
        self.family = "omnisql"
        _FakeSampler.instances.append(self)

    def sample(self, item, variant, n, temperature, top_p, hint=None):
        plan = {"partial": [(f"SELECT {i % 3}", i < 5) for i in range(n)],
                "solved": [(f"SELECT {i % 3}", True) for i in range(n)],
                "unsolved": [("SELECT 9", False)] * n}[item.id]
        return [{"final_sql": sql, "correct": ok, "exec_ok": True, "item_id": item.id, "variant": variant}
                for sql, ok in plan]


@pytest.mark.parametrize("policy,kept", [({}, {"partial": 3, "solved": 3}),
                                         ({"max_keep": 2, "max_keep_all": 1}, {"partial": 2, "solved": 1})])
def test_sample_file_keep_policy_and_h4s(tmp_path, monkeypatch, policy, kept):
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    _write_jsonl(d / "src.jsonl", [{"id": i, "source": "own_synth", "engine": "postgres", "gold_sql": "SELECT 0",
                                    "question_mn": "q"} for i in ("partial", "solved", "unsolved")])
    _FakeSampler.instances = []
    monkeypatch.setattr(sampling, "Sampler", _FakeSampler)
    rep = sampling.sample_file(load_config(), tcfg, MODEL, "src.jsonl", "h4s", index_dir="build/index_plus",
                               **policy)
    assert _FakeSampler.instances[0].args == {"model": MODEL, "prompt_mode": "h4s", "index_dir": "build/index_plus"}
    assert Path(rep["file"]).name == f"{MODEL}__h4s__src.jsonl"
    rows = [json.loads(x) for x in Path(rep["file"]).read_text(encoding="utf-8").splitlines()]
    got = {k: sum(1 for r in rows if r["item_id"] == k) for k in ("partial", "solved", "unsolved")}
    assert got == dict(kept, unsolved=0)
    assert rep["questions"] == 3 and rep["solved"] == 2 and rep["kept"] == sum(kept.values())
    stats = {json.loads(x)["item_id"]: json.loads(x) for x in Path(rep["stats"]).read_text(encoding="utf-8").splitlines()}
    assert stats["partial"]["n_correct"] == 5 and stats["solved"]["n_correct"] == 8


def test_sample_file_reads_keep_policy_from_config(tmp_path, monkeypatch):
    tcfg = _tmp_tcfg(tmp_path)
    tcfg["sampling"].update(max_keep=2, max_keep_all_correct=0)
    d = Path(tcfg["data"]["dir"])
    _write_jsonl(d / "src.jsonl", [{"id": i, "source": "own_synth", "engine": "postgres", "gold_sql": "SELECT 0",
                                    "question_mn": "q"} for i in ("partial", "solved")])
    monkeypatch.setattr(sampling, "Sampler", _FakeSampler)
    rep = sampling.sample_file(load_config(), tcfg, MODEL, "src.jsonl")
    assert rep["keep"] == {"max_keep": 2, "max_keep_all_correct": 0} and rep["kept"] == 2


def test_cli_exposes_r2a_options(monkeypatch):
    from mn_text2sql.training import cli, sft
    seen = {}
    monkeypatch.setattr(sampling, "sample_file", lambda *a, **k: seen.setdefault("sample", (a, k)) and {})
    monkeypatch.setattr(sampling, "cot_file", lambda *a, **k: seen.setdefault("cot", (a, k)) and {})
    monkeypatch.setattr(sft, "run_sft", lambda *a, **k: seen.setdefault("sft", (a, k)) and {})
    monkeypatch.setattr(sft, "merge_adapter", lambda *a, **k: seen.setdefault("merge", (a, k)) and "out")
    assert cli.main(["sample", "--model", MODEL, "--source", "own_synth_clean.jsonl", "--prompt", "h4s",
                     "--max-keep", "2", "--max-keep-all", "1", "--index-dir", "build/index_plus"]) == 0
    a, k = seen["sample"]
    assert a[4] == "h4s" and k == {"max_keep": 2, "max_keep_all": 1, "index_dir": "build/index_plus"}
    cli.main(["cot", "--model", MODEL, "--source", "s.jsonl", "--prompt", "h4s", "--index-dir", "ix"])
    assert seen["cot"][0][4] == "h4s" and seen["cot"][1] == {"index_dir": "ix"}
    cli.main(["sft", "--recipe", "d3", "--model", MODEL, "--base", "/k/d2_merged", "--eval-file", "e.jsonl"])
    assert seen["sft"][1]["base"] == "/k/d2_merged" and seen["sft"][1]["eval_file"] == "e.jsonl"
    cli.main(["merge", "--model", MODEL, "--adapter", "a", "--out", "o"])
    assert seen["merge"][1] == {"base": None, "dtype": "bf16"}
    # анхдагч: sample-ийн keep/index аргументгүй (training.yaml-аас), sft-ийн base/eval алга
    seen.clear()
    cli.main(["sample", "--model", MODEL, "--source", "s.jsonl"])
    assert seen["sample"][0][4] == "plain" and seen["sample"][1] == {"max_keep": None, "max_keep_all": None,
                                                                      "index_dir": None}


# --------------------------------------------------------------------------- B9: build_sft (sample_prompt, eval split)

def _own_sample(item_id: str, mode: str, n: int = 0) -> dict:
    return {"item_id": item_id, "variant": "mn", "engine": "postgres", "family": "omnisql", "cot": False,
            "final_sql": f"SELECT {n}", "prefill": PREFILL,
            "train_messages": [{"role": "user", "content": f"[{mode}] prompt for {item_id} #{n}"}],
            "raw_turns": [PREFILL + f" r </think>\n<answer>\n```sql\nSELECT {n}\n```\n</answer>"]}


def _build_fixture(tmp_path: Path, mode: str) -> dict:
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    fams = [f"SO-join_fk-{i:02d}" for i in range(6)]
    synth = [{"id": f"synth_own-SO-{f[-2:]}-{j}", "source": "own_synth", "engine": "postgres", "gold_sql": "SELECT 1",
              "question_mn": "q", "extra": {"family": f}} for f in fams for j in range(2)]
    human = [{"id": "human-AX03", "source": "human", "engine": "postgres", "gold_sql": GOLD, "question_mn": "q",
              "extra": {"family": "AX-f03"}}]
    tmpl = [{"id": "synth-human-AX03-0123abcd", "source": "own_synth", "engine": "postgres", "gold_sql": "SELECT 2",
             "question_mn": "q", "extra": {"template": "human-AX03"}}]
    _write_jsonl(d / "own_synth_clean.jsonl", synth + tmpl)
    _write_jsonl(d / "human.jsonl", human)
    ids = [r["id"] for r in synth + tmpl]
    _write_jsonl(d / "samples" / f"{MODEL}__{mode}__own_synth_clean.jsonl",
                 [_own_sample(i, mode, n) for n, i in enumerate(ids)] + [_own_sample(ids[0], mode, 99)])
    _write_jsonl(d / "samples" / f"{MODEL}__{mode}__human.jsonl", [_own_sample("human-AX03", mode, 50)])
    return tcfg


def test_build_sft_uses_h4s_samples_with_stored_prompt_and_family_eval_split(tmp_path, monkeypatch):
    tcfg = _build_fixture(tmp_path, "h4s")
    tcfg["recipes"]["r2a_t"] = {"kind": "sft", "data": ["synth", "human"], "prompt": "h4s", "sample_prompt": "h4s",
                                "eval_share": 0.3, "max_length": 100000}
    monkeypatch.setattr(formats, "Renderer", lambda *a, **k: pytest.fail("h4s-ээр түүвэрлэсэн бол дахин угсрахгүй"))
    rep = formats.build_sft(load_config(), tcfg, "r2a_t", MODEL, tokenizer=_Tok())
    train = [json.loads(x) for x in Path(rep["file"]).read_text(encoding="utf-8").splitlines()]
    ev = [json.loads(x) for x in Path(rep["eval_file"]).read_text(encoding="utf-8").splitlines()]
    assert rep["sample_prompt"] == "h4s" and rep["render"] == {}
    assert len(train) + len(ev) == 15 and rep["records"] == len(train) and rep["eval_records"] == len(ev) > 0
    assert all(r["prompt"].count("[h4s] prompt for") == 1 for r in train + ev)     # хадгалсан (h4s) prompt
    # бүлэг = family: 6 synth family + AX-f03 (human ба түүний template-ийн synth) = 7 → round(0.3·7) = 2
    assert rep["groups"] == 7 and rep["eval_groups"] == 2

    def fam_of(rec):
        iid = re.search(r"prompt for (\S+) #", rec["prompt"]).group(1)
        return "AX-f03" if "AX03" in iid else f"SO-join_fk-{iid.split('-')[2]}"
    assert not {fam_of(r) for r in train} & {fam_of(r) for r in ev}                   # family хоёр талд хуваагдахгүй
    before = (Path(rep["file"]).read_text(encoding="utf-8"), Path(rep["eval_file"]).read_text(encoding="utf-8"))
    rep2 = formats.build_sft(load_config(), tcfg, "r2a_t", MODEL, tokenizer=_Tok())   # детерминист
    assert (Path(rep2["file"]).read_text(encoding="utf-8"), Path(rep2["eval_file"]).read_text(encoding="utf-8")) == before


def test_build_sft_default_unchanged_no_eval_plain_samples(tmp_path):
    """Анхдагч (sample_prompt, eval_share алга): plain файл цуглуулж, бүх мөр сургалтад, эрэмбэ = өмнөх Random(42)."""
    tcfg = _build_fixture(tmp_path, "plain")
    _build_fixture(tmp_path, "h4s")                                    # h4s файл байсан ч анхдагч жор авахгүй
    tcfg["recipes"]["c_t"] = {"kind": "sft", "data": ["synth", "human"], "prompt": "plain", "max_length": 100000}
    rep = formats.build_sft(load_config(), tcfg, "c_t", MODEL, tokenizer=_Tok())
    assert "eval_file" not in rep and not (Path(rep["file"]).with_name(f"c_t__{MODEL}.eval.jsonl")).exists()
    samples = formats.collect_samples(tcfg, MODEL, "plain", ["synth", "human"])
    expected = [r for s in samples for r in formats.to_records(s, _Tok())]
    random.Random(42).shuffle(expected)
    got = [json.loads(x) for x in Path(rep["file"]).read_text(encoding="utf-8").splitlines()]
    assert got == expected and all("[plain]" in r["prompt"] for r in got)
    assert formats.sample_mode("rag") == "plain" and formats.sample_mode("rag", tool=True) == "rag"
    assert formats.sample_mode("h4s", sample_prompt="h4s") == "h4s"


def test_pick_eval_groups_deterministic():
    groups = [f"family:F{i}" for i in range(20)]
    a = formats.pick_eval_groups(groups, 0.1)
    assert len(a) == 2 and a == formats.pick_eval_groups(list(reversed(groups)), 0.1)
    assert formats.pick_eval_groups(groups, 0) == set() and formats.pick_eval_groups(["x"], 0.5) == set()
    assert len(formats.pick_eval_groups(["a", "b"], 0.9)) == 1          # ядаж 1 бүлэг сургалтад үлдэнэ


# --------------------------------------------------------------------------- L5

def test_human_items_keep_question_mixed():
    from mn_text2sql.training.data import human_items, item_questions
    items = human_items(load_config())
    mixed = [it for it in items if it.extra.get("question_mixed")]
    assert mixed and all(it.extra["question_mixed"] in item_questions(it) for it in mixed)
    assert all("question_mixed" not in it.extra for it in items if it not in mixed)


def test_dedup_checks_question_mixed():
    from mn_text2sql.config import resolve_path
    from mn_text2sql.eval.dataset import load_items
    from mn_text2sql.training.data import dedup_against_eval
    cfg = load_config()
    ev = next(it for it in load_items([resolve_path(cfg["eval"]["dev_file"])]) if it.question("mixed"))
    q = "qqzx wvvk 90817 jjyy"
    with_mixed = TrainItem(id="t1", source="own_synth", engine="sqlite", gold_sql="SELECT 1", question_mn=q,
                           extra={"question_mixed": ev.question("mixed")})
    without = TrainItem(id="t2", source="own_synth", engine="sqlite", gold_sql="SELECT 1", question_mn=q)
    kept, dropped = dedup_against_eval(cfg, [with_mixed, without], 0.8, use_embeddings=False)
    assert [k.id for k in kept] == ["t2"]
    assert dropped == [{"id": "t1", "eval_id": dropped[0]["eval_id"], "sim": 1.0}]
