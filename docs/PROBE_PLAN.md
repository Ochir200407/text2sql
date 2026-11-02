# Hidden-state probe: хариулах боломжгүй / хоёрдмол асуултын ангилагч (LatentRefusal маягийн)

`docs/RESEARCH_NEXT.md`-ийн 1-р зөвлөмж. SQL загварын (Arctic-Text2SQL-R1-7B + D2 LoRA) prompt-ыг уншсан үеийн
далд төлөвөөс P(хариулах боломжгүй), P(хоёрдмол)-ыг жижиг логистик регрессээр (probe) таамаглаж, E4-ийн дүрэмтэй
хослуулна (эхлээд дүрэм, дараа нь probe). Abstain v2 (хэллэгийн LR) dev CV-д E4-ийн дүрмийг давж чадаагүй (154.5 vs
159 in-sample; дүрэм + v2 = **160.9**, `docs/ACCURACY_PLAN.md` «Abstain v2»). Таамаг: загвар өөрөө асуултыг схемтэй
холбож чадахгүй байгаагаа далд төлөвтөө «мэддэг» — хэллэгийн дүрэм хүрэхгүй out-of-family тохиолдлыг барина.

## 1. Файлууд

| Файл | Үүрэг |
|---|---|
| `scripts/probe/extract_hidden.py` | Kaggle: prompt (Pipeline.prompt_for) → 4-bit forward → `L<i>_mean/last.npy`, `logit.npy` (fp16), `ids.json` |
| `src/mn_text2sql/pipeline/probe.py` | шинж ачаалах, стандартчилах → PCA → LR (Newton, numpy), family CV, `--train/--cv/--apply`, `probe_decision()` |
| `tests/test_probe.py` | синтетик массиваар CV harness, босго, хадгалах/ачаалах, `--final-test` хамгаалалт, part нэгтгэлт |

**Prompt.** `Pipeline.prompt_for` — үнэлгээ ба сургалтын prompt-ын цорын ганц эх сурвалж — run `M1a`-ийн тохиргоогоор
(I3 = H4 + `build/index_plus_dev`; variant `mn`; `en_prompt` горимд MADLAD орчуулгын кэш `m1_madlad_improved`; few-shot-оос
асуултын өөрийн family хасагдана — `runner`-тэй ижил `item_families`). Chat template + Arctic prefill
(`"Let me solve this step by step.\n<think>"`) — vLLM-ийн `continue_final_message`-тэй ижил текст. PG ба LLM сервер
шаардахгүй (executor-ийн оронд хуурмаг объект — дуудагдвал алдаа). `--check-run` нь M1a run-ий `prompt_tokens`-тэй
токены тоог тулгаж prompt зөрөөгүйг шалгана.

**Шинж.** Давхарга 16, 20, 24 (28-аас; `L = hidden_states[L]`) тус бүрийн prompt-ын токенуудын дундаж (`mean`) ба сүүлийн
токен (`last` — prefill-ийн `<think>`, үүсгэлтийн эхлэх цэг). `logit`: prefill-ийн дараах эхний токены энтропи, top-1
магадлал, top1−top2 зөрүү; `--gen-tokens G` бол эхний G greedy токены энтропийн дундаж/макс, top-1-ийн доод утга.
Hook дотор шууд дундажлах тул бүх `hidden_states`-ийг санах ойд хадгалахгүй; `lm_head` зөвхөн сүүлийн токенд.

**Probe.** Түүхий шинж стандартчилах → PCA (k = 32; n ≪ d тул; fold дотор л тооцно) → жижиг блок (`logit`, ≤ 64
хэмжээс) PCA-гүй залгана → дахин стандартчилах → 2 L2 LR (Newton; abstain.py-тэй ижил параметрчлэл, ангиллын жин) →
босго (t_u, t_a). Босгыг сургалтын fold-ын **дотоод** 3-fold family CV-ийн out-of-fold магадлалаар сонгоно (өндөр
хэмжээст probe-ийн in-sample магадлал хэт итгэлтэй). Дүрэмтэй горимд босгыг зөвхөн дүрэм «answer» гэсэн мөрөөр тааруулна.

## 2. Kaggle-ийн команд

