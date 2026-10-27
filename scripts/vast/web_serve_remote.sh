#!/usr/bin/env bash
# Vast instance дээр ажиллана: vLLM суулгаад 2 сервер асаана (A100 80GB).
#   8000: Snowflake/Arctic-Text2SQL-R1-7B + LoRA d2 (/workspace/adapters/d2) — served «arctic-r1-7b», LoRA «d2»
#   8001: Qwen/Qwen3.5-9B (fp8) — орчуулагч «qwen3.5-9b»
set -euo pipefail
cd /workspace
python -c "import vllm" 2>/dev/null || pip install -q vllm
export VLLM_USE_FLASHINFER_SAMPLER=0
nohup vllm serve Snowflake/Arctic-Text2SQL-R1-7B --served-model-name arctic-r1-7b --dtype bfloat16 \
  --max-model-len 24576 --gpu-memory-utilization 0.5 --enable-prefix-caching \
  --enable-lora --max-lora-rank 64 --lora-modules d2=/workspace/adapters/d2 --port 8000 > /workspace/vllm_sql.log 2>&1 &
# эхнийх санах ойгоо авсны дараа хоёр дахийг асаана (хоёулаа нэг GPU)
for i in $(seq 1 120); do curl -s -m 2 localhost:8000/v1/models >/dev/null && break; sleep 10; done
nohup vllm serve Qwen/Qwen3.5-9B --served-model-name qwen3.5-9b --max-model-len 8192 --gpu-memory-utilization 0.4 \
  --quantization fp8 --limit-mm-per-prompt '{"image":0,"video":0}' --port 8001 > /workspace/vllm_tr.log 2>&1 &
for i in $(seq 1 120); do curl -s -m 2 localhost:8001/v1/models >/dev/null && break; sleep 10; done
echo "WEB SERVERS READY"
