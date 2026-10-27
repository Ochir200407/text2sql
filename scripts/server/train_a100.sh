#!/usr/bin/env bash
# =====================================================================
# Сургалт (C, D, F4, F6, F10) — A100 80 GB. Хоёр ТУСДАА үе шат (туршилтын төлөвлөгөө: C6 нь хэмжсэн C-шилдэг дээр):
#   1) STEPS="data sample build sft" BEST_A=A6 bash scripts/server/train_a100.sh arctic-r1-7b
#      → adapters/-ийг L4 руу хуулж eval_adapters.sh-ээр C1–C5, D2, F6-г үнэлж C-шилдгийг (BEST_SFT) сонгоно
#   2) STEPS="grpo" BEST_A=A6 BEST_SFT=C3 bash scripts/server/train_a100.sh arctic-r1-7b      # C6, C7, F4, D4
# Урьдчилсан нөхцөл: setup.sh (DB + индекс) энэ сервер дээр (өөрийн DB-ийн sampling ба reward PostgreSQL-д хандана).
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
S="scripts/server"
MODEL="${1:?загвар: arctic-r1-7b | sqrl-9b}"
STEPS="${STEPS:?STEPS=\"data sample build sft\" эсвэл STEPS=grpo}"
: "${BEST_A:?BEST_A=<A1|A6|A7> заавал — сургалтын prompt нь A-шилдэгтэй ижил байна}"
export T2S_BEST_A="$BEST_A"
RECIPES="${RECIPES:-c1 c2 c3 c4_en30 c4_en50 c5_r64 d2 f6}"
if [ "$MODEL" = sqrl-9b ]; then RECIPES="${RECIPES} f10"; fi
GRPO_RECIPES="${GRPO_RECIPES:-c6 c7 f4 d4}"
has() { [[ " $STEPS " == *" $1 "* ]]; }

if has fetch; then   # зөвхөн BIRD DB татах (орчуулга өөр сервер/бүртгэлд хийгдсэн бол; data/train/*.jsonl хуулагдсан)
  log "### BIRD train DB татах (орчуулгагүй)"
  t2s_train fetch-bird
fi
if has data; then
  log "### Өгөгдөл: BIRD train → монгол орчуулга (qwen3.5-9b), өөрийн DB синтетик, давхардлын шүүлт"
  t2s_train fetch-bird
  bash "$S/serve_model.sh" qwen3.5-9b 8000 --80gb
  t2s_train translate
  t2s_train synth
  t2s_train dedup
fi
if has sample; then
  log "### C1/C2 sampling — $MODEL (5k асуулт × 8; ~5–9 цаг A100)"
  bash "$S/serve_model.sh" "$MODEL" 8000 --80gb
  # SAMPLE_LIMIT: эх сурвалж бүрээс дээд тал нь N асуулт (төсөв хязгаартай үед; тайланд хазайлт гэж тэмдэглэнэ)
  LIM=(); [ -n "${SAMPLE_LIMIT:-}" ] && LIM=(--limit "$SAMPLE_LIMIT")
  for SRC in bird_mn.jsonl own_synth_clean.jsonl human.jsonl; do
    t2s_train sample --model "$MODEL" --source "$SRC" "${LIM[@]}"
    t2s_train cot --model "$MODEL" --source "$SRC"
  done
  if [ "${SKIP_EN:-0}" != 1 ]; then   # C4/C7-ийн англи гаргалт (хасагдсан төлөвлөгөөнд SKIP_EN=1)
    t2s_train sample --model "$MODEL" --source bird_mn.jsonl --variants en "${LIM[@]}"
  fi
  if [ "$MODEL" = sqrl-9b ]; then
    t2s_train sample --model "$MODEL" --source own_synth_clean.jsonl --prompt rag --tool   # F10 траектор
    t2s_train sample --model "$MODEL" --source bird_mn.jsonl --prompt rag --tool
  fi
  bash "$S/serve_model.sh" stop
fi
if has build; then
  for R in $RECIPES; do t2s_train build --recipe "$R" --model "$MODEL"; done
fi
if has sft; then
  for R in $RECIPES; do
    log "### SFT $R — $MODEL"
    t2s_train sft --recipe "$R" --model "$MODEL"
  done
  log "SFT дууссан. adapters/-ийг L4 руу хуулж: BEST_A=$BEST_A bash scripts/server/eval_adapters.sh $MODEL"
fi
if has grpo; then
  : "${BEST_SFT:?BEST_SFT=<C1..C5> заавал — eval_adapters.sh-ийн dev дүнгээс сонгоно}"
  BEST_R="$(echo "$BEST_SFT" | tr 'C' 'c')"
  case "$BEST_R" in c4) BEST_R=c4_en30 ;; c4b) BEST_R=c4_en50 ;; c5) BEST_R=c5_r64 ;; esac
  log "### GRPO — эхлэл: $BEST_R (нэгтгэсэн жин)"
  merged() { [ -f "$1/config.json" ] && { log "нэгтгэсэн жин бэлэн: $1 (алгасав)"; return 0; }; return 1; }
  merged "adapters/${BEST_R}__${MODEL}__merged" || \
    t2s_train merge --model "$MODEL" --adapter "adapters/${BEST_R}__${MODEL}" --out "adapters/${BEST_R}__${MODEL}__merged"
  if [ -d "adapters/d2__${MODEL}" ]; then
    merged "adapters/d2__${MODEL}__merged" || \
      t2s_train merge --model "$MODEL" --adapter "adapters/d2__${MODEL}" --out "adapters/d2__${MODEL}__merged"
  fi
  for R in $GRPO_RECIPES; do
    INIT="adapters/${BEST_R}__${MODEL}__merged"
    if [ "$R" = d4 ]; then INIT="adapters/d2__${MODEL}__merged"; fi
    [ -d "$INIT" ] || { log "GRPO $R алгасав: $INIT алга"; continue; }
    log "### GRPO $R — $MODEL (init $INIT)"
    t2s_train grpo --recipe "$R" --model "$MODEL" --init "$INIT" ${GRPO_MAX_STEPS:+--max-steps "$GRPO_MAX_STEPS"}   # GRPO_MAX_STEPS: төсөв хязгаартай үед
    merged "adapters/${R}__${MODEL}__merged" || \
      t2s_train merge --model "$MODEL" --base "$INIT" --adapter "adapters/${R}__${MODEL}" --out "adapters/${R}__${MODEL}__merged"
  done
fi
log "Дууслаа (STEPS=$STEPS)."
