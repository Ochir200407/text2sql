#!/usr/bin/env bash
# =====================================================================
# Сургалтгүй тест (A, B, F, E бүлэг) — нэг L4 (24 GB). Үе шат бүрийг ТУСАД НЬ ажиллуулна: дараагийн үе шат нь
# өмнөхийн шилдэг тохиргооноос хамаардаг (туршилтын төлөвлөгөө). Дараалал:
#   STAGE=caches  bash scripts/server/run_plan_l4.sh             # A4 орчуулга, F8 router, F7 intent кэш
#   STAGE=A       bash scripts/server/run_plan_l4.sh             # → REPORT.md-ээс A1/A6/A7-ийн шилдгийг сонго
#   STAGE=B BEST_A=A6 bash scripts/server/run_plan_l4.sh         # B1–B10 (A-шилдэг дээр)
#   STAGE=F BEST_A=A6 bash scripts/server/run_plan_l4.sh         # F1, F2, F3, F5, F7, F8 (сургалтгүй F)
#   (C/D сургалт: train_a100.sh → eval_adapters.sh; эсвэл STAGE=adapters BEST_A=A7 — Kaggle-д хэсэгчилсэн zip-ээс)
#   STAGE=E BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # E1–E3 (A–D-ийн шилдэг дээр)
#   STAGE=G BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # G1–G6 agent (docs/AGENT.md; E-ийн дараа)
#   STAGE=thesis BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # text2sql-thesis-ийн кейс (docs/THESIS_BENCH.md)
#   STAGE=test BEST_A=A6 BEST_CONFIG=D1 TEST_IDS=A6,B5,D1,E2 bash scripts/server/run_plan_l4.sh
#   STAGE=e2test BEST_A=A7 BEST_CONFIG=H4 BEST_SFT=C3 bash scripts/server/run_plan_l4.sh   # Kaggle T4: E2 + H4 test (D2 нэгтгэсэн)
#   STAGE=istage BEST_A=A7 BEST_SFT=C3 I_RUNS="I1:dev I1:test E2@I1:test" bash scripts/server/run_plan_l4.sh   # I бүлэг
#   STAGE=train_d3 BEST_A=A7 [D3_EVAL=1] bash scripts/server/run_plan_l4.sh   # Kaggle 2×T4: D3 QLoRA → нэгтгэх → D3s dev
# BEST_* нь config/experiments.yaml-ийн vars-ийг (--var) дарна; утга нь run бүрийн .meta.json-д (baseline) бичигдэнэ.
# Тасарвал ижил командыг дахин ажиллуулахад дууссан асуултыг алгасна (тохиргоо өөрчлөгдсөн бол runner зогсоно).
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
S="scripts/server"
# Kaggle-ийн notebook-ийн тохиргооны нүдийг засах боломжгүй үед (editor гацсан) env-ийг кодын dataset-ээр дарж бичнэ:
# config/kaggle_overrides.env (KEY=VALUE мөрүүд). KO_UNTIL=YYYY-MM-DD (UTC)-ээс хойш үйлчлэхгүй — дараагийн сессүүдэд
# санамсаргүй үлдэхгүйн тулд.
if [ -f config/kaggle_overrides.env ]; then
  KO_UNTIL="$(sed -n 's/^KO_UNTIL=//p' config/kaggle_overrides.env | tr -d '"' | head -1)"
  if [ -n "$KO_UNTIL" ] && [ "$(date -u +%F)" \> "$KO_UNTIL" ]; then
    echo "[override] config/kaggle_overrides.env хугацаа нь дууссан (KO_UNTIL=$KO_UNTIL) — алгасав"
  else
    set -a; . config/kaggle_overrides.env; set +a
    echo "[override] $(grep -v '^#' config/kaggle_overrides.env | grep . | tr '\n' ' ')"
  fi
fi
STAGE="${STAGE:?STAGE=caches|A|B|adapters|e2test|istage|t2|agent|train_d3|mt|t2s_inv|F|E|G|thesis|test заана (дарааллыг скриптийн толгойноос үз)}"
MODELS="${MODELS:-arctic-r1-7b sqrl-9b}"
need() { [ -n "${!1:-}" ] || { echo "STAGE=$STAGE: $1=<ID> заавал (жишээ: $1=$2) — өмнөх үе шатын REPORT.md-ээс сонгоно"; exit 2; }; }
VARS=""
[ -n "${BEST_A:-}" ] && VARS="$VARS --var best_a=$BEST_A"
[ -n "${BEST_CONFIG:-}" ] && VARS="$VARS --var best_config=$BEST_CONFIG"
[ -n "${BEST_SFT:-}" ] && VARS="$VARS --var best_sft=$BEST_SFT --var best_sft_adapter=$(echo "$BEST_SFT" | tr 'C' 'c')"
run() { EXTRA="$VARS" VARIANTS="${VARIANTS:-}" bash "$S/run_experiments.sh" "$@"; }
# Kaggle T4: D2 LoRA-г (хэсэгчилсэн zip-ээс угсарч) fp32-оор нэгтгэсэн fp16 жингээр served=d2 болгож асаана.
# T4 дээр vLLM-ийн LoRA kernel ~3 дахин удаан (C1: LoRA-тай 22.7 с/асуулт, A1: LoRA-гүй 7.4 с) — merge_lora.py.
serve_d2_merged() {
  local MODEL=arctic-r1-7b AD="adapters/d2__arctic-r1-7b" M="adapters/d2__arctic-r1-7b__merged16" P0 D HF_ID
  if [ ! -f "$AD/adapter_model.safetensors" ]; then
    P0="$(find "${ADAPTER_SRC:-/kaggle/input}" -name 't2s_adapter_d2.zip.part00' 2>/dev/null | head -1 || true)"
    [ -n "$P0" ] || { log "D2 adapter алга: t2s_adapter_d2.zip.part00 олдсонгүй"; exit 2; }
    D="$(dirname "$P0")"
    cat "$D"/t2s_adapter_d2.zip.part* > /tmp/t2s_adapter_d2.zip
    [ "$(sha256sum /tmp/t2s_adapter_d2.zip | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$D/SHA256.txt")" ]       || { log "D2 adapter zip-ийн SHA256 таарсангүй"; exit 2; }
    python -c "import zipfile; zipfile.ZipFile('/tmp/t2s_adapter_d2.zip').extractall('.')"
  fi
  if [ ! -f "$M/t2s_merge.json" ]; then
    HF_ID="$(python -c "import yaml; print(yaml.safe_load(open('config/models.yaml', encoding='utf-8'))['models']['$MODEL']['hf_id'])")"
    log "D2 LoRA-г нэгтгэж байна → $M"
    python scripts/notebook/merge_lora.py --base "$HF_ID" --adapter "$AD" --out "$M"
    # Kaggle-ийн дискэнд 15 GB чөлөөлнө: vLLM нэгтгэсэн жинг үйлчилнэ, суурийн кэш дахин хэрэггүй
    [ -d /kaggle ] && rm -rf "${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${HF_ID//\//--}"
  fi
  [ "${D2_MERGE_ONLY:-0}" = 1 ] && return 0
  MODEL_PATH="$M" SERVED=d2 bash "$S/serve_model.sh" "$MODEL"
}
# Вэб/T2 (T4, 15 GB): vLLM-ийн шинэ хувилбар bitsandbytes-гүй тул transformers + 4-bit жижиг серверээр
# (scripts/server/hf_openai_server.py): D2 (port 8000, served=d2), орчуулагч qwen3.5-9b (port 8001).
# 2 GPU бол тус бүрт нэг загвар, 1 GPU (Colab) бол хоёуланг нэг GPU-д.
hf_models() {
  local NG G0 G1 i
  NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"
  if [ "${NG:-1}" -ge 2 ]; then G0=0; G1=1; else G0=0; G1=0; fi
  mkdir -p build/logs
  D2_MERGE_ONLY=1 serve_d2_merged
  log "GPU $NG — D2 GPU$G0, орчуулагч GPU$G1 (4-bit, transformers)"
  CUDA_VISIBLE_DEVICES=$G0 nohup python scripts/server/hf_openai_server.py --model adapters/d2__arctic-r1-7b__merged16 \
    --served d2 --port 8000 > build/logs/hf_d2.log 2>&1 &
  for i in $(seq 1 120); do curl -sf localhost:8000/v1/models >/dev/null && break; sleep 10; done
  curl -sf localhost:8000/v1/models >/dev/null || { tail -30 build/logs/hf_d2.log; exit 2; }
  CUDA_VISIBLE_DEVICES=$G1 nohup python scripts/server/hf_openai_server.py --model Qwen/Qwen3.5-9B \
    --served qwen3.5-9b --port 8001 > build/logs/hf_tr.log 2>&1 &
  for i in $(seq 1 120); do curl -sf localhost:8001/v1/models >/dev/null && break; sleep 10; done
  curl -sf localhost:8001/v1/models >/dev/null || { tail -30 build/logs/hf_tr.log; exit 2; }
  log "загварууд бэлэн: $(grep -h "ачааллаа" build/logs/hf_d2.log build/logs/hf_tr.log | tr "\n" " ")"
}
# D3-ийн сургалтын өгөгдөл (кодын архивт байхгүй): scripts/notebook/pack_train_d3.py-ийн t2s_train_d3.zip.partNN +
# SHA256_train_d3.txt-ийг /kaggle/input-ээс угсарч шалгаад data/train-д задална. zip-ийг шууд dataset болгосон бол
# Kaggle задалсан байдаг — тэр үед .../data/train/D3_MANIFEST.json-тэй хавтсыг хуулна.
unpack_d3_data() {
  local SRC="${TRAIN_SRC:-/kaggle/input}" Z=/tmp/t2s_train_d3.zip P0 D MAN
  [ -f data/train/D3_MANIFEST.json ] && return 0
  P0="$(find "$SRC" -name 't2s_train_d3.zip.part00' 2>/dev/null | head -1 || true)"
  if [ -n "$P0" ]; then
    D="$(dirname "$P0")"
    cat "$D"/t2s_train_d3.zip.part* > "$Z"
    [ "$(sha256sum "$Z" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$D/SHA256_train_d3.txt")" ] \
      || { log "D3 өгөгдлийн zip-ийн SHA256 таарсангүй ($D)"; exit 2; }
    python -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall('.')" "$Z"
  else
    MAN="$(find "$SRC" -name D3_MANIFEST.json -path '*data/train/*' 2>/dev/null | head -1 || true)"
    [ -n "$MAN" ] || { log "D3 өгөгдөл алга: t2s_train_d3.zip.part00 (эсвэл задалсан data/train/D3_MANIFEST.json) $SRC-д олдсонгүй — локалд python scripts/notebook/pack_train_d3.py --src <data/train> → Kaggle dataset → Add Input"; exit 2; }
    mkdir -p data/train && cp -r "$(dirname "$MAN")"/. data/train/
  fi
  log "D3 өгөгдөл (eval-ээр хассан = excluded): $(python -c "import json; m = json.load(open('data/train/D3_MANIFEST.json', encoding='utf-8')); print(m['counts'], 'excluded =', len(m['excluded']))")"
}
# e2test_offline: сэргээсэн E2 test-ийн (e2test stage, h_H4) хувилбаруудаас E4/E4f (reselect) ба E3c — GPU-гүй, хэдхэн
# минут; файл байхгүй бол алгасна. Q|/S| мөрийг логт хэвлэнэ (compact_results).
e2test_offline() {
  local E2T="results/runs/test/E2/${MODEL}__mn__s1__h_H4.jsonl" D
  if [ ! -f "$E2T" ]; then log "E2 test ($E2T) алга — E4/E4f/E3c test алгасав"; return 0; fi
  log "=== E2 test-ээс офлайн: E4, E4f, E3c ($(wc -l < "$E2T") мөр)"
  t2s reselect --from E2 --to E4 --tag h_H4 --split test
  t2s reselect --from E2 --to E4f --tag h_H4 --split test --no-refuse --no-clarify
  t2s derive-abstain --from E2 --to E3c --threshold 0.5 --action clarify --tag h_H4 --split test
  for D in E2 E4 E4f E3c; do python scripts/notebook/compact_results.py "results/runs/test/$D/${MODEL}__mn__s1__h_H4.jsonl"; done
}
# reselect_i_runs: өмнөх version-ий I3/I3t (dev, test) үр дүнд E4-ийн дүрмийг ЗАСВАРЛАСАН кодоор (enum, '%q', reselect-ийн
# татгалзал) дахин хэрэглэнэ → <ID>r2 (Version 9-ийн I3r/I3tr хуучин кодоор). GPU-гүй, хэдхэн минут; файлгүй бол алгасна.
reselect_i_runs() {
  local SPL ID F
  for SPL in dev test; do
    for ID in I3 I3t; do
      F="results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
      [ -f "$F" ] || continue
      log "=== $ID $SPL → ${ID}r2 (E4-ийн дүрэм, шинэ код)"
      t2s reselect --from "$ID" --to "${ID}r2" --split "$SPL"         && python scripts/notebook/compact_results.py "results/runs/$SPL/${ID}r2/${MODEL}__mn__s0.jsonl"
    done
  done
}
# ensemble_offline: E5 = E2-ийн 8 хувилбар (h_H4, seed 1) + I3t-ийн хувилбар (жин w = 1, 2, 3) хамт санал хураалт + E4-ийн
# шүүлт ба дүрэм — dev ба test. Жинг dev-ээр сонгоно (test-ийг сонголтод ашиглахгүй). GPU-гүй; файл дутвал алгасна.
ensemble_offline() {
  local SPL W
  for SPL in dev test; do
    [ -f "results/runs/$SPL/E2/${MODEL}__mn__s1__h_H4.jsonl" ] && [ -f "results/runs/$SPL/I3t/${MODEL}__mn__s0.jsonl" ] \
      || { log "E5 $SPL: E2 эсвэл I3t алга — алгасав"; continue; }
    for W in 1 2 3; do
      log "=== E5w$W $SPL (E2 + I3t×$W, E4-ийн дүрэм)"
      t2s reselect --from E2 --to "E5w$W" --tag h_H4 --split "$SPL" --extra I3t --extra-weight "$W" \
        && python scripts/notebook/compact_results.py "results/runs/$SPL/E5w$W/${MODEL}__mn__s1__h_H4.jsonl"
    done
  done
}
# d3_ckpt <хавтас>: хамгийн сүүлийн БҮРЭН d3 checkpoint (trainer_state.json-той) — хавтасны шууд checkpoint-* (сургалтын
# output_dir) эсвэл доторх (рекурсив) .../d3__*/checkpoint-* (өмнөх сессийн Output); байхгүй бол хоосон
d3_ckpt() {
  python - "$1" <<'PYEOF'
import re, sys
from pathlib import Path
best = None
root = Path(sys.argv[1])
hits = list(root.glob("checkpoint-*/trainer_state.json")) + list(root.glob("**/d3__*/checkpoint-*/trainer_state.json")) \
    if root.is_dir() else []
for p in hits:
    m = re.fullmatch(r"checkpoint-(\d+)", p.parent.name)
    if m and (best is None or int(m.group(1)) > best[0]):
        best = (int(m.group(1)), str(p.parent))
print(best[1] if best else "")
PYEOF
}