Урьдчилсан нөхцөл: `bootstrap.sh` (PG хэрэггүй ч bootstrap-ын бусад алхам), D2 adapter-ийн part-ууд (`t2s_adapter_d2.zip.part*`)
input-д, `m1_madlad_improved.jsonl` кэш (`$W` эсвэл input), `build/index_plus_dev` (t2s алхамтай ижил командаар).

```bash
# 0) D2 LoRA-г fp32-оор нэгтгэсэн fp16 жин (serve_d2_merged; vLLM асаахгүй)
D2_MERGE_ONLY=1 serve_d2_merged          # → adapters/d2__arctic-r1-7b__merged16

# 1) Smoke (10 асуулт, хурд ба санах ойг хэмжинэ)
T2S_EMBED_DEVICE=cpu python scripts/probe/extract_hidden.py --run M1a --var best_a=A7 --limit 10 \
    --model-path adapters/d2__arctic-r1-7b__merged16 --out build/probe/m1a_dev_smoke

# 2) Бүх dev (300) — тасарвал ижил командаар үргэлжилнэ (parts/<id>.npz)
T2S_EMBED_DEVICE=cpu python scripts/probe/extract_hidden.py --run M1a --var best_a=A7 \
    --model-path adapters/d2__arctic-r1-7b__merged16 --out build/probe/m1a_dev \
    --check-run results/runs/dev/M1a/arctic-r1-7b__mn__s0.jsonl

# 3) (Сонголт) нэмэлт сургалтын мөр: мэдлэгийн сангийн 160 жишээ (бүгд answerable; англи нь question_en)
T2S_EMBED_DEVICE=cpu python scripts/probe/extract_hidden.py --run M1a --var best_a=A7 --source knowledge \
    --model-path adapters/d2__arctic-r1-7b__merged16 --out build/probe/m1a_knowledge
#    синтетик unanswerable: --source jsonl:data/probe/synth_unans.jsonl (id, question_mn, category, family, question_en)

# Сонголт: суурь + LoRA (нэгтгэлгүй): --base Snowflake/Arctic-Text2SQL-R1-7B --adapter adapters/d2__arctic-r1-7b
#          logit-гүй, ~15% хурдан: --no-logit;  L4: --no-4bit (bf16 бүтэн жин, 24 GB-д багтана)
```

CV ба сургалт GPU-гүй (~20 с) — `build/probe/m1a_dev/*.npy` + `ids.json`-ийг (~15 MB; `parts/`, `prompts.jsonl` хэрэггүй)
татаж локалд:

```bash
python -m mn_text2sql.pipeline.probe --cv --features build/probe/m1a_dev                     # үндсэн (pre-registered)
python -m mn_text2sql.pipeline.probe --cv --features build/probe/m1a_dev --extra build/probe/m1a_knowledge   # 2-дагч
python -m mn_text2sql.pipeline.probe --cv --features build/probe/m1a_dev --sweep             # судалгааны (шийдвэрт биш)
python -m mn_text2sql.pipeline.probe --train --features build/probe/m1a_dev --mode rules+probe \
    --save build/probe/probe_m1a.npz                                                          # зөвхөн adopt бол
```

`--cv`-ийн гаралт: `rules` (E4 ба E4f), `cv.{v2, rules+v2, probe, rules+probe, probe+v2f, rules+probe+v2f}` (EX дундаж ±
sd, ангилал бүрийн хувь, confusion, давталт бүрийн нийлбэр, `paired_vs_rules+v2`), `verdict` (§5-ын дүрэм).
Суурь run анхдагчаар `results/runs/dev/E4f/arctic-r1-7b__mn__s1__h_H4.jsonl` (abstain `--cv`-тэй ижил).

**Эцсийн test (НЭГ удаа, шийдвэр dev-ээр тогтсоны дараа л):**

```bash
T2S_EMBED_DEVICE=cpu python scripts/probe/extract_hidden.py --run M1a --var best_a=A7 --split test --final-test \
    --translation-cache <M1a-ийн test кэш> --model-path adapters/d2__arctic-r1-7b__merged16 --out build/probe/m1a_test
python -m mn_text2sql.pipeline.probe --apply --model build/probe/probe_m1a.npz --features build/probe/m1a_test \
    --run results/runs/test/<суурь run>/<файл>.jsonl --out build/probe/decisions_test.jsonl
```

