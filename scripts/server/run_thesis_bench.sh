#!/usr/bin/env bash
# =====================================================================
# Thesis benchmark — text2sql-thesis-ийн кейс (цахим худалдааны DB) дээр шинэ систем ба thesis-ийн өөрийн agent
# системийг ИЖИЛ vLLM, ИЖИЛ өгөгдөл, ИЖИЛ шалгуураар харьцуулна (docs/THESIS_BENCH.md).
#
#   bash scripts/server/serve_model.sh arctic-r1-7b                 # (өөр терминалд) vLLM
#   bash scripts/server/run_thesis_bench.sh arctic-r1-7b            # шинэ систем: IDS (анхдагч A6,B5,G1)
#   ORIGINAL=1 bash scripts/server/run_thesis_bench.sh arctic-r1-7b # + thesis-ийн системийг хуулбараас ажиллуулж оноолох
#   IDS="A6,B5,E1,G1,G2,G3" SPLITS="dev test" VARIANTS="mn,en" ...   # тохиргоо
#   MATCHED_FEWSHOT=1 ...   # test дээр нэмэлтээр few-shot сан = train + dev (thesis-ийн сантай тэнцүү; tag fs_traindev)
# Adapter-тай ID (C*, D*, F4, F6, F10, adapter-тай BEST_CONFIG дээрх G1 г.м.) энд алгасагдана: суурь загвар асаалттай, ББСБ-д сургасан adapter өөр DB-д хамаарахгүй.
#
# text2sql-thesis-д юу ч бичихгүй: DB, кейс, мэдлэгийг уншиж авна; thesis-ийн системийг build/thesis_snapshot
# хуулбар дээр (өөрийн venv-тэй) ажиллуулна. Thesis-ийн систем зөвхөн Arctic-д зориулагдсан (prompt, <think> парсер).
# Гаралт: results/thesis/runs/<split>/..., results/thesis/COMPARE.md
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"
MODEL="${1:-arctic-r1-7b}"
CFG=config/thesis.yaml
SRC="${THESIS_SRC:-$T2S_ROOT/../text2sql-thesis}"
IDS="${IDS:-A6,B5,G1}"
SPLITS="${SPLITS:-dev test}"
VARIANTS="${VARIANTS:-mn,en}"
WORKERS="${WORKERS:-8}"
mkdir -p build/logs
# shellcheck disable=SC2086
needs_adapter() { [ -n "$(python scripts/server/thesis_stale.py --adapter "$1" ${EXTRA:-})" ]; }

[ -f "$SRC/build/data.db" ] || { log "text2sql-thesis олдсонгүй: $SRC (THESIS_SRC=...)"; exit 2; }
# Импорт хуучирсан эсэх: эх data.db/кейсийн sha1 ≠ MANIFEST, PostgreSQL DB эсвэл индекс алга бол дахин импортлоно
STALE=$(python scripts/server/thesis_stale.py "$SRC")
if [ -n "$STALE" ] || [ "${REIMPORT:-0}" = "1" ]; then
  log "thesis-import ($SRC → PostgreSQL thesis_ecom, data/thesis): ${STALE:-REIMPORT=1}"
  t2s --config "$CFG" thesis-import --src "$SRC" --overwrite > build/thesis_import.json
  rm -rf build/index_thesis_traindev
  T2S_EMBED_DEVICE="${T2S_EMBED_DEVICE:-cpu}" t2s --config "$CFG" index-build > build/thesis_index.json
  t2s --config "$CFG" check-gold > build/thesis_check_gold.json || { log "check-gold АЛДАА — build/thesis_check_gold.json"; exit 2; }
fi

