#!/usr/bin/env bash
# =====================================================================
# Серверийн бүрэн суулгалт (Ubuntu 22.04/24.04, NVIDIA L4 эсвэл A100, CUDA 12.x драйвертай).
#   bash scripts/server/setup.sh            # бүгд: PostgreSQL 16, venv, DB, test-suite, индекс, шалгалт
#   SKIP_PG=1 bash scripts/server/setup.sh  # PostgreSQL аль хэдийн суусан бол
#   CPU_ONLY=1 ...                          # GPU-гүй (vLLM, torch суулгахгүй)
# Дууссаны дараа: scripts/server/serve_model.sh arctic-r1-7b → scripts/server/run_experiments.sh A1 arctic-r1-7b
# =====================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
cd "$T2S_ROOT"

if [ "${SKIP_PG:-0}" != "1" ]; then
  log "PostgreSQL 16 (PGDG) суулгаж байна"
  sudo apt-get update -y
  sudo apt-get install -y curl ca-certificates gnupg lsb-release python3-venv python3-pip build-essential
  sudo install -d /usr/share/postgresql-common/pgdg
  sudo curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
  echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y postgresql-16
  # UTF-8 locale-тай cluster (кирилл lower()/ILIKE зөв ажиллахад)
  if ! sudo -u postgres psql -p "$T2S_PG_PORT" -tAc "SHOW server_encoding" 2>/dev/null | grep -q UTF8; then
    sudo pg_dropcluster --stop 16 main || true
    sudo pg_createcluster 16 main --port "$T2S_PG_PORT" --encoding UTF8 --locale C.UTF-8 --start
  fi
  sudo -u postgres psql -p "$T2S_PG_PORT" -c "ALTER USER postgres PASSWORD '${T2S_PG_PASSWORD}';"
  # Тестийн ачаалалд тохирох тохиргоо (8 зэрэг асуулт + test-suite)
  sudo -u postgres psql -p "$T2S_PG_PORT" -c "ALTER SYSTEM SET max_connections = 200;" \
                                          -c "ALTER SYSTEM SET shared_buffers = '2GB';" \
                                          -c "ALTER SYSTEM SET work_mem = '64MB';"
  sudo systemctl restart postgresql
fi

log "Python venv ба хамаарал"
PY="${PYTHON:-python3}"
[ -d "$T2S_ROOT/.venv" ] || "$PY" -m venv "$T2S_ROOT/.venv"
# shellcheck disable=SC1091
source "$T2S_ROOT/.venv/bin/activate"
pip install -U pip wheel
pip install -r requirements.txt
if [ "${CPU_ONLY:-0}" != "1" ]; then
  pip install -r requirements-gpu.txt
fi
pip install -e . --no-deps

mkdir -p build
log "DB үүсгэх (схем + синтетик өгөгдөл ~1 мин) — seed 42, scale 1.0, өнөөдөр = 2026-06-30"
t2s db-build
if ! t2s db-validate > build/db_validate.json; then log "db-validate АЛДАА — build/db_validate.json-ийг үз"; exit 2; fi
log "db-validate OK"
log "Test-suite хуулбар DB-үүд (8 × scale 0.25, ~3 мин)"
t2s testsuite-build > build/testsuite_build.json
log "Мэдлэгийн индекс (embedding: BAAI/bge-m3, GPU байвал)"
if [ "${CPU_ONLY:-0}" = "1" ]; then t2s index-build --no-embed; else T2S_EMBED_DEVICE=auto t2s index-build; fi
log "Шалгалтууд"
if ! t2s knowledge-check > build/knowledge_check.json; then log "knowledge-check АЛДАА — build/knowledge_check.json-ийг үз"; exit 2; fi
t2s check-gold --testsuite > build/check_gold.json || { log "check-gold АЛДАА — build/check_gold.json-ийг үз"; exit 2; }
t2s security-test > build/security_test.json || { log "security-test АЛДАА — build/security_test.json-ийг үз"; exit 2; }
log "Бэлэн. Дараагийн алхам: bash scripts/server/serve_model.sh arctic-r1-7b"