`--final-test`-гүй бол `--split test` шууд зогсоно (`m0_translate.py`-тэй ижил; тестээр баталгаажсан). test-ийн `ids.json`-д
`category` хадгалахгүй; `--cv/--train` нь `split: test`-тэй шинжийг татгалзана; `--apply` нь evalset-ийн шошгыг уншихгүй
(run-ий бичлэгээс асуулт, v2 шинж, дүрэм).

## 3. Хугацааны тооцоо (T4, 300 forward)

M1a/H4-ийн prompt урт (dev-ийн run-уудын `prompt_tokens`): медиан **~20.2k токен**, макс ~21k (бүтэн схем + тайлбар +
4 few-shot). Тиймээс `--max-len` анхдагч 24576 (models.yaml `max_model_len`); 12k-ийн хязгаар бүх prompt-ыг тасална.

| Алхам | T4 (16 GB) | L4 (24 GB) |
|---|---|---|
| D2 нэгтгэлт (`merged16` байхгүй бол) | 15–20 мин | 10–15 мин |
| Prompt угсрах (BGE-M3 CPU-д, 300) | 5–10 мин | 5–10 мин |
| 4-bit ачаалал | ~5 мин | ~5 мин |
| Forward: 7B × 20k токен ≈ 2.8·10¹⁴ FLOP + attention ≈ 1.6·10¹⁴ → **~20–35 с/асуулт** | **1.7–2.9 цаг** | 0.7–1.2 цаг |
| `--source knowledge` (160) | +0.9–1.5 цаг | +0.4–0.6 цаг |

Санах ой (T4): NF4 жин ~5.5 GB + embedding/lm_head ~2.2 GB + 20k токены идэвхжүүлэлт (MLP-ийн 18944 × 20k fp16 ≈ 0.8 GB
× 2–3) → оргил ~10–11 GB. T4-д `t4_sdpa_fix` (GQA-г унтрааж memory-efficient SDPA) заавал — эс бөгөөс math kernel
[28, 20k, 20k] fp32 ≈ 45 GB → OOM. OOM гарвал тухайн асуултыг `max_len/2`-оор дахин (truncated=true). Smoke (`--limit 10`)-ийн
лог `с/асуулт` ба `GPU оргил`-ыг хэвлэнэ — бүтэн ажиллуулахаас өмнө хугацааг тэрээр тооцно. 12 цагийн хязгаарт багтана;
тасарвал parts-аас үргэлжилнэ.

## 4. run_plan_l4.sh-д нэмэх (mt-ийн шинэ алхам `probe`) — ЭНД л, скриптийг засаагүй

`STAGE=mt` хэсэгт `rerank` блокийн дараа, `t2s`-ээс өмнө (vLLM асаахаас өмнө GPU чөлөөтэй үед):

```bash
    if [[ "$STEPS" == *" probe "* ]]; then   # Hidden-state probe (docs/PROBE_PLAN.md): D2-ийн далд төлөв, dev (+ knowledge); test-гүй
      PD="$W/probe"; mkdir -p "$PD"
      bash "$S/serve_model.sh" stop >/dev/null 2>&1 || true            # GPU-г vLLM-ээс чөлөөлнө
      for T in m1a_dev m1a_knowledge; do                               # өмнөх сессийн part-ууд (resumable)
        P="$(find "$SRC" -path "*probe/$T/prompts.jsonl" 2>/dev/null | head -1 || true)"
        if [ -n "$P" ] && [ ! -f "$PD/$T/prompts.jsonl" ]; then mkdir -p "$PD/$T"; cp -r "$(dirname "$P")"/. "$PD/$T/"; log "probe $T өмнөх сессээс"; fi
      done
      if mt_cached m1_madlad_improved; then
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
            --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        D2_MERGE_ONLY=1 serve_d2_merged
        CHK=""; [ -f "results/runs/dev/M1a/${MODEL}__mn__s0.jsonl" ] && CHK="--check-run results/runs/dev/M1a/${MODEL}__mn__s0.jsonl"
        for SQ in dev ${PROBE_EXTRA-knowledge}; do
          log "=== probe m1a_$SQ ($(mins))"
          # shellcheck disable=SC2086
          T2S_EMBED_DEVICE=cpu python scripts/probe/extract_hidden.py --run M1a $VARS --source "$SQ" \
            --model-path "adapters/d2__${MODEL}__merged16" --out "$PD/m1a_$SQ" --layers "${PROBE_LAYERS:-16,20,24}" \
            ${PROBE_LIMIT:+--limit $PROBE_LIMIT} $([ "$SQ" = dev ] && echo "$CHK") \
            || log "probe m1a_$SQ: алдаа/зогссон — дууссан part хадгалагдсан (дахин ажиллуулбал үргэлжилнэ)"
          CHK=""
        done
        E4F="$(find results/runs/dev/E4f "$SRC" -name "${MODEL}__mn__s1__h_H4.jsonl" -path '*E4f*' 2>/dev/null | head -1 || true)"
        if [ -n "$E4F" ]; then   # CV (GPU-гүй, ~20 с) — эцсийн тоог локалд дахин ажиллуулж баталгаажуулна
          python -m mn_text2sql.pipeline.probe --cv --features "$PD/m1a_dev" --run "$E4F" > "$PD/cv.json" 2>&1 || true
          tail -c 800 "$PD/cv.json"
        fi
      else
        log "probe: m1_madlad_improved кэш алга — алгасав"
      fi
    fi
```

