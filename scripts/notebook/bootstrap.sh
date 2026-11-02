#!/usr/bin/env bash
# =====================================================================
# Kaggle / Colab (root эрхтэй Ubuntu, systemd-гүй) дээр тестийн орчин бэлтгэх. notebooks/t2s_eval.ipynb дуудна.
#   bash scripts/notebook/bootstrap.sh            # PostgreSQL 16, pip, DB, test-suite DB, мэдлэгийн индекс
#   SKIP_INSTALL=1 bash scripts/notebook/bootstrap.sh   # дахин ажиллуулахад (суулгалт хийгдсэн бол)
#   PIN_VERSIONS=1 bash scripts/notebook/bootstrap.sh   # F6: vLLM/transformers/... хувилбар түгжих (анхдагч: түгжихгүй)
#   Kaggle-д (notebook-ийг засах боломжгүй): config/kaggle_overrides.env-д PIN_VERSIONS=1 [PIN_FILE=...] — энд зөвхөн
#   эдгээр 2 түлхүүрийг уншина (бусдыг нь run_plan_l4.sh source хийнэ); KO_UNTIL-ийг run_plan-тай ижил дагана.
# DB нь детерминист (seed 42) тул сесс бүрт дахин үүсгэнэ (~1–4 мин). Индексийг build/index-д хадгална.
# =====================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export T2S_PG_PASSWORD="${T2S_PG_PASSWORD:-t2s_nb_admin}"
source scripts/server/env.sh

# F6-ийн PIN_VERSIONS/PIN_FILE-ийг override-оос (bootstrap нь run_plan-аас ӨМНӨ ажилладаг тул тэнд source хийсэн нь хэтэрхий
# оройтдог). Файлыг source хийхгүй — зөвхөн 2 түлхүүр (CRLF-ийг хасна); KO_UNTIL (UTC) өнгөрсөн бол уншихгүй.
ko_get() {   # $1 = түлхүүр: «KEY=утга» мөрийн утга (хашилт ба « # тайлбар»-ыг хасна)
  tr -d '\r' < config/kaggle_overrides.env | sed -n "s/^$1=//p" | head -1 | sed -e 's/[[:space:]]\{1,\}#.*$//' -e 's/[[:space:]]*$//' | tr -d "\"'"
}
if [ -f config/kaggle_overrides.env ]; then
  KO_UNTIL="$(ko_get KO_UNTIL)"; KO_PIN="$(ko_get PIN_VERSIONS)"; KO_PIN_FILE="$(ko_get PIN_FILE)"
  if [ -n "$KO_UNTIL" ] && [ "$(date -u +%F)" \> "$KO_UNTIL" ]; then
    if [ -n "$KO_PIN$KO_PIN_FILE" ]; then log "[override] KO_UNTIL=$KO_UNTIL өнгөрсөн — PIN_VERSIONS/PIN_FILE-ийг тоохгүй"; fi
  else
    if [ -n "$KO_PIN" ]; then PIN_VERSIONS="$KO_PIN"; log "[override] PIN_VERSIONS=$PIN_VERSIONS"; fi
    if [ -n "$KO_PIN_FILE" ]; then PIN_FILE="$KO_PIN_FILE"; log "[override] PIN_FILE=$PIN_FILE"; fi
  fi
fi
SUDO=""; AS_PG=(runuser -u postgres --)
if [ "$(id -u)" != 0 ]; then SUDO="sudo"; AS_PG=(sudo -u postgres); fi
PG_BIN=/usr/lib/postgresql/16/bin
PG_DATA="${PG_DATA:-/tmp/pgdata}"

# apt-get update: гуравдагч repo (NVIDIA CUDA, r2u)-ийн толин сервер шинэчлэгдэж байхад алдаа өгдөг (Version 23:
# «File has unexpected size … Mirror sync in progress») — 3 удаа оролдоод, PGDG/Ubuntu-ийн индекс шинэчлэгдсэн бол үргэлжилнэ
apt_update() {
  for i in 1 2 3; do
    $SUDO apt-get update -y -qq && return 0
    log "apt-get update алдаа ($i/3) — 20 с дараа дахин"; sleep 20
  done
  log "apt-get update бүрэн амжилтгүй (гуравдагч repo) — суулгалтыг үргэлжлүүлнэ"
}

if [ "${SKIP_INSTALL:-0}" != 1 ]; then
  log "PostgreSQL 16 (PGDG)"
  if [ ! -x "$PG_BIN/initdb" ]; then
    apt_update
    $SUDO apt-get install -y -qq curl ca-certificates gnupg lsb-release procps > /dev/null
    $SUDO install -d /usr/share/postgresql-common/pgdg
    $SUDO curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
      | $SUDO tee /etc/apt/sources.list.d/pgdg.list > /dev/null
    apt_update
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq postgresql-16 > /dev/null
  fi
  log "Python хамаарал (vLLM ~5 мин)"
  pip install -q -r requirements.txt
  # F6 (сонголттой, анхдагчаар УНТРААЛТТАЙ): PIN_VERSIONS=1 бол ажиглагдсан хувилбаруудыг (PIN_FILE, анхдагч
  # requirements-d3.txt: vllm 0.32.0, transformers 5.19.0, trl 1.16.0, peft 0.21.2, accelerate 1.15.0, bitsandbytes 0.50.2)
  # requirements-gpu.txt-ийн суулгалтад pip-ийн constraints (-c) болгож өгнө: нэг удаа шийдэж нэг удаа татна (хоёр дахь
  # суулгалт биш), PIN_FILE-д байхгүй багцууд (torch, sentence-transformers, datasets …) requirements-gpu.txt-ээр хэвээр
  # суулгагдана. Файл алга бол анхааруулаад түгжээгүй үргэлжилнэ. Суусан хувилбарыг build/pip_versions.txt-д бичнэ.
  PIN_ON=0
  if [ "${PIN_VERSIONS:-0}" = 1 ]; then
    PIN_FILE="${PIN_FILE:-requirements-d3.txt}"
    if [ -f "$PIN_FILE" ]; then PIN_ON=1; else log "F6: PIN_VERSIONS=1 боловч $PIN_FILE алга — түгжээгүй үргэлжилнэ"; fi
  fi
  if [ "$PIN_ON" = 1 ]; then
    log "F6: хувилбар түгжих ($PIN_FILE — requirements-gpu.txt-ийн constraints)"
    pip install -q -r requirements-gpu.txt -c "$PIN_FILE"
    mkdir -p build
    pip freeze 2>/dev/null | grep -iE '^(vllm|transformers|torch|peft|trl|accelerate|bitsandbytes|sentence-transformers|xformers|flashinfer[a-z_-]*)==' \
      > build/pip_versions.txt || true
  else
    pip install -q -r requirements-gpu.txt
  fi
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
  # Түр алдаа (2026-11-01 V27: ачааллын дараах autovacuum-тай deadlock) — нэг удаа дахин оролдоно (DB-үүдийг дахин үүсгэнэ)
  t2s testsuite-build > build/testsuite_build.json || {
    log "testsuite-build алдаа — 10 с-ийн дараа дахин оролдоно"; sleep 10; t2s testsuite-build > build/testsuite_build.json; }
fi
if [ ! -f build/index/.ok ]; then
  log "Мэдлэгийн индекс (bge-m3, GPU)"
  T2S_EMBED_DEVICE=auto t2s index-build
  touch build/index/.ok
fi
log "Бэлэн."
