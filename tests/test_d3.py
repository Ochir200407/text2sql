"""D3: prompt-ын нэг эх сурвалж (Pipeline.prompt_for), сургалтын h4s prompt, d3 жор, BIRD-ийн дэд олонлог,
pack_train_d3 (детерминист, eval-гүй), QLoRA-гийн CPU-д шалгагдах хэсэг. GPU-гүй."""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from mn_text2sql.config import load_config
from mn_text2sql.pipeline.options import load_experiments, resolve_run
from mn_text2sql.training.data import TrainItem, load_training_cfg
from mn_text2sql.training.formats import (
    bird_subset,
    est_sample_tokens,
    is_truncated,
    recipe_max_length,
    sample_key,
    select_bird_samples,
)
from mn_text2sql.training.sft import last_checkpoint, quant_device_map, shift_targets

ROOT = Path(__file__).resolve().parents[1]
VARS = {"best_a": "A7", "best_sft": "C3", "best_sft_adapter": "c3", "best_config": "H4"}
spec = importlib.util.spec_from_file_location("pack_train_d3", ROOT / "scripts" / "notebook" / "pack_train_d3.py")
pack_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack_mod)


def _examples() -> dict[str, dict]:
    rows = [json.loads(x) for x in (ROOT / "knowledge" / "examples.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]
    return {r["id"]: r for r in rows}


# --------------------------------------------------------------------------- тохиргоо (GPU-гүй, DB-гүй)

def test_existing_fingerprints_unchanged():
    """H4s/D3s/I3s3 нэмсэн ч өмнөх run-уудын fingerprint (үргэлжлүүлэх hash) хөдлөхгүй (J1-ийг 2026-10-18-нд
    glossary_file-аар зориуд өөрчилсөн тул энд оруулаагүй)."""
    want = {"A7": "c352de91d0421dff", "B5": "8b2955924441ff5b", "D2": "0cf21876dd6ef26b", "H4": "7e3fec674464c4a1",
            "I3": "1185ff9165aabb5f", "E2": "67397c00f6328d57"}
    exp = load_experiments()
    got = {rid: hashlib.sha1(json.dumps(resolve_run(rid, exp, var_overrides=VARS).fingerprint(), sort_keys=True,
                                        default=str).encode()).hexdigest()[:16] for rid in want}
    assert got == want


def test_d3_inference_configs():
    exp = load_experiments()
    h4, h4s = resolve_run("H4", exp, var_overrides=VARS), resolve_run("H4s", exp, var_overrides=VARS)
    d3s, i3s3 = resolve_run("D3s", exp, var_overrides=VARS), resolve_run("I3s3", exp, var_overrides=VARS)
    assert h4s.schema_linking and not h4.schema_linking
    diff = {k for k, v in h4s.fingerprint().items() if h4.fingerprint().get(k) != v}
    assert diff == {"schema_linking", "index_dir"}       # tester M1: жишээний сан сургалттай ижил (build/index_plus)
    assert h4s.index_dir == "build/index_plus"
    assert d3s.adapter == "d3" and d3s.translate_mode == "en_prompt" and d3s.index_dir == "build/index_plus"
    assert i3s3.adapter == "d3" and i3s3.index_dir == "build/index_plus_dev" and i3s3.schema_linking
    i3s3t = resolve_run("I3s3t", exp, var_overrides=VARS)
    assert i3s3t.adapter == "d3" and i3s3t.index_dir == "build/index_plus_dev" and i3s3t.schema_linking
    assert i3s3t.translation_cache == "qwen3.5-9b.fix" == resolve_run("I3t", exp, var_overrides=VARS).translation_cache


def test_d3_recipe_parses(monkeypatch):
    monkeypatch.delenv("T2S_BEST_A", raising=False)
    tcfg = load_training_cfg()
    d3 = tcfg["recipes"]["d3"]
    assert d3["kind"] == "sft" and d3["prompt"] == "h4s" and d3["bird_limit"] == 600 and d3["drop_truncated"]
    assert d3["data"] == ["rejection", "cot", "synth", "human"] and d3["bird_prompt"] == "stored"
    assert d3["bird_max_tokens"] == 8192        # BIRD-ийн сонголт (pack-ийн SHA) max_length-аас үл хамаарна
    # run_sft-ийн hparam нэгтгэлт: зөвхөн sft_defaults-д байгаа түлхүүрийг жороос авна
    hp = dict(tcfg["sft_defaults"])
    hp.update({k: v for k, v in d3.items() if k in hp})
    assert (hp["max_length"], hp["lora_r"], hp["lora_alpha"], hp["epochs"]) == (9216, 32, 64, 2)
    assert (hp["per_device_batch_size"], hp["gradient_accumulation"], float(hp["learning_rate"])) == (1, 8, 2e-4)
    assert hp["quantize_4bit"] and hp["fp16"] and not hp["bf16"] and hp["gradient_checkpointing"]
    assert hp["completion_logits_only"]
    # бусад жор (A100) хэвээр: bf16, 4 битгүй
    for name in ("c3", "d2"):
        hp2 = dict(tcfg["sft_defaults"])
        hp2.update({k: v for k, v in tcfg["recipes"][name].items() if k in hp2})
        assert hp2["bf16"] and not hp2["fp16"] and not hp2["quantize_4bit"] and not hp2["completion_logits_only"]
    assert recipe_max_length(tcfg, "d3") == 9216 and recipe_max_length(tcfg, "c3") == 8192
    from mn_text2sql.training.sampling import run_options
    o = run_options("h4s", index_dir=d3["index_dir"])
    assert o.run_id == "H4s" and o.schema_linking and o.translate and o.translate_mode == "en_prompt"
    assert o.dialect == "sqlite_transpile" and o.candidates == 1 and o.repair_rounds == 0
    assert o.index_dir == "build/index_plus" and o.describe and o.fewshot_k == 4
    with pytest.raises(ValueError):
        run_options("nope")


def test_qlora_cpu_helpers(tmp_path):
    assert quant_device_map(2, 1) == {"": 1} and quant_device_map(1, 0) == "auto"
    # p байрлалын hidden state нь labels[p + 1]-ийг таамаглана: prompt (-100) → completion (5, 6), сүүлийнх loss-гүй
    assert shift_targets([-100, -100, 5, 6, -100]) == [-100, 5, 6, -100, -100]
    assert [p for p, t in enumerate(shift_targets([-100, -100, 5, 6])) if t != -100] == [1, 2]
    assert shift_targets([7]) == [-100]
    out = tmp_path / "d3"
    assert last_checkpoint(out) is None
    for n, complete in ((10, True), (20, True), (30, False)):
        (out / f"checkpoint-{n}").mkdir(parents=True)
        if complete:
            (out / f"checkpoint-{n}" / "trainer_state.json").write_text("{}", encoding="utf-8")
    (out / "checkpoint-x").mkdir()
    assert last_checkpoint(out) == str(out / "checkpoint-20")   # 30 нь дутуу (Kaggle хугацаа дууссан)


# --------------------------------------------------------------------------- BIRD-ийн дэд олонлог

def _bird(i: int, n_chars: int = 400, truncated: bool = False, cot: bool = False, variant: str = "mn") -> dict:
    end = "" if truncated else "\n</answer>"
    return {"item_id": f"bird-{i}", "variant": variant, "engine": "sqlite", "family": "omnisql", "cot": cot,
            "final_sql": f"SELECT {i}", "prefill": "Let me solve this step by step.\n<think>",
            "train_messages": [{"role": "user", "content": "CREATE TABLE t (a INT);\n" + "x" * n_chars}],
            "raw_turns": ["Let me solve this step by step.\n<think> ok </think>\n<answer>\n```sql\nSELECT 1\n```" + end],
            "question_en": f"bird question {i}"}


def test_select_bird_deterministic_and_idempotent():
    pool = [_bird(i) for i in range(50)] + [_bird(100, n_chars=200_000)]    # ~50k токен: max_length-аас урт
    import random
    shuffled = pool[:]
    random.Random(1).shuffle(shuffled)
    a = select_bird_samples(pool, 10, 42, 8192)
    b = select_bird_samples(shuffled, 10, 42, 8192)
    assert [sample_key(s) for s in a] == [sample_key(s) for s in b] and len(a) == 10
    assert select_bird_samples(a, 10, 42, 8192) == a                       # pack → Kaggle-ийн build ижил
    assert all(s["item_id"] != "bird-100" for s in select_bird_samples(pool, 100, 42, 8192))
    assert est_sample_tokens(pool[-1]) > 8192
    # bird_subset: дуусаагүй ба англи хувилбарыг хязгаараас өмнө хасна
    mixed = [_bird(i, truncated=i % 2 == 0) for i in range(20)] + [_bird(99, variant="en")]
    chosen, n = bird_subset(mixed, {"bird_limit": 5, "drop_truncated": True}, 42, 8192)
    assert n == 10 and len(chosen) == 5 and not any(is_truncated(s) for s in chosen)
    # bird_max_tokens нь max_length-ийг уртын шүүлтэд орлоно (жорын max_length өсөхөд BIRD-ийн сонголт хэвээр)
    longish = [_bird(i) for i in range(5)] + [_bird(77, n_chars=26_000)]          # ≈ 6.5k «токен»
    assert len(bird_subset(longish, {"bird_limit": None}, 42, 9216)[0]) == 6
    assert len(bird_subset(longish, {"bird_limit": None, "bird_max_tokens": 6000}, 42, 9216)[0]) == 5
    assert not is_truncated(dict(_bird(1, truncated=True), family="sqrl"))


def test_render_ddl_describe_columns_keeps_all_columns():
    """Schema linking (H4s): холбогдсон хүснэгтийн БҮХ багана DDL-д, тайлбар зөвхөн сонгосон баганад."""
    from mn_text2sql.knowledge.semantic import cached_model
    from mn_text2sql.llm.prompts import render_ddl
    cfg = load_config()
    model = cached_model(cfg["knowledge"]["semantic_model"])
    t = "loans" if "loans" in model.tables else next(iter(model.tables))
    cols = [c for c, col in model.tables[t].columns.items() if not col.fk and (col.mn or col.en)]
    keep = cols[0]
    full = render_ddl(model, [t], {}, {}, True)
    part = render_ddl(model, [t], {}, {}, True, describe_columns={t: {keep}})
    for c in model.tables[t].columns:                       # бүх багана хоёуланд нь зарлагдсан
        assert f"\n  {c} " in full and f"\n  {c} " in part
    commented = [ln.split()[0] for ln in part.splitlines() if "  -- " in ln and not ln.startswith("--")]
    assert commented == [keep] and len(part) < len(full)
    assert render_ddl(model, [t], {}, {}, True, describe_columns={}) == full     # тухайн хүснэгт жагсаалтад алга


# --------------------------------------------------------------------------- pack_train_d3 (DB-гүй)

def _eval_rows():
    cfg = load_config()
    dev = [json.loads(x) for x in open(ROOT / cfg["eval"]["dev_file"], encoding="utf-8") if x.strip()]
    test = [json.loads(x) for x in open(ROOT / cfg["eval"]["test_file"], encoding="utf-8") if x.strip()]
    return dev, test


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _own_sample(item_id: str, cot: bool = False, truncated: bool = False) -> dict:
    s = _bird(0, cot=cot, truncated=truncated)
    s.update(item_id=item_id, engine="postgres", final_sql=f"SELECT '{item_id}'", question_en=f"q {item_id}")
    return s


def _fake_train_dir(root: Path) -> Path:
    dev, test = _eval_rows()
    ex = _examples()
    ax = ex["AX03"]
    src = root / "train"
    human = [{"id": "human-AX03", "source": "human", "engine": "postgres", "gold_sql": ax["gold_sql"],
              "question_mn": ax["question_mn"], "question_en": ax["question_en"], "extra": {"family": ax["family"]}},
             # eval-ийн id-тай (хуурамч) ба eval-ийн асуулттай мөр — хасагдах ёстой
             {"id": f"human-{dev[0]['id']}", "source": "human", "engine": "postgres", "gold_sql": "SELECT 1",
              "question_mn": "өөр асуулт", "extra": {"family": "Z"}},
             {"id": "human-ZZ99", "source": "human", "engine": "postgres", "gold_sql": "SELECT 1",
              "question_mn": dev[1]["question_mn"].upper() + "  ", "extra": {"family": "Z2"}}]
    synth = [{"id": "synth-human-AX03-0123abcd", "source": "own_synth", "engine": "postgres", "gold_sql": "SELECT 2",
              "question_mn": "Дефолтын магадлал 5 хувиас өндөр скоринг хэд вэ?", "question_en": "How many > 5%?",
              "extra": {"template": "human-AX03"}},
             {"id": f"synth-human-{test[0]['id']}-89abcdef", "source": "own_synth", "engine": "postgres",
              "gold_sql": "SELECT 3", "question_mn": "асуулт", "extra": {"template": f"human-{test[0]['id']}"}}]
    _write_jsonl(src / "human.jsonl", human)
    _write_jsonl(src / "own_synth_clean.jsonl", synth)
    m = "arctic-r1-7b__plain__"
    _write_jsonl(src / "samples" / f"{m}human.jsonl", [_own_sample(h["id"]) for h in human])
    _write_jsonl(src / "samples" / f"{m}human__cot.jsonl", [_own_sample("human-AX03", cot=True),
                                                             _own_sample("human-AX03", cot=True, truncated=True)])
    _write_jsonl(src / "samples" / f"{m}own_synth_clean__cot.jsonl", [_own_sample(s["id"], cot=True) for s in synth])
    bird = [_bird(i) for i in range(12)] + [_bird(50, truncated=True)]
    bird.append(dict(_bird(77), question_en=test[1]["question_en"]))       # eval-ийн англи асуулттай BIRD мөр
    _write_jsonl(src / "samples" / f"{m}bird_mn.jsonl", bird)
    _write_jsonl(src / "samples" / f"{m}bird_mn__cot.jsonl", [_bird(i, cot=True) for i in range(200, 205)])
    _write_jsonl(src / "samples" / f"{m}bird_mn.stats.jsonl", [{"item_id": "bird-0"}])
    return src


def test_pack_train_d3_deterministic_and_eval_free(tmp_path):
    src = _fake_train_dir(tmp_path)
    tcfg = load_training_cfg()
    tcfg["recipes"]["d3"] = dict(tcfg["recipes"]["d3"], bird_limit=8)
    r1 = pack_mod.pack(src, tmp_path / "o1", tcfg=tcfg)
    r2 = pack_mod.pack(src, tmp_path / "o2", tcfg=tcfg)
    assert r1["sha256"] == r2["sha256"] and r1["parts"] == ["t2s_train_d3.zip.part00"]
    sha_line = (tmp_path / "o1" / "SHA256_train_d3.txt").read_text(encoding="utf-8")
    assert sha_line.split()[0] == r1["sha256"]
    # part-уудыг нийлүүлбэл яг zip (run_plan_l4.sh-ийн угсралт)
    parts = sorted((tmp_path / "o1").glob("t2s_train_d3.zip.part*"))
    assert hashlib.sha256(b"".join(p.read_bytes() for p in parts)).hexdigest() == r1["sha256"]
    dev, test = _eval_rows()
    eval_ids = {d["id"] for d in dev + test}
    with zipfile.ZipFile(tmp_path / "o1" / "t2s_train_d3.zip") as z:
        names = z.namelist()
        assert "data/train/D3_MANIFEST.json" in names and not any("stats" in n for n in names)
        rows = {n: [json.loads(x) for x in z.read(n).decode("utf-8").splitlines() if x.strip()]
                for n in names if n.endswith(".jsonl")}
        manifest = json.loads(z.read("data/train/D3_MANIFEST.json"))
    ids = {r.get("id") or r.get("item_id") for rs in rows.values() for r in rs}
    assert ids and not set().union(*(pack_mod.related_ids(i) for i in ids)) & eval_ids
    texts = {pack_mod.norm_text(d.get(f)) for d in dev + test for f in ("question_mn", "question_en") if d.get(f)}
    for rs in rows.values():
        for r in rs:
            assert pack_mod.norm_text(r.get("question_mn")) not in texts or not r.get("question_mn")
            assert pack_mod.norm_text(r.get("question_en")) not in texts or not r.get("question_en")
    assert {r["id"] for r in rows["data/train/human.jsonl"]} == {"human-AX03"}
    assert {r["id"] for r in rows["data/train/own_synth_clean.jsonl"]} == {"synth-human-AX03-0123abcd"}
    reasons = {(e["file"], e["id"]): e["reason"] for e in manifest["excluded"]}
    assert reasons[("human.jsonl", f"human-{dev[0]['id']}")].startswith("eval_id")
    assert reasons[("human.jsonl", "human-ZZ99")] == "eval_question"
    assert reasons[("own_synth_clean.jsonl", f"synth-human-{test[0]['id']}-89abcdef")].startswith("eval_id")
    assert reasons[("arctic-r1-7b__plain__human.jsonl", "human-ZZ99")] == "item_missing_or_excluded"
    assert reasons[("arctic-r1-7b__plain__bird_mn.jsonl", "bird-77")] == "eval_question"
    # BIRD: 12 + 5 cot бүрэн (тасарсан 1, eval 1 хасагдсан) → 8 (детерминист)
    bird_rows = rows["data/train/samples/arctic-r1-7b__plain__bird_mn.jsonl"] + \
        rows.get("data/train/samples/arctic-r1-7b__plain__bird_mn__cot.jsonl", [])
    assert manifest["bird_pool_after_filters"] == 17 and len(bird_rows) == 8
    assert not any(is_truncated(s) for s in bird_rows)


# --------------------------------------------------------------------------- DB + BM25 индекс

@pytest.fixture(scope="module")
def bm25_index(tmp_path_factory):
    """t2s index-build --no-embed --extra-examples knowledge/examples_plus.jsonl --out <tmp> (≈ index_plus)."""
    from mn_text2sql.retrieval.build import build_indexes
    out = tmp_path_factory.mktemp("index_plus")
    cfg = load_config()
    cfg["knowledge"]["index_dir"] = str(out)
    build_indexes(cfg, use_embeddings=False, extra_examples="knowledge/examples_plus.jsonl")
    return str(out)


class _CaptureLLM:
    name = "capture"

    def __init__(self):
        self.calls = []

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None):
        from mn_text2sql.llm.client import LLMResponse
        self.calls.append((list(messages), prefill))
        return LLMResponse([(prefill or "") + "</think>\n<answer>\n```sql\nSELECT 1\n```\n</answer>"] * n, 10, 10)


def _old_prompt(pipe, question, variant, item_id):
    """Өөрчлөлтөөс өмнөх ask()-ийн үүсгэлтийн өмнөх алхам (лавлагаа хувилбар)."""
    o = pipe.opts
    q, q_prompt = question, None
    if o.translate:
        en = pipe.translations.get(f"{item_id}:{variant}")
        if en and o.translate_mode == "bilingual":
            q_prompt = f"{question}\n(English translation: {en})"
        elif en and o.translate_mode == "en_prompt":
            q_prompt = en
        elif en:
            q = en
    ctx, info = pipe.build_context(q, variant, item_id)
    if q_prompt:
        ctx.question = q_prompt
    msgs, prefill = pipe.initial_messages(ctx)
    return msgs, prefill, info, q_prompt or q


def _strip_ms(info: dict) -> dict:
    return {k: v for k, v in info.items() if k != "retrieval_ms"}


@pytest.mark.db
@pytest.mark.parametrize("rid", ["A7", "H3", "H2", "H4", "H4s"])
def test_prompt_for_equals_ask_and_old_logic(rid, bm25_index):
    from mn_text2sql.pipeline.orchestrator import Pipeline
    ax = _examples()["AX03"]
    opts = resolve_run(rid, var_overrides=VARS, overrides={"index_dir": bm25_index, "intent_check": False})
    llm = _CaptureLLM()
    pipe = Pipeline(load_config(), opts, "arctic-r1-7b", llm, translations={"X1:mn": ax["question_en"]})
    pipe.item_families = {"X1": ax["family"]}
    for item_id in ("X1", None):
        msgs, prefill, ctx, info, used = pipe.prompt_for(ax["question_mn"], "mn", item_id)
        old_msgs, old_prefill, old_info, old_used = _old_prompt(pipe, ax["question_mn"], "mn", item_id)
        assert (msgs, prefill, used, _strip_ms(info)) == (old_msgs, old_prefill, old_used, _strip_ms(old_info))
        llm.calls.clear()
        ans = pipe.ask(ax["question_mn"], "mn", item_id=item_id)
        assert llm.calls[0] == (msgs, prefill)
        assert ans.question_used == used and _strip_ms(ans.retrieval) == _strip_ms(info)
        if rid in ("H4", "H4s") and item_id == "X1":
            assert ctx.question == ax["question_en"] and ax["question_en"] in msgs[-1]["content"]
            assert "AX03" not in info["examples"]              # өөрийн family хасагдсан
        if rid in ("H4", "H4s") and item_id is None:
            assert "AX03" in info["examples"]                  # хасахгүй бол өөрийгөө жишээ болгон харна


@pytest.mark.db
def test_h4s_training_render(bm25_index, monkeypatch):
    """Сургалтын h4s prompt: англи асуулт, өөрийн/эх жишээний family хасагдсан, B5/D2-ийн RAG-аас хэд дахин богино."""
    from mn_text2sql.training.formats import Renderer
    monkeypatch.setenv("T2S_BEST_A", "A7")
    cfg = load_config()
    ex = _examples()
    items = []
    for eid in ("AX03", "LX19", "PX07"):
        e = ex[eid]
        items.append(TrainItem(id=f"human-{eid}", source="human", engine="postgres", gold_sql=e["gold_sql"],
                               question_mn=e["question_mn"], question_en=e["question_en"],
                               extra={"family": e["family"], "values": e.get("values") or []}))
    synth = TrainItem(id="synth-human-AX03-0123abcd", source="own_synth", engine="postgres", gold_sql="SELECT 1",
                      question_mn="Дефолтын магадлал нь 5 хувиас өндөр гарсан скоринг хэд байна?",
                      question_en="How many credit scores have a probability of default above 5%?",
                      extra={"template": "human-AX03"})
    h4s = Renderer(cfg, "arctic-r1-7b", "h4s", index_dir=bm25_index)
    rag = Renderer(cfg, "arctic-r1-7b", "rag", index_dir=bm25_index)      # B5 — D2-ийн сургалтын prompt
    assert h4s.sampler.item_family(synth) == ex["AX03"]["family"]
    ratios = []
    for it in items + [synth]:
        msgs, prefill = h4s.messages(it, "mn")
        user = msgs[-1]["content"]
        question_part = user[user.index("\nQuestion:\n"):]
        assert it.question_en in question_part and it.question_mn not in user
        fam = h4s.sampler.item_family(it)
        _, _, ctx, info, used = h4s.sampler.pipe.prompt_for(it.question_mn, "mn", it.id)
        assert used == it.question_en and info["examples"]
        fams = {e.id: e.family for e in h4s.sampler.pipe.retriever.examples.examples}
        assert fam not in {fams[i] for i in info["examples"]}
        # schema linking нь зөвхөн хүснэгтийг шүүнэ: холбогдсон хүснэгт бүрийн БҮХ багана DDL-д зарлагдсан
        model = h4s.sampler.pipe.model
        assert ctx.columns is None and len(ctx.tables) < len(model.tables)
        for t in ctx.tables:
            assert all(f"\n  {c} " in user for c in model.tables[t].columns), t
        rag_msgs, _ = rag.messages(it, "mn")
        ratios.append(sum(len(m["content"]) for m in msgs) / sum(len(m["content"]) for m in rag_msgs))
    assert h4s.stats == {"english_question": 4, "family_excluded": 4}
    assert max(ratios) < 0.5, ratios          # хэмжилт: ~0.3–0.4 (Arctic токеноор H4 20.1k → H4s 5.5k)


class _Tok:
    """transformers-гүй tokenizer (ChatML, 4 тэмдэгт ≈ 1 токен)."""
    eos_token = "<|im_end|>"

    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=True):
        return "".join(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in msgs) + "<|im_start|>assistant\n"

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(range(len(text) // 4))}


@pytest.mark.db
def test_build_sft_d3_from_pack(tmp_path, bm25_index):
    """pack → задлах → build_sft (Kaggle-ийн урсгал): тасарсныг хасах, BIRD нь хадгалсан prompt-оор, өөрийн DB нь
    H4s-ээр (англи асуулт), урт мөрийн хасалтыг engine-ээр тайлагнах."""
    from mn_text2sql.training.formats import build_sft
    src = _fake_train_dir(tmp_path)
    tcfg = load_training_cfg()
    tcfg["recipes"]["d3"] = dict(tcfg["recipes"]["d3"], bird_limit=8, index_dir=bm25_index)
    pack_mod.pack(src, tmp_path / "dist", tcfg=tcfg)
    with zipfile.ZipFile(tmp_path / "dist" / "t2s_train_d3.zip") as z:
        z.extractall(tmp_path / "kaggle")
    tcfg["data"]["dir"] = str(tmp_path / "kaggle" / "data" / "train")
    rep = build_sft(load_config(), tcfg, "d3", "arctic-r1-7b", tokenizer=_Tok())
    recs = [json.loads(x) for x in Path(rep["file"]).read_text(encoding="utf-8").splitlines()]
    assert rep["dropped_truncated"] == {"postgres": 1} and rep["missing_items"] == 0
    assert rep["bird_selected"] == 8 and rep["by_engine"]["sqlite"] == {"records": 8, "kept": 8,
                                                                         "tokens": rep["by_engine"]["sqlite"]["tokens"]}
    assert rep["by_engine"]["postgres"]["records"] == 3 and rep["dropped_too_long"] == 0
    bird = [r for r in recs if "CREATE TABLE t (a INT)" in r["prompt"]]     # хадгалсан (sampling үеийн) prompt
    own = [r for r in recs if "CREATE TABLE t (a INT)" not in r["prompt"]]
    assert len(bird) == 8 and len(own) == 3
    ax = _examples()["AX03"]
    assert sum(ax["question_en"] in r["prompt"] for r in own) == 2 and sum("How many > 5%?" in r["prompt"] for r in own) == 1
    assert not any(ax["question_mn"] in r["prompt"] for r in own)
    assert all(r["completion"].endswith("<|im_end|>") and r["prompt"].endswith("<think>") for r in recs)
    assert rep["render"] == {"english_question": 3, "family_excluded": 3}
    comp = [len(r["completion"]) // 4 for r in recs]          # _Tok: 4 тэмдэгт = 1 «токен»
    assert rep["completion_tokens"] == {"total": sum(comp), "median": rep["completion_tokens"]["median"],
                                        "max": max(comp)}
    # өөрийн DB-ийн H4s prompt (~13k тэмдэгт ≈ 3.3k «токен») max_length=2000-д хасагдаж, engine-ээр тайлагнагдана
    tcfg["recipes"]["d3"]["max_length"] = 2000
    rep2 = build_sft(load_config(), tcfg, "d3", "arctic-r1-7b", tokenizer=_Tok())
    assert rep2["by_engine"]["postgres"] == {"records": 3, "dropped_too_long": 3}
    assert rep2["by_engine"]["sqlite"]["kept"] == 8 and rep2["records"] == 8 and rep2["dropped_too_long"] == 3


def test_deadline_callback_stops_and_saves(monkeypatch):
    import sys
    import time
    from types import ModuleType, SimpleNamespace

    from mn_text2sql.training.sft import deadline_callback
    # локалд transformers/torch байхгүй байж болно — callback-ийн логикийг хуурамч модулиар шалгана (DDP-гүй)
    fake_tf, fake_torch = ModuleType("transformers"), ModuleType("torch")
    fake_tf.TrainerCallback = object
    fake_torch.distributed = SimpleNamespace(is_available=lambda: False, is_initialized=lambda: False)
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    ctl = SimpleNamespace(should_save=False, should_training_stop=False)
    late = deadline_callback(time.time() + 3600)
    assert late.on_step_end(None, SimpleNamespace(global_step=3), ctl) is ctl and not ctl.should_training_stop
    cb = deadline_callback(time.time() - 1)
    cb.on_step_end(None, SimpleNamespace(global_step=7), ctl)
    assert ctl.should_save and ctl.should_training_stop and cb.stopped


def test_t4_sdpa_fix_disables_gqa_only_below_sm80(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    from mn_text2sql.training.sft import t4_sdpa_fix

    def install(cap):
        torch = ModuleType("torch")
        torch.cuda = SimpleNamespace(is_available=lambda: True, get_device_capability=lambda *a: cap)
        tf, integ, sd = ModuleType("transformers"), ModuleType("transformers.integrations"), \
            ModuleType("transformers.integrations.sdpa_attention")
        sd.use_gqa_in_sdpa = lambda mask, key, value: mask is None
        integ.sdpa_attention, tf.integrations = sd, integ
        for name, mod in {"torch": torch, "transformers": tf, "transformers.integrations": integ,
                          "transformers.integrations.sdpa_attention": sd}.items():
            monkeypatch.setitem(sys.modules, name, mod)
        return sd

    sd = install((7, 5))                       # T4
    assert t4_sdpa_fix() is True and sd.use_gqa_in_sdpa(None, 0, 0) is False
    sd = install((8, 0))                       # A100 — өөрчлөхгүй
    assert t4_sdpa_fix() is False and sd.use_gqa_in_sdpa(None, 0, 0) is True


def test_config_kwargs_refuses_to_drop_critical_keys():
    import dataclasses

    from mn_text2sql.training.sft import config_kwargs

    @dataclasses.dataclass
    class Cfg:
        output_dir: str = ""
        logging_steps: int = 10

    assert config_kwargs(Cfg, {"output_dir": "x", "report_to": "none"}).output_dir == "x"   # чухал биш → алгасна
    with pytest.raises(ValueError, match="max_length"):
        config_kwargs(Cfg, {"output_dir": "x", "max_length": 9216})


def test_unseen_tables_in_training_targets():
    from mn_text2sql.training.formats import unseen_tables
    prompt = 'CREATE TABLE loans (\n  loan_id INT\n);\nCREATE TABLE "customers" (\n  customer_id INT\n);'
    comp = " \nreasoning\n```sql\nWITH x AS (SELECT * FROM loans) SELECT * FROM x JOIN transactions t ON 1=1\n```"
    assert unseen_tables(prompt, comp) == {"transactions"}
    assert unseen_tables(prompt, " \n```sql\nSELECT * FROM loans l JOIN customers c ON 1=1\n```") == set()
    assert unseen_tables("no ddl", comp) is None and unseen_tables(prompt, "no sql") is None
