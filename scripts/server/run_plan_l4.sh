#!/usr/bin/env bash
# =====================================================================
# Сургалтгүй тест (A, B, F, E бүлэг) — нэг L4 (24 GB). Үе шат бүрийг ТУСАД НЬ ажиллуулна: дараагийн үе шат нь
# өмнөхийн шилдэг тохиргооноос хамаардаг (туршилтын төлөвлөгөө). Дараалал:
#   STAGE=caches  bash scripts/server/run_plan_l4.sh             # A4 орчуулга, F8 router, F7 intent кэш
#   STAGE=A       bash scripts/server/run_plan_l4.sh             # → REPORT.md-ээс A1/A6/A7-ийн шилдгийг сонго
#   STAGE=B BEST_A=A6 bash scripts/server/run_plan_l4.sh         # B1–B10 (A-шилдэг дээр)
#   STAGE=F BEST_A=A6 bash scripts/server/run_plan_l4.sh         # F1, F2, F3, F5, F7, F8 (сургалтгүй F)
#   (C/D сургалт: train_a100.sh → eval_adapters.sh; эсвэл STAGE=adapters BEST_A=A7 — Kaggle-д хэсэгчилсэн zip-ээс)
#   STAGE=E BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # E1–E3 (A–D-ийн шилдэг дээр)
#   STAGE=G BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # G1–G6 agent (docs/AGENT.md; E-ийн дараа)
#   STAGE=thesis BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # text2sql-thesis-ийн кейс (docs/THESIS_BENCH.md)
#   STAGE=test BEST_A=A6 BEST_CONFIG=D1 TEST_IDS=A6,B5,D1,E2 bash scripts/server/run_plan_l4.sh
#   STAGE=e2test BEST_A=A7 BEST_CONFIG=H4 BEST_SFT=C3 bash scripts/server/run_plan_l4.sh   # Kaggle T4: E2 + H4 test (D2 нэгтгэсэн)
#   STAGE=istage BEST_A=A7 BEST_SFT=C3 I_RUNS="I1:dev I1:test E2@I1:test" bash scripts/server/run_plan_l4.sh   # I бүлэг
#   STAGE=train_d3 BEST_A=A7 [D3_EVAL=1] bash scripts/server/run_plan_l4.sh   # Kaggle 2×T4: D3 QLoRA → нэгтгэх → D3s dev
# BEST_* нь config/experiments.yaml-ийн vars-ийг (--var) дарна; утга нь run бүрийн .meta.json-д (baseline) бичигдэнэ.
# Тасарвал ижил командыг дахин ажиллуулахад дууссан асуултыг алгасна (тохиргоо өөрчлөгдсөн бол runner зогсоно).
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
S="scripts/server"
# Notebook нь кодын zip-ийг задалсны ДАРАА өмнөх t2s_results.zip-ийг (results/ + build/) дээр нь задалдаг тул pack_code.py-ийн
# KEEP_FILES (build/ доторх локалаас илгээсэн оролт, жишээ нь build/synth_own/candidates.jsonl) архивын хуучин хуулбараар
# дарагддаг. Тэдгээрийг л кодын zip-ээс дахин задална (KEEP_CACHES — орчуулгын кэш — Kaggle дээр нэмэгддэг тул ҮГҮЙ).
# Zip-ийг notebook-ийн find("t2s_code.zip")-тэй ижил олно: /kaggle/working, дараа нь /kaggle/input (хамгийн шинэ mtime);
# Kaggle-аас гадна зөвхөн T2S_CODE_ZIP=<zip> өгсөн үед. Kaggle нь dataset-ийн zip-ийг ЗАДАЛЖ байршуулдаг (V27/V28-ийн log:
# «t2s_code.zip олдсонгүй» — KEEP_FILES огт сэргээгдээгүй байсан) тул zip алга бол задалсан кодын хавтаснаас
# (scripts/notebook/pack_code.py-тэй, хамгийн шинэ) хуулна. T2S_KEEP_SEARCH (os.pathsep-ээр) — хайх хавтас (тестэд).
restore_keep_files() {
  [ -d /kaggle ] || [ -n "${T2S_CODE_ZIP:-}${T2S_KEEP_SEARCH:-}" ] || return 0
  python - <<'PYEOF' || log "[keep] KEEP_FILES-ийг сэргээж чадсангүй — архивын хуулбар хэвээр"
import os, shutil, sys, zipfile
from pathlib import Path
sys.path.insert(0, "scripts/notebook")
from pack_code import KEEP_FILES   # нэг эх сурвалж (pack_code.py нь import-д юу ч бичихгүй)
bases = [Path(b) for b in os.environ.get("T2S_KEEP_SEARCH", os.pathsep.join(("/kaggle/working", "/kaggle/input"))).split(os.pathsep) if b]
z = os.environ.get("T2S_CODE_ZIP", "")
if not z:
    for base in bases:
        hits = sorted(base.rglob("t2s_code.zip"), key=lambda p: p.stat().st_mtime, reverse=True) if base.is_dir() else []
        if hits:
            z = str(hits[0])
            break
if z:
    with zipfile.ZipFile(z) as zf:
        names = sorted(n for n in zf.namelist() if n in KEEP_FILES)
        for n in names:
            zf.extract(n, ".")
    print(f"[keep] {z}: KEEP_FILES {len(names)} файлыг кодын zip-ээс дахин задлав: {', '.join(names) or '—'}")
    sys.exit(0)
here = Path(".").resolve()
roots = [p.parents[2] for base in bases if base.is_dir() for p in base.rglob("pack_code.py")
         if p.parent.name == "notebook" and p.parent.parent.name == "scripts" and p.parents[2].resolve() != here]
if not roots:
    print("[keep] t2s_code.zip ба задалсан кодын хавтас олдсонгүй — KEEP_FILES-ийг сэргээгээгүй")
    sys.exit(0)
root = max(roots, key=lambda r: (r / "scripts/notebook/pack_code.py").stat().st_mtime)
names = []
for n in sorted(KEEP_FILES):
    if (root / n).is_file():
        Path(n).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / n, n)
        names.append(n)
print(f"[keep] {root}: KEEP_FILES {len(names)} файлыг задалсан кодын хавтаснаас дахин хуулав: {', '.join(names) or '—'}")
PYEOF
}
restore_keep_files
# Kaggle-ийн notebook-ийн тохиргооны нүдийг засах боломжгүй үед (editor гацсан) env-ийг кодын dataset-ээр дарж бичнэ:
# config/kaggle_overrides.env (KEY=VALUE мөрүүд). KO_UNTIL=YYYY-MM-DD (UTC)-ээс хойш үйлчлэхгүй — дараагийн сессүүдэд
# санамсаргүй үлдэхгүйн тулд. Хугацаа нь дууссан override байвал notebook-ийн суурилуулсан (хуучин, үл мэдэгдэх) STAGE-ээр
# ажиллуулахгүй — exit 2 (FORCE_NOTEBOOK_STAGE=1 бол notebook-ийн STAGE-ээр үргэлжилнэ).
if [ -f config/kaggle_overrides.env ]; then
  sed -i 's/\r$//' config/kaggle_overrides.env   # Windows-ийн CRLF → LF (STAGE=$'mt\r' болж case таарахгүй байсан)
  KO_UNTIL="$(sed -n 's/^KO_UNTIL=//p' config/kaggle_overrides.env | tr -d '"' | head -1)"
  if [ -n "$KO_UNTIL" ] && [ "$(date -u +%F)" \> "$KO_UNTIL" ]; then
    if [ "${FORCE_NOTEBOOK_STAGE:-0}" = 1 ]; then
      echo "[override] config/kaggle_overrides.env хугацаа нь дууссан (KO_UNTIL=$KO_UNTIL) — FORCE_NOTEBOOK_STAGE=1: notebook-ийн STAGE=${STAGE:-<алга>}-ээр үргэлжилнэ"
    else
      echo "[override] АЛДАА: config/kaggle_overrides.env хугацаа нь дууссан (KO_UNTIL=$KO_UNTIL < $(date -u +%F) UTC) — notebook-ийн суурилуулсан STAGE=${STAGE:-<алга>} хуучин/үл мэдэгдэх тул ЗОГСОВ."
      echo "[override] Засах: шинэ override-ыг (жишээ нь config/kaggle_overrides_v27.env) kaggle_overrides.env болгож хуулаад KO_UNTIL-ийг шинэчилж кодын dataset-ийг дахин багцал; notebook-ийн STAGE-ээр санаатайгаар ажиллуулах бол FORCE_NOTEBOOK_STAGE=1."
      exit 2
    fi
  else
    set -a; . config/kaggle_overrides.env; set +a
    echo "[override] $(grep -v '^#' config/kaggle_overrides.env | grep . | tr '\n' ' ')"
  fi
fi
STAGE="${STAGE:?STAGE=caches|A|B|adapters|e2test|istage|t2|agent|train_d3|mt|t2s_inv|F|E|G|thesis|test заана (дарааллыг скриптийн толгойноос үз)}"
MODELS="${MODELS:-arctic-r1-7b sqrl-9b}"
need() { [ -n "${!1:-}" ] || { echo "STAGE=$STAGE: $1=<ID> заавал (жишээ: $1=$2) — өмнөх үе шатын REPORT.md-ээс сонгоно"; exit 2; }; }
VARS=""
[ -n "${BEST_A:-}" ] && VARS="$VARS --var best_a=$BEST_A"
[ -n "${BEST_CONFIG:-}" ] && VARS="$VARS --var best_config=$BEST_CONFIG"
[ -n "${BEST_SFT:-}" ] && VARS="$VARS --var best_sft=$BEST_SFT --var best_sft_adapter=$(echo "$BEST_SFT" | tr 'C' 'c')"
run() { EXTRA="$VARS" VARIANTS="${VARIANTS:-}" bash "$S/run_experiments.sh" "$@"; }
# Kaggle T4: D2 LoRA-г (хэсэгчилсэн zip-ээс угсарч) fp32-оор нэгтгэсэн fp16 жингээр served=d2 болгож асаана.
# T4 дээр vLLM-ийн LoRA kernel ~3 дахин удаан (C1: LoRA-тай 22.7 с/асуулт, A1: LoRA-гүй 7.4 с) — merge_lora.py.
serve_d2_merged() {
  local MODEL=arctic-r1-7b AD="adapters/d2__arctic-r1-7b" M="adapters/d2__arctic-r1-7b__merged16" P0 D HF_ID
  if [ ! -f "$AD/adapter_model.safetensors" ]; then
    P0="$(find "${ADAPTER_SRC:-/kaggle/input}" -name 't2s_adapter_d2.zip.part00' 2>/dev/null | head -1 || true)"
    [ -n "$P0" ] || { log "D2 adapter алга: t2s_adapter_d2.zip.part00 олдсонгүй"; exit 2; }
    D="$(dirname "$P0")"
    cat "$D"/t2s_adapter_d2.zip.part* > /tmp/t2s_adapter_d2.zip
    [ "$(sha256sum /tmp/t2s_adapter_d2.zip | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$D/SHA256.txt")" ]       || { log "D2 adapter zip-ийн SHA256 таарсангүй"; exit 2; }
    python -c "import zipfile; zipfile.ZipFile('/tmp/t2s_adapter_d2.zip').extractall('.')"
  fi
  if [ ! -f "$M/t2s_merge.json" ]; then
    HF_ID="$(python -c "import yaml; print(yaml.safe_load(open('config/models.yaml', encoding='utf-8'))['models']['$MODEL']['hf_id'])")"
    log "D2 LoRA-г нэгтгэж байна → $M"
    python scripts/notebook/merge_lora.py --base "$HF_ID" --adapter "$AD" --out "$M"
    # Kaggle-ийн дискэнд 15 GB чөлөөлнө: vLLM нэгтгэсэн жинг үйлчилнэ, суурийн кэш дахин хэрэггүй
    [ -d /kaggle ] && rm -rf "${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${HF_ID//\//--}"
  fi
  [ "${D2_MERGE_ONLY:-0}" = 1 ] && return 0
  if curl -sf "http://localhost:${T2S_VLLM_PORT:-8000}/v1/models" 2>/dev/null | grep -q '"d2"'; then
    log "vLLM d2 аль хэдийн ажиллаж байна — дахин эхлүүлэхгүй"; return 0
  fi
  MODEL_PATH="$M" SERVED=d2 bash "$S/serve_model.sh" "$MODEL"
}
# Вэб/T2 (T4, 15 GB): vLLM-ийн шинэ хувилбар bitsandbytes-гүй тул transformers + 4-bit жижиг серверээр
# (scripts/server/hf_openai_server.py): D2 (port 8000, served=d2), орчуулагч qwen3.5-9b (port 8001).
# 2 GPU бол тус бүрт нэг загвар, 1 GPU (Colab) бол хоёуланг нэг GPU-д.
hf_models() {
  local NG G0 G1 i
  NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"
  if [ "${NG:-1}" -ge 2 ]; then G0=0; G1=1; else G0=0; G1=0; fi
  mkdir -p build/logs
  D2_MERGE_ONLY=1 serve_d2_merged
  log "GPU $NG — D2 GPU$G0, орчуулагч GPU$G1 (4-bit, transformers)"
  CUDA_VISIBLE_DEVICES=$G0 nohup python scripts/server/hf_openai_server.py --model adapters/d2__arctic-r1-7b__merged16 \
    --served d2 --port 8000 > build/logs/hf_d2.log 2>&1 &
  for i in $(seq 1 120); do curl -sf localhost:8000/v1/models >/dev/null && break; sleep 10; done
  curl -sf localhost:8000/v1/models >/dev/null || { tail -30 build/logs/hf_d2.log; exit 2; }
  CUDA_VISIBLE_DEVICES=$G1 nohup python scripts/server/hf_openai_server.py --model Qwen/Qwen3.5-9B \
    --served qwen3.5-9b --port 8001 > build/logs/hf_tr.log 2>&1 &
  for i in $(seq 1 120); do curl -sf localhost:8001/v1/models >/dev/null && break; sleep 10; done
  curl -sf localhost:8001/v1/models >/dev/null || { tail -30 build/logs/hf_tr.log; exit 2; }
  log "загварууд бэлэн: $(grep -h "ачааллаа" build/logs/hf_d2.log build/logs/hf_tr.log | tr "\n" " ")"
}
# D3-ийн сургалтын өгөгдөл (кодын архивт байхгүй): scripts/notebook/pack_train_d3.py-ийн t2s_train_d3.zip.partNN +
# SHA256_train_d3.txt-ийг /kaggle/input-ээс угсарч шалгаад data/train-д задална. zip-ийг шууд dataset болгосон бол
# Kaggle задалсан байдаг — тэр үед .../data/train/D3_MANIFEST.json-тэй хавтсыг хуулна.
unpack_d3_data() {
  local SRC="${TRAIN_SRC:-/kaggle/input}" Z=/tmp/t2s_train_d3.zip P0 D MAN
  [ -f data/train/D3_MANIFEST.json ] && return 0
  P0="$(find "$SRC" -name 't2s_train_d3.zip.part00' 2>/dev/null | head -1 || true)"
  if [ -n "$P0" ]; then
    D="$(dirname "$P0")"
    cat "$D"/t2s_train_d3.zip.part* > "$Z"
    [ "$(sha256sum "$Z" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$D/SHA256_train_d3.txt")" ] \
      || { log "D3 өгөгдлийн zip-ийн SHA256 таарсангүй ($D)"; exit 2; }
    python -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall('.')" "$Z"
  else
    MAN="$(find "$SRC" -name D3_MANIFEST.json -path '*data/train/*' 2>/dev/null | head -1 || true)"
    [ -n "$MAN" ] || { log "D3 өгөгдөл алга: t2s_train_d3.zip.part00 (эсвэл задалсан data/train/D3_MANIFEST.json) $SRC-д олдсонгүй — локалд python scripts/notebook/pack_train_d3.py --src <data/train> → Kaggle dataset → Add Input"; exit 2; }
    mkdir -p data/train && cp -r "$(dirname "$MAN")"/. data/train/
  fi
  log "D3 өгөгдөл (eval-ээр хассан = excluded): $(python -c "import json; m = json.load(open('data/train/D3_MANIFEST.json', encoding='utf-8')); print(m['counts'], 'excluded =', len(m['excluded']))")"
}
# e2test_offline: сэргээсэн E2 test-ийн (e2test stage, h_H4) хувилбаруудаас E4/E4f (reselect) ба E3c — GPU-гүй, хэдхэн
# минут; файл байхгүй бол алгасна. Q|/S| мөрийг логт хэвлэнэ (compact_results).
e2test_offline() {
  local E2T="results/runs/test/E2/${MODEL}__mn__s1__h_H4.jsonl" D
  if [ ! -f "$E2T" ]; then log "E2 test ($E2T) алга — E4/E4f/E3c test алгасав"; return 0; fi
  log "=== E2 test-ээс офлайн: E4, E4f, E3c ($(wc -l < "$E2T") мөр)"
  t2s reselect --from E2 --to E4 --tag h_H4 --split test
  t2s reselect --from E2 --to E4f --tag h_H4 --split test --no-refuse --no-clarify
  t2s derive-abstain --from E2 --to E3c --threshold 0.5 --action clarify --tag h_H4 --split test
  for D in E2 E4 E4f E3c; do python scripts/notebook/compact_results.py "results/runs/test/$D/${MODEL}__mn__s1__h_H4.jsonl"; done
}
# reselect_i_runs: өмнөх version-ий I3/I3t (dev, test) үр дүнд E4-ийн дүрмийг ЗАСВАРЛАСАН кодоор (enum, '%q', reselect-ийн
# татгалзал) дахин хэрэглэнэ → <ID>r2 (Version 9-ийн I3r/I3tr хуучин кодоор). GPU-гүй, хэдхэн минут; файлгүй бол алгасна.
reselect_i_runs() {
  local SPL ID F
  for SPL in dev test; do
    for ID in I3 I3t; do
      F="results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
      [ -f "$F" ] || continue
      log "=== $ID $SPL → ${ID}r2 (E4-ийн дүрэм, шинэ код)"
      t2s reselect --from "$ID" --to "${ID}r2" --split "$SPL"         && python scripts/notebook/compact_results.py "results/runs/$SPL/${ID}r2/${MODEL}__mn__s0.jsonl"
    done
  done
}
# ensemble_offline: E5 = E2-ийн 8 хувилбар (h_H4, seed 1) + I3t-ийн хувилбар (жин w = 1, 2, 3) хамт санал хураалт + E4-ийн
# шүүлт ба дүрэм — dev ба test. Жинг dev-ээр сонгоно (test-ийг сонголтод ашиглахгүй). GPU-гүй; файл дутвал алгасна.
ensemble_offline() {
  local SPL W
  for SPL in dev test; do
    [ -f "results/runs/$SPL/E2/${MODEL}__mn__s1__h_H4.jsonl" ] && [ -f "results/runs/$SPL/I3t/${MODEL}__mn__s0.jsonl" ] \
      || { log "E5 $SPL: E2 эсвэл I3t алга — алгасав"; continue; }
    for W in 1 2 3; do
      log "=== E5w$W $SPL (E2 + I3t×$W, E4-ийн дүрэм)"
      t2s reselect --from E2 --to "E5w$W" --tag h_H4 --split "$SPL" --extra I3t --extra-weight "$W" \
        && python scripts/notebook/compact_results.py "results/runs/$SPL/E5w$W/${MODEL}__mn__s1__h_H4.jsonl"
    done
  done
}
# d3_ckpt <хавтас>: хамгийн сүүлийн БҮРЭН d3 checkpoint (trainer_state.json-той) — хавтасны шууд checkpoint-* (сургалтын
# output_dir) эсвэл доторх (рекурсив) .../d3__*/checkpoint-* (өмнөх сессийн Output); байхгүй бол хоосон
d3_ckpt() {
  python - "$1" <<'PYEOF'
import re, sys
from pathlib import Path
best = None
root = Path(sys.argv[1])
hits = list(root.glob("checkpoint-*/trainer_state.json")) + list(root.glob("**/d3__*/checkpoint-*/trainer_state.json")) \
    if root.is_dir() else []
for p in hits:
    m = re.fullmatch(r"checkpoint-(\d+)", p.parent.name)
    if m and (best is None or int(m.group(1)) > best[0]):
        best = (int(m.group(1)), str(p.parent))
print(best[1] if best else "")
PYEOF
}