Ажиллуулах (**анхаар, 2026-11-01:** run_plan_l4.sh-д `probe` алхам хараахан хэрэгжээгүй — одоо үл мэдэгдэх алхам гэж
анхааруулаад алгасна; доорх нь төлөвлөсөн хэлбэр): `STAGE=mt MT_STEPS="probe" bash scripts/server/run_plan_l4.sh` (smoke: `PROBE_LIMIT=10`; зөвхөн dev:
`PROBE_EXTRA=""`). `$VARS` = mt хэсгийн `--var best_a=${BEST_A:-A7}` — `extract_hidden.py --var`-тэй нийцнэ.

**reselect-д холбох (adopt болсон тохиолдолд л; одоо засаагүй).** `options.py`: `probe_model: str | None = None`,
`probe_features: str | None = None` (`_LATE_DEFAULTS`-д `None`, fingerprint-д анхдагч үед орохгүй); `experiments.yaml`:
`E4rp: {base: E4, probe_model: build/probe/probe_m1a.npz, probe_features: "build/probe/m1a_{split}", desc: "E4 + hidden-state probe (дүрэм эхэлж)", group: E}`.
`reselect.py`-д v2-ийн блокийн дараа:

```python
if probe is not None and ans.status not in ("refused", "clarify"):
    x = probe_X.get(it.id)                       # probe_X = dict(zip(*load_feature_dir(features, probe.meta["blocks"])[:2]))
    if x is not None:
        v2f = featurize(ans.question, load_knowledge(), conf, hashes, rows, len(cands), chosen.sql) if probe.v2_feats else None
        pd = probe_decision(x, probe, v2=v2f)    # дүрэм аль хэдийн хэрэгжсэн тул rule=None
        if pd is not None:
            ans.status, rd = ("refused" if pd[0] == "refuse" else "clarify"), pd
```

Онлайн pipeline-д (orchestrator) холбохгүй: нэмэлт 4-bit forward (~25 с/асуулт T4) хэрэгтэй, vLLM-ээс далд төлөв авах
дэмжлэггүй — зөвхөн офлайн reselect.

## 5. Шийдвэрийн дүрэм (урьдчилан бүртгэсэн, dev-ээр л)

Тохиргоо **өмнө нь** тогтсон: блок `L20_mean,L20_last,logit`, PCA 32, L2 = 1, дотоод 3-fold босго, нэмэлт мөргүй (dev-ийн
300 л), суурь run E4f, систем **`rules+probe`** (E4-ийн дүрэм эхэлж, дүрэм «answer» гэсэнд probe). Adopt зөвхөн:

1. `rules+probe` CV (family 5-fold × 10, abstain `--cv`-тэй ижил fold/хэмжүүр) дундаж **> 160.9** (дүрэм + v2), БА
2. ижил fold дээрх `rules+v2`-оос **≥ 8/10** давталтад илүү (`paired_vs_rules+v2.n_better`).