case "$STAGE" in
  caches)
    log "### Кэш: A4 орчуулга ба F8 router (qwen3.5-9b), F7 intent (gemma-4-e4b)"
    bash "$S/serve_model.sh" qwen3.5-9b
    t2s translate --split all --variants mn,latin,mixed
    t2s route --split all --variants mn,latin
    bash "$S/serve_model.sh" gemma-4-e4b
    t2s intent --split all --variants mn,latin,mixed
    ;;
  A)
    for MODEL in $MODELS; do
      log "### A бүлэг — $MODEL"
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin,en,mixed run A1 "$MODEL"          # A2 (en), A3 (latin) нь A1-ийн хувилбарууд
      VARIANTS=mn,latin,en run "A6,A7" "$MODEL"
      run A4 "$MODEL"
      if [ "$MODEL" = sqrl-9b ]; then run A5 "$MODEL"; fi
    done
    log "A дууссан: results/REPORT.md-ээс A1/A6/A7-ийн шилдгийг (dev, кирилл) сонгоод STAGE=B BEST_A=<ID> ..."
    ;;
  B)
    need BEST_A A6
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      if [ -n "${B_IDS:-}" ]; then   # удаан GPU (T4)-д цөөрүүлсэн дараалал, жишээ нь B_IDS=B5,B1,B2,B4,B3 B_VARIANTS=mn
        VARIANTS="${B_VARIANTS:-mn}" run "$B_IDS" "$MODEL"
      else
        run "B1,B2,B3,B4" "$MODEL"
        VARIANTS=mn,latin,en run B5 "$MODEL"
        run "B6,B7,B8,B9,B10" "$MODEL"
      fi
    done
    ;;
  adapters)   # C1–C3 adapter-ийн үнэлгээ (eval_adapters.sh). Kaggle: хэсэгчилсэн zip-ийг /kaggle/input-ээс угсарна
    need BEST_A A6
    if ! ls -d adapters/c*__* >/dev/null 2>&1; then
      P0="$(find "${ADAPTER_SRC:-/kaggle/input}" -name 't2s_adapters16.zip.part00' 2>/dev/null | head -1)"
      [ -n "$P0" ] || { log "adapters/ алга, t2s_adapters16.zip.part00 ч олдсонгүй"; exit 2; }
      D="$(dirname "$P0")"
      cat "$D"/t2s_adapters16.zip.part* > /tmp/t2s_adapters16.zip
      if [ -f "$D/SHA256.txt" ]; then
        [ "$(sha256sum /tmp/t2s_adapters16.zip | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$D/SHA256.txt")" ] || { log "adapter zip-ийн SHA256 таарсангүй"; exit 2; }
      fi
      python -c "import zipfile; zipfile.ZipFile('/tmp/t2s_adapters16.zip').extractall('.')"
      log "adapters угсарлаа: $(ls -d adapters/*__* | tr '\n' ' ')"
    fi
    for MODEL in $MODELS; do
      RECIPES="${RECIPES:-c1 c2 c3}" C_VARIANTS="${C_VARIANTS:-mn}" SKIP_GENERAL="${SKIP_GENERAL:-1}" \
        bash "$S/eval_adapters.sh" "$MODEL"
    done
    ;;
  e2test)   # E2 (BEST_CONFIG дээр засвар + 8 санал) test split, Kaggle T4; дараа нь ижил GPU-д H4 (tag t4)
    need BEST_A A7; need BEST_CONFIG H4
    MODEL=arctic-r1-7b
    serve_d2_merged
    for ID in ${E2TEST_IDS:-E2 H4}; do
      if [ "$ID" = E2 ]; then ARGS=(--seeds 1 --tag "h_${BEST_CONFIG}"); OUTF="${MODEL}__mn__s1__h_${BEST_CONFIG}"
      else ARGS=(--tag t4); OUTF="${MODEL}__mn__s0__t4"; fi
      log "=== TEST $ID ${ARGS[*]}"
      # shellcheck disable=SC2086
      t2s eval --run "$ID" --model "$MODEL" --split test --variants mn --workers "${WORKERS:-8}" $VARS "${ARGS[@]}"
      python scripts/notebook/compact_results.py "results/runs/test/$ID/$OUTF.jsonl"
    done
    ;;
  istage)   # I бүлэг (80%-ийн төлөвлөгөө): жишээний санд dev-ийн асуулт (ба шинэ жишээ) — индекс → I_RUNS.
            # dev дээр асуулт бүр өөрийн family-ийн жишээг татахгүй (Pipeline.item_families); test-д dev бүгд орно.
            # I_RUNS: "<ID>:<split>" эсвэл E2-ийг I тохиргоон дээр: "E2@I1:test" (seed 1, tag h_I1)
    need BEST_A A7
    MODEL=arctic-r1-7b
    # 0) Өмнөх version-ий (input-ээр сэргээсэн архив) E2 test → E4/E4f (reselect) ба E3c — GPU-гүй
    e2test_offline
    build_index() {   # build_index <хавтас> [index-build аргумент...] — bge-m3 GPU дээр, vLLM-ээс өмнө
      local out="$1"; shift
      [ -f "$out/.ok" ] || { T2S_EMBED_DEVICE=auto t2s index-build "$@" --out "$out" > "$out.json" && touch "$out/.ok"; }
    }
    build_index build/index_dev --dev-examples
    if [ -f knowledge/examples_plus.jsonl ]; then
      build_index build/index_plus --extra-examples knowledge/examples_plus.jsonl
      build_index build/index_plus_dev --dev-examples --extra-examples knowledge/examples_plus.jsonl
      # J1: 52 нэр томьёотой glossary_plus.yaml-ийн embedding-тэй тусдаа индекс (бусад тохиргоонд нөлөөлөхгүй)
      if [[ " ${I_RUNS:-} " == *" J1"[@:]* || " ${I_RUNS:-} " == *" J1r"[@:]* ]]; then
        build_index build/index_plus_dev_j --dev-examples --extra-examples knowledge/examples_plus.jsonl \
          --glossary knowledge/glossary_plus.yaml
      fi
    fi
    # I4: 2-р шатны жишээ нэмсэн сан (examples_plus_all.jsonl)
    if [[ " ${I_RUNS:-} " == *" I4"* ]] && [ -f knowledge/examples_plus_all.jsonl ]; then
      build_index build/index_plus2_dev --dev-examples --extra-examples knowledge/examples_plus_all.jsonl
    fi
    serve_d2_merged
    for R in ${I_RUNS:-I1:dev I1:test}; do
      ID="${R%%:*}"; SPL="${R##*:}"
      if [[ "$ID" == E2@* ]]; then
        BC="${ID#E2@}"; RID=E2; ARGS=(--var best_config="$BC" --seeds 1 --tag "h_${BC}"); OUTF="${MODEL}__mn__s1__h_${BC}"
      else
        RID="$ID"; ARGS=(); OUTF="${MODEL}__mn__s0"
      fi
      log "=== $RID $SPL ${ARGS[*]}"
      # shellcheck disable=SC2086
      t2s eval --run "$RID" --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-8}" $VARS "${ARGS[@]}"
      python scripts/notebook/compact_results.py "results/runs/$SPL/$RID/$OUTF.jsonl"
      # E4-ийн дүрмийг (хувилбар шүүлт, %q, дүрмийн татгалзал/тодруулга) мөн хэрэглэнэ — загваргүй
      if [ "$RID" = E2 ]; then DST=E4; TG=(--tag "h_${BC}"); else DST="${RID}r"; TG=(); fi
      t2s reselect --from "$RID" --to "$DST" --split "$SPL" "${TG[@]}"
      python scripts/notebook/compact_results.py "results/runs/$SPL/$DST/$OUTF.jsonl"
    done
    ;;
  web)   # Kaggle 2×T4 дээр асуулт-хариултын вэб: GPU0 = D2 (4-bit), GPU1 = орчуулагч qwen3.5-9b (4-bit), DB, вэб,
         # Cloudflare-ийн үнэгүй tunnel (trycloudflare) → нийтийн холбоос логт «WEB URL:». WEB_MIN минут ажиллана.
    MODEL=arctic-r1-7b
    python -c "import bitsandbytes" 2>/dev/null || pip install -q bitsandbytes
    pip cache purge > /dev/null 2>&1 || true
    log "диск: $(df -h / | tail -1 | awk '{print $4" сул / "$2}')"
    if [ ! -f build/index_web/.ok ]; then
      T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus_all.jsonl \
        --out build/index_web > build/index_web.json && touch build/index_web/.ok
    fi
    hf_models
    KEY="$(python -c "import secrets; print(secrets.token_urlsafe(9))")"
    T2S_CONFIG=config/web.yaml T2S_WEB_MODEL=d2 T2S_WEB_TRANSLATOR=qwen3.5-9b T2S_WEB_TRANSLATOR_URL=http://localhost:8001/v1 \
      T2S_WEB_KEY="$KEY" T2S_LLM_TIMEOUT_S=900 nohup python -m mn_text2sql serve --run WEB --host 127.0.0.1 --port 8090 \
      > build/logs/web.log 2>&1 &
    for i in $(seq 1 60); do curl -sf localhost:8090/health >/dev/null && break; sleep 5; done
    [ -x /tmp/cloudflared ] || curl -sL -o /tmp/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /tmp/cloudflared
    nohup /tmp/cloudflared tunnel --url http://localhost:8090 --no-autoupdate > build/logs/cloudflared.log 2>&1 &
    URL=""
    for i in $(seq 1 60); do URL="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' build/logs/cloudflared.log | head -1 || true)"; [ -n "$URL" ] && break; sleep 3; done
    log "WEB URL: ${URL:-олдсонгүй (build/logs/cloudflared.log)}/?k=$KEY"
    log "FLOW BUILDER: ${URL:-?}/builder?k=$KEY   (pipeline-ийг node-оор угсарч нэг асуултаар турших)"
    END=$(( $(date +%s) + ${WEB_MIN:-180} * 60 ))
    # 20 с тутам вэб/загварын логийн шинэ алдаа, загварын хугацааг энэ цонхонд хэвлэнэ; 5 мин тутам төлөв
    N0=0; T5=$(date +%s)
    while [ "$(date +%s)" -lt "$END" ]; do
      sleep 20
      N1=$(cat build/logs/web.log build/logs/hf_d2.log build/logs/hf_tr.log 2>/dev/null | wc -l)
      [ "$N1" -le "$N0" ] || cat build/logs/web.log build/logs/hf_d2.log build/logs/hf_tr.log 2>/dev/null | tail -n $(( N1 - N0 ))         | grep -E "Error|error|Exception|Traceback|амжилтгүй|токен|^  File|OutOfMemory" | grep -v "GET /job" | tail -40 || true
      N0=$N1
      if [ $(( $(date +%s) - T5 )) -ge 300 ]; then
        T5=$(date +%s)
        log "вэб ажиллаж байна: $(grep -c 'POST /ask' build/logs/web.log || true) асуулт; үлдсэн $(( (END - $(date +%s)) / 60 )) мин; WEB URL: ${URL}/?k=$KEY"
      fi
    done
    ;;
  t2)   # T2 орчуулга (маскыг DB-ийн оноосон нэрээр шүүх + semantic_model/glossary-ийн нэр томьёоны RAG; translate_terms.py)
        # → I3t2 (I3 + qwen3.5-9b.t2 кэш) ба I3t2r (E4-ийн дүрэм). Kaggle 2×T4: орчуулагч transformers 4-bit (GPU1) →
        # унтраагаад D2-ийг I3t-тэй ИЖИЛ замаар (serve_d2_merged, vLLM fp16) үйлчилнэ — тоо шууд харьцуулагдана.
        # (Colab 1×T4 дээр D2 4-bit + ~20k prompt: ~2 мин/асуулт, OOM — 2026-10-20 туршсан, тохирохгүй.)
        # T2_SPLITS (анхдагч "dev test"), RESULTS_DST — үр дүнг хуулах хавтас (жишээ нь Drive).
    MODEL=arctic-r1-7b
    VARS="--var best_a=${BEST_A:-A7}"
    python -c "import bitsandbytes" 2>/dev/null || pip install -q bitsandbytes
    if [ ! -f build/index_plus_dev/.ok ]; then
      T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
        --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
    fi
    mkdir -p build/logs
    NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"; GT=0; [ "${NG:-1}" -ge 2 ] && GT=1
    CUDA_VISIBLE_DEVICES=$GT nohup python scripts/server/hf_openai_server.py --model Qwen/Qwen3.5-9B \
      --served qwen3.5-9b --port 8001 > build/logs/hf_tr.log 2>&1 &
    for i in $(seq 1 120); do curl -sf localhost:8001/v1/models >/dev/null && break; sleep 10; done
    curl -sf localhost:8001/v1/models >/dev/null || { tail -30 build/logs/hf_tr.log; exit 2; }
    for SPL in ${T2_SPLITS:-dev test}; do
      log "T2 орчуулга: $SPL"
      t2s translate --t2 --split "$SPL" --variants mn --model qwen3.5-9b --base-url http://localhost:8001/v1 --workers 4
    done
    pkill -f "served qwen3.5-9b" || true
    sleep 10
    serve_d2_merged
    for SPL in ${T2_SPLITS:-dev test}; do
      log "=== I3t2 $SPL"
      # shellcheck disable=SC2086
      T2S_LLM_TIMEOUT_S=900 t2s eval --run I3t2 --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-4}" $VARS
      python scripts/notebook/compact_results.py "results/runs/$SPL/I3t2/${MODEL}__mn__s0.jsonl"
      t2s reselect --from I3t2 --to I3t2r --split "$SPL"
      python scripts/notebook/compact_results.py "results/runs/$SPL/I3t2r/${MODEL}__mn__s0.jsonl"
      if [ -n "${RESULTS_DST:-}" ]; then
        mkdir -p "$RESULTS_DST/$SPL"
        cp -r "results/runs/$SPL/I3t2" "results/runs/$SPL/I3t2r" "$RESULTS_DST/$SPL/"
        cp build/translations/qwen3.5-9b.t2.jsonl "$RESULTS_DST/"
        log "үр дүн хуулсан → $RESULTS_DST/$SPL"
      fi
    done
    ;;
  agent)   # K1–K3 (docs/AGENT.md «K»): I3t2 + agent. STAGE=t2-ийн дараа (qwen3.5-9b.t2 кэш хэрэгтэй). Шийдвэр dev дээр;
           # AGENT_TEST=<ID> өгвөл зөвхөн тэр ID-г test дээр нэг удаа. AGENT_IDS (анхдагч "K1 K2").
    MODEL=arctic-r1-7b
    VARS="--var best_a=${BEST_A:-A7}"
    [ -f build/translations/qwen3.5-9b.t2.jsonl ] || { echo "T2 кэш алга — эхлээд STAGE=t2"; exit 2; }
    curl -sf localhost:8000/v1/models >/dev/null || serve_d2_merged
    RUNS=""; for ID in ${AGENT_IDS:-K1 K2}; do RUNS="$RUNS dev:$ID"; done
    [ -n "${AGENT_TEST:-}" ] && RUNS="$RUNS test:$AGENT_TEST"
    for R in $RUNS; do
      SPL="${R%%:*}"; ID="${R#*:}"
      log "=== $ID $SPL"
      # shellcheck disable=SC2086
      T2S_LLM_TIMEOUT_S=900 t2s eval --run "$ID" --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-4}" $VARS
      python scripts/notebook/compact_results.py "results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl"
      if [ -n "${RESULTS_DST:-}" ]; then
        mkdir -p "$RESULTS_DST/$SPL" && cp -r "results/runs/$SPL/$ID" "$RESULTS_DST/$SPL/"
      fi
    done
    ;;
  train_d3)   # D3 (docs/IMPROVE_80_PLAN.md «D3»): H4s-ийн богино prompt-оор өөрийн DB + BIRD 600 → QLoRA SFT (2×T4 DDP)
              # → fp16 нэгтгэх → (D3_EVAL=1) served=d3, D3_RUNS (анхдагч "D3s:dev", нэг хувилбар). Checkpoint, SFT мөр,
              # эцсийн adapter нь /kaggle/working-д (12 цагийн хязгаараар тасарсан ч үлдэнэ). Үргэлжлүүлэх: өмнөх version-ий
              # Output-ыг Add Input хийгээд ижил STAGE=train_d3 — дууссан adapter/checkpoint/SFT мөрийг input-ээс авна.
    need BEST_A A7
    MODEL=arctic-r1-7b
    export T2S_BEST_A="$BEST_A"           # сургалтын prompt (H4s) нь үнэлгээний vars-тай ижил
    T0=$(date +%s); mins() { echo "$(( ($(date +%s) - T0) / 60 )) мин"; }
    # D3_BUDGET_MIN: энэ stage-д өгөх нийт минут (Kaggle-ийн долоо хоногийн GPU квот − bootstrap − нөөц), e2test_offline
    # орно. Сургалт T_END-ээс 10 мин өмнө checkpoint хадгалаад зогсоно; үнэлгээ бүр T_END-ээс 5 мин өмнө timeout-оор
    # зогсоно (дараа үргэлжилнэ)
    T_END=""; if [ -n "${D3_BUDGET_MIN:-}" ]; then T_END=$(( T0 + D3_BUDGET_MIN * 60 )); fi
    left_s() { if [ -n "$T_END" ]; then echo $(( T_END - $(date +%s) )); else echo 999999; fi; }
    # input-д E2 test (t2s-v8-output) байвал E4/E4f/E3c — алдаа гарвал сургалтыг зогсоохгүй
    if [ "${E2TEST_OFFLINE:-1}" = 1 ]; then e2test_offline || log "e2test_offline алдаатай — алгасав"; fi
    if [ "${E2TEST_OFFLINE:-1}" = 1 ]; then reselect_i_runs || log "reselect_i_runs алдаатай — алгасав"; fi
    if [ "${E2TEST_OFFLINE:-1}" = 1 ]; then ensemble_offline || log "ensemble_offline алдаатай — алгасав"; fi
    SRC="${TRAIN_SRC:-/kaggle/input}"
    W="${D3_WORK:-/kaggle/working}"; [ -d "$W" ] || W="$T2S_ROOT/adapters"   # build/ биш (notebook 15 мин тутам архивладаг)
    TD="$W/d3_train"; AD="$TD/d3__$MODEL"; DATA="$TD/d3__$MODEL.jsonl"; OUTA="$W/adapters_out/d3__$MODEL"
    M="adapters/d3__${MODEL}__merged16"
    mkdir -p "$TD"
    log "### D3: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ';') → $TD"
    # 0) sft.py-г шалгасан хувилбаруудыг түгжинэ (TRL/transformers-ийн API өөрчлөлтөөс хамгаална; D3_PINS=0 бол алгасна)
    if [ "${D3_PINS:-1}" = 1 ]; then
      log "D3: requirements-d3.txt (vllm/transformers/trl/peft/accelerate/bitsandbytes хувилбар түгжих)"
      pip install -q -r requirements-d3.txt
      pip uninstall -y -q torchaudio || true      # bootstrap-тай ижил: vLLM-ийн torch-той CUDA-гаар зөрдөг
    fi
    log "хувилбар: $(python -c "import torch, transformers, trl, peft, accelerate, bitsandbytes as b; print('torch', torch.__version__, '| transformers', transformers.__version__, '| trl', trl.__version__, '| peft', peft.__version__, '| accelerate', accelerate.__version__, '| bitsandbytes', b.__version__)")"
    # 1) Өмнөх сессийн гаралт: дууссан adapter бол сургалтгүй; эс бөгөөс checkpoint ба ТЭР сессийн SFT мөр (ижил өгөгдөл)
    if [ ! -f "$AD/t2s_meta.json" ]; then
      PREV="$(find "$SRC" -path "*adapters_out/d3__$MODEL/t2s_meta.json" 2>/dev/null | head -1 || true)"
      if [ -n "$PREV" ]; then mkdir -p "$AD"; cp "$(dirname "$PREV")"/* "$AD"/; log "D3 adapter өмнөх сессээс: $(dirname "$PREV")"; fi
    fi
    if [ ! -f "$AD/t2s_meta.json" ]; then
      if [ -z "$(d3_ckpt "$AD")" ]; then
        CK="$(d3_ckpt "$SRC")"
        if [ -n "$CK" ]; then mkdir -p "$AD"; cp -r "$CK" "$AD/"; log "checkpoint сэргээв: $CK"; fi
      fi
      if [ ! -f "$DATA" ]; then
        PD="$(find "$SRC" -path "*d3_train/d3__$MODEL.jsonl" 2>/dev/null | head -1 || true)"
        if [ -n "$PD" ]; then
          cp "$PD" "$DATA"; cp "$(dirname "$PD")/build_report.json" "$TD/" 2>/dev/null || true
          log "SFT мөрийг өмнөх сессээс авав ($PD) — checkpoint-той ижил өгөгдөл"
        fi
      fi
      # 2) SFT мөр: индекс (bootstrap-ийн build/index + build/index_plus = 160 + 195 жишээ, dev-гүй; bge-m3 GPU, vLLM-ээс
      #    өмнө) → өгөгдөл задлах → H4s-ээр угсрах (өөрийн family-г few-shot-оос хасна; урт мөрийн хасалтыг тайлагнана)
      if [ ! -f "$DATA" ]; then
        [ -f build/index/.ok ] || { T2S_EMBED_DEVICE=auto t2s index-build > build/index.json && touch build/index/.ok; }
        if [ ! -f build/index_plus/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --extra-examples knowledge/examples_plus.jsonl --out build/index_plus \
            > build/index_plus.json && touch build/index_plus/.ok
        fi
        unpack_d3_data
        log "D3 SFT мөр угсарч байна (prompt=h4s, index=build/index_plus) …"
        T2S_EMBED_DEVICE=auto t2s_train build --recipe d3 --model "$MODEL" > "$TD/build_report.json"
        cat "$TD/build_report.json"
        cp "data/train/sft/d3__$MODEL.jsonl" "$DATA"
        log "SFT мөр бэлэн ($(mins)): $(wc -l < "$DATA") мөр → $DATA"
      fi
      # 3) QLoRA SFT: 2 процесс (GPU бүрт нэг) — torch.distributed.run; checkpoint T2S_SAVE_STEPS алхам тутам $AD-д
      NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"; NP=1; [ "${NG:-0}" -ge 2 ] && NP=2
      # Kaggle-ийн 2×T4 (PCIe) дээр NCCL P2P гацах тохиолдол бий — SHM-ээр; NCCL алдаа гарвал гацахгүй зогсооно
      export T2S_SAVE_STEPS="${T2S_SAVE_STEPS:-10}" TOKENIZERS_PARALLELISM=false \
        PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" \
        NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}" TORCH_NCCL_ASYNC_ERROR_HANDLING="${TORCH_NCCL_ASYNC_ERROR_HANDLING:-1}"
      # D3_TRAIN_MIN: сургалтад өгөх дээд минут; D3_BUDGET_MIN-тэй бол T_END − 10 мин (аль эрт нь). Хэтэрвэл checkpoint
      # хадгалаад зогсоно (exit 0 — Output хадгалагдана), дараагийн сесс тэр checkpoint-оос үргэлжилнэ
      DL=""
      if [ -n "${D3_TRAIN_MIN:-}" ]; then DL=$(( $(date +%s) + D3_TRAIN_MIN * 60 )); fi
      if [ -n "$T_END" ] && { [ -z "$DL" ] || [ $(( T_END - 600 )) -lt "$DL" ]; }; then DL=$(( T_END - 600 )); fi
      if [ -n "$DL" ]; then export T2S_TRAIN_DEADLINE="$DL"; else unset T2S_TRAIN_DEADLINE; fi
      log "D3 SFT эхэлж байна: $NP процесс, checkpoint $T2S_SAVE_STEPS алхам тутам → $AD (сүүлийнх: $(d3_ckpt "$AD"))${DL:+, хязгаар $(( (DL - $(date +%s)) / 60 )) мин}"
      if [ "$NP" -ge 2 ]; then
        python -m torch.distributed.run --standalone --nproc_per_node "$NP" -m mn_text2sql.training \
          sft --recipe d3 --model "$MODEL" --output-dir "$AD" --data-file "$DATA"
      else
        t2s_train sft --recipe d3 --model "$MODEL" --output-dir "$AD" --data-file "$DATA"
      fi
      if [ ! -f "$AD/t2s_meta.json" ]; then
        CKL="$(d3_ckpt "$AD")"
        if [ -n "${T2S_TRAIN_DEADLINE:-}" ] && [ -n "$CKL" ]; then
          log "D3 SFT хугацааны хязгаарт зогсов ($(mins)); checkpoint: $CKL — дараагийн сесст энэ version-ий Output-ыг input болгоод ижил STAGE=train_d3"
          exit 0
        fi
        log "D3 SFT дуусаагүй ($AD) — дараагийн сесст Output-ыг input болгоод дахин"; exit 3
      fi
      log "D3 SFT дууслаа ($(mins))"
    fi
    # 4) Эцсийн adapter-ийг (15 GB-ийн нэгтгэсэн жин биш) татаж авах/дахин ашиглахад; checkpoint-ийг устгаж зай чөлөөлнө
    mkdir -p "$OUTA"
    for f in adapter_config.json adapter_model.safetensors t2s_meta.json; do cp "$AD/$f" "$OUTA/"; done
    [ -f "$TD/build_report.json" ] && cp "$TD/build_report.json" "$OUTA/"
    rm -rf "$AD"/checkpoint-*
    log "D3 adapter → $OUTA ($(du -sh "$OUTA" | cut -f1))"
    # 5) fp32-оор нэгтгээд fp16 жин (T4 дээр vLLM-ийн LoRA удаан — serve_d2_merged-тэй ижил); зөвхөн D3_EVAL=1 үед
    #    (нэгтгэсэн жин /tmp-д — сесс дуусахад алга болдог тул үнэлгээгүй бол квот үрэхгүй)
    if [ "${D3_EVAL:-0}" = 1 ] && [ "$(left_s)" -lt 2400 ]; then
      log "D3 үнэлгээ алгасав: D3_BUDGET_MIN-ээс $(( $(left_s) / 60 )) мин үлдсэн (нэгтгэх + vLLM + асуултад < 40 мин)"
      D3_EVAL=0
    fi
    if [ "${D3_EVAL:-0}" = 1 ] && [ ! -f "$M/t2s_merge.json" ]; then
      HF_ID="$(python -c "import yaml; print(yaml.safe_load(open('config/models.yaml', encoding='utf-8'))['models']['$MODEL']['hf_id'])")"
      log "D3 LoRA-г нэгтгэж байна → $M"
      python scripts/notebook/merge_lora.py --base "$HF_ID" --adapter "$AD" --out "$M"
      [ -d /kaggle ] && rm -rf "${HF_HOME:-$HOME/.cache/huggingface}/hub/models--${HF_ID//\//--}"
      log "нэгтгэсэн ($(mins))"
    fi
    # 6) D3_EVAL=1: D3_RUNS = "<ID>[@<served>]:<split> ..." (анхдагч "D3s:dev"). served = d3 (нэгтгэсэн D3) эсвэл d2
    #    (serve_d2_merged — t2s_adapter_d2 part-ууд input-д хэрэгтэй); @-гүй бол run-ий adapter (H4s → d2, D3s → d3).
    #    Жишээ: D3_RUNS="H4s:dev D3s:dev" (H4s нь D2-оор = зөвхөн богино prompt-ын нөлөө). Хоёр нэгтгэсэн загвар + суурь
    #    жин ~45 GB диск шаардана. I3s3-д build/index_plus_dev (GPU, vLLM-ээс өмнө).
    if [ "${D3_EVAL:-0}" = 1 ]; then
      RUNS="${D3_RUNS:-D3s:dev}"
      # H4s/D3s-ийн жишээний сан = сургалтынх (build/index_plus); өмнөх сессийн SFT мөрөөр үргэлжилсэн бол бүтээгдээгүй
      if [ ! -f build/index_plus/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --extra-examples knowledge/examples_plus.jsonl --out build/index_plus \
          > build/index_plus.json && touch build/index_plus/.ok
      fi
      # M1s2/M1s3: MADLAD M1 орчуулгын кэш (V19-ийн Output-оос үүсгэсэн хувийн dataset-ийг Add Input) → build/translations
      for F in $(find "${TRAIN_SRC:-/kaggle/input}" -path "*mt/m1_madlad_*.jsonl" 2>/dev/null || true); do
        [ -f "build/translations/$(basename "$F")" ] || cp "$F" build/translations/
      done
      if [[ " $RUNS " == *" M1s"* ]] && [ ! -f build/translations/m1_madlad_improved.jsonl ]; then
        log "M1s*: m1_madlad_improved.jsonl кэш алга (MADLAD M1 Output-ыг Add Input хий) — алгасав"; RUNS="$(echo " $RUNS " | sed -E 's/ M1s[0-9]+(@[a-z0-9]+)?:[a-z]+//g')"
      fi
      if [[ " $RUNS " == *" I3s3"* || " $RUNS " == *" M1s"* ]] && [ ! -f build/index_plus_dev/.ok ]; then   # I3s3, I3s3t, M1s2, M1s3
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
          --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
      fi
      if [[ " $RUNS " == *" I4"* ]] && [ -f knowledge/examples_plus_all.jsonl ] && [ ! -f build/index_plus2_dev/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus_all.jsonl           --out build/index_plus2_dev > build/index_plus2_dev.json && touch build/index_plus2_dev/.ok
      fi
      CUR=""
      for R in $RUNS; do
        SPL="${R##*:}"; IDS="${R%:*}"; ID="${IDS%%@*}"
        if [[ "$IDS" == *@* ]]; then SRV="${IDS#*@}"
        else SRV="$(python -c "from mn_text2sql.pipeline.options import resolve_run; print(resolve_run('$ID').adapter or 'd3')")"; fi
        if [ "$SRV" != "$CUR" ]; then
          log "served=$SRV-д шилжиж байна (диск: $(df -h . | tail -1 | awk '{print $4}') чөлөөтэй)"
          case "$SRV" in
            d3) MODEL_PATH="$M" SERVED=d3 bash "$S/serve_model.sh" "$MODEL" ;;
            d2) serve_d2_merged ;;
            *) log "D3_RUNS: «$R» — served нь d3 эсвэл d2 байна"; exit 2 ;;
          esac
          CUR="$SRV"
        fi
        REM=$(( $(left_s) - 300 ))
        if [ "$REM" -lt 600 ]; then log "=== $ID $SPL алгасав: хугацаа ($(( REM / 60 )) мин) — дараагийн сесст"; break; fi
        log "=== $ID $SPL (served $SRV)${T_END:+, хязгаар $(( REM / 60 )) мин}"
        # shellcheck disable=SC2086
        timeout "$REM" python -m mn_text2sql eval --run "$ID" --model "$MODEL" --split "$SPL" --variants mn --workers "${WORKERS:-8}" $VARS \
          || log "$ID $SPL: зогссон (timeout/алдаа $?) — дууссан асуулт хадгалагдсан, дараа үргэлжилнэ"
        python scripts/notebook/compact_results.py "results/runs/$SPL/$ID/${MODEL}__mn__s0.jsonl" || true
        # E4-ийн дүрэм (хувилбарын шүүлт, '%q', дүрмийн татгалзал/тодруулга) — загваргүй, хэдхэн минут
        t2s reselect --from "$ID" --to "${ID}r" --split "$SPL"           && python scripts/notebook/compact_results.py "results/runs/$SPL/${ID}r/${MODEL}__mn__s0.jsonl"           || log "$ID $SPL: reselect алгасав"
      done
    fi
    log "D3 бүгд ($(mins))"
    ;;
  mt)   # Орчуулгын загвар M0/M1 (docs/MT_INVARIANT_PLAN.md §5): Kaggle T4. MT_STEPS (анхдагч "m0 madlad eval t2s"):
        #   m0     — MADLAD-400-3B zero-shot dev (латин шууд «none» ба кирилл болгож «improved»)
        #   madlad — MADLAD QLoRA (M1; scripts/mt/train_mt.py, $MT_DATA/sft/sft_madlad.jsonl), val chrF++
        #   qwen   — Qwen3.5-9B QLoRA (M1; удаан — тусдаа сесст)
        #   eval   — fine-tune хийсэн загвар(ууд)-аар dev орчуулга → baseline_dev (chrF++, нэр томьёо, утга, тоо)
        #   t2s    — D2-оор (I3t-тэй ижил зам) M0a/M1a/M1q dev EX (MT-7) — кэш байгаа run-ийг л
        # Гаралт $W (/kaggle/working/mt): adapter, val_report.json, score_*.txt, кэш; RESULTS_DST руу хуулна. Тасарвал дахин
        # ажиллуулахад дууссан алхмыг алгасна (adapter-ийн t2s_meta.json, кэш файл).
    MODEL=arctic-r1-7b
    VARS="--var best_a=${BEST_A:-A7}"
    W="${MT_WORK:-/kaggle/working}/mt"; [ -d /kaggle/working ] || W="$T2S_ROOT/adapters/mt"
    MTD="${MT_DATA:-data/mt/v2}"
    STEPS=" ${MT_STEPS:-m0 madlad eval t2s} "
    mkdir -p "$W" build/translations
    python -c "import bitsandbytes, sentencepiece" 2>/dev/null || pip install -q bitsandbytes sentencepiece
    # Kaggle-ийн torchao 0.10 хуучин: peft LoRA үүсгэх/ачаалахад ImportError (Version 13, 20) — MT-д хэрэггүй тул үргэлж устгана
    pip uninstall -y -q torchao 2>/dev/null || true
    T0=$(date +%s); mins() { echo "$(( ($(date +%s) - T0) / 60 )) мин"; }
    mt_score() {   # $1 = кэшийн tag: baseline_dev-ийн оноо → $W/score_<tag>.txt; кэшийг $W-д хадгална
      python scripts/mt/baseline_dev.py --cache "build/translations/$1.jsonl" > "$W/score_$1.txt" 2>&1 || true
      tail -20 "$W/score_$1.txt"; cp "build/translations/$1.jsonl" "$W/"
    }
    mt_cached() { [ -f "build/translations/$1.jsonl" ] || { [ -f "$W/$1.jsonl" ] && cp "$W/$1.jsonl" build/translations/; }; [ -f "build/translations/$1.jsonl" ]; }
    # өмнөх сессийн гаралт (Output-ыг Add Input): дууссан adapter эсвэл сүүлийн бүрэн checkpoint, кэш, оноо
    SRC="${TRAIN_SRC:-/kaggle/input}"
    for A in madlad_m1 q9b_m1; do
      [ -f "$W/$A/t2s_meta.json" ] && continue
      P="$(find "$SRC" -path "*mt/$A/t2s_meta.json" 2>/dev/null | head -1 || true)"
      if [ -n "$P" ]; then mkdir -p "$W/$A"; cp -r "$(dirname "$P")"/. "$W/$A/"; log "MT $A adapter өмнөх сессээс"; continue; fi
      if [ -z "$(d3_ckpt "$W/$A")" ]; then
        CK="$(find "$SRC" -path "*mt/$A/checkpoint-*/trainer_state.json" 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$CK" ] && { mkdir -p "$W/$A"; cp -r "$(dirname "$CK")" "$W/$A/"; cp "$(dirname "$(dirname "$CK")")"/records.jsonl "$W/$A/" 2>/dev/null || true; log "MT $A checkpoint сэргээв"; }
      fi
    done
    for F in $(find "$SRC" -path "*mt/m[01]_*.jsonl" 2>/dev/null || true); do [ -f "$W/$(basename "$F")" ] || cp "$F" "$W/"; done
    # MT_TRAIN_MIN: нэг сургалтад өгөх дээд минут — хэтэрвэл checkpoint хадгалаад зогсоно, дараагийн сесст үргэлжилнэ
    mt_deadline() { if [ -n "${MT_TRAIN_MIN:-}" ]; then export T2S_TRAIN_DEADLINE=$(( $(date +%s) + MT_TRAIN_MIN * 60 )); else unset T2S_TRAIN_DEADLINE; fi; }
    log "### MT: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ';') өгөгдөл $MTD ($(python -c "import sys; print(''.join(open(sys.argv[1], encoding='utf-8').read().split())[:200])" "$MTD/sft/STATS.json" 2>/dev/null))"   # cut -c нь байтаар таслаж кирилл үсгийг эвддэг (Version 11 UnicodeDecodeError)
    if [[ "$STEPS" == *" m0 "* ]]; then
      for TR in none improved; do
        TAG="m0_madlad_$TR"
        mt_cached "$TAG" && { log "$TAG кэш бий — алгасав"; continue; }
        log "=== M0 MADLAD zero-shot (латин: $TR)"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --tag "$TAG" --translit "$TR" --batch 8 \
          && mt_score "$TAG" || log "$TAG: алдаа — алгасав (дараагийн алхам үргэлжилнэ)"
      done
    fi
    if [[ "$STEPS" == *" madlad "* ]]; then
      log "=== M1 MADLAD QLoRA ($(mins))"
      # Kaggle-ийн torchao 0.10 хуучин: peft LoRA үүсгэхэд ImportError (Version 13) — сургалтад хэрэггүй тул устгана
      pip uninstall -y -q torchao 2>/dev/null || true
      mt_deadline
      python scripts/mt/train_mt.py --backend seq2seq --batch 4 --grad-acc 8 --epochs 2 --model google/madlad400-3b-mt --data "$MTD/sft/sft_madlad.jsonl" \
        --pairs "$MTD/pairs.jsonl" --out "$W/madlad_m1" || log "MADLAD M1: алдаа (checkpoint байвал дараагийн сесст үргэлжилнэ)"
      log "MADLAD M1 дууслаа ($(mins)): $(cat "$W/madlad_m1/val_report.json" 2>/dev/null | tr -d '\n')"
    fi
    if [[ "$STEPS" == *" qwen "* ]]; then
      log "=== M1 Qwen3.5-9B QLoRA ($(mins))"
      # run_sft-ийн QLoRA-г эдгээр хувилбараар шалгасан (train_d3-тай ижил)
      [ "${D3_PINS:-1}" = 1 ] && { pip install -q -r requirements-d3.txt; pip uninstall -y -q torchaudio || true; }
      mt_deadline
      PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" \
        python scripts/mt/train_mt.py --backend causal --model qwen3.5-9b --recipe mt_q9b --data "$MTD/sft/sft_qwen.jsonl" \
        --pairs "$MTD/pairs.jsonl" --out "$W/q9b_m1"
      log "Qwen M1 ($(mins)): $(cat "$W/q9b_m1/val_report.json" 2>/dev/null | tr -d '\n')"
    fi
    if [[ "$STEPS" == *" eval "* ]]; then
      [ -f "$W/madlad_m1/t2s_meta.json" ] || [ -f "$W/q9b_m1/t2s_meta.json" ] || log "MT eval: дууссан adapter алга — алгасав"
      for TR in none improved; do
        if [ -f "$W/madlad_m1/t2s_meta.json" ] && ! mt_cached "m1_madlad_$TR"; then
          log "=== M1 MADLAD dev орчуулга (латин: $TR)"
          python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
            --hints lexicon --translit "$TR" --tag "m1_madlad_$TR" --batch 8 \
            && mt_score "m1_madlad_$TR" || log "m1_madlad_$TR: алдаа — алгасав"
        fi
        if [ -f "$W/q9b_m1/t2s_meta.json" ] && ! mt_cached "m1_q9b_$TR"; then
          log "=== M1 Qwen dev орчуулга (латин: $TR)"
          python scripts/mt/m0_translate.py --backend hf_causal --model Qwen/Qwen3.5-9B --load-4bit --cudnn-off \
            --adapter "$W/q9b_m1" --prompt sft --hints lexicon --translit "$TR" --tag "m1_q9b_$TR" --batch 8
          mt_score "m1_q9b_$TR"
        fi
      done
    fi
    if [[ "$STEPS" == *" mark "* ]]; then   # M1 + DB-ийн утгыг «» тэмдэглэх (сургалтын формат; M1-ийн утга хадгалалт 13.6%-ийг засах)
      if [ -f "$W/madlad_m1/t2s_meta.json" ] && ! mt_cached m1_madlad_marked; then
        log "=== M1 MADLAD dev орчуулга + утгын тэмдэглэгээ ($(mins))"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1"           --hints lexicon --translit improved --mark-values db --tag m1_madlad_marked --batch 8           && mt_score m1_madlad_marked || log "m1_madlad_marked: алдаа — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" t2s "* ]]; then
      RUNS=""
      # MT_T2S: ажиллуулах run-ууд (анхдагч "M0a M1a M1q"); кэш байгааг л
      for P in M0a:m0_madlad_improved M1a:m1_madlad_improved M1q:m1_q9b_improved M1m:m1_madlad_marked; do
        [[ " ${MT_T2S:-M0a M1a M1q} " == *" ${P%%:*} "* ]] || continue
        mt_cached "${P#*:}" && RUNS="$RUNS ${P%%:*}"
      done
      if [ -n "$RUNS" ]; then
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
            --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        serve_d2_merged
        for ID in $RUNS; do
          log "=== $ID dev ($(mins))"
          # shellcheck disable=SC2086
          T2S_LLM_TIMEOUT_S=900 t2s eval --run "$ID" --model "$MODEL" --split dev --variants mn --workers "${WORKERS:-4}" $VARS \
            || log "$ID: алдаа/зогссон — дууссан асуулт хадгалагдсан"
          python scripts/notebook/compact_results.py "results/runs/dev/$ID/${MODEL}__mn__s0.jsonl" || true
          mkdir -p "$W/results"; cp -r "results/runs/dev/$ID" "$W/results/" 2>/dev/null || true
        done
      else
        log "MT t2s: кэш алга — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" reselect "* ]]; then   # M1r = M1a (+ M1m → M1mr) + E4-ийн дүрэм, загваргүй (өмнөх сессийн run-ийг input-ээс)
      for P in M1a:M1r M1m:M1mr; do
        F="results/runs/dev/${P%%:*}/${MODEL}__mn__s0.jsonl"
        if [ ! -f "$F" ]; then
          SRCR="$(find "$SRC" "$W" -path "*results/${P%%:*}/${MODEL}__mn__s0.jsonl" 2>/dev/null | head -1 || true)"
          [ -n "$SRCR" ] && { mkdir -p "$(dirname "$F")"; cp "$SRCR" "$F"; }
        fi
        [ -f "$F" ] || { log "${P%%:*}: run алга — ${P#*:} алгасав"; continue; }
        log "=== ${P#*:} = ${P%%:*} + E4-ийн дүрэм (reselect)"
        t2s reselect --from "${P%%:*}" --to "${P#*:}" --split dev && python scripts/notebook/compact_results.py "results/runs/dev/${P#*:}/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results"; cp -r "results/runs/dev/${P#*:}" "$W/results/" 2>/dev/null || true
      done
    fi
    if [[ "$STEPS" == *" vote "* ]]; then   # MT_VOTE (анхдагч M1e2): 8 нэр дэвшигч + засвар dev → reselect (E4-ийн дүрэм) = M1e4 / M1me4
      V="${MT_VOTE:-M1e2}"; VR="${V%2}4"
      case "$V" in M1e2) C=m1_madlad_improved ;; M1me2) C=m1_madlad_marked ;; *) log "MT_VOTE: $V танигдаагүй"; C="" ;; esac
      if [ -n "$C" ] && mt_cached "$C"; then
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl             --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        serve_d2_merged
        log "=== $V dev ($(mins))"
        # shellcheck disable=SC2086
        T2S_LLM_TIMEOUT_S=900 t2s eval --run "$V" --model "$MODEL" --split dev --variants mn --workers "${WORKERS:-4}" $VARS           || log "$V: алдаа/зогссон — дууссан асуулт хадгалагдсан"
        for R in "$V" "$VR"; do
          [ "$R" = "$VR" ] && { t2s reselect --from "$V" --to "$VR" --split dev || continue; }
          for J in results/runs/dev/$R/*.jsonl; do python scripts/notebook/compact_results.py "$J" || true; done
          mkdir -p "$W/results"; cp -r "results/runs/dev/$R" "$W/results/" 2>/dev/null || true
        done
      else
        log "MT vote: $V-ийн кэш ($C) алга — алгасав"
      fi
    fi
    if [[ "$STEPS" == *" test "* ]]; then   # MT-9: M1a-г test дээр НЭГ удаа (docs/MT_INVARIANT_PLAN.md §5.6) — dev-ийн дараа л
      TAG=m1_madlad_improved
      if ! mt_cached "${TAG}_test"; then
        [ -f "$W/madlad_m1/t2s_meta.json" ] || { log "MT test: madlad_m1 adapter алга — алгасав"; STEPS="${STEPS/ test / }"; }
      fi
      if [[ "$STEPS" == *" test "* ]] && ! mt_cached "${TAG}_test"; then
        log "=== M1 MADLAD test орчуулга (final, нэг удаа)"
        python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
          --hints lexicon --translit improved --split test --final-test --variants mn --tag "${TAG}_test" --batch 8 \
          && cp "build/translations/${TAG}_test.jsonl" "$W/" || log "${TAG}_test: алдаа"
      fi
      if mt_cached "${TAG}_test"; then
        # M1a-ийн кэш (translation_cache: m1_madlad_improved) = dev (байвал) + test; id-ууд split хооронд давхцахгүй
        mt_cached "$TAG" || : > "build/translations/$TAG.jsonl"
        python - "build/translations/$TAG.jsonl" "build/translations/${TAG}_test.jsonl" <<'PY'
import json, sys
main, extra = sys.argv[1], sys.argv[2]
have = {(json.loads(x)["id"], json.loads(x)["variant"]) for x in open(main, encoding="utf-8") if x.strip()}
new = [x for x in open(extra, encoding="utf-8") if x.strip() and (json.loads(x)["id"], json.loads(x)["variant"]) not in have]
open(main, "a", encoding="utf-8", newline="\n").writelines(x if x.endswith("\n") else x + "\n" for x in new)
print(f"кэш {main}: +{len(new)} test мөр (өмнө {len(have)})")
PY
        if [ ! -f build/index_plus_dev/.ok ]; then
          T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl \
            --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
        fi
        serve_d2_merged
        log "=== M1a test ($(mins))"
        # shellcheck disable=SC2086
        T2S_LLM_TIMEOUT_S=900 t2s eval --run M1a --model "$MODEL" --split test --variants mn --workers "${WORKERS:-4}" $VARS \
          || log "M1a test: алдаа/зогссон — дууссан асуулт хадгалагдсан"
        python scripts/notebook/compact_results.py "results/runs/test/M1a/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results/test"; cp -r results/runs/test/M1a "$W/results/test/" 2>/dev/null || true
      fi
    fi
    if [ -n "${RESULTS_DST:-}" ]; then
      mkdir -p "$RESULTS_DST/mt"
      cp "$W"/*.txt "$W"/*.jsonl "$RESULTS_DST/mt/" 2>/dev/null || true
      for A in madlad_m1 q9b_m1; do [ -d "$W/$A" ] && mkdir -p "$RESULTS_DST/mt/$A" && cp "$W/$A"/*.json "$RESULTS_DST/mt/$A/" 2>/dev/null || true; done
      [ -d "$W/results" ] && cp -r "$W/results" "$RESULTS_DST/mt/"
      log "MT үр дүн → $RESULTS_DST/mt"
    fi
    log "MT бүгд ($(mins))"
    ;;
  t2s_inv)   # «Тогтмол мэдлэг»-ийн T2S (docs/MT_INVARIANT_PLAN.md §6): Kaggle 2×T4. INV_STEPS (анхдагч "verify sample train"):
             #   verify — S2 хувилбарыг PG-ийн view дээр эх gold-той харьцуулна (verify_variants.py) → *.verified.jsonl
             #   sample — prompts (зөвхөн баталгаажсан S2 + S1 + S3 + S4) → суурь Arctic (vLLM) n=4 rejection sampling → SFT мөр
             #   train  — t2s_inv1 QLoRA (2×T4 DDP, D3-тай ижил механизм; INV_TRAIN_MIN-д checkpoint хадгалаад зогсоно)
             #   eval   — (нэмж өгвөл) нэгтгэж served=inv1 → INV_RUNS (анхдагч "T1 T1n") dev
             #   shift  — (нэмж өгвөл) T-2: evalset_shift/dev дээр SHIFT_MODELS (base d2 inv1) — схем солигдоход EX-ийн уналт
             # Гаралт $W (/kaggle/working/t2s_inv): prompts/samples/records, adapter (adapters_out/t2s_inv1__arctic-r1-7b).
    MODEL=arctic-r1-7b
    W="${INV_WORK:-/kaggle/working}/t2s_inv"; [ -d /kaggle/working ] || W="$T2S_ROOT/adapters/t2s_inv"
    STEPS=" ${INV_STEPS:-verify sample train} "
    SRC="${TRAIN_SRC:-/kaggle/input}"
    mkdir -p "$W"
    T0=$(date +%s); mins() { echo "$(( ($(date +%s) - T0) / 60 )) мин"; }
    [ -f build/index/.ok ] || { T2S_EMBED_DEVICE=auto t2s index-build > build/index.json && touch build/index/.ok; }
    # өмнөх сессийн гаралт (samples.jsonl — үргэлжилнэ; records; checkpoint)
    for F in samples.jsonl records.jsonl prompts.jsonl; do
      if [ ! -f "$W/$F" ]; then P="$(find "$SRC" -path "*t2s_inv/$F" 2>/dev/null | head -1 || true)"; [ -n "$P" ] && cp "$P" "$W/"; fi
    done
    if [[ "$STEPS" == *" verify "* ]] && [ ! -f data/t2s_inv/v0/schema_variants.verified.jsonl ]; then
      log "=== S2 гүйцэтгэлийн баталгаажуулалт (PG)"
      python scripts/t2s_inv/verify_variants.py --dir data/t2s_inv/v0
      cp data/t2s_inv/v0/verify_report.json "$W/"
    fi
    if [[ "$STEPS" == *" sample "* ]] && [ ! -f "$W/records.jsonl" ]; then
      [ -f "$W/prompts.jsonl" ] || python scripts/t2s_inv/build_sft.py prompts --out "$W" --verified
      bash "$S/serve_model.sh" "$MODEL"
      log "=== rejection sampling ($(wc -l < "$W/prompts.jsonl") prompt, $(mins))"
      timeout "${INV_SAMPLE_MIN:-360}m" python scripts/t2s_inv/build_sft.py sample --dir "$W" --served "$MODEL" --workers "${WORKERS:-16}" \
        || log "sample зогссон (timeout/алдаа) — дараагийн сесст үргэлжилнэ"
      pkill -f "vllm serve" 2>/dev/null || true; sleep 15
      if [ "$(wc -l < "$W/samples.jsonl")" -ge "$(wc -l < "$W/prompts.jsonl")" ]; then
        python scripts/t2s_inv/build_sft.py records --dir "$W" --model "$MODEL"
      else
        log "sample дуусаагүй ($(wc -l < "$W/samples.jsonl")/$(wc -l < "$W/prompts.jsonl")) — Output-ыг input болгоод ижил STAGE"; exit 0
      fi
    fi
    if [[ "$STEPS" == *" train "* ]] && [ -f "$W/records.jsonl" ]; then
      AD="$W/t2s_inv1__$MODEL"; OUTA="$W/adapters_out/t2s_inv1__$MODEL"
      if [ ! -f "$AD/t2s_meta.json" ]; then     # дууссан adapter өмнөх сессээс (дахин сургахгүй)
        PREV="$(find "$SRC" -path "*adapters_out/t2s_inv1__$MODEL/t2s_meta.json" 2>/dev/null | head -1 || true)"
        [ -n "$PREV" ] && { mkdir -p "$AD"; cp "$(dirname "$PREV")"/* "$AD"/; log "t2s_inv1 adapter өмнөх сессээс"; }
      fi
      if [ ! -f "$AD/t2s_meta.json" ] && [ -z "$(d3_ckpt "$AD")" ]; then
        CK="$(find "$SRC" -path "*t2s_inv1__$MODEL/checkpoint-*/trainer_state.json" 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$CK" ] && { mkdir -p "$AD"; cp -r "$(dirname "$CK")" "$AD/"; log "checkpoint сэргээв: $(dirname "$CK")"; }
      fi
      if [ ! -f "$AD/t2s_meta.json" ]; then
        [ "${D3_PINS:-1}" = 1 ] && { pip install -q -r requirements-d3.txt; pip uninstall -y -q torchaudio || true; }
        NG="$(nvidia-smi -L 2>/dev/null | wc -l || true)"; NP=1; [ "${NG:-0}" -ge 2 ] && NP=2
        export T2S_SAVE_STEPS="${T2S_SAVE_STEPS:-20}" TOKENIZERS_PARALLELISM=false \
          PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}" NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}"
        if [ -n "${INV_TRAIN_MIN:-}" ]; then export T2S_TRAIN_DEADLINE=$(( $(date +%s) + INV_TRAIN_MIN * 60 )); fi
        log "=== t2s_inv1 QLoRA: $(wc -l < "$W/records.jsonl") мөр, $NP процесс ($(mins))"
        if [ "$NP" -ge 2 ]; then
          python -m torch.distributed.run --standalone --nproc_per_node "$NP" -m mn_text2sql.training \
            sft --recipe t2s_inv1 --model "$MODEL" --output-dir "$AD" --data-file "$W/records.jsonl"
        else
          t2s_train sft --recipe t2s_inv1 --model "$MODEL" --output-dir "$AD" --data-file "$W/records.jsonl"
        fi
      fi
      if [ -f "$AD/t2s_meta.json" ]; then
        mkdir -p "$OUTA"
        for f in adapter_config.json adapter_model.safetensors t2s_meta.json; do cp "$AD/$f" "$OUTA/"; done
        rm -rf "$AD"/checkpoint-*
        log "t2s_inv1 adapter → $OUTA ($(mins))"
      else
        log "t2s_inv1 дуусаагүй — checkpoint: $(d3_ckpt "$AD"); Output-ыг input болгоод ижил STAGE"
      fi
    fi
    # eval (INV_STEPS-д «eval»): adapter-ийг fp16 нэгтгэж served=inv1 → T1, T1n dev (I3t-ийн кэш, D2-тэй харьцуулна)
    if [[ "$STEPS" == *" eval "* ]] && [ -f "$W/adapters_out/t2s_inv1__$MODEL/t2s_meta.json" ]; then
      M="adapters/t2s_inv1__${MODEL}__merged16"
      if [ ! -f "$M/t2s_merge.json" ]; then
        HF_ID="$(python -c "import yaml; print(yaml.safe_load(open('config/models.yaml', encoding='utf-8'))['models']['$MODEL']['hf_id'])")"
        python scripts/notebook/merge_lora.py --base "$HF_ID" --adapter "$W/adapters_out/t2s_inv1__$MODEL" --out "$M"
      fi
      if [ ! -f build/index_plus_dev/.ok ]; then
        T2S_EMBED_DEVICE=auto t2s index-build --dev-examples --extra-examples knowledge/examples_plus.jsonl           --out build/index_plus_dev > build/index_plus_dev.json && touch build/index_plus_dev/.ok
      fi
      MODEL_PATH="$M" SERVED=inv1 bash "$S/serve_model.sh" "$MODEL"
      for ID in ${INV_RUNS:-T1 T1n}; do
        log "=== $ID dev ($(mins))"
        # shellcheck disable=SC2086
        t2s eval --run "$ID" --model "$MODEL" --split dev --variants mn --workers "${WORKERS:-8}" --var best_a="${BEST_A:-A7}"           || log "$ID: зогссон — дууссан асуулт хадгалагдсан"
        python scripts/notebook/compact_results.py "results/runs/dev/$ID/${MODEL}__mn__s0.jsonl" || true
        mkdir -p "$W/results"; cp -r "results/runs/dev/$ID" "$W/results/" 2>/dev/null || true
      done
    fi
    # shift (INV_STEPS-д «shift»): T-2 — evalset_shift/dev (сургалтад ороогүй 4 нэршил) дээр SHIFT_MODELS (анхдагч "base d2 inv1")-ийг
    # ижил compact prompt-оор; загвар бүрийн «эх → шилжүүлсэн» уналт (scripts/t2s_inv/eval_shift.py). inv1 нь eval алхмын нэгтгэсэн жин.
    if [[ "$STEPS" == *" shift "* ]]; then
      for SM in ${SHIFT_MODELS:-base d2 inv1}; do
        [ -f "results/shift/dev/$SM.summary.json" ] && [ "$(python -c "import json; print(len(json.load(open('results/shift/dev/$SM.summary.json'))['maps']))")" -ge 5 ] && continue
        case "$SM" in
          base) bash "$S/serve_model.sh" "$MODEL"; SRV="$MODEL" ;;
          d2) serve_d2_merged; SRV=d2 ;;
          inv1) [ -f "adapters/t2s_inv1__${MODEL}__merged16/t2s_merge.json" ] || { log "shift inv1: нэгтгэсэн жин алга (eval алхам) — алгасав"; continue; }
                MODEL_PATH="adapters/t2s_inv1__${MODEL}__merged16" SERVED=inv1 bash "$S/serve_model.sh" "$MODEL"; SRV=inv1 ;;
          *) log "SHIFT_MODELS: $SM үл мэдэгдэх"; continue ;;
        esac
        log "=== T-2 shift: $SM ($(mins))"
        python scripts/t2s_inv/eval_shift.py --served "$SRV" --tag "$SM" --workers "${WORKERS:-8}" || log "shift $SM: зогссон — үргэлжилнэ"
        python scripts/t2s_inv/eval_shift.py --set defs --served "$SRV" --tag "$SM" --workers "${WORKERS:-8}" || log "defs $SM: зогссон"
        mkdir -p "$W/shift"; cp results/shift/dev/"$SM".* "$W/shift/" 2>/dev/null || true
      done
    fi
    [ -n "${RESULTS_DST:-}" ] && { mkdir -p "$RESULTS_DST/t2s_inv"; cp "$W"/*.json "$RESULTS_DST/t2s_inv/" 2>/dev/null || true; }
    log "t2s_inv бүгд ($(mins))"
    ;;
  F)
    need BEST_A A6
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin run "F2,F3" "$MODEL"
      run "F5,F7,F8" "$MODEL"
      if [ -f data/bird/bird_mn.jsonl ]; then
        VARIANTS=mn,latin,en run "F1,F1b" "$MODEL"
      else
        log "F1 алгасав: эхлээд t2s bird-prepare && (qwen3.5-9b асаалттай) t2s bird-translate (+ хүний шалгалт)"
      fi
    done
    ;;
  E)
    need BEST_A A6; need BEST_CONFIG D1
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin,en run E1 "$MODEL"
      run E2 "$MODEL"
    done
    t2s report >/dev/null
    log "E3: results/REPORT.md-ийн «Санал хураалт» муруйгаас босгыг сонгоод: t2s derive-abstain --from E2 --to E3 --threshold <t>"
    ;;
  G)
    need BEST_A A6; need BEST_CONFIG D1
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      VARIANTS=mn,latin,en run G1 "$MODEL"
      run "G2,G3,G4,G5,G6" "$MODEL"
    done
    ;;
  thesis)
    need BEST_A A6; need BEST_CONFIG D1
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      ORIG=0; [ "$MODEL" = arctic-r1-7b ] && ORIG="${ORIGINAL:-1}"   # thesis-ийн систем зөвхөн Arctic-д
      # Adapter-тай BEST_CONFIG (C*/D*) нь run_thesis_bench.sh-д алгасагдана (LoRA асаагаагүй, ББСБ-д сургасан)
      ORIGINAL=$ORIG IDS="${THESIS_IDS:-A6,B5,$BEST_CONFIG,G1,G2,G3}" EXTRA="$VARS" \
        bash "$S/run_thesis_bench.sh" "$MODEL"
    done
    ;;
  test)
    need BEST_A A6; need TEST_IDS "A6,B5,E2"
    for MODEL in $MODELS; do
      bash "$S/serve_model.sh" "$MODEL"
      SPLIT=test VARIANTS=mn,latin,en run "$TEST_IDS" "$MODEL"
    done
    ;;
  *) echo "үл мэдэгдэх STAGE=$STAGE"; exit 2 ;;
esac
bash "$S/serve_model.sh" stop
log "STAGE=$STAGE дууслаа"
