#!/usr/bin/env bash
# =====================================================================
# Туршилтын ID-уудыг нэг загвар дээр ажиллуулах (vLLM тухайн загвараар асаалттай байх ёстой).
#   bash scripts/server/run_experiments.sh "A1,A6,A7" arctic-r1-7b            # dev, кирилл
#   VARIANTS=mn,latin,en bash scripts/server/run_experiments.sh A1 sqrl-9b       # хэлний/галигийн зөрүү
#   SPLIT=test bash scripts/server/run_experiments.sh B5 arctic-r1-7b           # бүлгийн эцэст test
#   EXTRA="--var best_a=A7" bash scripts/server/run_experiments.sh B1 arctic-r1-7b
# Үр дүн: results/runs/<split>/<ID>/<model>__<variant>__s<seed>.jsonl (+ .summary.json); тасарвал дахин ажиллуулахад
# өмнөх асуултуудыг алгасна (resume). Дараа нь: t2s ts-score --run <ID> ; t2s report
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
IDS="${1:?туршилтын ID(ууд): A1 эсвэл A1,A6}"
MODEL="${2:?загвар: arctic-r1-7b | sqrl-9b}"
SPLIT="${SPLIT:-dev}"
WORKERS="${WORKERS:-8}"
VARG=()
[ -n "${VARIANTS:-}" ] && VARG=(--variants "$VARIANTS")
mkdir -p build/logs
for ID in ${IDS//,/ }; do
  log "=== $ID / $MODEL / $SPLIT ${VARIANTS:+($VARIANTS)}"
  # shellcheck disable=SC2086
  t2s eval --run "$ID" --model "$MODEL" --split "$SPLIT" --workers "$WORKERS" "${VARG[@]}" ${EXTRA:-} \
    2>&1 | tee -a "build/logs/eval_${ID}_${MODEL}_${SPLIT}.log"
  if [ "${TS:-1}" = "1" ]; then
    t2s ts-score --run "$ID" --split "$SPLIT" >> "build/logs/ts_${ID}_${SPLIT}.log" 2>&1 || log "ts-score алгасав"
  fi
done
OUT="results/REPORT.md"; [ "$SPLIT" != dev ] && OUT="results/REPORT_${SPLIT}.md"
t2s report --split "$SPLIT" --out "$OUT" >/dev/null && log "Тайлан: $OUT"