case "$STAGE" in
  caches)
    log "### Кэш: A4 орчуулга ба F8 router (qwen3.5-9b), F7 intent (gemma-4-e4b)"
    bash "$S/serve_model.sh" qwen3.5-9b
    t2s translate --split all --variants mn,latin,mixed
    t2s route --split all --variants mn,latin
    bash "$S/serve_model.sh" gemma-4-e4b
    t2s intent --split all --variants mn,latin,mixed
    ;;
  A)
    for MODEL in $MODELS; do
      log "### A бүлэг — $MODEL"
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin,en,mixed run A1 "$MODEL"          # A2 (en), A3 (latin) нь A1-ийн хувилбарууд
      VARIANTS=mn,latin,en run "A6,A7" "$MODEL"
      run A4 "$MODEL"
      if [ "$MODEL" = sqrl-9b ]; then run A5 "$MODEL"; fi
    done
    log "A дууссан: results/REPORT.md-ээс A1/A6/A7-ийн шилдгийг (dev, кирилл) сонгоод STAGE=B BEST_A=<ID> ..."
    ;;
  B)
    need BEST_A A6
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      if [ -n "${B_IDS:-}" ]; then   # удаан GPU (T4)-д цөөрүүлсэн дараалал, жишээ нь B_IDS=B5,B1,B2,B4,B3 B_VARIANTS=mn
        VARIANTS="${B_VARIANTS:-mn}" run "$B_IDS" "$MODEL"
      else
        run "B1,B2,B3,B4" "$MODEL"
        VARIANTS=mn,latin,en run B5 "$MODEL"
        run "B6,B7,B8,B9,B10" "$MODEL"
      fi
    done
    ;;
  adapters)   # C1–C3 adapter-ийн үнэлгээ (eval_adapters.sh). Kaggle: хэсэгчилсэн zip-ийг /kaggle/input-ээс угсарна
    need BEST_A A6
    if ! ls -d adapters/c*__* >/dev/null 2>&1; then
      P0="$(find "${ADAPTER_SRC:-/kaggle/input}" -name 't2s_adapters16.zip.part00' 2>/dev/null | head -1)"
      [ -n "$P0" ] || { log "adapters/ алга, t2s_adapters16.zip.part00 ч олдсонгүй"; exit 2; }
      D="$(dirname "$P0")"
      cat "$D"/t2s_adapters16.zip.part* > /tmp/t2s_adapters16.zip
      if [ -f "$D/SHA256.txt" ]; then
        [ "$(sha256sum /tmp/t2s_adapters16.zip | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$D/SHA256.txt")" ] || { log "adapter zip-ийн SHA256 таарсангүй"; exit 2; }
      fi
      python -c "import zipfile; zipfile.ZipFile('/tmp/t2s_adapters16.zip').extractall('.')"
      log "adapters угсарлаа: $(ls -d adapters/*__* | tr '\n' ' ')"
    fi
    for MODEL in $MODELS; do
      RECIPES="${RECIPES:-c1 c2 c3}" C_VARIANTS="${C_VARIANTS:-mn}" SKIP_GENERAL="${SKIP_GENERAL:-1}" \
        bash "$S/eval_adapters.sh" "$MODEL"
    done
    ;;
  e2test)   # E2 (BEST_CONFIG дээр засвар + 8 санал) test split, Kaggle T4; дараа нь ижил GPU-д H4 (tag t4)
    need BEST_A A7; need BEST_CONFIG H4
    MODEL=arctic-r1-7b
    serve_d2_merged
    for ID in ${E2TEST_IDS:-E2 H4}; do
      if [ "$ID" = E2 ]; then ARGS=(--seeds 1 --tag "h_${BEST_CONFIG}"); OUTF="${MODEL}__mn__s1__h_${BEST_CONFIG}"
      else ARGS=(--tag t4); OUTF="${MODEL}__mn__s0__t4"; fi
      log "=== TEST $ID ${ARGS[*]}"
      # shellcheck disable=SC2086
      t2s eval --run "$ID" --model "$MODEL" --split test --variants mn --workers "${WORKERS:-8}" $VARS "${ARGS[@]}"
      python scripts/notebook/compact_results.py "results/runs/test/$ID/$OUTF.jsonl"
    done
    ;;
  istage)   # I бүлэг (80%-ийн төлөвлөгөө): жишээний санд dev-ийн асуулт (ба шинэ жишээ) — индекс → I_RUNS.
            # dev дээр асуулт бүр өөрийн family-ийн жишээг татахгүй (Pipeline.item_families); test-д dev бүгд орно.
            # I_RUNS: "<ID>:<split>" эсвэл E2-ийг I тохиргоон дээр: "E2@I1:test" (seed 1, tag h_I1)
    need BEST_A A7
    MODEL=arctic-r1-7b
    # 0) Өмнөх version-ий (input-ээр сэргээсэн архив) E2 test → E4/E4f (reselect) ба E3c — GPU-гүй
    e2test_offline
    build_index() {   # build_index <хавтас> [index-build аргумент...] — bge-m3 GPU дээр, vLLM-ээс өмнө
      local out="$1"; shift
      [ -f "$out/.ok" ] || { T2S_EMBED_DEVICE=auto t2s index-build "$@" --out "$out" > "$out.json" && touch "$out/.ok"; }
    }
    build_index build/index_dev --dev-examples
    if [ -f knowledge/examples_plus.jsonl ]; then
      build_index build/index_plus --extra-examples knowledge/examples_plus.jsonl
      build_index build/index_plus_dev --dev-examples --extra-examples knowledge/examples_plus.jsonl
      # J1: 52 нэр томьёотой glossary_plus.yaml-ийн embedding-тэй тусдаа индекс (бусад тохиргоонд нөлөөлөхгүй)
      if [[ " ${I_RUNS:-} " == *" J1"[@:]* || " ${I_RUNS:-} " == *" J1r"[@:]* ]]; then
        build_index build/index_plus_dev_j --dev-examples --extra-examples knowledge/examples_plus.jsonl \
          --glossary knowledge/glossary_plus.yaml
      fi
    fi
    # I4: 2-р шатны жишээ нэмсэн сан (examples_plus_all.jsonl)
    if [[ " ${I_RUNS:-} " == *" I4"* ]] && [ -f knowledge/examples_plus_all.jsonl ]; then
      build_index build/index_plus2_dev --dev-examples --extra-examples knowledge/examples_plus_all.jsonl
    fi
    serve_d2_merged
    for R in ${I_RUNS:-I1:dev I1:test}; do
      ID="${R%%:*}"; SPL="${R##*:}"
      if [[ "$ID" == E2@* ]]; then
        BC="${ID#E2@}"; RID=E2; ARGS=(--var best_config="$BC" --seeds 1 --tag "h_${BC}"); OUTF="${MODEL}__mn__s1__h_${BC}"
      else
        RID="$ID"; ARGS=(); OUTF="${MODEL}__mn__s0"
      fi
      log "=== $RID $SPL ${ARGS[*]}"
      # shellcheck disable=SC2086
      t2s eval --run "$RID" --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-8}" $VARS "${ARGS[@]}"
      python scripts/notebook/compact_results.py "results/runs/$SPL/$RID/$OUTF.jsonl"
      # E4-ийн дүрмийг (хувилбар шүүлт, %q, дүрмийн татгалзал/тодруулга) мөн хэрэглэнэ — загваргүй
      if [ "$RID" = E2 ]; then DST=E4; TG=(--tag "h_${BC}"); else DST="${RID}r"; TG=(); fi
      t2s reselect --from "$RID" --to "$DST" --split "$SPL" "${TG[@]}"
      python scripts/notebook/compact_results.py "results/runs/$SPL/$DST/$OUTF.jsonl"
    done
    ;;
  web)   # Kaggle 2×T4 дээр асуулт-хариултын вэб: GPU0 = D2 (4-bit), GPU1 = орчуулагч qwen3.5-9b (4-bit), DB, вэб,
         # Cloudflare-ийн үнэгүй tunnel (trycloudflare) → нийтийн холбоос логт «WEB URL:». WEB_MIN минут ажиллана.
    MODEL=arctic-r1-7b
    python -c "import bitsandbytes" 2>/dev/null || pip install -q bitsandbytes
    pip cache purge > /dev/null 2>&1 || true
    log "диск: $(df -h / | tail -1 | awk '{print $4" сул / "$2}')"
    if [ ! -f build/index_web/.ok ]; then
      T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus_all.jsonl \
        --out build/index_web > build/index_web.json && touch build/index_web/.ok
    fi
    hf_models
    KEY="$(python -c "import secrets; print(secrets.token_urlsafe(9))")"
    T2S_CONFIG=config/web.yaml T2S_WEB_MODEL=d2 T2S_WEB_TRANSLATOR=qwen3.5-9b T2S_WEB_TRANSLATOR_URL=http://localhost:8001/v1 \
      T2S_WEB_KEY="$KEY" T2S_LLM_TIMEOUT_S=900 nohup python -m mn_text2sql serve --run WEB --host 127.0.0.1 --port 8090 \
      > build/logs/web.log 2>&1 &
    for i in $(seq 1 60); do curl -sf localhost:8090/health >/dev/null && break; sleep 5; done
    [ -x /tmp/cloudflared ] || curl -sL -o /tmp/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /tmp/cloudflared
    nohup /tmp/cloudflared tunnel --url http://localhost:8090 --no-autoupdate > build/logs/cloudflared.log 2>&1 &
    URL=""
    for i in $(seq 1 60); do URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' build/logs/cloudflared.log | head -1 || true)"; [ -n "$URL" ] && break; sleep 3; done
    log "WEB URL: ${URL:-олдсонгүй (build/logs/cloudflared.log)}/?k=$KEY"
    log "FLOW BUILDER: ${URL:-?}/builder?k=$KEY   (pipeline-ийг node-оор угсарч нэг асуултаар турших)"
    END=$(( $(date +%s) + ${WEB_MIN:-180} * 60 ))
    # 20 с тутам вэб/загварын логийн шинэ алдаа, загварын хугацааг энэ цонхонд хэвлэнэ; 5 мин тутам төлөв
    N0=0; T5=$(date +%s)
    while [ "$(date +%s)" -lt "$END" ]; do
      sleep 20
      N1=$(cat build/logs/web.log build/logs/hf_d2.log build/logs/hf_tr.log 2>/dev/null | wc -l)
      [ "$N1" -le "$N0" ] || cat build/logs/web.log build/logs/hf_d2.log build/logs/hf_tr.log 2>/dev/null | tail -n $(( N1 - N0 ))         | grep -E "Error|error|Exception|Traceback|амжилтгүй|токен|^  File|OutOfMemory" | grep -v "GET /job" | tail -40 || true
      N0=$N1
      if [ $(( $(date +%s) - T5 )) -ge 300 ]; then
        T5=$(date +%s)
        log "вэб ажиллаж байна: $(grep -c 'POST /ask' build/logs/web.log || true) асуулт; үлдсэн $(( (END - $(date +%s)) / 60 )) мин; WEB URL: ${URL}/?k=$KEY"
      fi
    done
    ;;
  t2)   # T2 орчуулга (маскыг DB-ийн оноосон нэрээр шүүх + semantic_model/glossary-ийн нэр томьёоны RAG; translate_terms.py)
        # → I3t2 (I3 + qwen3.5-9b.t2 кэш) ба I3t2r (E4-ийн дүрэм). Kaggle 2×T4: орчуулагч transformers 4-bit (GPU1) →
        # унтраагаад D2-ийг I3t-тэй ИЖИЛ замаар (serve_d2_merged, vLLM fp16) үйлчилнэ — тоо шууд харьцуулагдана.
        # (Colab 1×T4 дээр D2 4-bit + ~20k prompt: ~2 мин/асуулт, OOM — 2026-10-20 туршсан, тохирохгүй.)
        # T2_SPLITS (анхдагч "dev test"), RESULTS_DST — үр дүнг хуулах хавтас (жишээ нь Drive).
    MODEL=arctic-r1-7b
    VARS="--var best_a=${BEST_A:-A7}"
    python -c "import bitsandbytes" 2>/dev/null || pip install -q bitsandbytes
    if [ ! -f build/index_plus_dev/.ok ]; then
      T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
        --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
    fi
    mkdir -p build/logs
    NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"; GT=0; [ "${NG:-1}" -ge 2 ] && GT=1
    CUDA_VISIBLE_DEVICES=$GT nohup python scripts/server/hf_openai_server.py --model Qwen/Qwen3.5-9B \
      --served qwen3.5-9b --port 8001 > build/logs/hf_tr.log 2>&1 &
    for i in $(seq 1 120); do curl -sf localhost:8001/v1/models >/dev/null && break; sleep 10; done
    curl -sf localhost:8001/v1/models >/dev/null || { tail -30 build/logs/hf_tr.log; exit 2; }
    for SPL in ${T2_SPLITS:-dev test}; do
      log "T2 орчуулга: $SPL"
      t2s translate --t2 --split "$SPL" --variants mn --model qwen3.5-9b --base-url http://localhost:8001/v1 --workers 4
    done
    pkill -f "served qwen3.5-9b" || true
    sleep 10
    serve_d2_merged
    for SPL in ${T2_SPLITS:-dev test}; do
      log "=== I3t2 $SPL"
      # shellcheck disable=SC2086
      T2S_LLM_TIMEOUT_S=900 t2s eval --run I3t2 --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-4}" $VARS
      python scripts/notebook/compact_results.py "results/runs/$SPL/I3t2/${MODEL}__mn__s0.jsonl"
      t2s reselect --from I3t2 --to I3t2r --split "$SPL"
      python scripts/notebook/compact_results.py "results/runs/$SPL/I3t2r/${MODEL}__mn__s0.jsonl"
      if [ -n "${RESULTS_DST:-}" ]; then
        mkdir -p "$RESULTS_DST/$SPL"
        cp -r "results/runs/$SPL/I3t2" "results/runs/$SPL/I3t2r" "$RESULTS_DST/$SPL/"
        cp build/translations/qwen3.5-9b.t2.jsonl "$RESULTS_DST/"
        log "үр дүн хуулсан → $RESULTS_DST/$SPL"
      fi
    done
    ;;
  agent)   # K1–K3 (docs/AGENT.md «K»): I3t2 + agent. STAGE=t2-ийн дараа (qwen3.5-9b.t2 кэш хэрэгтэй). Шийдвэр dev дээр;
           # AGENT_TEST=<ID> өгвөл зөвхөн тэр ID-г test дээр нэг удаа. AGENT_IDS (анхдагч "K1 K2").
    MODEL=arctic-r1-7b
    VARS="--var best_a=${BEST_A:-A7}"
    [ -f build/translations/qwen3.5-9b.t2.jsonl ] || { echo "T2 кэш алга — эхлээд STAGE=t2"; exit 2; }
    curl -sf localhost:8000/v1/models >/dev/null || serve_d2_merged
    RUNS=""; for ID in ${AGENT_IDS:-K1 K2}; do RUNS="$RUNS dev:$ID"; done
    [ -n "${AGENT_TEST:-}" ] && RUNS="$RUNS test:$AGENT_TEST"
    for R in $RUNS; do
      SPL="${R%%:*}"; ID="${R#*:}"
      log "=== $ID $SPL"
      # shellcheck disable=SC2086
      T2S_LLM_TIMEOUT_S=900 t2s eval --run "$ID" --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-4}" $VARS
      python scripts/notebook/compact_results.py "results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
      if [ -n "${RESULTS_DST:-}" ]; then
        mkdir -p "$RESULTS_DST/$SPL" && cp -r "results/runs/$SPL/$ID" "$RESULTS_DST/$SPL/"
      fi
    done
    ;;
  train_d3)   # D3 (docs/IMPROVE_80_PLAN.md «D3»): H4s-ийн богино prompt-оор өөрийн DB + BIRD 600 → QLoRA SFT (2×T4 DDP)
              # → fp16 нэгтгэх → (D3_EVAL=1) served=d3, D3_RUNS (анхдагч "D3s:dev", нэг хувилбар). Checkpoint, SFT мөр,
              # эцсийн adapter нь /kaggle/working-д (12 цагийн хязгаараар тасарсан ч үлдэнэ). Үргэлжлүүлэх: өмнөх version-ий
              # Output-ыг Add Input хийгээд ижил STAGE=train_d3 — дууссан adapter/checkpoint/SFT мөрийг input-ээс авна.
    need BEST_A A7
    MODEL=arctic-r1-7b
    export T2S_BEST_A="$BEST_A"           # сургалтын prompt (H4s) нь үнэлгээний vars-тай ижил
    T0=$(date +%s); mins() { echo "$(( ($(date +%s) - T0) / 60 )) мин"; }
    # D3_BUDGET_MIN: энэ stage-д өгөх нийт минут (Kaggle-ийн долоо хоногийн GPU квот − bootstrap − нөөц), e2test_offline
    # орно. Сургалт T_END-ээс 10 мин өмнө checkpoint хадгалаад зогсоно; үнэлгээ бүр T_END-ээс 5 мин өмнө timeout-оор
    # зогсоно (дараа үргэлжилнэ)
    T_END=""; if [ -n "${D3_BUDGET_MIN:-}" ]; then T_END=$(( T0 + D3_BUDGET_MIN * 60 )); fi
    left_s() { if [ -n "$T_END" ]; then echo $(( T_END - $(date +%s) )); else echo 999999; fi; }
    # input-д E2 test (t2s-v8-output) байвал E4/E4f/E3c — алдаа гарвал сургалтыг зогсоохгүй
    if [ "${E2TEST_OFFLINE:-1}" = 1 ]; then e2test_offline || log "e2test_offline алдаатай — алгасав"; fi
    if [ "${E2TEST_OFFLINE:-1}" = 1 ]; then reselect_i_runs || log "reselect_i_runs алдаатай — алгасав"; fi
    if [ "${E2TEST_OFFLINE:-1}" = 1 ]; then ensemble_offline || log "ensemble_offline алдаатай — алгасав"; fi
    SRC="${TRAIN_SRC:-/kaggle/input}"
    W="${D3_WORK:-/kaggle/working}"; [ -d "$W" ] || W="$T2S_ROOT/adapters"   # build/ биш (notebook 15 мин тутам архивладаг)
    TD="$W/d3_train"; AD="$TD/d3__$MODEL"; DATA="$TD/d3__$MODEL.jsonl"; OUTA="$W/adapters_out/d3__$MODEL"
    M="adapters/d3__${MODEL}__merged16"
    mkdir -p "$TD"
    log "### D3: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ';') → $TD"
    # 0) sft.py-г шалгасан хувилбаруудыг түгжинэ (TRL/transformers-ийн API өөрчлөлтөөс хамгаална; D3_PINS=0 бол алгасна)
    if [ "${D3_PINS:-1}" = 1 ]; then
      log "D3: requirements-d3.txt (vllm/transformers/trl/peft/accelerate/bitsandbytes хувилбар түгжих)"
      pip install -q -r requirements-d3.txt
      pip uninstall -y -q torchaudio || true      # bootstrap-тай ижил: vLLM-ийн torch-той CUDA-гаар зөрдөг
    fi
    log "хувилбар: $(python -c "import torch, transformers, trl, peft, accelerate, bitsandbytes as b; print('torch', torch.__version__, '| transformers', transformers.__version__, '| trl', trl.__version__, '| peft', peft.__version__, '| accelerate', accelerate.__version__, '| bitsandbytes', b.__version__)")"
    # 1) Өмнөх сессийн гаралт: дууссан adapter бол сургалтгүй; эс бөгөөс checkpoint ба ТЭР сессийн SFT мөр (ижил өгөгдөл)
    if [ ! -f "$AD/t2s_meta.json" ]; then
      PREV="$(find "$SRC" -path "*adapters_out/d3__$MODEL/t2s_meta.json" 2>/dev/null | head -1 || true)"
      if [ -n "$PREV" ]; then mkdir -p "$AD"; cp "$(dirname "$PREV")"/* "$AD"/; log "D3 adapter өмнөх сессээс: $(dirname "$PREV")"; fi
    fi
    if [ ! -f "$AD/t2s_meta.json" ]; then
      if [ -z "$(d3_ckpt "$AD")" ]; then
        CK="$(d3_ckpt "$SRC")"
        if [ -n "$CK" ]; then mkdir -p "$AD"; cp -r "$CK" "$AD/"; log "checkpoint сэргээв: $CK"; fi
      fi
      if [ ! -f "$DATA" ]; then
        PD="$(find "$SRC" -path "*d3_train/d3__$MODEL.jsonl" 2>/dev/null | head -1 || true)"
        if [ -n "$PD" ]; then
          cp "$PD" "$DATA"; cp "$(dirname "$PD")/build_report.json" "$TD/" 2>/dev/null || true
          log "SFT мөрийг өмнөх сессээс авав ($PD) — checkpoint-той ижил өгөгдөл"
        fi
      fi
      # 2) SFT мөр: индекс (bootstrap-ийн build/index + build/index_plus = 160 + 195 жишээ, dev-гүй; bge-m3 GPU, vLLM-ээс
      #    өмнө) → өгөгдөл задлах → H4s-ээр угсрах (өөрийн family-г few-shot-оос хасна; урт мөрийн хасалтыг тайлагнана)
      if [ ! -f "$DATA" ]; then
        [ -f build/index/.ok ] || { T2S_EMBED_DEVICE=auto t2s index-build > build/index.json && touch build/index/.ok; }
        if [ ! -f build/index_plus/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --extra-examples knowledge/examples_plus.jsonl --out build/index_plus \
            > build/index_plus.json && touch build/index_plus/.ok
        fi
        unpack_d3_data
        log "D3 SFT мөр угсарч байна (prompt=h4s, index=build/index_plus) …"
        T2S_EMBED_DEVICE=auto t2s_train build --recipe d3 --model "$MODEL" > "$TD/build_report.json"
        cat "$TD/build_report.json"
        cp "data/train/sft/d3__$MODEL.jsonl" "$DATA"
        log "SFT мөр бэлэн ($(mins)): $(wc -l < "$DATA") мөр → $DATA"
      fi
      # 3) QLoRA SFT: 2 процесс (GPU бүрт нэг) — torch.distributed.run; checkpoint T2S_SAVE_STEPS алхам тутам $AD-д
      NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"; NP=1; [ "${NG:-0}" -ge 2 ] && NP=2
      # Kaggle-ийн 2×T4 (PCIe) дээр NCCL P2P гацах тохиолдол бий — SHM-ээр; NCCL алдаа гарвал гацахгүй зогсооно
      export T2S_SAVE_STEPS="${T2S_SAVE_STEPS:-10}" TOKENIZERS_PARALLELISM=false \
        PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" \
        NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}" TORCH_NCCL_ASYNC_ERROR_HANDLING="${TORCH_NCCL_ASYNC_ERROR_HANDLING:-1}"
      # D3_TRAIN_MIN: сургалтад өгөх дээд минут; D3_BUDGET_MIN-тэй бол T_END − 10 мин (аль эрт нь). Хэтэрвэл checkpoint
      # хадгалаад зогсоно (exit 0 — Output хадгалагдана), дараагийн сесс тэр checkpoint-оос үргэлжилнэ
      DL=""
      if [ -n "${D3_TRAIN_MIN:-}" ]; then DL=$(( $(date +%s) + D3_TRAIN_MIN * 60 )); fi
      if [ -n "$T_END" ] && { [ -z "$DL" ] || [ $(( T_END - 600 )) -lt "$DL" ]; }; then DL=$(( T_END - 600 )); fi
      if [ -n "$DL" ]; then export T2S_TRAIN_DEADLINE="$DL"; else unset T2S_TRAIN_DEADLINE; fi
      log "D3 SFT эхэлж байна: $NP процесс, checkpoint $T2S_SAVE_STEPS алхам тутам → $AD (сүүлийнх: $(d3_ckpt "$AD"))${DL:+, хязгаар $(( (DL - $(date +%s)) / 60 )) мин}"
      if [ "$NP" -ge 2 ]; then
        python -m torch.distributed.run --standalone --nproc_per_node "$NP" -m mn_text2sql.training \
          sft --recipe d3 --model "$MODEL" --output-dir "$AD" --data-file "$DATA"
      else
        t2s_train sft --recipe d3 --model "$MODEL" --output-dir "$AD" --data-file "$DATA"
      fi
      if [ ! -f "$AD/t2s_meta.json" ]; then
        CKL="$(d3_ckpt "$AD")"
        if [ -n "${T2S_TRAIN_DEADLINE:-}" ] && [ -n "$CKL" ]; then
          log "D3 SFT хугацааны хязгаарт зогсов ($(mins)); checkpoint: $CKL — дараагийн сесст энэ version-ий Output-ыг input болгоод ижил STAGE=train_d3"
          exit 0
        fi
        log "D3 SFT дуусаагүй ($AD) — дараагийн сесст Output-ыг input болгоод дахин"; exit 3
      fi
      log "D3 SFT дууслаа ($(mins))"
    fi
    # 4) Эцсийн adapter-ийг (15 GB-ийн нэгтгэсэн жин биш) татаж авах/дахин ашиглахад; checkpoint-ийг устгаж зай чөлөөлнө
    mkdir -p "$OUTA"
    for f in adapter_config.json adapter_model.safetensors t2s_meta.json; do cp "$AD/$f" "$OUTA/"; done
    [ -f "$TD/build_report.json" ] && cp "$TD/build_report.json" "$OUTA/"
    rm -rf "$AD"/checkpoint-*
    log "D3 adapter → $OUTA ($(du -sh "$OUTA" | cut -f1))"
    # 5) fp32-оор нэгтгээд fp16 жин (T4 дээр vLLM-ийн LoRA удаан — serve_d2_merged-тэй ижил); зөвхөн D3_EVAL=1 үед
    #    (нэгтгэсэн жин /tmp-д — сесс дуусахад алга болдог тул үнэлгээгүй бол квот үрэхгүй)
    if [ "${D3_EVAL:-0}" = 1 ] && [ "$(left_s)" -lt 2400 ]; then
      log "D3 үнэлгээ алгасав: D3_BUDGET_MIN-ээс $(( $(left_s) / 60 )) мин үлдсэн (нэгтгэх + vLLM + асуултад < 40 мин)"
      D3_EVAL=0
    fi
    if [ "${D3_EVAL:-0}" = 1 ] && [ ! -f "$M/t2s_merge.json" ]; then
      HF_ID="$(python -c "import yaml; print(yaml.safe_load(open('config/models.yaml', encoding='utf-8'))['models']['$MODEL']['hf_id'])")"
      log "D3 LoRA-г нэгтгэж байна → $M"
      python scripts/notebook/merge_lora.py --base "$HF_ID" --adapter "$AD" --out "$M"
      [ -d /kaggle ] && rm -rf "${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${HF_ID//\//--}"
      log "нэгтгэсэн ($(mins))"
    fi
    # 6) D3_EVAL=1: D3_RUNS = "<ID>[@<served>]:<split> ..." (анхдагч "D3s:dev"). served = d3 (нэгтгэсэн D3) эсвэл d2
    #    (serve_d2_merged — t2s_adapter_d2 part-ууд input-д хэрэгтэй); @-гүй бол run-ий adapter (H4s → d2, D3s → d3).
    #    Жишээ: D3_RUNS="H4s:dev D3s:dev" (H4s нь D2-оор = зөвхөн богино prompt-ын нөлөө). Хоёр нэгтгэсэн загвар + суурь
    #    жин ~45 GB диск шаардана. I3s3-д build/index_plus_dev (GPU, vLLM-ээс өмнө).
    if [ "${D3_EVAL:-0}" = 1 ]; then
      RUNS="${D3_RUNS:-D3s:dev}"
      # H4s/D3s-ийн жишээний сан = сургалтынх (build/index_plus); өмнөх сессийн SFT мөрөөр үргэлжилсэн бол бүтээгдээгүй
      if [ ! -f build/index_plus/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --extra-examples knowledge/examples_plus.jsonl --out build/index_plus \
          > build/index_plus.json && touch build/index_plus/.ok
      fi
      # M1s2/M1s3: MADLAD M1 орчуулгын кэш (V19-ийн Output-оос үүсгэсэн хувийн dataset-ийг Add Input) → build/translations
      for F in $(find "${TRAIN_SRC:-/kaggle/input}" -path "*mt/m1_madlad_*.jsonl" 2>/dev/null || true); do
        [ -f "build/translations/$(basename "$F")" ] || cp "$F" build/translations/
      done
      if [[ " $RUNS " == *" M1s"* ]] && [ ! -f build/translations/m1_madlad_improved.jsonl ]; then
        log "M1s*: m1_madlad_improved.jsonl кэш алга (MADLAD M1 Output-ыг Add Input хий) — алгасав"; RUNS="$(echo " $RUNS " | sed -E 's/ M1s[0-9]+(@[a-z0-9]+)?:[a-z]+//g')"
      fi
      if [[ " $RUNS " == *" I3s3"* || " $RUNS " == *" M1s"* ]] && [ ! -f build/index_plus_dev/.ok ]; then   # I3s3, I3s3t, M1s2, M1s3
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
          --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
      fi
      if [[ " $RUNS " == *" I4"* ]] && [ -f knowledge/examples_plus_all.jsonl ] && [ ! -f build/index_plus2_dev/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus_all.jsonl           --out build/index_plus2_dev > build/index_plus2_dev.json && touch build/index_plus2_dev/.ok
      fi
      CUR=""
      for R in $RUNS; do
        SPL="${R##*:}"; IDS="${R%:*}"; ID="${IDS%%@*}"
        if [[ "$IDS" == *@* ]]; then SRV="${IDS#*@}"
        else SRV="$(python -c "from mn_text2sql.pipeline.options import resolve_run; print(resolve_run('$ID').adapter or 'd3')")"; fi
        if [ "$SRV" != "$CUR" ]; then
          log "served=$SRV-д шилжиж байна (диск: $(df -h . | tail -1 | awk '{print $4}') чөлөөтэй)"
          case "$SRV" in
            d3) MODEL_PATH="$M" SERVED=d3 bash "$S/serve_model.sh" "$MODEL" ;;
            d2) serve_d2_merged ;;
            *) log "D3_RUNS: «$R» — served нь d3 эсвэл d2 байна"; exit 2 ;;
          esac
          CUR="$SRV"
        fi
        REM=$(( $(left_s) - 300 ))
        if [ "$REM" -lt 600 ]; then log "=== $ID $SPL алгасав: хугацаа ($(( REM / 60 )) мин) — дараагийн сесст"; break; fi
        log "=== $ID $SPL (served $SRV)${T_END:+, хязгаар $(( REM / 60 )) мин}"
        # shellcheck disable=SC2086
        timeout "$REM" python -m mn_text2sql eval --run "$ID" --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-8}" $VARS \
          || log "$ID $SPL: зогссон (timeout/алдаа $?) — дууссан асуулт хадгалагдсан, дараа үргэлжилнэ"
        python scripts/notebook/compact_results.py "results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl" || true
        # E4-ийн дүрэм (хувилбарын шүүлт, '%q', дүрмийн татгалзал/тодруулга) — загваргүй, хэдхэн минут
        t2s reselect --from "$ID" --to "${ID}r" --split "$SPL"           && python scripts/notebook/compact_results.py "results/runs/$SPL/${ID}r/${MODEL}__mn__s0.jsonl"           || log "$ID $SPL: reselect алгасав"
      done
    fi
    log "D3 бүгд ($(mins))"
    ;;
  mt)   # Орчуулгын загвар M0/M1 (docs/MT_INVARIANT_PLAN.md §5): Kaggle T4. MT_STEPS (анхдагч "m0 madlad eval t2s"):
        #   m0     — MADLAD-400-3B zero-shot dev (латин шууд «none» ба кирилл болгож «improved»)
        #   madlad — MADLAD QLoRA (M1; scripts/mt/train_mt.py, $MT_DATA/sft/sft_madlad.jsonl), val chrF++
        #   qwen   — Qwen3.5-9B QLoRA (M1; удаан — тусдаа сесст)
        #   eval   — fine-tune хийсэн загвар(ууд)-аар dev орчуулга → baseline_dev (chrF++, нэр томьёо, утга, тоо)
        #   lex2   — (V28, F2) M1 MADLAD dev орчуулгыг --hints lexicon2-оор дахин → m1_madlad_lex2 кэш (M1lx-д); vLLM-ийг зогсооно
        #   g1     — (DB, загваргүй) FULL_REVIEW G1: gold SQL-ийн валютын шалгалт → results/g1_currency.json (log-д хураангуй)
        #   secdb  — (DB, загваргүй) 110_roles_rls.sql-ийн REVOKE bot role-д үйлчилж буйг шалгана → results/db_revoke_check.json
        #   ense2  — (CPU) M1ensE2 = E2 (8 хувилбар, h_H4) + M1ks M1a M1g8 M1m M1rr I3t (жин 2): dev; test нь ALLOW_TEST=1 үед л
        #            (2026-11-01 dev CV: хариулах 137 → 149.7/224; урьдчилан бүртгэсэн — docs/ACCURACY_PLAN.md «M1ensE2»);
        #            + M1ensE2r = ижил + M1ensr-ийн дүрэм (V29-өөс; бүрэн гаралттай run-ийг дахин тооцохгүй)
        #   r7     — (CPU) postproc R7 (гаралтын DATE_TRUNC → DATE, reselect --trunc-date): M1ens_r7, M1ensE2r_r7; dev, test нь
        #            ALLOW_TEST=1 үед л (2026-11-01 V27 dev-ийн алдааны шинжилгээ; урьдчилан бүртгэсэн — ACCURACY_PLAN «R7»)
        #   tzfix  — (CPU) метрикийн цагийн бүсийн мэдрэмж: M1a, M1ens-ийг хадгалсан SQL-ээр дахин оноолно — *_rs (хяналт,
        #            анхны тоог давтах ёстой) ба *_tzn (T2S_TZ_NAIVE=1); dev, test нь ALLOW_TEST=1 үед л
        #   afx    — (CPU) M1afx-ийн 4 засвар (guard_scope, transpile_fix, extract_fallback, literal_fix) хадгалсан raw_output-д
        #            (reselect --refix afx): M1a_fx (суурь M1a_rs), M1ensfx, M1ensfxr, M1ensE2rfx_r7; dev, test нь ALLOW_TEST=1 үед л
        #            (урьдчилан бүртгэсэн — ACCURACY_PLAN «afx»)
        #   t2s    — D2-оор (I3t-тэй ижил зам) MT_T2S-ийн run-уудыг dev дээр, MT_T2S-ийн ЭРЭМБЭЭР (кэш байгааг л); run бүр
        #            timeout-тай (төсвийн төгсгөлөөс MT_TAIL_MIN=25 мин өмнө зогсоно — дараагийн сесст үргэлжилнэ)
        #   tens / test — TEST split (эцсийн хэмжилт): override-д ALLOW_TEST=1 тодорхой заасан үед Л ажиллана
        # Алхмууд скриптийн доорх дарааллаар ажиллана (MT_STEPS-ийн дарааллаар биш). Үл мэдэгдэх алхам/run-ийг анхааруулна.
        # Гаралт $W (/kaggle/working/mt): adapter, val_report.json, score_*.txt, кэш; RESULTS_DST руу хуулна. Тасарвал дахин
        # ажиллуулахад дууссан алхмыг алгасна (adapter-ийн t2s_meta.json, кэш файл).
    MODEL=arctic-r1-7b
    VARS="--var best_a=${BEST_A:-A7}"
    W="${MT_WORK:-/kaggle/working}/mt"; [ -d /kaggle/working ] || W="$T2S_ROOT/adapters/mt"
    MTD="${MT_DATA:-data/mt/v2}"
    STEPS=" ${MT_STEPS:-m0 madlad eval t2s} "
    # t2s алхмын run → орчуулгын кэш (config/experiments.yaml-ийн translation_cache; кэш байгаа run-ийг л ажиллуулна)
    MT_T2S_CACHES="M0a:m0_madlad_improved M1a:m1_madlad_improved M1q:m1_q9b_improved M1m:m1_madlad_marked M1i4:m1_madlad_improved
      M1g8:m1_madlad_improved M1ks:m1_madlad_improved M1rr:m1_madlad_improved M1ip:m1_madlad_improved M1fs:m1_madlad_improved
      M1vf:m1_madlad_improved M1rt:m1_madlad_improved M1cte:m1_madlad_improved M1th:m1_madlad_improved M1dec:m1_madlad_improved
      M1ksf:m1_madlad_improved M1fk:m1_madlad_improved M1tc:m1_madlad_improved M1alt1:m1_madlad_alt_b1 M1alt8:m1_madlad_alt_b8
      M1lx:m1_madlad_lex2 M1afx:m1_madlad_improved"
    t2s_cache() { local P; for P in $MT_T2S_CACHES; do [ "${P%%:*}" = "$1" ] && { echo "${P#*:}"; return 0; }; done; return 1; }
    # Үл мэдэгдэх MT_STEPS/MT_T2S/MT_TENS утгыг өмнө нь чимээгүй алгасдаг байсан — эхэнд ба төгсгөлд анхааруулна
    MT_WARN=""
    for X in $STEPS; do
      case " m0 madlad qwen eval mark lex2 g1 secdb rerank t2s reselect tens ens pick ensx ense2 r7 tzfix afx v2l fgold fens vote test " in *" $X "*) ;;
        *) MT_WARN="$MT_WARN MT_STEPS:$X" ;; esac
    done
    for X in ${MT_T2S:-}; do t2s_cache "$X" >/dev/null || MT_WARN="$MT_WARN MT_T2S:$X"; done
    for X in ${MT_TENS:-}; do case " M1ks M1rr M1g8 M1m " in *" $X "*) ;; *) MT_WARN="$MT_WARN MT_TENS:$X" ;; esac; done
    for X in ${MT_FENS:-}; do case " M1a M1rr M1ks M1g8 M1m " in *" $X "*) ;; *) MT_WARN="$MT_WARN MT_FENS:$X" ;; esac; done
    [ -z "$MT_WARN" ] || log "!!! АНХААР: үл мэдэгдэх утга (алгасагдана):$MT_WARN — MT_STEPS: m0 madlad qwen eval mark lex2 g1 secdb rerank t2s reselect tens ens pick ensx ense2 r7 tzfix afx v2l fgold fens vote test; MT_T2S: $(for P in $MT_T2S_CACHES; do echo -n "${P%%:*} "; done)"
    # TEST-ийг давтан харахаас хамгаална: tens/test алхам override-д ALLOW_TEST=1 тодорхой заагаагүй бол ажиллахгүй
    # (V26 = эцсийн test хийгдсэн; дуусаагүй V26-г үргэлжлүүлэх бол ALLOW_TEST=1-ийг санаатайгаар нэмнэ)
    for X in tens test; do
      if [[ "$STEPS" == *" $X "* ]] && [ "${ALLOW_TEST:-0}" != 1 ]; then
        log "!!! $X алгасав: TEST split-ийг хамгаалсан — override-д ALLOW_TEST=1 заагаагүй (V26-ийн эцсийн test хийгдсэн; давтан хэмжихгүй)"
        STEPS="${STEPS/ $X / }"; MT_WARN="$MT_WARN $X:ALLOW_TEST"
      fi
    done
    mkdir -p "$W" build/translations
    python -c "import bitsandbytes, sentencepiece" 2>/dev/null || pip install -q bitsandbytes sentencepiece
    # Kaggle-ийн torchao 0.10 хуучин: peft LoRA үүсгэх/ачаалахад ImportError (Version 13, 20) — MT-д хэрэггүй тул үргэлж устгана
    pip uninstall -y -q torchao 2>/dev/null || true
    T0=$(date +%s); mins() { echo "$(( ($(date +%s) - T0) / 60 )) мин"; }
    # MT_BUDGET_MIN: энэ stage-ийн нийт минут (12 ц − bootstrap − гаралт хадгалах нөөц). Байхгүй бол хязгааргүй.
    mt_left_min() { if [ -n "${MT_BUDGET_MIN:-}" ]; then echo $(( (T0 + MT_BUDGET_MIN * 60 - $(date +%s)) / 60 )); else echo 99999; fi; }
    mt_score() {   # $1 = кэшийн tag: baseline_dev-ийн оноо → $W/score_<tag>.txt; кэшийг $W-д хадгална
      python scripts/mt/baseline_dev.py --cache "build/translations/$1.jsonl" > "$W/score_$1.txt" 2>&1 || true
      tail -20 "$W/score_$1.txt"; cp "build/translations/$1.jsonl" "$W/"
    }
    mt_cached() { [ -f "build/translations/$1.jsonl" ] || { [ -f "$W/$1.jsonl" ] && cp "$W/$1.jsonl" build/translations/; }; [ -f "build/translations/$1.jsonl" ]; }
    # өмнөх сессийн гаралт (Output-ыг Add Input): дууссан adapter эсвэл сүүлийн бүрэн checkpoint, кэш, оноо
    SRC="${TRAIN_SRC:-/kaggle/input}"
    for A in madlad_m1 q9b_m1; do
      [ -f "$W/$A/t2s_meta.json" ] && continue
      P="$(find "$SRC" -path "*mt/$A/t2s_meta.json" 2>/dev/null | head -1 || true)"
      if [ -n "$P" ]; then mkdir -p "$W/$A"; cp -r "$(dirname "$P")"/. "$W/$A/"; log "MT $A adapter өмнөх сессээс"; continue; fi
      if [ -z "$(d3_ckpt "$W/$A")" ]; then
        CK="$(find "$SRC" -path "*mt/$A/checkpoint-*/trainer_state.json" 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$CK" ] && { mkdir -p "$W/$A"; cp -r "$(dirname "$CK")" "$W/$A/"; cp "$(dirname "$(dirname "$CK")")"/records.jsonl "$W/$A/" 2>/dev/null || true; log "MT $A checkpoint сэргээв"; }
      fi
    done
    for F in $(find "$SRC" -path "*mt/m[01]_*.jsonl" 2>/dev/null || true); do [ -f "$W/$(basename "$F")" ] || cp "$F" "$W/"; done
    # MT_TRAIN_MIN: нэг сургалтад өгөх дээд минут — хэтэрвэл checkpoint хадгалаад зогсоно, дараагийн сесст үргэлжилнэ
    mt_deadline() { if [ -n "${MT_TRAIN_MIN:-}" ]; then export T2S_TRAIN_DEADLINE=$(( $(date +%s) + MT_TRAIN_MIN * 60 )); else unset T2S_TRAIN_DEADLINE; fi; }
    log "### MT: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ';') өгөгдөл $MTD ($(python -c "import sys; print(''.join(open(sys.argv[1], encoding='utf-8').read().split())[:200])" "$MTD/sft/STATS.json" 2>/dev/null))"   # cut -c нь байтаар таслаж кирилл үсгийг эвддэг (Version 11 UnicodeDecodeError)
    if [[ "$STEPS" == *" m0 "* ]]; then
      for TR in none improved; do
        TAG="m0_madlad_$TR"
        mt_cached "$TAG" && { log "$TAG кэш бий — алгасав"; continue; }
        log "=== M0 MADLAD zero-shot (латин: $TR)"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --tag "$TAG" --translit "$TR" --batch 8 \
          && mt_score "$TAG" || log "$TAG: алдаа — алгасав (дараагийн алхам үргэлжилнэ)"
      done
    fi
    if [[ "$STEPS" == *" madlad "* ]]; then
      log "=== M1 MADLAD QLoRA ($(mins))"
      # Kaggle-ийн torchao 0.10 хуучин: peft LoRA үүсгэхэд ImportError (Version 13) — сургалтад хэрэггүй тул устгана
      pip uninstall -y -q torchao 2>/dev/null || true
      mt_deadline
      python scripts/mt/train_mt.py --backend seq2seq --batch 4 --grad-acc 8 --epochs 2 --model google/madlad400-3b-mt --data "$MTD/sft/sft_madlad.jsonl" \
        --pairs "$MTD/pairs.jsonl" --out "$W/madlad_m1" || log "MADLAD M1: алдаа (checkpoint байвал дараагийн сесст үргэлжилнэ)"
      log "MADLAD M1 дууслаа ($(mins)): $(cat "$W/madlad_m1/val_report.json" 2>/dev/null | tr -d '\n')"
    fi
    if [[ "$STEPS" == *" qwen "* ]]; then
      log "=== M1 Qwen3.5-9B QLoRA ($(mins))"
      # run_sft-ийн QLoRA-г эдгээр хувилбараар шалгасан (train_d3-тай ижил)
      [ "${D3_PINS:-1}" = 1 ] && { pip install -q -r requirements-d3.txt; pip uninstall -y -q torchaudio || true; }
      mt_deadline
      PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" \
        python scripts/mt/train_mt.py --backend causal --model qwen3.5-9b --recipe mt_q9b --data "$MTD/sft/sft_qwen.jsonl" \
        --pairs "$MTD/pairs.jsonl" --out "$W/q9b_m1"
      log "Qwen M1 ($(mins)): $(cat "$W/q9b_m1/val_report.json" 2>/dev/null | tr -d '\n')"
    fi
    if [[ "$STEPS" == *" eval "* ]]; then
      [ -f "$W/madlad_m1/t2s_meta.json" ] || [ -f "$W/q9b_m1/t2s_meta.json" ] || log "MT eval: дууссан adapter алга — алгасав"
      for TR in none improved; do
        if [ -f "$W/madlad_m1/t2s_meta.json" ] && ! mt_cached "m1_madlad_$TR"; then
          log "=== M1 MADLAD dev орчуулга (латин: $TR)"
          python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
            --hints lexicon --translit "$TR" --tag "m1_madlad_$TR" --batch 8 \
            && mt_score "m1_madlad_$TR" || log "m1_madlad_$TR: алдаа — алгасав"
        fi
        if [ -f "$W/q9b_m1/t2s_meta.json" ] && ! mt_cached "m1_q9b_$TR"; then
          log "=== M1 Qwen dev орчуулга (латин: $TR)"
          python scripts/mt/m0_translate.py --backend hf_causal --model Qwen/Qwen3.5-9B --load-4bit --cudnn-off \
            --adapter "$W/q9b_m1" --prompt sft --hints lexicon --translit "$TR" --tag "m1_q9b_$TR" --batch 8
          mt_score "m1_q9b_$TR"
        fi
      done
    fi
    if [[ "$STEPS" == *" mark "* ]]; then   # M1 + DB-ийн утгыг «» тэмдэглэх (сургалтын формат; M1-ийн утга хадгалалт 13.6%-ийг засах)
      if [ -f "$W/madlad_m1/t2s_meta.json" ] && ! mt_cached m1_madlad_marked; then
        log "=== M1 MADLAD dev орчуулга + утгын тэмдэглэгээ ($(mins))"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1"           --hints lexicon --translit improved --mark-values db --tag m1_madlad_marked --batch 8           && mt_score m1_madlad_marked || log "m1_madlad_marked: алдаа — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" lex2 "* ]]; then   # V28 F2 (docs/ACCURACY_PLAN.md «F2»): M1a-ийн кэштэй ижил тохиргоо, зөвхөн --hints lexicon2
      # → build/translations/m1_madlad_lex2.jsonl (M1lx-ийн translation_cache). Зөвхөн dev (test-д хүрэхгүй); MT_STEPS-д
      # тодорхой «lex2» байхад л. MADLAD (fp32/NF4) нь vLLM GPU-г эзэлсэн үед OOM (Version 21) — эхлээд vLLM-ийг зогсооно.
      if mt_cached m1_madlad_lex2; then
        log "m1_madlad_lex2 кэш бий — алгасав"
      elif [ ! -f "$W/madlad_m1/t2s_meta.json" ]; then
        log "lex2: madlad_m1 adapter алга (өмнөх гаралтын mt/madlad_m1) — алгасав"
      elif [ "$(mt_left_min)" -lt "${MT_LEX2_MIN:-60}" ]; then
        log "lex2 алгасав: төсвөөс $(mt_left_min) мин үлдсэн (< MT_LEX2_MIN=${MT_LEX2_MIN:-60})"
      else
        bash "$S/serve_model.sh" stop >/dev/null 2>&1 || true; sleep 10
        log "=== M1 MADLAD dev орчуулга, lexicon2 зөвлөмж → m1_madlad_lex2 ($(mins))"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
          --hints lexicon2 --translit improved --split dev --tag m1_madlad_lex2 --batch 8 \
          && mt_score m1_madlad_lex2 || log "m1_madlad_lex2: алдаа — алгасав (M1lx кэшгүй тул t2s-д алгасагдана)"
      fi
    fi
    if [[ "$STEPS" == *" g1 "* ]]; then   # G1 (FULL_REVIEW «Нээлттэй»): USD мөргүйгээр gold-ийг дахин ажиллуулж харьцуулна
      mkdir -p results
      log "=== G1: gold SQL-ийн валютын шалгалт ($(mins))"
      if python scripts/dev/g1_currency_check.py > results/g1_currency.json 2> "$W/g1.err"; then
        while read -r L; do log "G1 $L"; done < results/g1_currency.json
      else
        log "G1: алдаа — алгасав ($(tail -1 "$W/g1.err" 2>/dev/null))"
      fi
    fi
    if [[ "$STEPS" == *" secdb "* ]]; then   # 2026-11-01: DB-ийн REVOKE (ts_stat, ts_rewrite, *_to_xml, pg_sleep) bot role-д үйлчилж буй эсэх
      mkdir -p results
      log "=== secdb: DB түвшний хамгаалалтын шалгалт ($(mins))"
      if python scripts/dev/db_revoke_check.py > results/db_revoke_check.json 2> "$W/secdb.err"; then
        while read -r L; do log "SECDB $L"; done < results/db_revoke_check.json
      else
        log "secdb: алдаа — алгасав ($(tail -1 "$W/secdb.err" 2>/dev/null))"
      fi
    fi
    if [[ "$STEPS" == *" rerank "* ]]; then   # R: нэр томьёоны reranker-ийг өөрийн жишээгээр сургах → dev-ийн сонголтын кэш (vLLM-ээс өмнө)
      RR="$W/rr_bge_ft"
      bash "$S/serve_model.sh" stop >/dev/null 2>&1 || true
      if [ ! -f "$RR/rr_meta.json" ]; then
        P="$(find "$SRC" -path "*rr_bge_ft/rr_meta.json" 2>/dev/null | head -1 || true)"
        [ -n "$P" ] && { mkdir -p "$RR"; cp -r "$(dirname "$P")"/. "$RR/"; log "reranker өмнөх сессээс"; }
      fi
      mt_cached m1_madlad_improved || log "rerank: MADLAD орчуулгын кэш алга — монгол асуултаар сонгоно"
      if [ ! -f "$RR/rr_meta.json" ]; then
        log "=== reranker сургалт ($(mins))"
        # BGE-M3 хайлтыг CPU-д (GPU-г cross-encoder-ийн сургалтад чөлөөлөх; Version 24 OOM)
        T2S_EMBED_DEVICE=cpu PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True python scripts/rerank/rr.py train --out "$RR" || log "reranker сургалт: алдаа"
      fi
      if [ -f "$RR/rr_meta.json" ]; then
        log "reranker: $(tr -d '
