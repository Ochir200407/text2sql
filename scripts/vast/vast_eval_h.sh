#!/usr/bin/env bash
# =====================================================================
# Vast A100: H бүлэг (ерөнхий засвар) + E1/E2 — кирилл dev. vast_eval_plus.sh дууссаны дараа эхэлнэ.
#   nohup bash scripts/vast/vast_eval_h.sh > /workspace/h.log 2>&1 &
# H1 = D2 + SQL засвар + татгалзал + алдаатай SQL-ээс татгалзах; H2 = H1 + англи мөр; H3 = H1 + англи орчуулга;
# H4 = H1 + загварт англи (retrieval монголоор).
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
MODEL=arctic-r1-7b
while [ -f /workspace/plus.log ] && ! grep -q "PLUS ДУУСЛАА" /workspace/plus.log; do sleep 30; done
SKIP_INSTALL=1 bash scripts/notebook/bootstrap.sh
source scripts/server/env.sh
say() { echo "[$(date '+%F %T')] ### $*"; }
VARS=(--var best_a=A7 --var best_sft=C3 --var best_sft_adapter=c3)
step() {   # step <нэр> <ID> [нэмэлт аргумент...]
  local name="$1" id="$2"; shift 2
  [ -f "build/vast/h_${name}.done" ] && { say "$name алгасав (дууссан)"; return 0; }
  say "$name: $id $*"
  t2s eval --run "$id" --model "$MODEL" --split dev --variants mn --workers 8 "${VARS[@]}" "$@"
  touch "build/vast/h_${name}.done"
}
correct() {   # correct <ID> <tag> [seed]
  python - "$1" "$2" "${3:-0}" <<'PY'
import json, sys
cid, tag, seed = sys.argv[1], sys.argv[2], sys.argv[3]
name = f"arctic-r1-7b__mn__s{seed}" + (f"__{tag}" if tag else "")
try:
    print(sum(json.loads(x).get("correct", False) for x in open(f"results/runs/dev/{cid}/{name}.jsonl", encoding="utf-8") if x.strip()))
except FileNotFoundError:
    print(-1)
PY
}

LORA="d2=adapters/d2__${MODEL}" bash scripts/server/serve_model.sh "$MODEL"
step h3 H3
step h2 H2
step h4 H4
step h1 H1
H1N=$(correct H1 ""); H2N=$(correct H2 ""); H3N=$(correct H3 ""); H4N=$(correct H4 "")
say "H1=$H1N H2=$H2N H3=$H3N H4=$H4N"
BEST=H3; BN=$H3N
[ "$H2N" -gt "$BN" ] && { BEST=H2; BN=$H2N; }
[ "$H4N" -gt "$BN" ] && { BEST=H4; BN=$H4N; }
step e1 E1 --var best_config="$BEST" --tag "h_${BEST}"
step e2 E2 --var best_config="$BEST" --seeds 1 --tag "h_${BEST}"
say "E1=$(correct E1 "h_${BEST}") E2=$(correct E2 "h_${BEST}" 1) (суурь $BEST)"
bash scripts/server/serve_model.sh stop
t2s report > /dev/null || true
say "H ДУУСЛАА"
