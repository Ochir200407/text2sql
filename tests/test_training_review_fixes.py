"""2026-10-31 review-ийн засвар (сургалт, BIRD): H3 (BIRD литерал хамгаалалт, placeholders_ok шүүлт), L1 (dev/test-тэй
индекс, C3-ийн загварын dedup), L2 (sampling-ийн seed, GRPO-ийн эрэмбэ, t2s_meta), L7 (cot --tool нэр), L9 (GRPO-ийн жорын
тохиргоо), L10 (зөвийн шалгалт: order_matters + код↔нэрийн map). GPU/torch/DB/сүлжээгүй — хуурамч pipe/executor/LLM-ээр.
Локалд BIRD өгөгдөл алга тул H3-ыг синтетик жишээгээр шалгана."""
import copy
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from mn_text2sql.bird.data import _protect, _restore, load_bird_mn
from mn_text2sql.config import ROOT, load_config, load_models, resolve_path
from mn_text2sql.db.executor import ExecResult
from mn_text2sql.llm.client import LLMResponse
from mn_text2sql.training import formats, grpo, sampling, sft, synth
from mn_text2sql.training.data import TrainItem, human_items, load_training_cfg, read_items, write_items

MODEL = "arctic-r1-7b"
PREFILL = "Let me solve this step by step.\n<think>"
GOLD = "SELECT COUNT(*) FROM t2s.loans WHERE status_code = 'CLOSED'"


def _tmp_tcfg(tmp_path: Path) -> dict:
    tcfg = copy.deepcopy(load_training_cfg())
    tcfg["data"]["dir"] = str(tmp_path / "train")
    tcfg["sampling"]["workers"] = 2
    return tcfg


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class _NoLLM:
    name = "none"

    def chat(self, *a, **k):  # pragma: no cover
        raise AssertionError("LLM дуудахгүй")


class _FakePipe:
    """Pipeline-ийн оронд: prompt_for (eval-ийн prompt), process_sql (буруу/ажиллаагүй), sqrl-ийн _agent (seed бүртгэнэ)."""

    def __init__(self):
        self.item_families: dict = {}
        self.translations: dict = {}
        self.retriever = SimpleNamespace(examples=SimpleNamespace(examples=[]))
        self.agent_seeds: list = []

    def prompt_for(self, q, variant, item_id):
        return [{"role": "user", "content": f"Q: {q}"}], PREFILL, None, {}, q

    def process_sql(self, sql):
        return SimpleNamespace(ok=False, result=None, sql=None, raw_sql=sql)

    def _agent(self, messages, dec, seed=None):
        self.agent_seeds.append(seed)
        return SimpleNamespace(ok=False, result=None, sql="SELECT 1", raw_sql="SELECT 1", raw_turns=["t"], messages=[])