' < "$RR/rr_meta.json")"
        python scripts/rerank/rr.py select --model "$RR" --split dev --tag bge_ft_dev && cp build/rerank/bge_ft_dev.json "$W/"
        python scripts/rerank/rr.py select --split dev --tag bge_zero_dev --threshold 0.5 || true   # сургаагүйтэй харьцуулах
      fi
    fi
    if [[ "$STEPS" == *" t2s "* ]]; then
      RUNS=""
      mkdir -p build/rerank; [ -f "$W/bge_ft_dev.json" ] && [ ! -f build/rerank/bge_ft_dev.json ] && cp "$W/bge_ft_dev.json" build/rerank/
      # MT_T2S: ажиллуулах run-ууд (анхдагч "M0a M1a M1q") — MT_T2S-ийн ЭРЭМБЭЭР (= ач холбогдол); кэш байгааг л.
      # MT_T2S_CACHES-д байхгүй ID-г эхэнд анхааруулсан (алгасна)
      for ID in ${MT_T2S:-M0a M1a M1q}; do
        C="$(t2s_cache "$ID")" || continue
        [[ " $RUNS " == *" $ID "* ]] && continue
        [ "$ID" = M1rr ] && [ ! -f build/rerank/bge_ft_dev.json ] && { log "M1rr: reranker-ийн кэш алга — алгасав"; continue; }
        if mt_cached "$C"; then RUNS="$RUNS $ID"; else log "$ID: орчуулгын кэш $C алга — алгасав"; fi
      done
      if [ -n "$RUNS" ]; then
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
            --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        if [[ " $RUNS " == *" M1i4 "* ]] && [ ! -f build/index_plus2_dev/.ok ]; then   # 2-р шатны жишээ (examples_plus_all)
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus_all.jsonl             --out build/index_plus2_dev > build/index_plus2_dev.json && touch build/index_plus2_dev/.ok
        fi
        log "MT t2s: run-ууд (эрэмбээр):$RUNS"
        for ID in $RUNS; do
          if [ "$(mt_left_min)" -lt "${MT_RUN_MIN:-120}" ]; then
            log "=== $ID dev алгасав: төсвөөс $(mt_left_min) мин үлдсэн (< MT_RUN_MIN=${MT_RUN_MIN:-120}) — дараагийн сесст"; continue
          fi
          # vLLM унасан бол дахин асаана (/v1/models-оор шалгадаг тул ажиллаж байвал юу ч хийхгүй) — нэг унасан нь бусдыг алгасуулахгүй
          serve_d2_merged || { log "$ID dev алгасав: vLLM d2 асаагдсангүй (код $?) — дараагийн run дахин оролдоно"; continue; }
          # Run бүрийг хугацаагаар хязгаарлана: төсвийн төгсгөлөөс MT_TAIL_MIN (25) мин өмнө зогсоно (ensx + архивт хугацаа
          # үлдээнэ). timeout нь shell функц (t2s) ажиллуулж чаддаггүй тул python шууд; дууссан асуулт хадгалагдана.
          TO=$(( $(mt_left_min) - ${MT_TAIL_MIN:-25} ))
          if [ "$TO" -lt 10 ]; then log "=== $ID dev алгасав: $TO мин л үлдэнэ (MT_TAIL_MIN=${MT_TAIL_MIN:-25})"; continue; fi
          log "=== $ID dev ($(mins), хязгаар $TO мин)"
          # shellcheck disable=SC2086
          T2S_LLM_TIMEOUT_S=900 timeout -k 2m "${TO}m" python -m mn_text2sql eval --run "$ID" --model "$MODEL" --split dev \
            --variants mn --workers "${WORKERS:-4}" $VARS \
            || log "$ID: алдаа/зогссон (код $?; 124 = хугацаа) — дууссан асуулт хадгалагдсан, дараагийн сесст үргэлжилнэ"
          python scripts/notebook/compact_results.py "results/runs/dev/$ID/${MODEL}__mn__s0.jsonl" || true
          mkdir -p "$W/results"; cp -r "results/runs/dev/$ID" "$W/results/" 2>/dev/null || true
        done
      else
        log "MT t2s: кэш алга — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" reselect "* ]]; then   # M1r = M1a (+ M1m → M1mr) + E4-ийн дүрэм, загваргүй (өмнөх сессийн run-ийг input-ээс)
      for P in M1a:M1r M1m:M1mr; do
        F="results/runs/dev/${P%%:*}/${MODEL}__mn__s0.jsonl"
        if [ ! -f "$F" ]; then
          SRCR="$(find "$SRC" "$W" -path "*results/${P%%:*}/${MODEL}__mn__s0.jsonl" 2>/dev/null | head -1 || true)"
          [ -n "$SRCR" ] && { mkdir -p "$(dirname "$F")"; cp "$SRCR" "$F"; cp "${SRCR%.jsonl}.meta.json" "${F%.jsonl}.meta.json" 2>/dev/null || true; }
        fi
        [ -f "$F" ] || { log "${P%%:*}: run алга — ${P#*:} алгасав"; continue; }
        log "=== ${P#*:} = ${P%%:*} + E4-ийн дүрэм (reselect)"
        t2s reselect --from "${P%%:*}" --to "${P#*:}" --split dev && python scripts/notebook/compact_results.py "results/runs/dev/${P#*:}/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results"; cp -r "results/runs/dev/${P#*:}" "$W/results/" 2>/dev/null || true
      done
    fi
    if [[ "$STEPS" == *" tens "* ]]; then   # Эцсийн test (нэг удаа): dev-ээр сонгосон чуулгыг (M1ens/M1ensr) test дээр хэмжинэ.  (ens/pick-ээс ӨМНӨ: 12 цагийн хязгаарт test эхэлж)
      # ALLOW_TEST=1 (override) байхгүй бол дээр STEPS-ээс хасагдсан — энэ блок ажиллахгүй (V26-ийн дараа test-ийг давтахгүй).
      # Гишүүд ба дараалал dev-тэй ЯГ ижил (V25: суурь M1rr, нэмэлт M1ks M1a M1g8 M1m); дүрэм dev-ээс тогтсон, test-ийг хараагүй.
      # MT_TENS (анхдагч "M1ks M1rr M1g8 M1m") — test дээр ажиллуулах гишүүд; M1a test нь V22-ийн гаралтаас (input).
      TAG=m1_madlad_improved
      t_restore() {   # $1 = run ID: test-ийн run-ийг input/өмнөх алхмаас сэргээнэ
        local F="results/runs/test/$1/${MODEL}__mn__s0.jsonl" SRCR
        if [ ! -f "$F" ]; then
          SRCR="$(find "$SRC" "$W" -path "*results/test/$1/${MODEL}__mn__s0.jsonl" 2>/dev/null | head -1 || true)"
          [ -n "$SRCR" ] && { mkdir -p "$(dirname "$F")"; cp "$SRCR" "$F"; cp "${SRCR%.jsonl}.meta.json" "${F%.jsonl}.meta.json" 2>/dev/null || true; }
        fi
        [ -f "$F" ]
      }
      t_merge() {   # $1 = үндсэн кэш, $2 = test кэш (id-ууд split хооронд давхцахгүй)
        mt_cached "$1" || : > "build/translations/$1.jsonl"
        python - "build/translations/$1.jsonl" "build/translations/$2.jsonl" <<'PY'
import json, sys
main, extra = sys.argv[1], sys.argv[2]
have = {(json.loads(x)["id"], json.loads(x)["variant"]) for x in open(main, encoding="utf-8") if x.strip()}
new = [x for x in open(extra, encoding="utf-8") if x.strip() and (json.loads(x)["id"], json.loads(x)["variant"]) not in have]
open(main, "a", encoding="utf-8", newline="\n").writelines(x if x.endswith("\n") else x + "\n" for x in new)
print(f"кэш {main}: +{len(new)} test мөр (өмнө {len(have)})")
PY
      }
      TENS=" ${MT_TENS:-M1ks M1rr M1g8 M1m} "
      bash "$S/serve_model.sh" stop >/dev/null 2>&1 || true; sleep 5
      # 1) орчуулгын кэш: M1 test (V22-оос) ба M1m-д тэмдэглэгээтэй test орчуулга. Нэгтгэл амжилтгүй бол тэр кэштэй гишүүдийг
      #    test дээр ажиллуулахгүй (өмнө нь vLLM асааж, гишүүн бүр ~20 мин дараа унадаг байсан)
      TC_OK=0; MC_OK=0
      if mt_cached "${TAG}_test" && t_merge "$TAG" "${TAG}_test"; then TC_OK=1
      else log "tens: ${TAG}_test кэш алга/нэгтгэл алдаатай — M1ks/M1rr/M1g8 test алгасна"; fi
      if [[ "$TENS" == *" M1m "* ]] && ! mt_cached m1_madlad_marked_test && [ -f "$W/madlad_m1/t2s_meta.json" ]; then
        log "=== M1 MADLAD test орчуулга + утгын тэмдэглэгээ ($(mins))"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
          --hints lexicon --translit improved --mark-values db --split test --final-test --variants mn \
          --tag m1_madlad_marked_test --batch 8 && cp build/translations/m1_madlad_marked_test.jsonl "$W/" || log "m1_madlad_marked_test: алдаа"
      fi
      if mt_cached m1_madlad_marked_test && t_merge m1_madlad_marked m1_madlad_marked_test; then MC_OK=1
      else log "tens: m1_madlad_marked test кэш алга/нэгтгэл алдаатай — M1m test алгасна"; fi
      # 2) M1rr: сургасан reranker-ийн test сонголт → M1rr-ийн кэш файлд нэмнэ (id давхцахгүй)
      if [[ "$TENS" == *" M1rr "* ]]; then
        RR="$W/rr_bge_ft"; mkdir -p build/rerank
        if [ ! -f "$RR/rr_meta.json" ]; then
          P="$(find "$SRC" -path "*rr_bge_ft/rr_meta.json" 2>/dev/null | head -1 || true)"
          [ -n "$P" ] && { mkdir -p "$RR"; cp -r "$(dirname "$P")"/. "$RR/"; }
        fi
        [ -f build/rerank/bge_ft_dev.json ] || { P="$(find "$SRC" "$W" -name bge_ft_dev.json 2>/dev/null | head -1 || true)"; [ -n "$P" ] && cp "$P" build/rerank/; }
        if [ -f "$RR/rr_meta.json" ] && [ -f build/rerank/bge_ft_dev.json ]; then
          if T2S_EMBED_DEVICE=cpu python scripts/rerank/rr.py select --model "$RR" --split test --tag bge_ft_test; then
            cp build/rerank/bge_ft_test.json "$W/" || true
            # дахин ажиллуулахад давхар нэмэхгүй (test id-ууд аль хэдийн байвал дарж бичнэ); алдаа гарвал сессийг унагаахгүй
            python - <<'PY' || { log "tens: reranker кэш нэгтгэл алдаа — M1rr test алгасна"; TENS="${TENS/ M1rr / }"; }
