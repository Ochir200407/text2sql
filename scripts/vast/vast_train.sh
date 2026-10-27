#!/usr/bin/env bash
# =====================================================================
# Vast.ai (Docker, root, нэг A100 80 GB) дээр D2 ба C6 сургалт + тэдгээрийн dev тест (кирилл) — нэг удаагийн ажил.
# Урьдчилан хуулах: код (t2s_code.zip задласан), data/train/ (samples, sft), adapters/c1..c3__arctic-r1-7b/.
#   GRPO_MAX_STEPS=60 nohup bash scripts/vast/vast_train.sh > vast.log 2>&1 &   # BEST_SFT-гүй: C1–C3-ыг энд тестлээд сонгоно
#   BEST_SFT=C3 ... (сонгосон бол C-тестийг алгасна)
# Алхам бүр дууссаныг build/vast/<алхам>.done-оор тэмдэглэнэ — дахин ажиллуулахад алгасна.
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
MODEL=arctic-r1-7b
export BEST_A="${BEST_A:-A7}" T2S_SAVE_STEPS="${T2S_SAVE_STEPS:-25}"
mkdir -p build/vast build/logs
# data/train/*.jsonl-ийн BIRD db_path нь Modal-ын /root/t2s/... замтай — ижил замаар хандах боломж олгоно
[ -e /root/t2s ] || ln -sfn "$PWD" /root/t2s
done_() { touch "build/vast/$1.done"; }
is_done() { [ -f "build/vast/$1.done" ]; }
say() { echo "[$(date '+%F %T')] ### $*"; }

if ! is_done setup; then
  say "Орчин (PostgreSQL, pip, DB, индекс)"
  pip install -q -e . --no-deps
  bash scripts/notebook/bootstrap.sh
  done_ setup
else
  SKIP_INSTALL=1 bash scripts/notebook/bootstrap.sh
fi
source scripts/server/env.sh

if [ -z "${BEST_SFT:-}" ] && [ -f build/vast/best_sft.txt ]; then BEST_SFT="$(cat build/vast/best_sft.txt)"; fi
if [ -z "${BEST_SFT:-}" ]; then
  say "C1–C3 тест (кирилл, dev) — C-шилдгийг сонгоно"
  LORA="c1=adapters/c1__${MODEL} c2=adapters/c2__${MODEL} c3=adapters/c3__${MODEL}" bash scripts/server/serve_model.sh "$MODEL"
  VARIANTS=mn EXTRA="--var best_a=$BEST_A" bash scripts/server/run_experiments.sh "C1,C2,C3" "$MODEL"
  bash scripts/server/serve_model.sh stop
  BEST_SFT="$(python - <<'PY'
import glob, json
best = None
for cid in ("C1", "C2", "C3"):
    files = [f for f in glob.glob(f"results/runs/dev/{cid}/*__mn__s0*.jsonl")]
    n = sum(json.loads(x).get("correct", False) for f in files for x in open(f, encoding="utf-8") if x.strip())
    print(f"{cid}: {n}", file=__import__("sys").stderr)
    if best is None or n > best[1]:
        best = (cid, n)
print(best[0])
PY
)"
  echo "$BEST_SFT" > build/vast/best_sft.txt
fi
export BEST_SFT
BEST_R="$(echo "$BEST_SFT" | tr 'C' 'c')"
EXTRA="--var best_a=$BEST_A --var best_sft=$BEST_SFT --var best_sft_adapter=$BEST_R"
say "C-шилдэг: $BEST_SFT"
sed -i "s/^best_sft: .*/best_sft: $BEST_R                   # Vast: C1–C3 dev (кирилл) дүнгээр/" config/training.yaml
[ -d "adapters/${BEST_R}__${MODEL}" ] || { echo "adapters/${BEST_R}__${MODEL} алга"; exit 2; }

if ! is_done d2_train; then
  say "D2 сургалт (BIRD DB татах → өгөгдөл → SFT)"
  STEPS="fetch build sft" RECIPES="d2" bash scripts/server/train_a100.sh "$MODEL"
  done_ d2_train
fi
if ! is_done d2_eval; then
  say "D2 тест (кирилл, dev)"
  LORA="d2=adapters/d2__${MODEL}" bash scripts/server/serve_model.sh "$MODEL"
  VARIANTS=mn EXTRA="$EXTRA" bash scripts/server/run_experiments.sh D2 "$MODEL"
  bash scripts/server/serve_model.sh stop
  done_ d2_eval
fi
if ! is_done c6_train; then
  say "C6 GRPO (эхлэл: ${BEST_R}, max_steps=${GRPO_MAX_STEPS:-бүтэн})"
  STEPS="grpo" GRPO_RECIPES="c6" bash scripts/server/train_a100.sh "$MODEL"
  done_ c6_train
fi
if ! is_done c6_eval; then
  say "C6 тест (кирилл, dev)"
  MODEL_PATH="adapters/c6__${MODEL}__merged" SERVED=c6 bash scripts/server/serve_model.sh "$MODEL"
  VARIANTS=mn EXTRA="$EXTRA" bash scripts/server/run_experiments.sh C6 "$MODEL"
  bash scripts/server/serve_model.sh stop
  done_ c6_eval
fi
t2s report > /dev/null || true
say "БҮГД ДУУСЛАА"