for SPLIT in $SPLITS; do
  for ID in ${IDS//,/ }; do
    if needs_adapter "$ID"; then log "алгасав: $ID (adapter шаардлагатай)"; continue; fi
    log "=== thesis-bench $ID / $MODEL / $SPLIT ($VARIANTS)"
    # shellcheck disable=SC2086
    t2s --config "$CFG" eval --run "$ID" --model "$MODEL" --split "$SPLIT" --variants "$VARIANTS" --workers "$WORKERS" \
      ${EXTRA:-} 2>&1 | tee -a "build/logs/thesis_${ID}_${MODEL}_${SPLIT}.log"
  done
done

if [ "${MATCHED_FEWSHOT:-0}" = "1" ] && [[ " $SPLITS " == *" test "* ]]; then
  # Thesis-ийн few-shot сан = train + dev. Test дээр ижил сантай нөхцөл (dev нь test-тэй давхцахгүй)
  if [ ! -f build/index_thesis_traindev/meta.json ]; then
    T2S_EMBED_DEVICE="${T2S_EMBED_DEVICE:-cpu}" t2s --config "$CFG" index-build \
      --extra-examples data/thesis/examples_dev.jsonl --out build/index_thesis_traindev > /dev/null
  fi
  for ID in ${IDS//,/ }; do
    if needs_adapter "$ID"; then continue; fi
    case "$ID" in A*) continue ;; esac            # A бүлэг few-shot ашигладаггүй
    log "=== thesis-bench $ID / $MODEL / test, few-shot = train+dev"
    # shellcheck disable=SC2086
    t2s --config "$CFG" eval --run "$ID" --model "$MODEL" --split test --variants "$VARIANTS" --workers "$WORKERS" \
      --set index_dir=build/index_thesis_traindev --tag fs_traindev ${EXTRA:-} 2>&1 \
      | tee -a "build/logs/thesis_${ID}_${MODEL}_test_fs.log"
  done
fi

if [ "${ORIGINAL:-0}" = "1" ]; then
  SNAP="$T2S_ROOT/build/thesis_snapshot"
  OUT="$T2S_ROOT/results/thesis/original"
  log "thesis-ийн системийн хуулбар: $SNAP"
  mkdir -p "$SNAP" "$OUT"
  tar -C "$SRC" --exclude=./.venv --exclude=./.git --exclude='./build/dashboard-*' --exclude=./build/presentation \
      --exclude=./results --exclude=./tmp --exclude=./.cache --exclude=./diplom-main --exclude=./thesis -cf - . \
    | tar -C "$SNAP" -xf -
  if [ ! -x "$SNAP/.venv/bin/python" ]; then
    "${PYTHON:-python3}" -m venv "$SNAP/.venv"
    "$SNAP/.venv/bin/pip" install -q -U pip
    "$SNAP/.venv/bin/pip" install -q -r "$SNAP/requirements.txt"   # vLLM-гүй: манай vLLM серверийг HTTP-ээр ашиглана
  fi
  SERVED=$(python -c "from mn_text2sql.config import load_models; print(load_models()['models']['$MODEL'].get('served_name','$MODEL'))")
  "$SNAP/.venv/bin/python" - "$SNAP" "$SERVED" "$T2S_LLM_BASE_URL" "$OUT" <<'PY'
import sys, yaml, pathlib
snap, served, url, out = sys.argv[1:5]
cfg = yaml.safe_load(pathlib.Path(snap, "config", "runpod.yaml").read_text(encoding="utf-8-sig"))
cfg["llm"]["backend"] = "vllm"
cfg["llm"]["model"] = served          # манай vLLM-ийн served-model-name
cfg.setdefault("vllm", {})["url"] = url
cfg["paths"]["results_dir"] = out
pathlib.Path(snap, "config", "t2s_bench.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
PY
  FILES=()
  MARK="$OUT/.started_$$"
  touch "$MARK"                                  # зөвхөн энэ ажиллуулалтын үр дүнгийн файлыг авна
  for SPLIT in $SPLITS; do
    log "=== thesis-ийн систем / $SERVED / $SPLIT (sqlite, workers=${THESIS_WORKERS:-1} — E-41: зэрэгцээ нь детерминист биш)"
    (cd "$SNAP" && PYTHONUTF8=1 .venv/bin/python scripts/run_agent_eval.py --config config/t2s_bench.yaml \
        --dialect sqlite --split "$SPLIT" --label "t2s-bench-$SPLIT" --workers "${THESIS_WORKERS:-1}") \
      2>&1 | tee -a "build/logs/thesis_original_${SPLIT}.log"
    F=$(find "$OUT" -maxdepth 1 -name "*-t2s-bench-$SPLIT*.json" ! -name 'partial-*' -newer "$MARK" | sort | tail -1)
    if [ -n "$F" ]; then FILES+=(--file "$F"); else log "АНХААР: $SPLIT-ийн үр дүнгийн JSON олдсонгүй ($OUT)"; fi
  done
  rm -f "$MARK"
  if [ ${#FILES[@]} -gt 0 ]; then
    t2s --config "$CFG" thesis-score "${FILES[@]}" --name thesis-agent --llm "$MODEL" | tee build/thesis_score.json
  fi
fi

t2s --config "$CFG" thesis-compare > /dev/null && log "Харьцуулалт: results/thesis/COMPARE.md"