import json
d = json.load(open("build/rerank/bge_ft_dev.json", encoding="utf-8")); t = json.load(open("build/rerank/bge_ft_test.json", encoding="utf-8"))
d.update(t); json.dump(d, open("build/rerank/bge_ft_dev.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"reranker кэш: dev+test {len(d)} (test {len(t)})")
PY
          else
            log "tens: reranker test сонголт алдаа — M1rr test алгасна"; TENS="${TENS/ M1rr / }"
          fi
        else
          log "tens: reranker эсвэл dev кэш алга — M1rr test алгасна"; TENS="${TENS/ M1rr / }"
        fi
      fi
      # 3) гишүүдийг test дээр (тасарвал дууссан асуулт хадгалагдана — дахин ажиллуулахад үргэлжилнэ)
      if [ ! -f build/index_plus_dev/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
          --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
      fi
      if t_restore M1a; then   # бүтэн M1a test-ийг гаралтад (ingest-ийн McNemar нийт 200 асуултаар)
        mkdir -p "$W/results/test"; cp -r results/runs/test/M1a "$W/results/test/" 2>/dev/null || true
      else
        log "tens: M1a test run алга (V22 гаралтыг input-ээр нэмнэ)"
      fi
      for ID in M1ks M1rr M1g8 M1m; do
        [[ "$TENS" == *" $ID "* ]] || continue
        C=$TAG; OK=$TC_OK; [ "$ID" = M1m ] && { C=m1_madlad_marked; OK=$MC_OK; }
        mt_cached "$C" || { log "$ID test: кэш $C алга — алгасав"; continue; }
        t_restore "$ID" && [ "$(wc -l < "results/runs/test/$ID/${MODEL}__mn__s0.jsonl")" -ge 200 ] && { log "$ID test бүрэн — алгасав"; continue; }
        [ "$OK" = 1 ] || { log "$ID test алгасав: ${C}_test орчуулга $C кэшэд нэгтгэгдээгүй (vLLM асаахгүй)"; continue; }
        serve_d2_merged
        log "=== $ID test ($(mins))"
        # shellcheck disable=SC2086
        T2S_LLM_TIMEOUT_S=900 t2s eval --run "$ID" --model "$MODEL" --split test --variants mn --workers "${WORKERS:-4}" $VARS \
          || log "$ID test: алдаа/зогссон — дууссан асуулт хадгалагдсан"
        python scripts/notebook/compact_results.py "results/runs/test/$ID/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results/test"; cp -r "results/runs/test/$ID" "$W/results/test/" 2>/dev/null || true
      done
      # 4) загваргүй: M1ar (M1a + дүрэм) ба чуулга — dev-тэй ижил бүрэлдэхүүнээр л (гишүүн дутвал чуулгыг ажиллуулахгүй)
      if t_restore M1a; then
        t2s reselect --from M1a --to M1r --split test && python scripts/notebook/compact_results.py "results/runs/test/M1r/${MODEL}__mn__s0.jsonl" || true
        cp -r results/runs/test/M1r "$W/results/test/" 2>/dev/null || true
      fi
      MISS=""; for ID in M1rr M1ks M1a M1g8 M1m; do t_restore "$ID" && [ "$(wc -l < "results/runs/test/$ID/${MODEL}__mn__s0.jsonl")" -ge 200 ] || MISS="$MISS $ID"; done
      if [ -z "$MISS" ]; then
        log "=== чуулга test: суурь M1rr, нэмэлт M1ks M1a M1g8 M1m (dev-тэй ижил)"
        t2s reselect --from M1rr --to M1ens --split test --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m \
          && python scripts/notebook/compact_results.py "results/runs/test/M1ens/${MODEL}__mn__s0.jsonl" || true
        t2s reselect --from M1rr --to M1ensr --split test --extra M1ks M1a M1g8 M1m \
          && python scripts/notebook/compact_results.py "results/runs/test/M1ensr/${MODEL}__mn__s0.jsonl" || true
        cp -r results/runs/test/M1ens results/runs/test/M1ensr "$W/results/test/" 2>/dev/null || true
        # P-г test-д зөвхөн dev дээр батлагдсаны дараа (PICK_TEST=1-ийг dev-ийн дүнг харж өмнөх сессийн дараа тавина)
        if [ "${PICK_TEST:-0}" = 1 ]; then
          serve_d2_merged
          T2S_LLM_TIMEOUT_S=900 t2s reselect --from M1rr --to M1ensp --split test --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --pick \
            && python scripts/notebook/compact_results.py "results/runs/test/M1ensp/${MODEL}__mn__s0.jsonl" || true
          T2S_LLM_TIMEOUT_S=900 t2s reselect --from M1rr --to M1enspr --split test --extra M1ks M1a M1g8 M1m --pick \
            && python scripts/notebook/compact_results.py "results/runs/test/M1enspr/${MODEL}__mn__s0.jsonl" || true
          cp -r results/runs/test/M1ensp results/runs/test/M1enspr "$W/results/test/" 2>/dev/null || true
        fi
      else
        log "чуулга test: бүрэн бус гишүүн:$MISS — дараагийн сесст (дууссан run-ууд гаралтад)"
      fi
    fi
    if [[ "$STEPS" == *" ens "* ]]; then   # Олон тохиргооны чуулга (загваргүй): dev-ийн нэг үүсгэлттэй run-уудын SQL-ийг үр дүнгээр санал хураана
      # Дээд хязгаар (M1a ∪ M1m ∪ M1g8 ∪ M1ks) = 146/224 (65.2%), шилдэг ганц нь 130 — сонголтоор +16 хүртэл (WORKLOG 2026-10-28)
      HAVE=""
      for ID in M1a M1ks M1g8 M1m M1rr M1i4; do
        F="results/runs/dev/$ID/${MODEL}__mn__s0.jsonl"
        if [ ! -f "$F" ]; then
          SRCR="$(find "$SRC" "$W" -path "*results/$ID/${MODEL}__mn__s0.jsonl" 2>/dev/null | head -1 || true)"
          [ -n "$SRCR" ] && { mkdir -p "$(dirname "$F")"; cp "$SRCR" "$F"; cp "${SRCR%.jsonl}.meta.json" "${F%.jsonl}.meta.json" 2>/dev/null || true; }
        fi
        [ -f "$F" ] && HAVE="$HAVE $ID"
      done
      log "=== чуулга: run-ууд:$HAVE"
      if [ "$(echo $HAVE | wc -w)" -ge 3 ]; then
        # суурь = сүүлийн олдсон run (M1a байхгүй байж болно — V22 Output); бусад нь нэмэлт, тэнцвэл эхнийх нь (шилдэг) ялна
        if [[ " $HAVE " == *" M1rr "* && " $HAVE " == *" M1ks "* && " $HAVE " == *" M1a "* && " $HAVE " == *" M1g8 "* && " $HAVE " == *" M1m "* ]]; then
          ENSB=M1rr; ENSX="M1ks M1a M1g8 M1m "   # test-ийн (tens) жортой ЯГ ижил — M1i4 байсан ч оруулахгүй
        else
          ENSB="$(echo $HAVE | awk '{print $NF}')"
          ENSX="$(for x in M1ks M1rr M1i4 M1a M1g8 M1m; do [ "$x" != "$ENSB" ] && [[ " $HAVE " == *" $x "* ]] && echo -n "$x "; done; true)"   # сүүлийн давталтын худал нөхцөл set -e-ээр унагаахгүй (Version 24)
        fi
        log "чуулга: суурь $ENSB, нэмэлт $ENSX"
        # shellcheck disable=SC2086
        t2s reselect --from "$ENSB" --to M1ens --split dev --no-refuse --no-clarify --extra $ENSX && python scripts/notebook/compact_results.py "results/runs/dev/M1ens/${MODEL}__mn__s0.jsonl" || true
        # shellcheck disable=SC2086
        t2s reselect --from "$ENSB" --to M1ensr --split dev --extra $ENSX && python scripts/notebook/compact_results.py "results/runs/dev/M1ensr/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results"; cp -r results/runs/dev/M1ens results/runs/dev/M1ensr "$W/results/" 2>/dev/null || true
        if [[ "$STEPS" == *" pick "* ]] && [ "$(mt_left_min)" -lt "${MT_PICK_MIN:-180}" ]; then
          log "pick алгасав: төсвөөс $(mt_left_min) мин үлдсэн (< MT_PICK_MIN=${MT_PICK_MIN:-180}) — дараагийн сесст"
        elif [[ "$STEPS" == *" pick "* ]]; then   # P: санал зөрвөл шилдэг 2 бүлгийг Arctic (d2) хос харьцуулна (eval/pick.py) — dev
          serve_d2_merged
          log "=== M1ensp = чуулга + Arctic-ийн хос харьцуулалт (dev)"
          # timeout: reselect гаралтаа төгсгөлд бичдэг — төсөв хэтэрвэл зогсоож (гаралтгүй), бусад гаралтыг 12 ц-т багтаана
          # shellcheck disable=SC2086
          T2S_LLM_TIMEOUT_S=900 timeout "$(( ($(mt_left_min) - 15) * 60 ))s" python -m mn_text2sql reselect --from "$ENSB" --to M1ensp --split dev --no-refuse --no-clarify --extra $ENSX --pick \
            && python scripts/notebook/compact_results.py "results/runs/dev/M1ensp/${MODEL}__mn__s0.jsonl" || log "M1ensp: алдаа/хугацаа"
          if [ "$(mt_left_min)" -ge "${MT_PICK_MIN:-180}" ]; then   # M1enspr (дүрэмтэй) нь M1ensp-ийн шийдвэрт хоёрдогч — хугацаа үлдвэл
            # shellcheck disable=SC2086
            T2S_LLM_TIMEOUT_S=900 timeout "$(( ($(mt_left_min) - 15) * 60 ))s" python -m mn_text2sql reselect --from "$ENSB" --to M1enspr --split dev --extra $ENSX --pick \
              && python scripts/notebook/compact_results.py "results/runs/dev/M1enspr/${MODEL}__mn__s0.jsonl" || log "M1enspr: алдаа/хугацаа"
          else
            log "M1enspr алгасав: төсвөөс $(mt_left_min) мин үлдсэн"
          fi
          cp -r results/runs/dev/M1ensp results/runs/dev/M1enspr "$W/results/" 2>/dev/null || true
        fi
      else
        log "чуулга: ≥3 run хэрэгтэй — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" ensx "* ]]; then   # M1ensx: M1ens-ийн жор + шинэ үүсгэгчид (--extra-ийн ТӨГСГӨЛД); dev, загваргүй (CPU)
      # Санал тэнцвэл Pipeline.vote хамгийн ЭХНИЙ хувилбарыг сонгоно; reselect-ийн merge_candidates нь --extra-ийн хувилбаруудыг
      # өгсөн эрэмбээр ЭХЭНД, суурь (--from M1rr)-ийг ХАМГИЙН СҮҮЛД тавина → тэнцвэл: M1ks > M1a > M1g8 > M1m > шинэ гишүүд > M1rr
      # (шинэ гишүүд хуучин нэмэлт гишүүдэд ялагдана, харин суурь M1rr-ийг ялна).
      # Суурь харьцуулалт: M1ensx0 / M1ensxr0 = M1ens / M1ensr-ийн ЯГ ижил жор (суурь M1rr + M1ks M1a M1g8 M1m) энэ сессийн кодоор —
      # M1ensx-тэй ижил кодоор тул McNemar шударга. Өмнөх сессийн (V26) сэргээсэн dev M1ens/M1ensr-ийг ДАРЖ БИЧИХГҮЙ (тэдгээр нь
      # V26-ийн test M1ens-тэй ижил кодоор; «ens» алхам бол дарж бичнэ — V27-д ашиглахгүй).
      BASE5="M1ks M1a M1g8 M1m"; NEW=""; MISSB=""
      # Шинэ гишүүд: MT_ENSX_NEW (анхдагч = V27-ийнх; V28-д жишээ нь «M1lx M1ksf» нэмнэ). M1rt нь M1a-ийн орлуулагч — оруулахгүй
      for ID in M1rr $BASE5 ${MT_ENSX_NEW:-M1cte M1dec M1th M1ip M1fs M1vf}; do
        F="results/runs/dev/$ID/${MODEL}__mn__s0.jsonl"
        if [ ! -f "$F" ]; then
          SRCR="$(find "$SRC" "$W" -path "*results/$ID/${MODEL}__mn__s0.jsonl" 2>/dev/null | head -1 || true)"
          [ -n "$SRCR" ] && { mkdir -p "$(dirname "$F")"; cp "$SRCR" "$F"; cp "${SRCR%.jsonl}.meta.json" "${F%.jsonl}.meta.json" 2>/dev/null || true; }
        fi
        case " $BASE5 M1rr " in
          *" $ID "*)   # суурь гишүүн заавал бүрэн (reselect --extra нь дутуу гишүүнд FileNotFoundError — өмнө нь чимээгүй алгасдаг байсан)
            if [ ! -f "$F" ]; then MISSB="$MISSB $ID"; log "M1ensx: суурь гишүүн $ID алга (base member missing)"
            elif [ "$(wc -l < "$F")" -lt 300 ]; then MISSB="$MISSB $ID"; log "M1ensx: суурь гишүүн $ID бүрэн бус ($(wc -l < "$F") мөр < 300)"; fi ;;
          *)
            if [ -f "$F" ] && [ "$(wc -l < "$F")" -ge 300 ]; then NEW="$NEW $ID"; elif [ -f "$F" ]; then log "M1ensx: $ID бүрэн бус ($(wc -l < "$F") мөр) — оруулахгүй"; fi ;;
        esac
      done
      if [ -n "$MISSB" ]; then
        log "M1ensx алгасав: суурь гишүүн дутуу:$MISSB (M1ens-ийн жортой ижил байх ёстой)"
      elif [ -z "$NEW" ]; then
        log "M1ensx алгасав: шинэ үүсгэгчийн бүрэн (≥300 мөр) run алга"
      else
        log "=== M1ensx0 = M1ens-ийн жор (энэ сессийн кодоор; M1ensx-ийн суурь харьцуулалт) ($(mins))"
        # shellcheck disable=SC2086
        t2s reselect --from M1rr --to M1ensx0 --split dev --no-refuse --no-clarify --extra $BASE5 \
          && python scripts/notebook/compact_results.py "results/runs/dev/M1ensx0/${MODEL}__mn__s0.jsonl" || log "M1ensx0: алдаа"
        log "=== M1ensx = M1ens + $NEW (dev)"
        # shellcheck disable=SC2086
        t2s reselect --from M1rr --to M1ensx --split dev --no-refuse --no-clarify --extra $BASE5 $NEW \
          && python scripts/notebook/compact_results.py "results/runs/dev/M1ensx/${MODEL}__mn__s0.jsonl" || log "M1ensx: алдаа"
        mkdir -p "$W/results"; cp -r results/runs/dev/M1ensx0 results/runs/dev/M1ensx "$W/results/" 2>/dev/null || true
        # Дүрэмтэй хувилбар ба M1ensra нь хоёрдогч — төсвөөс MT_ENSX_GRACE (20) мин-ээс илүү хэтэрсэн бол алгасна (12 ц-т архив)
        if [ "$(mt_left_min)" -lt "-${MT_ENSX_GRACE:-20}" ]; then
          log "M1ensxr0/M1ensxr/M1ensra алгасав: төсвөөс $(( 0 - $(mt_left_min) )) мин хэтэрсэн (> MT_ENSX_GRACE=${MT_ENSX_GRACE:-20})"
        else
          # shellcheck disable=SC2086
          t2s reselect --from M1rr --to M1ensxr0 --split dev --extra $BASE5 \
            && python scripts/notebook/compact_results.py "results/runs/dev/M1ensxr0/${MODEL}__mn__s0.jsonl" || log "M1ensxr0: алдаа"
          # shellcheck disable=SC2086
          t2s reselect --from M1rr --to M1ensxr --split dev --extra $BASE5 $NEW \
            && python scripts/notebook/compact_results.py "results/runs/dev/M1ensxr/${MODEL}__mn__s0.jsonl" || log "M1ensxr: алдаа"
          cp -r results/runs/dev/M1ensxr0 results/runs/dev/M1ensxr "$W/results/" 2>/dev/null || true
          # M1ensra (M1ensr + Abstain v2): IN-SAMPLE — abstain.py-ийн DEFAULT_WEIGHTS/босгыг бүх 300 dev дээр сургасан тул
          # dev-ийн дүн нь өөдрөг; ШИЙДВЭРТ АШИГЛАХГҮЙ (ingest_results.py --tag V27: «§ in-sample», McNemar хосгүй). Лавлагаа л.
          log "=== M1ensra (IN-SAMPLE лавлагаа — шийдвэрт ашиглахгүй: abstain v2-ийн жин бүх dev дээр сургагдсан)"
          # shellcheck disable=SC2086
          t2s reselect --from M1rr --to M1ensra --split dev --extra $BASE5 --abstain-v2 \
            && python scripts/notebook/compact_results.py "results/runs/dev/M1ensra/${MODEL}__mn__s0.jsonl" || log "M1ensra: алдаа"
          cp -r results/runs/dev/M1ensra "$W/results/" 2>/dev/null || true
        fi
      fi
    fi
    if [[ "$STEPS" == *" ense2 "* ]]; then   # M1ensE2 (2026-11-01, dev CV): E2-ийн 8 хувилбар + 5 гишүүн + I3t (жин 2) — CPU
      for SPL in dev test; do
        if [ "$SPL" = test ] && [ "${ALLOW_TEST:-0}" != 1 ]; then log "M1ensE2 test алгасав (ALLOW_TEST=1 биш)"; continue; fi
        N=300; [ "$SPL" = test ] && N=200
        MISS=""
        F="results/runs/$SPL/E2/${MODEL}__mn__s1__h_H4.jsonl"
        { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS E2"
        for ID in M1ks M1a M1g8 M1m M1rr I3t; do
          F="results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
          { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS $ID"
        done
        if [ -n "$MISS" ]; then log "M1ensE2 $SPL: дутуу/бүрэн бус run:$MISS — алгасав"; continue; fi
        # M1ensE2r (2026-11-01, V28-ийн test-ийн дүнгээс ӨМНӨ бүртгэсэн хоёрдогч; docs/ACCURACY_PLAN.md «M1ensE2r») = ижил санал +
        # M1ensr-ийн дүрэм (rule_refuse/rule_clarify). Бүрэн гаралт (≥ N мөр) байвал дахин тооцохгүй — өмнөх сессийн дүн хэвээр.
        for DST in M1ensE2 M1ensE2r; do
          OUTF="results/runs/$SPL/$DST/${MODEL}__mn__s1__h_H4.jsonl"
          if [ -f "$OUTF" ] && [ "$(wc -l < "$OUTF")" -ge "$N" ]; then log "$DST $SPL: бүрэн гаралт байна — алгасав"; continue; fi
          RULES="--no-refuse --no-clarify"; [ "$DST" = M1ensE2r ] && RULES=""
          log "=== $DST ($SPL) = E2 (8, h_H4) + M1ks M1a M1g8 M1m M1rr I3t, жин 2, дүрэм: ${RULES:-асаалттай} (урьдчилан бүртгэсэн) ($(mins))"
          # shellcheck disable=SC2086
          t2s reselect --from E2 --tag h_H4 --to "$DST" --split "$SPL" $RULES \
            --extra M1ks M1a M1g8 M1m M1rr I3t --extra-weight 2 \
            && python scripts/notebook/compact_results.py "$OUTF" \
            || log "$DST $SPL: алдаа"
          mkdir -p "$W/results/$SPL"; cp -r "results/runs/$SPL/$DST" "$W/results/$SPL/" 2>/dev/null || true
        done
      done
    fi
    if [[ "$STEPS" == *" r7 "* ]]; then   # 2026-11-01 (V27 dev): postproc R7 — гаралтын DATE_TRUNC → DATE, хадгалсан хувилбарт (CPU)
      for SPL in dev test; do
        if [ "$SPL" = test ] && [ "${ALLOW_TEST:-0}" != 1 ]; then log "r7 test алгасав (ALLOW_TEST=1 биш)"; continue; fi
        N=300; [ "$SPL" = test ] && N=200
        MISS=""
        F="results/runs/$SPL/E2/${MODEL}__mn__s1__h_H4.jsonl"
        { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS E2"
        for ID in M1rr M1ks M1a M1g8 M1m I3t; do
          F="results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
          { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS $ID"
        done
        if [ -n "$MISS" ]; then log "r7 $SPL: дутуу/бүрэн бус run:$MISS — алгасав"; continue; fi
        log "=== r7 $SPL: M1ens_r7, M1ensE2r_r7 (урьдчилан бүртгэсэн) ($(mins))"
        t2s reselect --from M1rr --to M1ens_r7 --split "$SPL" --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --trunc-date \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ens_r7/${MODEL}__mn__s0.jsonl" || log "M1ens_r7 $SPL: алдаа"
        t2s reselect --from E2 --tag h_H4 --to M1ensE2r_r7 --split "$SPL" --extra M1ks M1a M1g8 M1m M1rr I3t --extra-weight 2 \
          --trunc-date && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensE2r_r7/${MODEL}__mn__s1__h_H4.jsonl" \
          || log "M1ensE2r_r7 $SPL: алдаа"
        mkdir -p "$W/results/$SPL"; cp -r "results/runs/$SPL/M1ens_r7" "results/runs/$SPL/M1ensE2r_r7" "$W/results/$SPL/" 2>/dev/null || true
      done
    fi
    if [[ "$STEPS" == *" tzfix "* ]]; then   # 2026-11-01: compare.norm_value-ийн timestamptz → UTC+8 (UTC сесст date_trunc 08:00)
      for SPL in dev test; do
        if [ "$SPL" = test ] && [ "${ALLOW_TEST:-0}" != 1 ]; then log "tzfix test алгасав (ALLOW_TEST=1 биш)"; continue; fi
        for MODE in rs tzn; do
          TZV=0; [ "$MODE" = tzn ] && TZV=1
          log "=== tzfix $SPL: M1a_$MODE, M1ens_$MODE (T2S_TZ_NAIVE=$TZV) ($(mins))"
          env T2S_TZ_NAIVE=$TZV python -m mn_text2sql reselect --from M1a --to "M1a_$MODE" --split "$SPL" --no-refuse --no-clarify --no-filter \
            && python scripts/notebook/compact_results.py "results/runs/$SPL/M1a_$MODE/${MODEL}__mn__s0.jsonl" || log "tzfix M1a_$MODE $SPL: алдаа"
          env T2S_TZ_NAIVE=$TZV python -m mn_text2sql reselect --from M1rr --to "M1ens_$MODE" --split "$SPL" --no-refuse --no-clarify \
            --extra M1ks M1a M1g8 M1m \
            && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ens_$MODE/${MODEL}__mn__s0.jsonl" || log "tzfix M1ens_$MODE $SPL: алдаа"
          mkdir -p "$W/results/$SPL"; cp -r "results/runs/$SPL/M1a_$MODE" "results/runs/$SPL/M1ens_$MODE" "$W/results/$SPL/" 2>/dev/null || true
        done
      done
    fi
    if [[ "$STEPS" == *" afx "* ]]; then   # 2026-11-01 (V28 dev M1afx): M1afx-ийн 4 засварыг хадгалсан raw_output-д (CPU, --refix afx)
      # Үүсгэлт ижил тул онлайн M1afx-тай ижил шийдвэр, vLLM-ийн шуугиангүй; raw_output тасархай (сүүлийн 4000 тэмдэгт) бичлэгт
      # хадгалсан SQL (eval/reselect.py make_refixer). Урьдчилан бүртгэл: docs/ACCURACY_PLAN.md «afx» (test-ээс ӨМНӨ).
      for SPL in dev test; do
        if [ "$SPL" = test ] && [ "${ALLOW_TEST:-0}" != 1 ]; then log "afx test алгасав (ALLOW_TEST=1 биш)"; continue; fi
        N=300; [ "$SPL" = test ] && N=200
        MISS=""
        F="results/runs/$SPL/E2/${MODEL}__mn__s1__h_H4.jsonl"
        { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS E2"
        for ID in M1rr M1ks M1a M1g8 M1m I3t; do
          F="results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
          { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS $ID"
        done
        if [ -n "$MISS" ]; then log "afx $SPL: дутуу/бүрэн бус run:$MISS — алгасав"; continue; fi
        log "=== afx $SPL: M1a_rs/M1a_fx, M1ens_rs/M1ensfx, M1ensr_rs/M1ensfxr, M1ensE2rfx_r7 (урьдчилан бүртгэсэн) ($(mins))"
        OUTF="results/runs/$SPL/M1a_rs/${MODEL}__mn__s0.jsonl"
        if ! { [ -f "$OUTF" ] && [ "$(wc -l < "$OUTF")" -ge "$N" ]; }; then   # харьцуулах суурь (tzfix-ийн M1a_rs-тэй ижил)
          t2s reselect --from M1a --to M1a_rs --split "$SPL" --no-refuse --no-clarify --no-filter \
            && python scripts/notebook/compact_results.py "$OUTF" || log "M1a_rs $SPL: алдаа"
        fi
        # Ижил кодын ихэр суурь (нэмэлт 2026-11-01 ~21:45 УБ, afx-ийн ямар ч дүнгээс ӨМНӨ): test-ийн M1ens/M1ensr нь V26-ийн кодоор
        # тооцогдсон — кодын шилжилтийг засварын нөлөөнөөс салгахын тулд --refix-гүй ИЖИЛ жорыг энэ сессийн кодоор
        # (M1ens_rs = tzfix-ийн M1ens_rs-тэй ижил тушаал; байвал дахин тооцохгүй)
        OUTF="results/runs/$SPL/M1ens_rs/${MODEL}__mn__s0.jsonl"
        if ! { [ -f "$OUTF" ] && [ "$(wc -l < "$OUTF")" -ge "$N" ]; }; then
          t2s reselect --from M1rr --to M1ens_rs --split "$SPL" --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m \
            && python scripts/notebook/compact_results.py "$OUTF" || log "M1ens_rs $SPL: алдаа"
        fi
        t2s reselect --from M1rr --to M1ensr_rs --split "$SPL" --extra M1ks M1a M1g8 M1m \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensr_rs/${MODEL}__mn__s0.jsonl" || log "M1ensr_rs $SPL: алдаа"
        t2s reselect --from M1a --to M1a_fx --split "$SPL" --no-refuse --no-clarify --no-filter --refix afx \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1a_fx/${MODEL}__mn__s0.jsonl" || log "M1a_fx $SPL: алдаа"
        t2s reselect --from M1rr --to M1ensfx --split "$SPL" --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --refix afx \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensfx/${MODEL}__mn__s0.jsonl" || log "M1ensfx $SPL: алдаа"
        t2s reselect --from M1rr --to M1ensfxr --split "$SPL" --extra M1ks M1a M1g8 M1m --refix afx \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensfxr/${MODEL}__mn__s0.jsonl" || log "M1ensfxr $SPL: алдаа"
        t2s reselect --from E2 --tag h_H4 --to M1ensE2rfx_r7 --split "$SPL" --extra M1ks M1a M1g8 M1m M1rr I3t --extra-weight 2 \
          --refix afx --trunc-date \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensE2rfx_r7/${MODEL}__mn__s1__h_H4.jsonl" \
          || log "M1ensE2rfx_r7 $SPL: алдаа"
        mkdir -p "$W/results/$SPL"
        for D in M1a_rs M1ens_rs M1ensr_rs M1a_fx M1ensfx M1ensfxr M1ensE2rfx_r7; do cp -r "results/runs/$SPL/$D" "$W/results/$SPL/" 2>/dev/null || true; done
      done
    fi
    if [[ "$STEPS" == *" v2l "* ]]; then   # 2026-11-02 (систем сайжруулалт): afx + rules v2 + vote_lint — CPU reselect,
      # хадгалсан гаралтын офлайн загварчлалыг (dev 213/300, test 143/200) бодит DB/vote_filter-тэй батална
      for SPL in dev test; do
        if [ "$SPL" = test ] && [ "${ALLOW_TEST:-0}" != 1 ]; then log "v2l test алгасав (ALLOW_TEST=1 биш)"; continue; fi
        N=300; [ "$SPL" = test ] && N=200
        MISS=""
        for ID in M1rr M1ks M1a M1g8 M1m; do
          F="results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
          { [ -f "$F" ] && [ "$(wc -l < "$F")" -ge "$N" ]; } || MISS="$MISS $ID"
        done
        if [ -n "$MISS" ]; then log "v2l $SPL: дутуу/бүрэн бус run:$MISS — алгасав"; continue; fi
        log "=== v2l $SPL: M1ensfxv2, M1ensfxl, M1ensfxv2l ($(mins))"
        t2s reselect --from M1rr --to M1ensfxv2 --split "$SPL" --extra M1ks M1a M1g8 M1m --refix afx --rules v2 \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensfxv2/${MODEL}__mn__s0.jsonl" || log "M1ensfxv2 $SPL: алдаа"
        t2s reselect --from M1rr --to M1ensfxl --split "$SPL" --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --refix afx --lint \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensfxl/${MODEL}__mn__s0.jsonl" || log "M1ensfxl $SPL: алдаа"
        t2s reselect --from M1rr --to M1ensfxv2l --split "$SPL" --extra M1ks M1a M1g8 M1m --refix afx --rules v2 --lint \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensfxv2l/${MODEL}__mn__s0.jsonl" || log "M1ensfxv2l $SPL: алдаа"
        # бүх CPU сайжруулалт: + KPI/схемийн детерминист засвар (kpi_fix) + R7 (DATE_TRUNC → DATE)
        t2s reselect --from M1rr --to M1ensfxv2lk --split "$SPL" --extra M1ks M1a M1g8 M1m --refix afx --rules v2 --lint \
          --kpi-fix --trunc-date \
          && python scripts/notebook/compact_results.py "results/runs/$SPL/M1ensfxv2lk/${MODEL}__mn__s0.jsonl" \
          || log "M1ensfxv2lk $SPL: алдаа"
        mkdir -p "$W/results/$SPL"
        for D in M1ensfxv2 M1ensfxl M1ensfxv2l M1ensfxv2lk; do cp -r "results/runs/$SPL/$D" "$W/results/$SPL/" 2>/dev/null || true; done
      done
    fi
    if [[ "$STEPS" == *" fgold "* ]]; then   # 2026-11-02: шинэ 400 асуултын (evalset/fresh.jsonl) gold-ийг DB дээр шалгах (CPU)
      if [ -f evalset/fresh.jsonl ]; then
        log "=== fgold: evalset/fresh.jsonl ($(wc -l < evalset/fresh.jsonl) мөр) ($(mins))"
        python -m mn_text2sql check-gold evalset/fresh.jsonl > "$W/fresh_checkgold.json" 2> "$W/fresh_checkgold.err" \
          || log "fgold: алдаатай gold байна — $W/fresh_checkgold.json"
        # tail -c биш (V32: UTF-8 тэмдэгтийн дундуур тасалж notebook UnicodeDecodeError-оор унасан) — python-оор товч дүн
        python - "$W/fresh_checkgold.json" <<'PY' || true
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"fgold: {r.get('items')} асуулт, алдаа {len(r.get('errors', []))}, анхааруулга {len(r.get('warnings', []))}")
for e in (r.get("errors", []) + r.get("warnings", []))[:40]:
    print("  ", e.get("id"), "|", str(e.get("error") or e.get("warning"))[:160])
PY
      else
        log "fgold: evalset/fresh.jsonl алга — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" fens "* ]]; then   # 2026-11-02: шинэ (fresh) асуулт дээр эцсийн систем — 5 гишүүн + чуулга (test шиг нэг удаа)
      # tens-тэй ижил: M1 MADLAD орчуулга (M1m-д DB-ийн утгын тэмдэглэгээ) → M1rr-ийн reranker сонголт → гишүүд (тасарвал дараагийн
      # сесст үргэлжилнэ) → бүх гишүүн бүрэн бол M1ens (анхны жор) ба M1ensfxv2lk (afx + rules v2 + lint + kpi_fix + R7)
      if [ "${ALLOW_TEST:-0}" != 1 ] || [ ! -f evalset/fresh.jsonl ]; then
        log "fens алгасав (ALLOW_TEST=1 биш эсвэл evalset/fresh.jsonl алга)"
      else
        NF="$(grep -c . evalset/fresh.jsonl)"
        f_restore() {   # $1 = run ID: fresh-ийн run-ийг өмнөх сессийн гаралтаас сэргээнэ
          local F="results/runs/fresh/$1/${MODEL}__mn__s0.jsonl" SRCR
          if [ ! -f "$F" ]; then
            SRCR="$(find "$SRC" "$W" -path "*results/fresh/$1/${MODEL}__mn__s0.jsonl" 2>/dev/null | head -1 || true)"
            [ -n "$SRCR" ] && { mkdir -p "$(dirname "$F")"; cp "$SRCR" "$F"; cp "${SRCR%.jsonl}.meta.json" "${F%.jsonl}.meta.json" 2>/dev/null || true; }
          fi
          [ -f "$F" ]
        }
        f_prune() {   # $1 = run ID: асуулт нь fresh.jsonl-оос өөрчлөгдсөн/хасагдсан бичлэгийг хаяна (gold засварын дараа дахин үүсгэнэ)
          local F="results/runs/fresh/$1/${MODEL}__mn__s0.jsonl"
          [ -f "$F" ] || return 0
          python - "$F" <<'PY' || log "f_prune $1: алдаа"
import json, sys
f = sys.argv[1]
cur = {d["id"]: d["question_mn"] for d in map(json.loads, filter(str.strip, open("evalset/fresh.jsonl", encoding="utf-8")))}
rows = [x for x in open(f, encoding="utf-8") if x.strip()]
keep = []
for x in rows:
    try:
        r = json.loads(x)
    except ValueError:
        continue
    if cur.get(r.get("id")) == r.get("question"):
        keep.append(x if x.endswith("\n") else x + "\n")
if len(keep) != len(rows):
    open(f, "w", encoding="utf-8", newline="\n").writelines(keep)
    print(f"f_prune {f}: {len(rows) - len(keep)} хуучирсан/эвдэрсэн мөр хасав ({len(keep)} үлдэв)")
PY
        }
        f_full() { f_restore "$1" && f_prune "$1" && [ "$(grep -c . "results/runs/fresh/$1/${MODEL}__mn__s0.jsonl")" -ge "$NF" ]; }
        f_merge() {   # $1 = үндсэн кэш, $2 = fresh кэш (id давхцахгүй — N-угтвартай)
          mt_cached "$1" || : > "build/translations/$1.jsonl"
          python - "build/translations/$1.jsonl" "build/translations/$2.jsonl" <<'PY'
import json, sys
main, extra = sys.argv[1], sys.argv[2]
have = {(json.loads(x)["id"], json.loads(x)["variant"]) for x in open(main, encoding="utf-8") if x.strip()}
new = [x for x in open(extra, encoding="utf-8") if x.strip() and (json.loads(x)["id"], json.loads(x)["variant"]) not in have]
open(main, "a", encoding="utf-8", newline="\n").writelines(x if x.endswith("\n") else x + "\n" for x in new)
print(f"кэш {main}: +{len(new)} fresh мөр (өмнө {len(have)})")
PY
        }
        bash "$S/serve_model.sh" stop >/dev/null 2>&1 || true; sleep 5
        # 1) орчуулга
        FC_OK=0; FM_OK=0
        if [ -f "$W/madlad_m1/t2s_meta.json" ]; then
          if ! mt_cached m1_madlad_improved_fresh; then
            log "=== fens: M1 MADLAD fresh орчуулга ($(mins))"
            python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
              --hints lexicon --translit improved --split fresh --final-test --variants mn --tag m1_madlad_improved_fresh --batch 8 \
              && cp build/translations/m1_madlad_improved_fresh.jsonl "$W/" || log "fens: m1_madlad_improved_fresh алдаа"
          fi
          if ! mt_cached m1_madlad_marked_fresh; then
            python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
              --hints lexicon --translit improved --mark-values db --split fresh --final-test --variants mn \
              --tag m1_madlad_marked_fresh --batch 8 && cp build/translations/m1_madlad_marked_fresh.jsonl "$W/" \
              || log "fens: m1_madlad_marked_fresh алдаа"
          fi
        else
          log "fens: madlad_m1 adapter алга — орчуулгагүй гишүүдийг алгасна"
        fi
        mt_cached m1_madlad_improved_fresh && f_merge m1_madlad_improved m1_madlad_improved_fresh && FC_OK=1
        mt_cached m1_madlad_marked_fresh && f_merge m1_madlad_marked m1_madlad_marked_fresh && FM_OK=1
        # 2) M1rr: сургасан reranker-ийн fresh сонголт → M1rr-ийн кэшид нэмнэ
        FENS=" ${MT_FENS:-M1a M1rr M1ks M1g8 M1m} "
        if [[ "$FENS" == *" M1rr "* ]]; then
          RR="$W/rr_bge_ft"; mkdir -p build/rerank
          if [ ! -f "$RR/rr_meta.json" ]; then
            P="$(find "$SRC" -path "*rr_bge_ft/rr_meta.json" 2>/dev/null | head -1 || true)"
            [ -n "$P" ] && { mkdir -p "$RR"; cp -r "$(dirname "$P")"/. "$RR/"; }
          fi
          [ -f build/rerank/bge_ft_dev.json ] || { P="$(find "$SRC" "$W" -name bge_ft_dev.json 2>/dev/null | head -1 || true)"; [ -n "$P" ] && cp "$P" build/rerank/; }
          [ -f build/rerank/bge_ft_dev.json ] || echo '{}' > build/rerank/bge_ft_dev.json   # fresh-д dev-ийн сонголт хэрэггүй
          if [ -f "$RR/rr_meta.json" ] \
              && T2S_EMBED_DEVICE=cpu python scripts/rerank/rr.py select --model "$RR" --split fresh --tag bge_ft_fresh; then
            python - <<'PY' || { log "fens: reranker кэш нэгтгэл алдаа — M1rr fresh алгасна"; FENS="${FENS/ M1rr / }"; }
import json
d = json.load(open("build/rerank/bge_ft_dev.json", encoding="utf-8")); t = json.load(open("build/rerank/bge_ft_fresh.json", encoding="utf-8"))
d.update(t); json.dump(d, open("build/rerank/bge_ft_dev.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"reranker кэш: + fresh {len(t)} (нийт {len(d)})")
PY
          else
            log "fens: reranker/кэш алга эсвэл сонголт алдаа — M1rr fresh алгасна"; FENS="${FENS/ M1rr / }"
          fi
        fi
        # 3) гишүүд (тасарвал дууссан асуулт хадгалагдана — дараагийн сесст үргэлжилнэ)
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
            --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        for ID in M1a M1rr M1ks M1g8 M1m; do
          [[ "$FENS" == *" $ID "* ]] || continue
          OK=$FC_OK; [ "$ID" = M1m ] && OK=$FM_OK
          f_full "$ID" && { log "$ID fresh бүрэн — алгасав"; continue; }
          [ "$OK" = 1 ] || { log "$ID fresh алгасав: орчуулгын кэш нэгтгэгдээгүй"; continue; }
          if [ "$(mt_left_min)" -lt "${MT_RUN_MIN:-30}" ]; then log "$ID fresh: төсөв ($(mt_left_min) мин) — дараагийн сесст"; break; fi
          serve_d2_merged
          TO=$(( $(mt_left_min) - 10 )); log "=== $ID fresh ($(mins); timeout ${TO} мин)"
          # shellcheck disable=SC2086
          T2S_LLM_TIMEOUT_S=900 timeout -k 2m "${TO}m" python -m mn_text2sql eval --run "$ID" --model "$MODEL" --split fresh \
            --variants mn --workers "${WORKERS:-8}" $VARS || log "$ID fresh: зогссон — дууссан асуулт хадгалагдсан"
          python scripts/notebook/compact_results.py "results/runs/fresh/$ID/${MODEL}__mn__s0.jsonl" || true
          mkdir -p "$W/results/fresh"; cp -r "results/runs/fresh/$ID" "$W/results/fresh/" 2>/dev/null || true
        done
        # 4) загваргүй: M1a-ийн ганц гишүүний хувилбар ба чуулга (бүх гишүүн бүрэн бол)
        if f_full M1a; then
          t2s reselect --from M1a --to M1a_rs --split fresh --no-refuse --no-clarify --no-filter \
            && python scripts/notebook/compact_results.py "results/runs/fresh/M1a_rs/${MODEL}__mn__s0.jsonl" || true
          t2s reselect --from M1a --to M1a_fxv2k --split fresh --no-filter --refix afx --rules v2 --kpi-fix --trunc-date \
            && python scripts/notebook/compact_results.py "results/runs/fresh/M1a_fxv2k/${MODEL}__mn__s0.jsonl" || true
        fi
        MISS=""; for ID in M1rr M1ks M1a M1g8 M1m; do f_full "$ID" || MISS="$MISS $ID"; done
        if [ -z "$MISS" ]; then
          log "=== fens чуулга: M1ens (анхны жор) ба M1ensfxv2lk (бүх засвар) ($(mins))"
          t2s reselect --from M1rr --to M1ens --split fresh --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m \
            && python scripts/notebook/compact_results.py "results/runs/fresh/M1ens/${MODEL}__mn__s0.jsonl" || true
          t2s reselect --from M1rr --to M1ensfxv2lk --split fresh --extra M1ks M1a M1g8 M1m --refix afx --rules v2 --lint \
            --kpi-fix --trunc-date \
            && python scripts/notebook/compact_results.py "results/runs/fresh/M1ensfxv2lk/${MODEL}__mn__s0.jsonl" || true
        else
          log "fens: чуулга алгасав — дутуу гишүүн:$MISS (дараагийн сесст)"
        fi
        mkdir -p "$W/results/fresh"
        for D in M1a_rs M1a_fxv2k M1ens M1ensfxv2lk; do cp -r "results/runs/fresh/$D" "$W/results/fresh/" 2>/dev/null || true; done
      fi
    fi
    if [[ "$STEPS" == *" vote "* ]]; then   # MT_VOTE (анхдагч M1e2): 8 нэр дэвшигч + засвар dev → reselect (E4-ийн дүрэм) = M1e4 / M1me4
      V="${MT_VOTE:-M1e2}"; VR="${V%2}4"
      case "$V" in M1e2) C=m1_madlad_improved ;; M1me2) C=m1_madlad_marked ;; *) log "MT_VOTE: $V танигдаагүй"; C="" ;; esac
      if [ -n "$C" ] && mt_cached "$C"; then
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl             --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        serve_d2_merged
        log "=== $V dev ($(mins))"
        # shellcheck disable=SC2086
        T2S_LLM_TIMEOUT_S=900 t2s eval --run "$V" --model "$MODEL" --split dev --variants mn --workers "${WORKERS:-4}" $VARS           || log "$V: алдаа/зогссон — дууссан асуулт хадгалагдсан"
        for R in "$V" "$VR"; do
          [ "$R" = "$VR" ] && { t2s reselect --from "$V" --to "$VR" --split dev || continue; }
          for J in results/runs/dev/$R/*.jsonl; do python scripts/notebook/compact_results.py "$J" || true; done
          mkdir -p "$W/results"; cp -r "results/runs/dev/$R" "$W/results/" 2>/dev/null || true
        done
      else
        log "MT vote: $V-ийн кэш ($C) алга — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" test "* ]]; then   # MT-9: M1a-г test дээр НЭГ удаа (docs/MT_INVARIANT_PLAN.md §5.6) — dev-ийн дараа л
      # tens-тэй адил: ALLOW_TEST=1 (override) байхгүй бол дээр STEPS-ээс хасагдсан.
      TAG=m1_madlad_improved
      if ! mt_cached "${TAG}_test"; then
        [ -f "$W/madlad_m1/t2s_meta.json" ] || { log "MT test: madlad_m1 adapter алга — алгасав"; STEPS="${STEPS/ test / }"; }
      fi
      if [[ "$STEPS" == *" test "* ]] && ! mt_cached "${TAG}_test"; then
        log "=== M1 MADLAD test орчуулга (final, нэг удаа)"
        # Өмнөх t2s/vote алхмын vLLM хоёр GPU-г эзэлсэн хэвээр байж болно (Version 21: MADLAD OOM) — чөлөөлнө
        bash "$S/serve_model.sh" stop >/dev/null 2>&1 || true; sleep 10
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
          --hints lexicon --translit improved --split test --final-test --variants mn --tag "${TAG}_test" --batch 8 \
          && cp "build/translations/${TAG}_test.jsonl" "$W/" || log "${TAG}_test: алдаа"
      fi
      if mt_cached "${TAG}_test"; then
        # M1a-ийн кэш (translation_cache: m1_madlad_improved) = dev (байвал) + test; id-ууд split хооронд давхцахгүй
        mt_cached "$TAG" || : > "build/translations/$TAG.jsonl"
        python - "build/translations/$TAG.jsonl" "build/translations/${TAG}_test.jsonl" <<'PY'
import json, sys
main, extra = sys.argv[1], sys.argv[2]
have = {(json.loads(x)["id"], json.loads(x)["variant"]) for x in open(main, encoding="utf-8") if x.strip()}
new = [x for x in open(extra, encoding="utf-8") if x.strip() and (json.loads(x)["id"], json.loads(x)["variant"]) not in have]
open(main, "a", encoding="utf-8", newline="\n").writelines(x if x.endswith("\n") else x + "\n" for x in new)
print(f"кэш {main}: +{len(new)} test мөр (өмнө {len(have)})")
PY
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
            --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        serve_d2_merged
        log "=== M1a test ($(mins))"
        # shellcheck disable=SC2086
        T2S_LLM_TIMEOUT_S=900 t2s eval --run M1a --model "$MODEL" --split test --variants mn --workers "${WORKERS:-4}" $VARS \
          || log "M1a test: алдаа/зогссон — дууссан асуулт хадгалагдсан"
        python scripts/notebook/compact_results.py "results/runs/test/M1a/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results/test"; cp -r results/runs/test/M1a "$W/results/test/" 2>/dev/null || true
      fi
    fi
    if [ -n "${RESULTS_DST:-}" ]; then
      mkdir -p "$RESULTS_DST/mt"
      cp "$W"/*.txt "$W"/*.jsonl "$RESULTS_DST/mt/" 2>/dev/null || true
      for A in madlad_m1 q9b_m1; do [ -d "$W/$A" ] && mkdir -p "$RESULTS_DST/mt/$A" && cp "$W/$A"/*.json "$RESULTS_DST/mt/$A/" 2>/dev/null || true; done
      [ -d "$W/results" ] && cp -r "$W/results" "$RESULTS_DST/mt/"
      log "MT үр дүн → $RESULTS_DST/mt"
    fi
    [ -z "$MT_WARN" ] || log "!!! АНХААР (давтав): алгассан/үл мэдэгдэх:$MT_WARN"
    log "MT бүгд ($(mins))"
    ;;
  t2s_inv)   # «Тогтмол мэдлэг»-ийн T2S (docs/MT_INVARIANT_PLAN.md §6): Kaggle 2×T4. INV_STEPS (анхдагч "verify sample train"):
             #   verify — S2 хувилбарыг PG-ийн view дээр эх gold-той харьцуулна (verify_variants.py) → *.verified.jsonl
             #   sample — prompts (зөвхөн баталгаажсан S2 + S1 + S3 + S4) → суурь Arctic (vLLM) n=4 rejection sampling → SFT мөр
             #   train  — t2s_inv1 QLoRA (2×T4 DDP, D3-тай ижил механизм; INV_TRAIN_MIN-д checkpoint хадгалаад зогсоно)
             #   eval   — (нэмж өгвөл) нэгтгэж served=inv1 → INV_RUNS (анхдагч "T1 T1n") dev
             #   shift  — (нэмж өгвөл) T-2: evalset_shift/dev дээр SHIFT_MODELS (base d2 inv1) — схем солигдоход EX-ийн уналт
             # Гаралт $W (/kaggle/working/t2s_inv): prompts/samples/records, adapter (adapters_out/t2s_inv1__arctic-r1-7b).
    MODEL=arctic-r1-7b
    W="${INV_WORK:-/kaggle/working}/t2s_inv"; [ -d /kaggle/working ] || W="$T2S_ROOT/adapters/t2s_inv"
    STEPS=" ${INV_STEPS:-verify sample train} "
    SRC="${TRAIN_SRC:-/kaggle/input}"
    mkdir -p "$W"
    T0=$(date +%s); mins() { echo "$(( ($(date +%s) - T0) / 60 )) мин"; }
    [ -f build/index/.ok ] || { T2S_EMBED_DEVICE=auto t2s index-build > build/index.json && touch build/index/.ok; }
    # өмнөх сессийн гаралт (samples.jsonl — үргэлжилнэ; records; checkpoint)
    for F in samples.jsonl records.jsonl prompts.jsonl; do
      if [ ! -f "$W/$F" ]; then P="$(find "$SRC" -path "*t2s_inv/$F" 2>/dev/null | head -1 || true)"; [ -n "$P" ] && cp "$P" "$W/"; fi
    done
    if [[ "$STEPS" == *" verify "* ]] && [ ! -f data/t2s_inv/v0/schema_variants.verified.jsonl ]; then
      log "=== S2 гүйцэтгэлийн баталгаажуулалт (PG)"
      python scripts/t2s_inv/verify_variants.py --dir data/t2s_inv/v0
      cp data/t2s_inv/v0/verify_report.json "$W/"
    fi
    if [[ "$STEPS" == *" sample "* ]] && [ ! -f "$W/records.jsonl" ]; then
      [ -f "$W/prompts.jsonl" ] || python scripts/t2s_inv/build_sft.py prompts --out "$W" --verified
      bash "$S/serve_model.sh" "$MODEL"
      log "=== rejection sampling ($(wc -l < "$W/prompts.jsonl") prompt, $(mins))"
      timeout "${INV_SAMPLE_MIN:-360}m" python scripts/t2s_inv/build_sft.py sample --dir "$W" --served "$MODEL" --workers "${WORKERS:-16}" \
        || log "sample зогссон (timeout/алдаа) — дараагийн сесст үргэлжилнэ"
      pkill -f "vllm serve" 2>/dev/null || true; sleep 15
      if [ "$(wc -l < "$W/samples.jsonl")" -ge "$(wc -l < "$W/prompts.jsonl")" ]; then
        python scripts/t2s_inv/build_sft.py records --dir "$W" --model "$MODEL"
      else
        log "sample дуусаагүй ($(wc -l < "$W/samples.jsonl")/$(wc -l < "$W/prompts.jsonl")) — Output-ыг input болгоод ижил STAGE"; exit 0
      fi
    fi
    if [[ "$STEPS" == *" train "* ]] && [ -f "$W/records.jsonl" ]; then
      AD="$W/t2s_inv1__$MODEL"; OUTA="$W/adapters_out/t2s_inv1__$MODEL"
      if [ ! -f "$AD/t2s_meta.json" ]; then     # дууссан adapter өмнөх сессээс (дахин сургахгүй)
        PREV="$(find "$SRC" -path "*adapters_out/t2s_inv1__$MODEL/t2s_meta.json" 2>/dev/null | head -1 || true)"
        [ -n "$PREV" ] && { mkdir -p "$AD"; cp "$(dirname "$PREV")"/* "$AD"/; log "t2s_inv1 adapter өмнөх сессээс"; }
      fi
      if [ ! -f "$AD/t2s_meta.json" ] && [ -z "$(d3_ckpt "$AD")" ]; then
        CK="$(find "$SRC" -path "*t2s_inv1__$MODEL/checkpoint-*/trainer_state.json" 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$CK" ] && { mkdir -p "$AD"; cp -r "$(dirname "$CK")" "$AD/"; log "checkpoint сэргээв: $(dirname "$CK")"; }
      fi
      if [ ! -f "$AD/t2s_meta.json" ]; then
        [ "${D3_PINS:-1}" = 1 ] && { pip install -q -r requirements-d3.txt; pip uninstall -y -q torchaudio || true; }
        NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"; NP=1; [ "${NG:-0}" -ge 2 ] && NP=2
        export T2S_SAVE_STEPS="${T2S_SAVE_STEPS:-20}" TOKENIZERS_PARALLELISM=false \
          PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}"
        if [ -n "${INV_TRAIN_MIN:-}" ]; then export T2S_TRAIN_DEADLINE=$(( $(date +%s) + INV_TRAIN_MIN * 60 )); fi
        log "=== t2s_inv1 QLoRA: $(wc -l < "$W/records.jsonl") мөр, $NP процесс ($(mins))"
        if [ "$NP" -ge 2 ]; then
          python -m torch.distributed.run --standalone --nproc_per_node "$NP" -m mn_text2sql.training \
            sft --recipe t2s_inv1 --model "$MODEL" --output-dir "$AD" --data-file "$W/records.jsonl"
        else
          t2s_train sft --recipe t2s_inv1 --model "$MODEL" --output-dir "$AD" --data-file "$W/records.jsonl"
        fi
      fi
      if [ -f "$AD/t2s_meta.json" ]; then
        mkdir -p "$OUTA"
        for f in adapter_config.json adapter_model.safetensors t2s_meta.json; do cp "$AD/$f" "$OUTA/"; done
        rm -rf "$AD"/checkpoint-*
        log "t2s_inv1 adapter → $OUTA ($(mins))"
      else
        log "t2s_inv1 дуусаагүй — checkpoint: $(d3_ckpt "$AD"); Output-ыг input болгоод ижил STAGE"
      fi
    fi
    # eval (INV_STEPS-д «eval»): adapter-ийг fp16 нэгтгэж served=inv1 → T1, T1n dev (I3t-ийн кэш, D2-тэй харьцуулна)
    if [[ "$STEPS" == *" eval "* ]] && [ -f "$W/adapters_out/t2s_inv1__$MODEL/t2s_meta.json" ]; then
      M="adapters/t2s_inv1__${MODEL}__merged16"
      if [ ! -f "$M/t2s_merge.json" ]; then
        HF_ID="$(python -c "import yaml; print(yaml.safe_load(open('config/models.yaml', encoding='utf-8'))['models']['$MODEL']['hf_id'])")"
        python scripts/notebook/merge_lora.py --base "$HF_ID" --adapter "$W/adapters_out/t2s_inv1__$MODEL" --out "$M"
      fi
      if [ ! -f build/index_plus_dev/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl           --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
      fi
      MODEL_PATH="$M" SERVED=inv1 bash "$S/serve_model.sh" "$MODEL"
      for ID in ${INV_RUNS:-T1 T1n}; do
        log "=== $ID dev ($(mins))"
        # shellcheck disable=SC2086
        t2s eval --run "$ID" --model "$MODEL" --split dev --variants mn --workers "${WORKERS:-8}" --var best_a="${BEST_A:-A7}"           || log "$ID: зогссон — дууссан асуулт хадгалагдсан"
        python scripts/notebook/compact_results.py "results/runs/dev/$ID/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results"; cp -r "results/runs/dev/$ID" "$W/results/" 2>/dev/null || true
      done
    fi
    # shift (INV_STEPS-д «shift»): T-2 — evalset_shift/dev (сургалтад ороогүй 4 нэршил) дээр SHIFT_MODELS (анхдагч "base d2 inv1")-ийг
    # ижил compact prompt-оор; загвар бүрийн «эх → шилжүүлсэн» уналт (scripts/t2s_inv/eval_shift.py). inv1 нь eval алхмын нэгтгэсэн жин.
    if [[ "$STEPS" == *" shift "* ]]; then
      for SM in ${SHIFT_MODELS:-base d2 inv1}; do
        [ -f "results/shift/dev/$SM.summary.json" ] && [ "$(python -c "import json; print(len(json.load(open('results/shift/dev/$SM.summary.json'))['maps']))")" -ge 5 ] && continue
        case "$SM" in
          base) bash "$S/serve_model.sh" "$MODEL"; SRV="$MODEL" ;;
          d2) serve_d2_merged; SRV=d2 ;;
          inv1) [ -f "adapters/t2s_inv1__${MODEL}__merged16/t2s_merge.json" ] || { log "shift inv1: нэгтгэсэн жин алга (eval алхам) — алгасав"; continue; }
                MODEL_PATH="adapters/t2s_inv1__${MODEL}__merged16" SERVED=inv1 bash "$S/serve_model.sh" "$MODEL"; SRV=inv1 ;;
          *) log "SHIFT_MODELS: $SM үл мэдэгдэх"; continue ;;
        esac
        log "=== T-2 shift: $SM ($(mins))"
        python scripts/t2s_inv/eval_shift.py --served "$SRV" --tag "$SM" --workers "${WORKERS:-8}" || log "shift $SM: зогссон — үргэлжилнэ"
        python scripts/t2s_inv/eval_shift.py --set defs --served "$SRV" --tag "$SM" --workers "${WORKERS:-8}" || log "defs $SM: зогссон"
        mkdir -p "$W/shift"; cp results/shift/dev/"$SM".* "$W/shift/" 2>/dev/null || true
      done
    fi
    [ -n "${RESULTS_DST:-}" ] && { mkdir -p "$RESULTS_DST/t2s_inv"; cp "$W"/*.json "$RESULTS_DST/t2s_inv/" 2>/dev/null || true; }
    log "t2s_inv бүгд ($(mins))"
    ;;
  F)
    need BEST_A A6
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin run "F2,F3" "$MODEL"
      run "F5,F7,F8" "$MODEL"
      if [ -f data/bird/bird_mn.jsonl ]; then
        VARIANTS=mn,latin,en run "F1,F1b" "$MODEL"
      else
        log "F1 алгасав: эхлээд t2s bird-prepare && (qwen3.5-9b асаалттай) t2s bird-translate (+ хүний шалгалт)"
      fi
    done
    ;;
  E)
    need BEST_A A6; need BEST_CONFIG D1
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin,en run E1 "$MODEL"
      run E2 "$MODEL"
    done
    t2s report >/dev/null
    log "E3: results/REPORT.md-ийн «Санал хураалт» муруйгаас босгыг сонгоод: t2s derive-abstain --from E2 --to E3 --threshold <t>"
    ;;
  G)
    need BEST_A A6; need BEST_CONFIG D1
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin,en run G1 "$MODEL"
      run "G2,G3,G4,G5,G6" "$MODEL"
    done
    ;;
  thesis)
    need BEST_A A6; need BEST_CONFIG D1
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      ORIG=0; [ "$MODEL" = arctic-r1-7b ] && ORIG="${ORIGINAL:-1}"   # thesis-ийн систем зөвхөн Arctic-д
      # Adapter-тай BEST_CONFIG (C*/D*) нь run_thesis_bench.sh-д алгасагдана (LoRA асаагаагүй, ББСБ-д сургасан)
      ORIGINAL=$ORIG IDS="${THESIS_IDS:-A6,B5,$BEST_CONFIG,G1,G2,G3}" EXTRA="$VARS" \
        bash "$S/run_thesis_bench.sh" "$MODEL"
    done
    ;;
  test)
    need BEST_A A6; need TEST_IDS "A6,B5,E2"
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      SPLIT=test VARIANTS=mn,latin,en run "$TEST_IDS" "$MODEL"
    done
    ;;
  *) echo "үл мэдэгдэх STAGE=$STAGE"; exit 2 ;;
esac
bash "$S/serve_model.sh" stop
log "STAGE=$STAGE дууслаа"
