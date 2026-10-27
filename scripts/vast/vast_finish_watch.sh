#!/usr/bin/env bash
# =====================================================================
# Локал хяналт: Vast дээр «TEST ДУУСЛАА» гарах эсвэл үлдэгдэл босгоос доош орохоор үр дүнг татаж instance-ийг устгана.
#   bash scripts/vast/vast_finish_watch.sh <local_out_dir> [min_credit=0.30]
# Устгахаас өмнө архивт results/runs байгааг шалгана (татаж чадаагүй бол устгахгүй).
# =====================================================================
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
OUT="${1:?local out dir}"; MIN="${2:-0.30}"
C="scripts/vast/vast_ctl.sh"
while true; do
  credit="$(bash "$C" balance 2>/dev/null | awk '{print $2}')"
  done_n="$(timeout 60 bash "$C" sh "grep -c 'TEST ДУУСЛАА' /workspace/test.log 2>/dev/null || true" 2>/dev/null | tr -d '\r' | tail -1)"
  echo "[$(date '+%F %T')] credit=$credit test_done=${done_n:-?}"
  low=$(python -c "import sys; print(1 if float(sys.argv[1] or 99) < float(sys.argv[2]) else 0)" "${credit:-99}" "$MIN" 2>/dev/null || echo 0)
  if [ "${done_n:-0}" -ge 1 ] 2>/dev/null || [ "$low" = 1 ]; then
    echo "татаж байна (done=${done_n:-0}, low=$low)"
    for attempt in 1 2 3; do
      if timeout 1200 bash "$C" fetch "$OUT" && tar --force-local -tzf "$OUT/vast_out.tgz" | grep -q "results/runs/"; then
        tar --force-local -tzf "$OUT/vast_out.tgz" | grep -c "\.jsonl$"
        echo y | bash "$C" destroy && { echo "УСТГАВ"; exit 0; }
      fi
      echo "татах/устгах оролдлого $attempt амжилтгүй"; sleep 30
    done
    echo "АНХААР: татаж чадсангүй — instance-ийг устгаагүй"; exit 2
  fi
  sleep 120
done
