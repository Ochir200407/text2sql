#!/usr/bin/env bash
# =====================================================================
# Вэб туршилт: Vast GPU дээр 2 vLLM сервер (SQL загвар + орчуулагч), локал вэб нь SSH tunnel-ээр холбогдоно.
#   bash scripts/vast/vast_web.sh offers           # хямд A100 80GB
#   bash scripts/vast/vast_web.sh up <offer_id>    # түрээслэх (vast_ctl.sh up)
#   bash scripts/vast/vast_web.sh push             # D2 adapter + серверийн скрипт
#   bash scripts/vast/vast_web.sh serve            # vLLM ×2 асаах (8000: arctic + d2 LoRA, 8001: qwen3.5-9b)
#   bash scripts/vast/vast_web.sh ready            # хоёр сервер бэлэн эсэх
#   bash scripts/vast/vast_web.sh tunnel           # localhost:8000, 8001 → instance (энэ цонхонд ажиллана)
#   bash scripts/vast/vast_web.sh web              # локал вэб http://127.0.0.1:8090
#   bash scripts/vast/vast_web.sh destroy          # ДУУСААД ЗААВАЛ
# Орчин: VASTAI (vastai.exe), SSH_KEY, D2_DIR (fp32 adapter хавтас).
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
CTL="bash scripts/vast/vast_ctl.sh"
VASTAI="${VASTAI:-vastai}"
KEY="${SSH_KEY:-$HOME/.ssh/vast_ed25519}"
IDF=build/vast_instance.txt
SSHO=(-o ConnectTimeout=30 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -o LogLevel=ERROR -i "$KEY")
sshinfo() { "$VASTAI" show instance "$(tr -d '\r\n' < "$IDF")" --raw | python -c "import json,sys; d=json.load(sys.stdin); print(d['ssh_host'], d['ssh_port'])" | tr -d '\r'; }

case "${1:-}" in
  offers|up|state|destroy|balance) $CTL "$@" ;;
  push)
    D2="${D2_DIR:?D2_DIR=<d2__arctic-r1-7b adapter хавтас>}"
    $CTL sh "mkdir -p /workspace/adapters"
    read -r H P < <(sshinfo)
    scp "${SSHO[@]}" -P "$P" -r "$D2" "root@$H:/workspace/adapters/d2"
    scp "${SSHO[@]}" -P "$P" scripts/vast/web_serve_remote.sh "root@$H:/workspace/"
    $CTL sh "ls -la /workspace/adapters/d2" ;;
  serve)
    $CTL sh "nohup bash /workspace/web_serve_remote.sh > /workspace/web.log 2>&1 < /dev/null & echo started" ;;
  log)
    $CTL sh "tail -n 30 /workspace/web.log; tail -n 5 /workspace/vllm_sql.log /workspace/vllm_tr.log 2>/dev/null" ;;
  ready)
    $CTL sh "curl -s -m 5 localhost:8000/v1/models | head -c 300; echo; curl -s -m 5 localhost:8001/v1/models | head -c 300; echo; nvidia-smi --query-gpu=memory.used --format=csv,noheader" ;;
  tunnel)
    read -r H P < <(sshinfo)
    echo "tunnel: localhost:8000 (SQL), localhost:8001 (орчуулагч) → $H:$P  (Ctrl+C зогсооно)"
    ssh -N "${SSHO[@]}" -p "$P" -L 8000:localhost:8000 -L 8001:localhost:8001 "root@$H" ;;
  web)
    PYTHONPATH=src T2S_CONFIG=config/web.yaml T2S_WEB_MODEL=d2 T2S_WEB_TRANSLATOR=qwen3.5-9b \
      T2S_WEB_TRANSLATOR_URL=http://localhost:8001/v1 T2S_LLM_TIMEOUT_S=300 PYTHONIOENCODING=utf-8 \
      python -X utf8 -m mn_text2sql serve --run WEB --host 127.0.0.1 --port 8090 ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