Хоёул биелбэл `--train` → test-д нэг удаа (§2). Эс бөгөөс probe-ийг диплом/тайланд **сөрөг үр дүн** гэж тайлбарлана
(test-д ажиллуулахгүй). `--sweep`, `--extra`, `probe+v2f` нь судалгааны — тэдгээрээс илүү хувилбар олдвол шинэ таамаг
болгож тэмдэглэнэ, энэ удаагийн шийдвэрт ашиглахгүй (dev-д олон тохиргоо туршвал CV өөдрөг болно).

**Харьцуулалтын баталгаа (локал, 2026-10-30).** Санамсаргүй (N(0,1)) «далд төлөв»-өөр harness-ийг E4f дээр ажиллуулахад
v2 = **154.5 ± 1.72**, дүрэм + v2 = **160.9 ± 0.99** — ACCURACY_PLAN-ийн тоотой яг таарсан (ижил fold). Сөрөг хяналт
(санамсаргүй шинж): `rules+probe` 156.9 ± 3.5 (дүрмээс доош), `rules+probe+v2f` 160.0 ± 2.1 (paired: 3 илүү / 4 дутуу).
Өөрөөр хэлбэл зөвхөн 160.9-ийг давах нь шуугианы түвшинд (~±2) ойр тул 2-р нөхцөл (paired ≥ 8/10) хэрэгтэй.

## 6. Эрсдэл

* **Жижиг дээж.** Эерэг жишээ 38 + 38; шинж 21k хэмжээс → overfit. PCA-г fold дотор, босгыг дотоод CV-ээр; гэвч CV-ийн
  sd ±3.5 (санамсаргүй шинжид) — 1–2 асуултын ахиц статистикийн хувьд ялгагдахгүй.
* **Систем зөрүү.** Далд төлөв M1a-ийн prompt-оос (D2, MADLAD англи), харин «хариулбал зөв» E4f-ийн бичлэгээс (160.9-тэй
  харьцуулахын тулд). Probe нь асуултын түвшний дохио тул зөвшөөрөгдөх боловч M1a-ийн run-ээр (`--run results/runs/dev/M1a/...`)
  дахин ажиллуулж тууштай эсэхийг шалгана (тэр үед лавлах нь тухайн run дээрх `rules+v2`, 160.9 биш).
* **Prompt-ын зөрүү.** Prompt `prompt_for`-оос тул кодоор ижил; гэвч индекс (`build/index_plus_dev`), орчуулгын кэш,
  tokenizer-ийн хувилбар өөр байвал зөрнө → `--check-run` (|Δтокен| медиан ≈ 0 байх ёстой), `prompts.jsonl`-ийн `sha`.
* **4-bit ↔ vLLM fp16.** Далд төлөв NF4 жингээс — серверийн загвартай яг ижил биш. Сургалт ба хэрэглээ (test) хоёулаа ижил
  extract-аар тул тууштай, гэхдээ «загвар өөрөө мэддэг» гэсэн тайлбарыг болгоомжтой хэлнэ. L4-д `--no-4bit`-ээр шалгаж болно.
* **T4 санах ой / хугацаа.** 20k токенд `t4_sdpa_fix` ажиллахгүй (transformers-ийн хувилбар) бол OOM → `max_len/2`
  (тасалсан prompt) — `ids.json`-ийн `truncated` тоог шалгах; ≥ 5% бол L4 дээр давтана. Хугацаа 2–3 цаг (T4).
* **Нэмэлт мөрийн шилжилт.** Мэдлэгийн сангийн 160 жишээ бүгд answerable, англи нь гараар бичсэн `question_en` (MADLAD биш) —
  P(unanswerable)-ын суурь түвшинг хазайлгаж болно; тиймээс үндсэн шийдвэр нэмэлтгүй.
* **Leakage.** `index_plus_dev` dev-ийн асуултыг few-shot санд агуулдаг; асуулт бүрийн өөрийн family хасагдана (үнэлгээтэй
  ижил). Family CV нь асуултын family-гаар хуваадаг тул ижил загварын хэллэг train/test-д зэрэг орохгүй.
* **Онлайн өртөг.** Adopt болсон ч онлайн pipeline-д асуулт бүрт нэмэлт ~25 с (T4) forward — вэб демод ашиглахгүй, зөвхөн
  офлайн reselect/тайлан.
