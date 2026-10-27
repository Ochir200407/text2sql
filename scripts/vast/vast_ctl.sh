#!/usr/bin/env bash
# =====================================================================
# Vast.ai-г локалаас удирдах (vastai CLI + ssh). Нэг instance (build/vast_instance.txt-д ID).
#   bash scripts/vast/vast_ctl.sh offers                 # хямд A100 80GB (CUDA ≥ 12.8) жагсаалт
#   bash scripts/vast/vast_ctl.sh up <offer_id>          # түрээслэх (150 GB диск)
#   bash scripts/vast/vast_ctl.sh push <data_train_dir> <adapters_dir>   # код + өгөгдөл + c1..c3 adapter
#   bash scripts/vast/vast_ctl.sh start [GRPO_MAX_STEPS] [BEST_SFT]     # BEST_SFT-гүй бол C1–C3-ыг энд тестлээд сонгоно
#   bash scripts/vast/vast_ctl.sh status | log | fetch <local_dir> | destroy | balance
# Орчин: VASTAI (vastai.exe зам), SSH_KEY (анхдагч ~/.ssh/vast_ed25519).
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
VASTAI="${VASTAI:-vastai}"
KEY="${SSH_KEY:-$HOME/.ssh/vast_ed25519}"
IMAGE="${VAST_IMAGE:-pytorch/pytorch:2.8.0-cuda12.8-cudnn9-devel}"
IDF=build/vast_instance.txt
mkdir -p build
iid() { tr -d '\r\n' < "$IDF"; }
sshinfo() {   # host port (Windows python CR-ийг хасна)
  "$VASTAI" show instance "$(iid)" --raw | python -c "import json,sys; d=json.load(sys.stdin); print(d['ssh_host'], d['ssh_port'])" | tr -d '\r'
}
SSHO=(-o ConnectTimeout=30 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -o LogLevel=ERROR -i "$KEY")
rsh() { read -r H P < <(sshinfo); ssh -n "${SSHO[@]}" -p "$P" "root@$H" "$@"; }
rcp() { read -r H P < <(sshinfo); scp "${SSHO[@]}" -P "$P" "$@"; }
host() { read -r H P < <(sshinfo); echo "root@$H"; }

case "${1:-}" in
  offers)
    "$VASTAI" search offers 'gpu_name=A100_SXM4 num_gpus=1 gpu_ram>=79 reliability>0.97 inet_down>500 disk_space>=150 cuda_max_good>=12.8 rentable=true' \
      -o 'dph+' --raw | python -c "
import json,sys
for o in json.load(sys.stdin)[:8]:
    print(o['id'], o['gpu_name'], round(o['dph_total'], 3), 'rel', round(o['reliability2'], 3), o.get('geolocation'), 'cuda', o.get('cuda_max_good'))" ;;
  up)
    out="$("$VASTAI" create instance "${2:?offer_id}" --image "$IMAGE" --disk 150 --ssh --direct --raw)"
    echo "$out"
    echo "$out" | python -c "import json,sys; print(json.load(sys.stdin)['new_contract'])" > "$IDF"
    echo "instance: $(iid)" ;;
  state)
    "$VASTAI" show instance "$(iid)" --raw | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('actual_status'), d.get('cur_state'), round(d.get('dph_total', 0), 3), d.get('ssh_host'), d.get('ssh_port'))" ;;
  push)
    DATA="${2:?data/train хавтас}"; AD="${3:?adapters хавтас}"
    python scripts/notebook/pack_code.py
    rsh "mkdir -p /workspace/t2s/data /workspace/t2s/adapters"
    rcp dist/t2s_code.zip "$(host):/workspace/"
    rsh "cd /workspace/t2s && python -c \"import zipfile; zipfile.ZipFile('/workspace/t2s_code.zip').extractall('.')\""
    rcp -r "$DATA" "$(host):/workspace/t2s/data/"
    for R in c1 c2 c3; do rcp -r "$AD/${R}__arctic-r1-7b" "$(host):/workspace/t2s/adapters/"; done
    rsh "ls /workspace/t2s/data/train /workspace/t2s/adapters/*" ;;
  code)   # зөвхөн кодыг шинэчлэх (өгөгдөл, adapter-ийг дахин хуулахгүй)
    python scripts/notebook/pack_code.py
    rcp dist/t2s_code.zip "$(host):/workspace/"
    rsh "cd /workspace/t2s && python -c \"import zipfile; zipfile.ZipFile('/workspace/t2s_code.zip').extractall('.')\" && echo code-ok" ;;
  start)
    rsh "cd /workspace/t2s && ${3:+BEST_SFT=$3} GRPO_MAX_STEPS=${2:-60} nohup bash scripts/vast/vast_train.sh > /workspace/vast.log 2>&1 < /dev/null & echo started" ;;
  sh)
    shift; rsh "$@" ;;
  log)
    rsh "tail -c 4000 /workspace/vast.log | tr '\r' '\n' | grep -av '^\s*$' | tail -25" ;;
  status)
    rsh "ls /workspace/t2s/build/vast/ 2>/dev/null; grep -a '###\|Traceback\|Error\|/300 (' /workspace/vast.log | tail -8; tr '\r' '\n' < /workspace/vast.log | grep -a 'it\]' | tail -1; nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader" ;;
  fetch)
    L="${2:?local dir}"; mkdir -p "$L"
    rsh "cd /workspace/t2s && tar czf /workspace/vast_out.tgz --exclude='checkpoint-*' --exclude='*__merged' results adapters/d2__arctic-r1-7b adapters/c6__arctic-r1-7b build/logs build/vast config/training.yaml 2>/dev/null; ls -la /workspace/vast_out.tgz" || true
    rcp "$(host):/workspace/vast_out.tgz" "$L/" && ls -la "$L" ;;
  destroy)
    "$VASTAI" destroy instance "$(iid)" && rm -f "$IDF" ;;
  balance)
    "$VASTAI" show user --raw | python -c "import json,sys; d=json.load(sys.stdin); print('credit', round(d.get('credit', 0), 3))" ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
