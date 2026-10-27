#!/usr/bin/env bash
# Нийтлэг орчин — бусад скрипт ба интерактив shell `source scripts/server/env.sh` хийнэ. Өөрийн серверт тааруулна.
# Анхаар: энд `set -e` тавихгүй (интерактив shell-ийг алдаа гарахад хаахгүйн тулд) — скриптүүд өөрсдөө тавина.
export T2S_ROOT="${T2S_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export PYTHONPATH="$T2S_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONIOENCODING=utf-8
# PostgreSQL-ийн админ (superuser) холболт. setup.sh нь postgres хэрэглэгчид энэ нууц үгийг тохируулна.
export T2S_PG_PORT="${T2S_PG_PORT:-5432}"
export T2S_PG_PASSWORD="${T2S_PG_PASSWORD:-change_me_admin}"
export T2S_ADMIN_DSN="${T2S_ADMIN_DSN:-postgresql://postgres:${T2S_PG_PASSWORD}@localhost:${T2S_PG_PORT}/postgres}"
# vLLM (OpenAI-compatible) — туршилтын SQL загвар
export T2S_VLLM_PORT="${T2S_VLLM_PORT:-8000}"
export T2S_LLM_BASE_URL="${T2S_LLM_BASE_URL:-http://localhost:${T2S_VLLM_PORT}/v1}"
# Retrieval-ийн embedding (bge-m3): eval үед CPU дээр (vLLM GPU-ийн 90%-ийг эзэлдэг тул GPU-д багтахгүй байж болно).
# index-build нь setup.sh-д vLLM асахаас өмнө GPU-г ашиглана.
export T2S_EMBED_DEVICE="${T2S_EMBED_DEVICE:-cpu}"
# Hugging Face кэш (загварууд ~15–25 GB тус бүр)
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
# Аудит логийн шифрлэлт (service): python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export T2S_AUDIT_KEY="${T2S_AUDIT_KEY:-}"
VENV="${T2S_VENV:-$T2S_ROOT/.venv}"
if [ -f "$VENV/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
fi
t2s() { python -m mn_text2sql "$@"; }
t2s_train() { python -m mn_text2sql.training "$@"; }
log() { echo "[$(date '+%F %T')] $*"; }
