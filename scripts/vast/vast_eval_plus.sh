#!/usr/bin/env bash
# =====================================================================
# Vast A100: D2-ийн дээр нэмэлт тест (кирилл dev) — boolean засвар, mn→en орчуулга (A4 кэш), E1/E2.
#   nohup bash scripts/vast/vast_eval_plus.sh > /workspace/plus.log 2>&1 &
# Урьдчилсан нөхцөл: vast_train.sh-ийн setup (PostgreSQL, DB, индекс), adapters/d2__arctic-r1-7b,
#   build/translations/qwen3.5-9b.jsonl. Алхам бүрийг build/vast/plus_<нэр>.done-оор тэмдэглэнэ.
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
MODEL=arctic-r1-7b
SKIP_INSTALL=1 bash scripts/notebook/bootstrap.sh
source scripts/server/env.sh
EXTRA=(--var best_a=A7 --var best_sft=C3 --var best_sft_adapter=c3 --var best_config=D2)
say() { echo "[$(date '+%F %T')] ### $*"; }
step() {   # step <нэр> <ID> [нэмэлт аргумент...]
  local name="$1" id="$2"; shift 2
  [ -f "build/vast/plus_${name}.done" ] && { say "$name алгасав (дууссан)"; return 0; }
  say "$name: $id $*"
  t2s eval --run "$id" --model "$MODEL" --split dev --variants mn --workers 8 "${EXTRA[@]}" "$@"
  touch "build/vast/plus_${name}.done"
}
correct() {   # correct <ID> <tag> [seed] → зөв хариултын тоо
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
step d2_fix D2 --tag fix                                   # boolean 0/1 → TRUE/FALSE засвартай D2
step d2_tr D2 --set translate=true --tag tr               # асуултыг англи руу орчуулж (A4 кэш) D2
step b5_tr B5 --set translate=true --tag tr               # суурь загвар + RAG + орчуулга
FIX=$(correct D2 fix); TR=$(correct D2 tr)
say "D2 fix=$FIX, D2 tr=$TR, B5 tr=$(correct B5 tr)"
TRSET=(); TAGP=""
if [ "$TR" -gt "$FIX" ]; then TRSET=(--set translate=true); TAGP="tr_"; fi
step e1 E1 "${TRSET[@]}" --tag "${TAGP}d2"                 # + алдааны мессежээр засах (2 удаа)
step e2 E2 "${TRSET[@]}" --seeds 1 --tag "${TAGP}d2"       # + 8 нэр дэвшигчийн санал хураалт (seed 1)
say "E1=$(correct E1 "${TAGP}d2"), E2=$(correct E2 "${TAGP}d2" 1)"
bash scripts/server/serve_model.sh stop
t2s report > /dev/null || true
say "PLUS ДУУСЛАА"
