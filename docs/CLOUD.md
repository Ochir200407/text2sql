# Cloud дээр ажиллуулах: тест — Kaggle/Colab, сургалт — Modal

| Ажил | Хаана | Файл |
|---|---|---|
| Сургалтгүй тест (A, B, F, E, G, thesis, test) | Kaggle (2×T4, үнэгүй) эсвэл Colab (L4/A100) | `notebooks/t2s_eval.ipynb` |
| Сургалт (data → sample → build/SFT → GRPO) ба adapter-ийн тест | Modal (A100 80 GB / L4) | `scripts/modal/t2s_modal.py` |

Дараалал (туршилтын төлөвлөгөө): **A (notebook) → BEST_A сонгох → сургалт (Modal) → B/E/G (notebook)**.
Сургалтын prompt нь A-шилдэгтэй ижил тул A үе шатыг эхэлж хийнэ.

## Notebook (Kaggle / Colab)

1. Локал: `python scripts/notebook/pack_code.py` → `dist/t2s_code.zip`.
2. Kaggle: zip-ийг Dataset болгож, `notebooks/t2s_eval.ipynb`-ийг импортлон Add Input хийнэ. Accelerator = GPU T4 x2, Internet = On.
   Colab: zip-ийг `MyDrive/t2s/`-д хуулна, Runtime-ийг L4/A100 болгоно.
3. ТОХИРГОО нүдэнд эхлээд `MODE="smoke"` (10 асуулт), дараа нь `MODE="stage"`, `STAGE="A"`, `MODELS="arctic-r1-7b"`.
   Kaggle-д **Save & Run All** ашиглана: browser хаасан ч 12 цаг ажиллана.
4. Бүх үр дүн нэг файлд байна: `t2s_results.zip` (results/ + build/; 15 мин тутам). Дараагийн сесс:
   - Kaggle: өмнөх version-ий Output-ыг Add Input хийнэ.
   - Colab: Drive-д байгаа файлаас автоматаар үргэлжилнэ.
5. Локалд задлаад `python -m mn_text2sql report` ба `report-latex` → `report/build.sh` ажиллуулна.

**T4-ийн хязгаарлалт.** T4 нь BF16/FP8 дэмждэггүй тул notebook загварыг `--dtype half --tensor-parallel-size 2`-оор ажиллуулна
(`T2S_VLLM_ARGS`, `scripts/server/serve_model.sh`). Тайланд «T4, fp16» гэж тэмдэглэнэ.
sqrl-9b (Qwen3.5) T4 дээр ажиллах эсэхийг smoke-оор шалгана. Ажиллахгүй бол Colab L4 эсвэл Modal L4 ашиглана.

## Modal (сургалт)

`~/.modal.toml`-д token байх ёстой (`modal setup`). Үнэ (2026-10): L4 ~$0.80/цаг, A100-80GB ~$2.50/цаг, H100 ~$3.95/цаг.
Сар бүр $30 үнэгүй кредит олгодог.

**Зөвлөмж: deploy + spawn.** Ажил компьютерээс бүрэн хамааралгүй явна. `modal run --detach`-ийг
`timeout` гэх мэтээр тасалбал ажил цуцлагддаг (2026-10-16-нд ажигласан).

```bash
python -m modal deploy scripts/modal/t2s_modal.py                       # код өөрчлөгдөх бүрт
python scripts/modal/launch.py --stage A --models sqrl-9b                # тест (L4)
python scripts/modal/launch.py --train --model arctic-r1-7b --steps sample --best-a A6   # сургалт (A100)
python scripts/modal/launch.py --status fc-...                           # дууссан эсэх
```

`modal run` хувилбар (терминал нээлттэй байх үед):

```bash
modal run scripts/modal/t2s_modal.py --action smoke                      # орчин шалгах (~$1)
modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps data --best-a A6
modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps sample --best-a A6
modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps "build sft" --best-a A6 --recipes "c1 c2 c3"
modal run --detach scripts/modal/t2s_modal.py --action eval-adapters --model arctic-r1-7b --best-a A6
modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps grpo --best-a A6 --best-sft C3 --grpo-recipes c6
modal volume get t2s-work results ./results_modal
modal volume get t2s-work adapters ./adapters                            # adapter-уудыг notebook/сервер рүү
```

**Тасарвал** ижил командыг дахин ажиллуулна. Дууссан хэсгийг алгасна:
- тест: дууссан асуулт;
- орчуулга ба sampling: дууссан мөр;
- SFT ба GRPO: дууссан жор (`t2s_meta.json`); эхэлсэн жор нь сүүлийн checkpoint-оос үргэлжилнэ (SFT 200, GRPO 50 алхам тутам хадгална).

Функц бүр 24 цагийн хязгаартай тул алхмуудыг тусад нь ажиллуулна.

**Хамгийн бага сургалт (зардал хэмнэх):** Arctic дээр C1–C3 ба D1 → шаардлагатай бол C6 (GRPO).
Ойролцоогоор A100-ийн 15–30 цаг (~$40–75) болно (төлөвлөгөөний цаг × Modal-ийн үнэ). Хоёр загвар, бүх жорыг хийвэл
A100-ийн 190–385 цаг болно (~$475–960).
