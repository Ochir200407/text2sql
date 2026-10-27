#!/usr/bin/env bash
# =====================================================================
# Kaggle / Colab (root эрхтэй Ubuntu, systemd-гүй) дээр тестийн орчин бэлтгэх. notebooks/t2s_eval.ipynb дуудна.
#   bash scripts/notebook/bootstrap.sh            # PostgreSQL 16, pip, DB, test-suite DB, мэдлэгийн индекс
#   SKIP_INSTALL=1 bash scripts/notebook/bootstrap.sh   # дахин ажиллуулахад (суулгалт хийгдсэн бол)
# DB нь детерминист (seed 42) тул сесс бүрт дахин үүсгэнэ (~1–4 мин). Индексийг build/index-д хадгална.
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export T2S_PG_PASSWORD="${T2S_PG_PASSWORD:-t2s_nb_admin}"
source scripts/server/env.sh
SUDO=""; AS_PG=(runuser -u postgres --)
if [ "$(id -u)" != 0 ]; then SUDO="sudo"; AS_PG=(sudo -u postgres); fi
PG_BIN=/usr/lib/postgresql/16/bin
PG_DATA="${PG_DATA:-/tmp/pgdata}"

if [ "${SKIP_INSTALL:-0}" != 1 ]; then
  log "PostgreSQL 16 (PGDG)"
  if [ ! -x "$PG_BIN/initdb" ]; then
    $SUDO apt-get update -y -qq
    $SUDO apt-get install -y -qq curl ca-certificates gnupg lsb-release procps > /dev/null
    $SUDO install -d /usr/share/postgresql-common/pgdg
    $SUDO curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
      | $SUDO tee /etc/apt/sources.list.d/pgdg.list > /dev/null
    $SUDO apt-get update -y -qq
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq postgresql-16 > /dev/null
  fi
  log "Python хамаарал (vLLM ~5 мин)"
  pip install -q -r requirements.txt
  pip install -q -r requirements-gpu.txt
  # Kaggle/Colab-ийн урьдчилан суусан torchaudio нь vLLM-ийн шинэ torch-той CUDA-гаар зөрж transformers-ийг унагадаг;
  # энэ системд torchaudio хэрэггүй тул устгана.
  pip uninstall -y -q torchaudio || true
  pip cache purge > /dev/null 2>&1 || true   # Kaggle-ийн /kaggle/working-аас гадуурх диск бага — wheel кэш хэрэггүй
fi

if ! "${AS_PG[@]}" "$PG_BIN/pg_ctl" -D "$PG_DATA" status > /dev/null 2>&1; then
  if [ ! -f "$PG_DATA/PG_VERSION" ]; then
    $SUDO mkdir -p "$PG_DATA"; echo "$T2S_PG_PASSWORD" > /tmp/pgpw
    $SUDO chown -R postgres:postgres "$PG_DATA" /tmp/pgpw
    "${AS_PG[@]}" "$PG_BIN/initdb" -D "$PG_DATA" -E UTF8 --locale=C.UTF-8 -U postgres \
      --auth-local=trust --auth-host=scram-sha-256 --pwfile=/tmp/pgpw > /dev/null
  fi
  "${AS_PG[@]}" "$PG_BIN/pg_ctl" -D "$PG_DATA" -l /tmp/pg.log -w -t 120 \
    -o "-p $T2S_PG_PORT -c max_connections=200 -c shared_buffers=2GB -c work_mem=64MB -c listen_addresses=localhost" start
fi
rm -f build/vllm_*.pid          # өмнөх сессийн PID (архиваас сэргээсэн) — өөр процессыг алахгүй
mkdir -p build
log "DB (seed 42) ба test-suite хуулбарууд"
t2s db-build
t2s db-validate > build/db_validate.json || { log "db-validate АЛДАА — build/db_validate.json"; exit 2; }
if [ "${STAGE:-}" = web ]; then
  log "test-suite DB алгасав (STAGE=web — үнэлгээгүй, дискийг хэмнэнэ)"
else
  t2s testsuite-build > build/testsuite_build.json
fi
if [ ! -f build/index/.ok ]; then
  log "Мэдлэгийн индекс (bge-m3, GPU)"
  T2S_EMBED_DEVICE=auto t2s index-build
  touch build/index/.ok
fi
log "Бэлэн."
