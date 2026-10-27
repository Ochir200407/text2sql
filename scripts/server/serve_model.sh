#!/usr/bin/env bash
# =====================================================================
# vLLM OpenAI-compatible сервер асаах (config/models.yaml-ийн hf_id, served_name, vllm_args).
#   bash scripts/server/serve_model.sh arctic-r1-7b              # :8000, арын горимд, бэлэн болтол хүлээнэ
#   bash scripts/server/serve_model.sh sqrl-9b 8000 --80gb        # A100: FP8-гүй BF16 (vllm_args_80gb)
#   LORA="c1=adapters/c1__arctic-r1-7b c2=adapters/c2__arctic-r1-7b" bash scripts/server/serve_model.sh arctic-r1-7b
#   MODEL_PATH=adapters/c6__arctic-r1-7b__merged SERVED=c6 bash scripts/server/serve_model.sh arctic-r1-7b
#   bash scripts/server/serve_model.sh stop                       # зогсоох
# sqrl: загварын картын дагуу --reasoning-parser ХЭРЭГЛЭХГҮЙ (</think>-ийн дараах текстийг pipeline авна).
# vllm_args-ийг shlex-ээр задалж bash массиваар дамжуулна (JSON аргумент нь YAML дотор нэг хашилттай).
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
NAME="${1:?загварын нэр (config/models.yaml) эсвэл stop}"
PORT="${2:-$T2S_VLLM_PORT}"
PIDFILE="$T2S_ROOT/build/vllm_${PORT}.pid"
mkdir -p "$T2S_ROOT/build/logs"
if [ "$NAME" = "stop" ]; then
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
  pkill -f "vllm serve" 2>/dev/null || true
  log "vLLM зогслоо"; exit 0
fi
ARGS_KEY="vllm_args"
if [ "${3:-}" = "--80gb" ]; then ARGS_KEY="vllm_args_80gb"; fi
mapfile -t SPEC < <(python - "$NAME" "$ARGS_KEY" <<'PYEOF'
import shlex, sys, yaml
spec = yaml.safe_load(open("config/models.yaml", encoding="utf-8"))["models"][sys.argv[1]]
out = [spec["hf_id"], spec.get("served_name", sys.argv[1])]
out += shlex.split(spec.get(sys.argv[2]) or spec.get("vllm_args", ""))
sys.stdout.write("\n".join(out) + "\n")
PYEOF
)
CR=$(printf '\r')
SPEC=("${SPEC[@]%"$CR"}")
HF_ID="${SPEC[0]:?config/models.yaml-д $NAME алга}"
SERVED_NAME="${SPEC[1]}"
VARGS=("${SPEC[@]:2}")
# GPU-д тааруулах (Kaggle 2×T4 г.м.): T2S_VLLM_ARGS="--dtype half --tensor-parallel-size 2 ..." нь models.yaml-ийн
# vllm_args-ийг бүтнээр орлоно (тайланд тэмдэглэх: төлөвлөгөөний L4 BF16/FP8-аас ялгаатай).
# Загвар тус бүрээр: T2S_VLLM_ARGS__<нэр> (- ба . → _), жишээ нь T2S_VLLM_ARGS__sqrl_9b; эс бөгөөс T2S_VLLM_ARGS.
OVR_NAME="T2S_VLLM_ARGS__${NAME//[-.]/_}"
OVR="${!OVR_NAME:-${T2S_VLLM_ARGS:-}}"
if [ -n "$OVR" ]; then read -r -a VARGS <<< "$OVR"; log "vllm_args-ийг орлуулав ($OVR_NAME / T2S_VLLM_ARGS)"; fi
MODEL="${MODEL_PATH:-$HF_ID}"
SERVED="${SERVED:-$SERVED_NAME}"
EXTRA=()
if [ -n "${LORA:-}" ]; then
  EXTRA+=(--enable-lora --max-lora-rank 64 --lora-modules)
  for m in $LORA; do EXTRA+=("$m"); done
fi
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  log "Өмнөх vLLM-ийг зогсооно"; kill "$(cat "$PIDFILE")"; sleep 5
fi
LOGF="$T2S_ROOT/build/logs/vllm_${SERVED}_$(date +%Y%m%d_%H%M%S).log"
log "vLLM: $MODEL → served-model-name=$SERVED, port=$PORT, args=${VARGS[*]} ${EXTRA[*]:-}"
nohup vllm serve "$MODEL" --served-model-name "$SERVED" --port "$PORT" --seed 0 "${VARGS[@]}" "${EXTRA[@]}" > "$LOGF" 2>&1 &
echo $! > "$PIDFILE"
for i in $(seq 1 180); do
  if curl -sf "http://localhost:${PORT}/v1/models" | grep -q "\"$SERVED\""; then
    log "vLLM бэлэн ($((i*5)) с), served: $SERVED. Лог: $LOGF"; exit 0
  fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then log "vLLM унтарлаа — лог: $LOGF"; tail -40 "$LOGF"; exit 3; fi
  sleep 5
done
log "vLLM 15 минутад бэлэн болсонгүй — лог: $LOGF"; exit 4
