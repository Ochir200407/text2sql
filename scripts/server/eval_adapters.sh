#!/usr/bin/env bash
# =====================================================================
# Сургасан загваруудыг L4 дээр үнэлэх (C, D, F4, F6, F10). adapters/ хавтаст байгаа adapter-ийг л үнэлнэ.
#   BEST_A=A6 bash scripts/server/eval_adapters.sh arctic-r1-7b
#   BEST_A=A6 BEST_SFT=C3 bash scripts/server/eval_adapters.sh arctic-r1-7b     # D1 (C-шилдэг + RAG)-д
# LoRA adapter-ууд (SFT) нэг vLLM дээр --lora-modules-оор; GRPO-ийн нэгтгэсэн жинг тусдаа загвар болгон.
# Удаан GPU (T4): RECIPES="c1 c2 c3" C_VARIANTS=mn SKIP_GENERAL=1 — цөөрүүлсэн үнэлгээ.
# Fine-tune-ийн дараах мартагдал: англи dev (variant=en), BIRD-dev (англи, F1 + tag), Belebele khk_Cyrl.
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
S="scripts/server"
MODEL="${1:?загвар}"
: "${BEST_A:?BEST_A=<A1|A6|A7> заавал (C/D-ийн үнэлгээний prompt)}"
VARS="--var best_a=$BEST_A"
if [ -n "${BEST_SFT:-}" ]; then VARS="$VARS --var best_sft=$BEST_SFT --var best_sft_adapter=$(echo "$BEST_SFT" | tr 'C' 'c')"; fi
# жор → туршилтын ID
declare -A ID_OF=([c1]=C1 [c2]=C2 [c3]=C3 [c4_en30]=C4 [c4_en50]=C4b [c5_r64]=C5 [d2]=D2 [f6]=F6 [f10]=F10)
LORA=""; IDS=()
for R in ${RECIPES:-$(echo "${!ID_OF[@]}" | tr ' ' '\n' | sort)}; do   # RECIPES="c1 c2 c3" — зөвхөн эдгээрийг (дарааллаар)
  [ -n "${ID_OF[$R]:-}" ] || continue
  if [ -d "adapters/${R}__${MODEL}" ]; then
    LORA="$LORA ${R}=adapters/${R}__${MODEL}"; IDS+=("${ID_OF[$R]}")
  fi
done
if [ -n "${BEST_SFT:-}" ] && [ -d "adapters/$(echo "$BEST_SFT" | tr 'C' 'c')__${MODEL}" ]; then IDS+=(D1); fi
if [ -d "adapters/d2__${MODEL}" ]; then IDS+=(D3); fi
if [ -n "$LORA" ]; then
  LORA="$LORA" bash "$S/serve_model.sh" "$MODEL"
  for ID in "${IDS[@]}"; do
    case "$ID" in
      C1|C2|C3|C4|C4b|C5) VARIANTS="${C_VARIANTS:-mn,latin,en}" EXTRA="$VARS" bash "$S/run_experiments.sh" "$ID" "$MODEL" ;;
      F10) [ "$MODEL" = sqrl-9b ] && EXTRA="$VARS" bash "$S/run_experiments.sh" "$ID" "$MODEL" ;;
      *) EXTRA="$VARS" bash "$S/run_experiments.sh" "$ID" "$MODEL" ;;
    esac
  done
  [ "${SKIP_GENERAL:-0}" = 1 ] || for R in $LORA; do
    t2s_train general-eval --model "$MODEL" --served-name "${R%%=*}" || log "Belebele алгасав (${R%%=*})"
  done
  [ "${SKIP_GENERAL:-0}" = 1 ] || t2s_train general-eval --model "$MODEL" || log "Belebele (суурь) алгасав"
  if [ "${SKIP_GENERAL:-0}" != 1 ] && [ -f data/bird/bird_mn.jsonl ]; then
    for R in c1 c3; do
      if [ -d "adapters/${R}__${MODEL}" ]; then
        t2s eval --run F1 --model "$MODEL" --set adapter="$R" --tag "$R" --variants en || log "BIRD-dev ($R) алгасав"
      fi
    done
  fi
else
  log "adapters/*__${MODEL} (SFT) алга — алгасав"
fi
for R in c6 c7 f4 d4; do
  M="adapters/${R}__${MODEL}__merged"
  [ -d "$M" ] || continue
  MODEL_PATH="$M" SERVED="$R" bash "$S/serve_model.sh" "$MODEL"
  ID="$(echo "$R" | tr 'cdf' 'CDF')"
  VARIANTS=mn,latin,en EXTRA="$VARS" bash "$S/run_experiments.sh" "$ID" "$MODEL"
  if [ "$MODEL" = sqrl-9b ]; then
    # sqrl-ийн GRPO нэг ээлжит (max_steps 0) сургагдсан тул нэмэлтээр ижил протоколоор (tag s0) үнэлнэ
    t2s eval --run "$ID" --model "$MODEL" --set agent_max_steps=0 --tag s0 $VARS || true
  fi
  t2s_train general-eval --model "$MODEL" --served-name "$R" || log "Belebele алгасав ($R)"
done
bash "$S/serve_model.sh" stop
t2s report >/dev/null && log "Тайлан: results/REPORT.md"