class _Tok:
    eos_token = "<|im_end|>"

    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=True):
        return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs) + "<|im_start|>assistant\n"

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(range(len(text) // 4))}


# =========================================================================== H3: BIRD литерал хамгаалалт

def _old_protect(text: str, sql: str):
    """Засварын өмнөх bird/data._protect (лавлагаа)."""
    spans = set(re.findall(r"`[^`]+`", text)) | set(re.findall(r"'([^']{1,80})'", sql))
    spans |= set(re.findall(r'"([^"]{1,80})"', text))
    spans = {s for s in spans if s and s in text}
    mapping = {}
    for i, s in enumerate(sorted(spans, key=len, reverse=True), 1):
        key = f"[V{i}]"
        text = text.replace(s, key)
        mapping[key] = s
    return text, mapping


def _old_restore(text: str, mapping: dict):
    ok = True
    for k, v in mapping.items():
        if k in text:
            text = text.replace(k, v)
        else:
            ok = False
    return text.strip(), ok


Q_FIND = ("Find the number of female clients in Alameda.",
          "SELECT COUNT(*) FROM client WHERE gender = 'F' AND district = 'Alameda'")
Q_NEST = ("How many schools in Alameda have charter = 1?",
          "SELECT COUNT(*) FROM schools WHERE County = 'Alameda' AND Charter = '1'")


def test_old_protect_corrupted_the_reported_examples():
    """Review-ийн жишээ: 'F' нь "Find" доторх → "[V2]ind"; '1' нь "[V1]" доторх → "[V[V2]]" → сэргээсэн асуултад "[V1]"."""
    masked, _ = _old_protect(*Q_FIND)
    assert masked.startswith("[V2]ind ")
    masked, mapping = _old_protect(*Q_NEST)
    assert "[V[V2]]" in masked
    restored, ok = _old_restore(masked, mapping)
    assert not ok and "[V1]" in restored


@pytest.mark.parametrize("text,sql,masked,mapping", [
    (*Q_FIND, "Find the number of female clients in [V1].", {"[V1]": "Alameda"}),
    (*Q_NEST, "How many schools in [V1] have charter = [V2]?", {"[V1]": "Alameda", "[V2]": "1"}),
    # богино литерал — зөвхөн бие даасан токен (хашилт дотор), "F-score"-ийн хэсэг биш
    ("female refers to gender = 'F'", "SELECT 1 FROM c WHERE gender = 'F'", "female refers to gender = '[V1]'",
     {"[V1]": "F"}),
    ("Give the F-score of model F.", "SELECT score FROM m WHERE name = 'F'", "Give the F-score of model [V1].",
     {"[V1]": "F"}),
    # аравтын бутархай / "12-year-old"-ийн тоо маскжихгүй; урт тоо бүтэн токен бол
    ("What is the rate of 1.5 for type A players?", "SELECT 1 FROM p WHERE type = 'A' AND x = '5'",
     "What is the rate of 1.5 for type [V1] players?", {"[V1]": "A"}),
    ("In 2012, how many 12-year-old patients?", "SELECT 1 FROM p WHERE age = '12' AND year = '2012'",
     "In [V1], how many 12-year-old patients?", {"[V1]": "2012"}),
    # "давхар хашилт" ба 1 оронтой тоо: placeholder дахин маскжихгүй
    ('Which card has the "Flying" keyword and costs 1?', "SELECT 1 FROM c WHERE k = 'Flying' AND m = '1'",
     'Which card has the "[V1]" keyword and costs [V2]?', {"[V1]": "Flying", "[V2]": "1"}),
    # `backtick` нэр (хашилттай нь), SQL-ийн '' escape бүтэн литерал болно
    ("What is the `Free Meal Count (K-12)` in Fresno?", "SELECT `Free Meal Count (K-12)` FROM f WHERE c = 'Fresno'",
     "What is the [V1] in [V2]?", {"[V1]": "`Free Meal Count (K-12)`", "[V2]": "Fresno"}),
    ("How many posts by O'Brien?", "SELECT COUNT(*) FROM posts WHERE owner = 'O''Brien'", "How many posts by [V1]?",
     {"[V1]": "O'Brien"}),
    # урт нь эхэлж (давхцсан литерал), дугаар = текст дэх анхны байрлал
    ("Users from Sao Paulo, SP and SP Brazil", "SELECT * FROM u WHERE city = 'Sao Paulo' AND state = 'SP'",
     "Users from [V1], [V2] and [V2] Brazil", {"[V1]": "Sao Paulo", "[V2]": "SP"}),
])
def test_protect_masks_whole_tokens_once(text, sql, masked, mapping):
    got, mp = _protect(text, sql)
    assert (got, mp) == (masked, mapping)
    assert _restore(got, mp) == (text, True)                # placeholder хадгалсан «орчуулга» → эх текст


def test_protect_numbering_independent_of_hash_seed():
    """Ижил урттай литералын дугаар өмнө set-ийн эрэмбээр (PYTHONHASHSEED) өөрчлөгддөг байв."""
    text, sql = "Accounts of type A or B opened in 1995 or 1996 in Pisek.", \
        "SELECT 1 FROM a WHERE t IN ('A', 'B') AND y IN ('1995', '1996') AND d = 'Pisek'"
    code = ("import json, sys; from mn_text2sql.bird.data import _protect; "
            "print(json.dumps(_protect(sys.argv[1], sys.argv[2])))")
    outs = set()
    for seed in ("0", "1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(ROOT / "src"))
        outs.add(subprocess.run([sys.executable, "-c", code, text, sql], capture_output=True, text=True, env=env,
                                check=True).stdout)
    assert len(outs) == 1
    masked, mp = json.loads(outs.pop())
    assert masked == "Accounts of type [V1] or [V2] opened in [V3] or [V4] in [V5]."
    assert mp == {"[V1]": "A", "[V2]": "B", "[V3]": "1995", "[V4]": "1996", "[V5]": "Pisek"}


def test_restore_flags_missing_and_unknown_placeholders():
    mp = {"[V1]": "Alameda", "[V2]": "1"}
    assert _restore("[V1]-д [V2] сургууль ", mp) == ("Alameda-д 1 сургууль", True)
    assert _restore("[В1]-д [ V2 ] сургууль", mp) == ("Alameda-д 1 сургууль", True)     # кирилл В, хаалтын зай
    assert _restore("[V1]-д сургууль", mp) == ("Alameda-д сургууль", False)              # [V2] алдагдсан
    assert _restore("[V1]-д [V2] [V3]", mp) == ("Alameda-д 1 [V3]", False)               # зохиосон placeholder
    assert _restore("энгийн", {}) == ("энгийн", True)


def test_load_bird_mn_drops_unrestored_rows(tmp_path):
    cfg = load_config(overrides={"bird": {"data_dir": str(tmp_path)}})
    rows = [{"question_id": 1, "question_mn": "a", "placeholders_ok": True},
            {"question_id": 2, "question_mn": "[V1]-д", "placeholders_ok": False},
            {"question_id": 3, "question_mn": "засварласан", "placeholders_ok": False, "reviewed": True},
            {"question_id": 4, "question_mn": "хуучин мөр"}]                         # талбаргүй → сайн
    _write_jsonl(tmp_path / "bird_mn.jsonl", rows)
    stats: dict = {}
    assert [d["question_id"] for d in load_bird_mn(cfg, stats=stats)] == [1, 3, 4]
    assert stats == {"dropped_placeholders": 1}
    assert [d["question_id"] for d in load_bird_mn(cfg, reviewed_only=True, stats=stats)] == [3]
    assert stats == {"dropped_placeholders": 0}


class _SelectiveTranslator:
    """Masked текстийг «орчуулна» (placeholder хадгална); drop_word агуулсан текстэд placeholder-ийг гээнэ."""
    name = "sel"

    def __init__(self, drop_word: str):
        self.drop_word = drop_word

    def chat(self, messages, **kw):
        text = messages[-1]["content"]
        if self.drop_word in text:
            text = re.sub(r"\[V\d+\]", "", text)               # орчуулагч placeholder-ийг гээсэн
        return LLMResponse([text + " (мн)"])


class _KeepSampler:
    """sample_file-д: бүх асуултад нэг зөв гаргалт (аль мөр sampling-д орсныг бүртгэнэ)."""
    seen: list = []

    def __init__(self, *a, **k):
        self.family = "omnisql"

    def sample(self, item, variant, n, temperature, top_p, hint=None):
        _KeepSampler.seen.append(item.id)
        return [{"final_sql": "SELECT 1", "correct": True, "exec_ok": True, "item_id": item.id, "variant": variant}]


def test_translate_items_keeps_flag_and_sample_file_drops_bad_rows(tmp_path, monkeypatch):
    from mn_text2sql.training import data
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    write_items([TrainItem(id="bird-1", source="bird", engine="sqlite", gold_sql="SELECT 1 FROM s WHERE c = 'Alameda'",
                           question_en="How many schools are in Alameda?", db_id="s"),
                 TrainItem(id="bird-2", source="bird", engine="sqlite", gold_sql="SELECT 1 FROM s WHERE c = 'Kern'",
                           question_en="Which schools are in Kern county?", db_id="s",
                           evidence_en="Kern refers to County = 'Kern'")], d / "bird_selected.jsonl")
    monkeypatch.setattr("mn_text2sql.llm.client.make_llm", lambda llm_cfg: _SelectiveTranslator("county"))
    rep = data.translate_items(load_config(), tcfg, workers=1)
    assert rep["translated"] == 2 and rep["placeholders_bad"] == 1
    items = {it.id: it for it in read_items(d / "bird_mn.jsonl")}
    assert items["bird-1"].extra["placeholders_ok"] is True and items["bird-1"].question_mn.startswith("How many")
    assert "Alameda" in items["bird-1"].question_mn and "[V" not in items["bird-1"].question_mn
    assert items["bird-2"].extra["placeholders_ok"] is False and not items["bird-2"].placeholders_ok
    _KeepSampler.seen = []
    monkeypatch.setattr(sampling, "Sampler", _KeepSampler)
    rep = sampling.sample_file(load_config(), tcfg, MODEL, "bird_mn.jsonl")
    assert _KeepSampler.seen == ["bird-1"] and rep["dropped_placeholders"] == 1 and rep["questions"] == 1


# =========================================================================== L1: dev/test-тэй индекс, C3-ийн загвар

def _index(path: Path, meta: dict, ids: list[str]) -> str:
    path.mkdir(parents=True)
    (path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (path / "examples.json").write_text(json.dumps([{"id": i, "question_mn": "q", "sql": "SELECT 1"} for i in ids]),
                                        encoding="utf-8")
    return str(path)


def _dev_id(cfg) -> str:
    return json.loads(resolve_path(cfg["eval"]["dev_file"]).read_text(encoding="utf-8").splitlines()[0])["id"]


def test_training_refuses_index_with_dev_or_test_questions(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    cfg = load_config()
    flagged = _index(tmp_path / "plus_dev", {"include_dev": True, "examples": 1}, ["AX03"])
    with pytest.raises(ValueError, match="include_dev"):
        sampling.Sampler(cfg, MODEL, "rag", llm=_NoLLM(), index_dir=flagged)
    dev = _dev_id(cfg)
    legacy = _index(tmp_path / "legacy", {"examples": 2}, ["AX03", dev])     # include_dev талбаргүй хуучин индекс
    with pytest.raises(ValueError, match=re.escape(dev)):
        sampling.Sampler(cfg, MODEL, "h4s", llm=_NoLLM(), index_dir=legacy)
    # build (Renderer), grpo (grpo_sampler), sample/cot — бүгд Sampler-ээр дамжина
    with pytest.raises(ValueError, match="include_dev"):
        formats.Renderer(cfg, MODEL, "h4s", index_dir=flagged)
    tcfg = copy.deepcopy(load_training_cfg())
    tcfg["recipes"]["gx"] = {"kind": "grpo", "prompt": "rag", "index_dir": flagged}
    with pytest.raises(ValueError, match="include_dev"):
        grpo.grpo_sampler(cfg, tcfg, "gx", MODEL)
    # dev-гүй индекс, retrieval-гүй (plain) prompt, байхгүй индекс — зөвшөөрнө
    sampling.Sampler(cfg, MODEL, "rag", llm=_NoLLM(), index_dir=_index(tmp_path / "clean", {"include_dev": False},
                                                                        ["AX03"]))
    sampling.Sampler(cfg, MODEL, "plain", llm=_NoLLM(), index_dir=flagged)
    sampling.Sampler(cfg, MODEL, "rag", llm=_NoLLM(), index_dir=str(tmp_path / "missing"))


def _old_jobs(templates, values, rng, per, cap):
    """Засварын өмнөх synthesize-ийн ажлын жагсаалт (base_hash-гүй лавлагаа)."""
    jobs = []
    for t in templates:
        for s in synth.sql_variants(t.gold_sql, values, rng, per):
            jobs.append((t, s, None))
    rng.shuffle(jobs)
    return jobs[: cap * 2]


def test_template_jobs_identical_except_excluded_templates():
    templates = human_items(load_config())
    old = _old_jobs(templates, {}, random.Random(42), 6, 3000)
    new = synth.template_jobs(templates, {}, random.Random(42), 6, 3000)
    assert old and [(t.id, s) for t, s, _ in new] == [(t.id, s) for t, s, _ in old]
    drop = {old[0][0].id, old[5][0].id}
    cut = synth.template_jobs(templates, {}, random.Random(42), 6, 3000, excluded=drop)
    assert [(t.id, s) for t, s, _ in cut] == [(t.id, s) for t, s, _ in old if t.id not in drop]
    assert len(cut) < len(new)


def test_template_exclusions_use_dedup_rule(monkeypatch):
    from mn_text2sql.training import data
    seen = {}

    def fake_dedup(cfg, items, threshold, use_embeddings=True):
        seen.update(n=len(items), threshold=threshold, emb=use_embeddings)
        return items[1:], [{"id": items[0].id, "eval_id": "D038", "sim": 0.81}]
    monkeypatch.setattr(data, "dedup_against_eval", fake_dedup)
    tcfg = load_training_cfg()
    templates = human_items(load_config())
    got = synth.template_exclusions(load_config(), tcfg, templates, use_embeddings=False)
    assert got == {templates[0].id: {"id": templates[0].id, "eval_id": "D038", "sim": 0.81}}
    assert seen == {"n": len(templates), "threshold": float(tcfg["data"]["dedup_threshold"]), "emb": False}


def test_synthesize_skips_excluded_templates_and_inherits_order(tmp_path, monkeypatch):
    from mn_text2sql.llm.client import MockLLM
    keep = TrainItem(id="human-A1", source="human", engine="postgres", question_mn="салбараар зээл",
                     gold_sql="SELECT branch_code, COUNT(*) FROM t2s.loans WHERE disbursement_date >= '2025-01-01' "
                              "GROUP BY 1 ORDER BY 2 DESC LIMIT 5", extra={"family": "F1", "order_matters": True})
    leak = TrainItem(id="human-B2", source="human", engine="postgres", question_mn="их дүнтэй зээл",
                     gold_sql="SELECT COUNT(*) FROM t2s.loans WHERE principal_amount > 1000000 LIMIT 10",
                     extra={"family": "F2"})

    class FakeEx:
        def __init__(self, *a, **k):
            pass

        def run(self, sql, max_rows=None):
            return ExecResult(ok=True, columns=["c"], rows=[(zlib.crc32(sql.encode()) % 997 + 1,)], rowcount=1)

        def close(self):
            pass
    seen = {}
    monkeypatch.setattr(synth, "human_items", lambda cfg: [keep, leak])
    monkeypatch.setattr(synth, "template_exclusions",
                        lambda cfg, tcfg, templates, use_embeddings=True: (seen.update(emb=use_embeddings),
                                                                           {"human-B2": {"id": "human-B2"}})[1])
    monkeypatch.setattr(synth, "_values_by_column", lambda cfg: {})
    monkeypatch.setattr(synth, "bot_dsn", lambda cfg: "dsn")
    monkeypatch.setattr(synth, "Executor", FakeEx)
    monkeypatch.setattr(synth, "make_guard", lambda cfg: SimpleNamespace(check=lambda s: SimpleNamespace(ok=True, sql=s)))
    llm = MockLLM(lambda m: '{"mn": "шинэ асуулт", "en": "new question"}')
    rep = synth.synthesize(load_config(), _tmp_tcfg(tmp_path), llm=llm, workers=2, use_embeddings=False)
    items = read_items(Path(rep["file"]))
    assert items and rep["kept"] == len(items) and rep["templates_excluded"] == ["human-B2"] and seen == {"emb": False}
    assert {it.extra["template"] for it in items} == {"human-A1"}                     # eval-тэй давхардсан загвар алга
    assert all(it.order_matters and it.extra["order_matters"] is True for it in items)   # L10: ORDER BY өвлөнө


# =========================================================================== L2: seed, GRPO-ийн эрэмбэ, t2s_meta

class _SeedLLM:
    name = "seed"

    def __init__(self):
        self.seeds: list = []

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None, prefill=None):
        self.seeds.append(seed)
        return LLMResponse([(prefill or "") + " x </think>\n<answer>\n```sql\nSELECT 1\n```\n</answer>"] * n, 1, 1)


HUMAN = TrainItem(id="human-AX03", source="human", engine="postgres", gold_sql=GOLD, question_mn="хэдэн зээл?",
                  extra={"family": "AX-f03"})
OTHER = TrainItem(id="synth-x", source="own_synth", engine="postgres", gold_sql=GOLD, question_mn="өөр асуулт",
                  extra={"family": "BX-f01"})


def _seeded_sampler(model=MODEL, llm=None):
    s = sampling.Sampler(load_config(), model, "plain", llm=llm or _SeedLLM())
    s._pipe = _FakePipe()
    return s


def test_sampler_sends_deterministic_per_item_seed(monkeypatch):
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    s = _seeded_sampler()
    first = s.sample(HUMAN, "mn", 2, 0.8, 0.95)
    s.sample(HUMAN, "mn", 2, 0.8, 0.95)                          # ижил асуулт дахин (C2-ийн 2-р оролдлого) — өөр seed
    s.sample(HUMAN, "mn", 2, 0.8, 0.95, hint=GOLD)
    s.sample(OTHER, "latin", 2, 0.8, 0.95)
    want = [sampling.sample_seed("human-AX03", "mn", "free", 0), sampling.sample_seed("human-AX03", "mn", "free", 1),
            sampling.sample_seed("human-AX03", "mn", "hint", 0), sampling.sample_seed("synth-x", "latin", "free", 0)]
    assert s.llm.seeds == want and len(set(want)) == 4 and all(0 <= x <= sampling.SEED_MASK for x in want)
    assert [o["seed"] for o in first] == [want[0]] * 2
    s2 = _seeded_sampler()                                       # шинэ процесс шиг — ижил seed
    s2.sample(HUMAN, "mn", 2, 0.8, 0.95)
    assert s2.llm.seeds == want[:1]
    assert want[0] == 1549476466                                 # sha256 — PYTHONHASHSEED/платформоос үл хамаарна


def test_sqrl_trajectories_get_item_seed_plus_index(monkeypatch):
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    s = _seeded_sampler("sqrl-9b", _NoLLM())
    out = s.sample(HUMAN, "mn", 3, 0.8, 0.95)
    base = sampling.sample_seed("human-AX03", "mn", "free", 0)
    assert s._pipe.agent_seeds == [base, base + 1, base + 2] == [o["seed"] for o in out]   # өмнө бүх мөрд 0, 1, 2


def test_bird_generate_seed_only_when_given():
    from mn_text2sql.bird.runner import bird_generate
    msgs = [{"role": "user", "content": "q"}]
    llm = _SeedLLM()
    bird_generate(None, msgs, PREFILL, "omnisql", llm, 64, SimpleNamespace(agent_max_steps=0), 0.8, 0.95, 4, seed=77)
    assert llm.seeds == [77]
    llm = _SeedLLM()
    bird_generate(None, msgs, PREFILL, "omnisql", llm, 64, SimpleNamespace(agent_max_steps=0))   # үнэлгээ: seed-гүй
    assert llm.seeds == [None]

    class SqrlLLM(_SeedLLM):
        def chat(self, messages, **kw):
            self.seeds.append(kw.get("seed"))
            return LLMResponse(["<answer>SELECT 1</answer>"], 1, 1)
    llm = SqrlLLM()
    outs = bird_generate(None, msgs, None, "sqrl", llm, 64, SimpleNamespace(agent_max_steps=0), 0.8, 0.95, 3, seed=5)
    assert llm.seeds == [5, 6, 7] and [o["sql"] for o in outs] == ["SELECT 1"] * 3


def _grpo_fixture(tmp_path: Path) -> dict:
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    _write_jsonl(d / "human.jsonl", [{"id": f"human-H{i}", "source": "human", "engine": "postgres", "gold_sql": GOLD,
                                      "question_mn": f"асуулт {i}",
                                      "extra": {"family": f"F{i}", **({"order_matters": True} if i == 2 else {})}}
                                     for i in range(8)])
    for name, ids in (("human", range(0, 4)), ("own_synth_clean", range(4, 8))):
        _write_jsonl(d / "samples" / f"{MODEL}__plain__{name}.stats.jsonl",
                     [{"item_id": f"human-H{i}", "variant": "mn", "n": 8, "n_correct": 3} for i in ids])
    tcfg["recipes"]["gq"] = {"kind": "grpo", "init": "best_sft", "prompt": "plain", "n_questions": 5}   # L9: жорын утга
    return tcfg


def test_grpo_selection_independent_of_glob_order_and_recipe_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    tcfg = _grpo_fixture(tmp_path)
    s = sampling.Sampler(load_config(), MODEL, "plain", llm=_NoLLM())
    s._pipe = _FakePipe()
    rows = grpo.build_grpo_dataset(load_config(), tcfg, "gq", MODEL, sampler=s, tokenizer=_Tok())
    assert len(rows) == 5                                        # n_questions: 5 (өмнө grpo_defaults-ийн 2000)
    assert all(r["order_matters"] is (r["qid"] == "human-H2") for r in rows)
    orig_glob = Path.glob
    monkeypatch.setattr(Path, "glob", lambda self, pat: list(reversed(sorted(orig_glob(self, pat)))))
    again = grpo.build_grpo_dataset(load_config(), tcfg, "gq", MODEL, sampler=s, tokenizer=_Tok())
    assert [r["qid"] for r in again] == [r["qid"] for r in rows]


def _sft_helpers():
    spec = importlib.util.spec_from_file_location("t2s_sft_test_helpers", ROOT / "tests" / "test_training_fixes_sft.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sft_meta_records_model_revision_and_sampling_settings(tmp_path, monkeypatch):
    h = _sft_helpers()
    calls: dict = {}
    h._install_fake_libs(monkeypatch, calls)
    monkeypatch.setenv("T2S_MODEL_REVISION", "0123abcd")
    tcfg = load_training_cfg()
    train = h._rows(tmp_path / "c3.jsonl", 2)
    sft.run_sft(load_config(), tcfg, "c3", MODEL, data_file=str(train), output_dir=str(tmp_path / "out"))
    meta = json.loads((tmp_path / "out" / "t2s_meta.json").read_text(encoding="utf-8"))
    hf_id = load_models()["models"][MODEL]["hf_id"]
    assert meta["model_revision"] == {"hf_id": hf_id, "revision": "0123abcd"}
    assert meta["sampling"]["sampling"] == tcfg["sampling"] and meta["sampling"]["cot"] == tcfg["cot"]
    assert "seed" in meta["sampling"]


def test_model_revision_fallback_without_eval_imports(monkeypatch):
    from mn_text2sql.training import provenance
    monkeypatch.setitem(sys.modules, "mn_text2sql.eval.runner", None)         # psycopg2-гүй орчин шиг импорт алдаа
    monkeypatch.setenv("T2S_MODEL_REVISION", "feedbeef")
    got = provenance.model_revision({"hf_id": "x/y", "revision": "main"})
    assert got["hf_id"] == "x/y" and got["revision"] == "feedbeef" and "error" in got
    monkeypatch.delenv("T2S_MODEL_REVISION")
    assert provenance.model_revision({"hf_id": "x/y", "revision": "main"})["revision"] == "main"


# =========================================================================== L7: cot --tool

class _CotSampler:
    family = "sqrl"

    def sample(self, item, variant, n, temperature, top_p, hint=None):
        raw = "<think> count closed loans </think>\n<answer>SELECT 1</answer>"
        return [{"final_sql": "SELECT 1", "correct": True, "exec_ok": True, "raw_turns": [raw], "train_messages": [],
                 "conversation": [], "prefill": None, "item_id": item.id, "variant": variant, "engine": item.engine,
                 "family": "sqrl"}]


def test_cot_tool_uses_sample_naming_and_is_collected(tmp_path, monkeypatch):
    tcfg = _tmp_tcfg(tmp_path)
    d = Path(tcfg["data"]["dir"])
    _write_jsonl(d / "bird_mn.jsonl", [{"id": "q0", "source": "bird", "engine": "sqlite", "gold_sql": GOLD,
                                        "question_mn": "асуулт"}])
    tag = sampling.sample_tag("sqrl-9b", "rag", "bird_mn.jsonl", tool=True)
    assert tag == "sqrl-9b__rag__tool__bird_mn" and sampling.sample_tag(MODEL, "plain", "x.jsonl", variants=["en"]) == \
        f"{MODEL}__plain__x__en"
    _write_jsonl(d / "samples" / f"{tag}.stats.jsonl", [{"item_id": "q0", "variant": "mn", "n": 8, "n_correct": 0}])
    seen = {}
    monkeypatch.setattr(sampling, "Sampler", lambda *a, **k: (seen.update(k), _CotSampler())[1])
    with pytest.raises(FileNotFoundError, match="cot --tool"):     # өмнөх шиг __tool-гүй нэрээр хайвал олдохгүй
        sampling.cot_file(load_config(), tcfg, "sqrl-9b", "bird_mn.jsonl", "rag")
    tot = sampling.cot_file(load_config(), tcfg, "sqrl-9b", "bird_mn.jsonl", "rag", tool=True, pivot=True)
    assert Path(tot["file"]).name == f"{tag}__cot.jsonl" and tot["recovered"] == 1
    assert seen["tool"] is True and seen["pivot"] is True
    got = formats.collect_samples(tcfg, "sqrl-9b", "rag", ["rejection", "cot"], tool=True)
    assert [(s["item_id"], s["cot"]) for s in got] == [("q0", True)]


def test_cli_cot_tool_pivot_and_synth_no_embed(monkeypatch):
    from mn_text2sql.training import cli
    seen = {}
    monkeypatch.setattr(sampling, "cot_file", lambda *a, **k: seen.setdefault("cot", (a, k)) and {})
    monkeypatch.setattr(synth, "synthesize", lambda *a, **k: seen.setdefault("synth", (a, k)) and {})
    assert cli.main(["cot", "--model", "sqrl-9b", "--source", "bird_mn.jsonl", "--prompt", "rag", "--tool",
                     "--pivot"]) == 0
    assert seen["cot"][1] == {"index_dir": None, "tool": True, "pivot": True}
    seen.clear()
    cli.main(["cot", "--model", MODEL, "--source", "s.jsonl"])
    assert seen["cot"][1] == {"index_dir": None}                 # анхдагч дуудлага хэвээр
    cli.main(["synth", "--no-embed"])
    assert seen["synth"][1]["use_embeddings"] is False
    seen.clear()
    cli.main(["synth"])
    assert seen["synth"][1]["use_embeddings"] is True


# =========================================================================== L9: GRPO-ийн жорын тохиргоо

def test_grpo_params_current_recipes_unchanged_and_overrides_merge():
    tcfg = load_training_cfg()
    grpo_recipes = [r for r, spec in tcfg["recipes"].items() if spec.get("kind") == "grpo"]
    assert grpo_recipes
    for r in grpo_recipes:                                       # одоогийн жорд дарах түлхүүр алга — өөрчлөлтгүй
        assert grpo.grpo_params(tcfg, r) == tcfg["grpo_defaults"]
    t2 = copy.deepcopy(tcfg)
    t2["recipes"]["gx"] = {"kind": "grpo", "learning_rate": 5e-7, "num_generations": 4,
                           "reward": {"executable": 0.0}, "desc": "x"}
    gp = grpo.grpo_params(t2, "gx")
    assert gp["learning_rate"] == 5e-7 and gp["num_generations"] == 4 and "desc" not in gp
    assert gp["reward"] == {"correct": 1.0, "executable": 0.0, "error": 0.0}
    assert t2["grpo_defaults"]["reward"]["executable"] == 0.1    # defaults хөндөгдөхгүй


def test_run_grpo_applies_recipe_overrides_and_records_revision(tmp_path, monkeypatch):
    import dataclasses
    import types
    calls: dict = {}
    datasets = types.ModuleType("datasets")
    datasets.Dataset = SimpleNamespace(from_list=lambda rows: list(rows))
    peft = types.ModuleType("peft")
    peft.LoraConfig = lambda **kw: SimpleNamespace(**kw)
    trl = types.ModuleType("trl")

    @dataclasses.dataclass
    class GRPOConfig:
        output_dir: str = ""
        learning_rate: float = 1e-6
        beta: float = 0.0
        num_generations: int = 8
        temperature: float = 1.0
        max_prompt_length: int = 512
        max_completion_length: int = 256
        per_device_train_batch_size: int = 8
        gradient_accumulation_steps: int = 1
        num_train_epochs: float = 1.0
        bf16: bool = False
        gradient_checkpointing: bool = False
        use_vllm: bool = False
        vllm_mode: str = "server"
        vllm_gpu_memory_utilization: float = 0.3
        logging_steps: int = 10
        save_steps: int = 500
        save_total_limit: int | None = None
        report_to: str = "all"
        seed: int = 0
        max_steps: int = -1
        shuffle_dataset: bool = True
        generation_batch_size: int | None = None

    class GRPOTrainer:
        def __init__(self, model=None, reward_funcs=None, args=None, train_dataset=None, peft_config=None):
            calls.update(args=args, reward=reward_funcs[0])

        def train(self, resume_from_checkpoint=None):
            pass

        def save_model(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)
    trl.GRPOConfig, trl.GRPOTrainer = GRPOConfig, GRPOTrainer
    for name, mod in {"datasets": datasets, "peft": peft, "trl": trl}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delenv("T2S_SAVE_STEPS", raising=False)
    monkeypatch.setenv("T2S_MODEL_REVISION", "c0ffee")
    monkeypatch.setattr(grpo, "grpo_sampler", lambda *a: SimpleNamespace(pipe=object(), opts=SimpleNamespace(
        dialect="sqlite_transpile")))
    rows = [{"prompt": "p", "engine": "sqlite", "db_path": "", "gold_sql": "SELECT 1", "qid": "q1", "variant": "mn",
             "order_matters": False}]
    monkeypatch.setattr(grpo, "build_grpo_dataset", lambda *a, **k: rows)
    tcfg = copy.deepcopy(load_training_cfg())
    tcfg["recipes"]["gx"] = {"kind": "grpo", "init": "best_sft", "prompt": "plain", "learning_rate": 5e-7,
                             "reward": {"executable": 0.0}}
    grpo.run_grpo(load_config(), tcfg, "gx", MODEL, output_dir=str(tmp_path / "gx"))
    assert calls["args"].learning_rate == 5e-7                   # өмнө grpo_defaults-ийн 1e-6 (чимээгүй)
    assert calls["reward"].w == {"correct": 1.0, "executable": 0.0, "error": 0.0}
    meta = json.loads((tmp_path / "gx" / "t2s_meta.json").read_text(encoding="utf-8"))
    assert meta["hparams"]["learning_rate"] == 5e-7
    assert meta["model_revision"] == {"hf_id": load_models()["models"][MODEL]["hf_id"], "revision": "c0ffee"}


# =========================================================================== L10: order_matters + код↔нэр

class _PG:
    """Хуурамч executor: gold SQL → үр дүн; maps=True бол build_code_maps-ийн асуулгад status_code ↔ status_name."""

    def __init__(self, results: dict, maps: bool = False):
        self.results, self.maps, self.calls = results, maps, []

    def run(self, sql, max_rows=None):
        self.calls.append(sql)
        if "information_schema.columns" in sql:
            rows = [("loans", "status_code", "status_name")] if self.maps else []
            return ExecResult(ok=True, columns=["t", "c", "n"], rows=rows, rowcount=len(rows))
        if sql.startswith('SELECT DISTINCT "status_code"'):
            rows = [("CLOSED", "Хаагдсан"), ("ACTIVE", "Идэвхтэй")]
            return ExecResult(ok=True, columns=["c", "n"], rows=rows, rowcount=2)
        return self.results[sql]


def _pipe(ex, by_sql: dict | None = None):
    return SimpleNamespace(guard=SimpleNamespace(check=lambda s: SimpleNamespace(ok=True, sql=s)), executor=ex,
                           cfg=load_config(), process_sql=lambda sql: (by_sql or {})[sql])


def _cand(rows, cols=("branch", "n")):
    return SimpleNamespace(ok=True, result=ExecResult(ok=True, columns=list(cols), rows=list(rows), rowcount=len(rows)))


ORDERED = "SELECT branch, n FROM t2s.x ORDER BY n DESC"


def test_pg_correct_uses_order_matters_and_value_maps_like_eval():
    lock = threading.Lock()
    ex = _PG({ORDERED: ExecResult(ok=True, columns=["branch", "n"], rows=[("A", 3), ("B", 2)], rowcount=2)})
    rev = _cand([("B", 2), ("A", 3)])
    assert sampling.pg_correct(_pipe(ex), ORDERED, rev, {}, lock)                 # анхдагч: дараалалгүй (өмнөх)
    assert not sampling.pg_correct(_pipe(ex), ORDERED, rev, {}, lock, True)      # order_matters (үнэлгээ шиг)
    assert sampling.pg_correct(_pipe(ex), ORDERED, _cand([("A", 3), ("B", 2)]), {}, lock, True)
    gold = "SELECT status_code, COUNT(*) FROM t2s.loans GROUP BY 1"
    res = ExecResult(ok=True, columns=["status_code", "count"], rows=[("CLOSED", 5), ("ACTIVE", 7)], rowcount=2)
    by_name = _cand([("Хаагдсан", 5), ("Идэвхтэй", 7)], ("status_name", "count"))
    assert not sampling.pg_correct(_pipe(_PG({gold: res})), gold, by_name, {}, lock)      # map-гүй DB
    ex2, cache = _PG({gold: res}, maps=True), {}
    assert sampling.pg_correct(_pipe(ex2), gold, by_name, cache, lock)          # код ↔ нэр (eval/runner._match)
    assert sampling.pg_correct(_pipe(ex2), gold, by_name, cache, lock)
    assert sum("information_schema" in c for c in ex2.calls) == 1 and sum(c == gold for c in ex2.calls) == 1   # кэш


def test_sampler_and_grpo_reward_pass_item_order_matters(monkeypatch):
    from mn_text2sql.training.grpo import ExecReward
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    ex = _PG({ORDERED: ExecResult(ok=True, columns=["branch", "n"], rows=[("A", 3), ("B", 2)], rowcount=2)})
    pipe = _pipe(ex, {"SELECT rev": _cand([("B", 2), ("A", 3)])})
    reward = ExecReward(load_config(), "omnisql", {"correct": 1.0, "executable": 0.1, "error": 0.0}, pipe=pipe)
    comp = " ok</think>\n<answer>\n```sql\nSELECT rev\n```\n</answer>"
    kw = dict(prompts=["p"] * 2, completions=[comp] * 2, engine=["postgres"] * 2, db_path=[""] * 2,
              gold_sql=[ORDERED] * 2)
    assert reward(**kw, order_matters=[False, True]) == [1.0, 0.1]
    assert reward(**kw) == [1.0, 1.0]                            # багана алга (хуучин dataset) — өмнөх дүрэм
    s = sampling.Sampler(load_config(), MODEL, "plain", llm=_NoLLM())
    s._pipe = pipe
    ordered_item = TrainItem(id="o", source="human", engine="postgres", gold_sql=ORDERED, extra={"order_matters": True})
    plain_item = TrainItem(id="p", source="human", engine="postgres", gold_sql=ORDERED)
    rev = pipe.process_sql("SELECT rev")
    assert not s._correct_pg(ordered_item, rev) and s._correct_pg(plain_item, rev)


def test_human_items_carry_order_matters():
    cfg = load_config()
    raw = [json.loads(x) for x in resolve_path(cfg["knowledge"]["examples"]).read_text(encoding="utf-8").splitlines()
           if x.strip()]
    want = {f"human-{d['id']}" for d in raw if d.get("gold_sql") and d.get("order_matters")}
    items = human_items(cfg)
    assert want and {it.id for it in items if it.order_matters} == want
    assert all("order_matters" not in it.extra for it in items if it.id not in want)    # бусад мөр хэвээр
