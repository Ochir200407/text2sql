#!/usr/bin/env bash
# =====================================================================
# Vast A100: dev-ээр сонгосон тохиргоог TEST split (200 асуулт, кирилл) дээр нэг удаа баталгаажуулах.
#   nohup bash scripts/vast/vast_eval_test.sh > /workspace/test.log 2>&1 &
# vast_eval_h.sh дууссаны («H ДУУСЛАА») дараа эхэлнэ. D2 (суурь) ба H4, H2.
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
MODEL=arctic-r1-7b
while ! grep -q "H ДУУСЛАА" /workspace/h.log 2>/dev/null; do sleep 30; done
SKIP_INSTALL=1 bash scripts/notebook/bootstrap.sh
source scripts/server/env.sh
say() { echo "[$(date '+%F %T')] ### $*"; }
VARS=(--var best_a=A7 --var best_sft=C3 --var best_sft_adapter=c3)
LORA="d2=adapters/d2__${MODEL}" bash scripts/server/serve_model.sh "$MODEL"
for ID in H4 D2 H2; do
  [ -f "build/vast/test_${ID}.done" ] && continue
  say "TEST $ID"
  t2s eval --run "$ID" --model "$MODEL" --split test --variants mn --workers 8 "${VARS[@]}"
  touch "build/vast/test_${ID}.done"
done
bash scripts/server/serve_model.sh stop
say "TEST ДУУСЛАА"
