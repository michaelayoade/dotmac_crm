#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="${DOTMAC_ROOT_DIR:-${DEFAULT_ROOT_DIR}}"
ENV_FILE="${DOTMAC_ENV_FILE:-${ROOT_DIR}/.env}"
TARGET_DB_CONTAINER="${TARGET_DB_CONTAINER:-dotmac_omni_db}"
TARGET_APP_SERVICES="${TARGET_APP_SERVICES:-app celery-worker celery-beat}"
TARGET_TEMPLATE_DB="${TARGET_TEMPLATE_DB:-template0}"
TARGET_TEMP_DB_NAME="${TARGET_TEMP_DB_NAME:-${TARGET_DB_NAME:-dotmac_crm}_sync_restore}"
TARGET_ARCHIVE_DB_PREFIX="${TARGET_ARCHIVE_DB_PREFIX:-${TARGET_DB_NAME:-dotmac_crm}_pre_sync}"
SOURCE_DB_HOST="${SOURCE_DB_HOST:-149.102.149.5}"
SOURCE_DB_PORT="${SOURCE_DB_PORT:-5432}"
SOURCE_DB_NAME="${SOURCE_DB_NAME:-}"
SOURCE_DB_USER="${SOURCE_DB_USER:-}"
SOURCE_DB_PASSWORD="${SOURCE_DB_PASSWORD:-}"
SOURCE_DB_SSLMODE="${SOURCE_DB_SSLMODE:-prefer}"
SOURCE_DUMP_MODE="${SOURCE_DUMP_MODE:-ssh}"
SOURCE_SSH_HOST="${SOURCE_SSH_HOST:-}"
SOURCE_SSH_PORT="${SOURCE_SSH_PORT:-22}"
SOURCE_SSH_USER="${SOURCE_SSH_USER:-}"
SOURCE_SSH_PASSWORD="${SOURCE_SSH_PASSWORD:-}"
SOURCE_SSH_IDENTITY_FILE="${SOURCE_SSH_IDENTITY_FILE:-}"
SOURCE_DB_CONTAINER="${SOURCE_DB_CONTAINER:-dotmac_omni_db}"
RUN_COMPOSE_RESTART="${RUN_COMPOSE_RESTART:-1}"
SYNC_TMP_DIR="${SYNC_TMP_DIR:-/tmp/dotmac-staging-sync}"
SYNC_LOCK_FILE="${SYNC_LOCK_FILE:-/tmp/dotmac-staging-sync.lock}"
SYNC_LABEL="${SYNC_LABEL:-dotmac_staging_refresh}"
DRY_RUN="${1:-}"

log() {
  printf '[%s] %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "Missing required command: $1"
    exit 1
  fi
}

cleanup() {
  if [[ -n "${DUMP_FILE:-}" && -f "${DUMP_FILE}" ]]; then
    rm -f "${DUMP_FILE}"
  fi
}

restart_services_if_needed() {
  if [[ "${RUN_COMPOSE_RESTART}" == "1" && "${SERVICES_STOPPED:-0}" == "1" && "${SERVICES_RESTARTED:-0}" != "1" ]]; then
    log "Attempting to restart staging application services after failure"
    (
      cd "${ROOT_DIR}" && docker compose up -d ${TARGET_APP_SERVICES}
    ) || log "Failed to restart staging application services automatically"
    SERVICES_RESTARTED=1
  fi
}

docker_psql() {
  docker exec \
    -e PGPASSWORD="${TARGET_DB_PASSWORD}" \
    "${TARGET_DB_CONTAINER}" \
    psql \
    -U "${TARGET_DB_USER}" \
    -d postgres \
    -v ON_ERROR_STOP=1 \
    "$@"
}

on_exit() {
  exit_code=$?
  if [[ ${exit_code} -ne 0 ]]; then
    restart_services_if_needed
  fi
  cleanup
  exit ${exit_code}
}

trap on_exit EXIT

require_command docker

if [[ ! -d "${ROOT_DIR}" ]]; then
  log "Missing root directory: ${ROOT_DIR}"
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  log "Missing env file: ${ENV_FILE}"
  exit 1
fi

mkdir -p "${SYNC_TMP_DIR}"

exec 9>"${SYNC_LOCK_FILE}"
if ! flock -n 9; then
  log "Another staging sync is already running."
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

TARGET_DB_NAME="${TARGET_DB_NAME:-${POSTGRES_DB:-}}"
TARGET_DB_USER="${TARGET_DB_USER:-${POSTGRES_USER:-}}"
TARGET_DB_PASSWORD="${TARGET_DB_PASSWORD:-${POSTGRES_PASSWORD:-}}"

if [[ -z "${SOURCE_DB_NAME}" || -z "${SOURCE_DB_USER}" || -z "${SOURCE_DB_PASSWORD}" ]]; then
  log "SOURCE_DB_NAME, SOURCE_DB_USER, and SOURCE_DB_PASSWORD must be set."
  exit 1
fi

if [[ -z "${TARGET_DB_NAME}" || -z "${TARGET_DB_USER}" || -z "${TARGET_DB_PASSWORD}" ]]; then
  log "Target database credentials are missing from ${ENV_FILE}."
  exit 1
fi

if [[ "${SOURCE_DUMP_MODE}" != "ssh" && "${SOURCE_DUMP_MODE}" != "tcp" ]]; then
  log "SOURCE_DUMP_MODE must be either 'ssh' or 'tcp'."
  exit 1
fi

STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
DUMP_FILE="${SYNC_TMP_DIR}/${SYNC_LABEL}_${STAMP}.dump"

log "Starting one-way staging refresh from source database to target ${TARGET_DB_NAME}"
log "Preparing source dump at ${DUMP_FILE}"

if [[ "${SOURCE_DUMP_MODE}" == "ssh" ]]; then
  if [[ -z "${SOURCE_SSH_HOST}" ]]; then
    SOURCE_SSH_HOST="${SOURCE_DB_HOST}"
  fi
  if [[ -z "${SOURCE_SSH_USER}" ]]; then
    log "SOURCE_SSH_USER must be set for SOURCE_DUMP_MODE=ssh."
    exit 1
  fi
  require_command ssh
  log "Streaming dump over SSH from ${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}:${SOURCE_SSH_PORT}"
  if [[ -n "${SOURCE_SSH_PASSWORD}" ]]; then
    require_command sshpass
    SSHPASS="${SOURCE_SSH_PASSWORD}" \
      sshpass -e ssh \
        -o StrictHostKeyChecking=no \
        -o PreferredAuthentications=password \
        -o PubkeyAuthentication=no \
        -p "${SOURCE_SSH_PORT}" \
        "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}" \
        "docker exec -e PGPASSWORD='${SOURCE_DB_PASSWORD}' '${SOURCE_DB_CONTAINER}' pg_dump --format=custom --no-owner --no-privileges -U '${SOURCE_DB_USER}' -d '${SOURCE_DB_NAME}'" \
        > "${DUMP_FILE}"
  else
    ssh_cmd=(ssh -o StrictHostKeyChecking=no -p "${SOURCE_SSH_PORT}")
    if [[ -n "${SOURCE_SSH_IDENTITY_FILE}" ]]; then
      ssh_cmd+=(-i "${SOURCE_SSH_IDENTITY_FILE}")
    fi
    ssh_cmd+=(
      "${SOURCE_SSH_USER}@${SOURCE_SSH_HOST}"
      "docker exec -e PGPASSWORD='${SOURCE_DB_PASSWORD}' '${SOURCE_DB_CONTAINER}' pg_dump --format=custom --no-owner --no-privileges -U '${SOURCE_DB_USER}' -d '${SOURCE_DB_NAME}'"
    )
    "${ssh_cmd[@]}" > "${DUMP_FILE}"
  fi
else
  require_command pg_dump
  if [[ "${SOURCE_DB_HOST}" == "10.120.121.20" ]]; then
    log "Refusing to use the target host as the source. This job is one-way only."
    exit 1
  fi
  log "Pulling dump over TCP from ${SOURCE_DB_HOST}:${SOURCE_DB_PORT}/${SOURCE_DB_NAME}"
  PGPASSWORD="${SOURCE_DB_PASSWORD}" \
    pg_dump \
      --format=custom \
      --no-owner \
      --no-privileges \
      --host="${SOURCE_DB_HOST}" \
      --port="${SOURCE_DB_PORT}" \
      --username="${SOURCE_DB_USER}" \
      --dbname="${SOURCE_DB_NAME}" \
      --sslmode="${SOURCE_DB_SSLMODE}" \
      --file="${DUMP_FILE}"
fi

if [[ "${DRY_RUN}" == "--dry-run" ]]; then
  log "Dry run complete. Dump created successfully and restore was skipped."
  exit 0
fi

cd "${ROOT_DIR}"

log "Preparing fresh restore database ${TARGET_TEMP_DB_NAME}"
docker_psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${TARGET_TEMP_DB_NAME}' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
docker_psql -c "DROP DATABASE IF EXISTS ${TARGET_TEMP_DB_NAME};"
docker_psql -c "CREATE DATABASE ${TARGET_TEMP_DB_NAME} TEMPLATE ${TARGET_TEMPLATE_DB};"
docker exec \
  -e PGPASSWORD="${TARGET_DB_PASSWORD}" \
  "${TARGET_DB_CONTAINER}" \
  psql \
  -U "${TARGET_DB_USER}" \
  -d "${TARGET_TEMP_DB_NAME}" \
  -v ON_ERROR_STOP=1 \
  -c 'CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'

log "Restoring dump into fresh database ${TARGET_TEMP_DB_NAME}"
cat "${DUMP_FILE}" | docker exec \
  -i \
  -e PGPASSWORD="${TARGET_DB_PASSWORD}" \
  "${TARGET_DB_CONTAINER}" \
  pg_restore \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  --single-transaction \
  -U "${TARGET_DB_USER}" \
  -d "${TARGET_TEMP_DB_NAME}"

if [[ "${RUN_COMPOSE_RESTART}" == "1" ]]; then
  log "Stopping staging application services: ${TARGET_APP_SERVICES}"
  docker compose stop ${TARGET_APP_SERVICES}
  SERVICES_STOPPED=1
fi

archive_db_name="${TARGET_ARCHIVE_DB_PREFIX}_$(date -u +"%Y%m%dT%H%M%SZ")"
log "Terminating active connections to ${TARGET_DB_NAME} and ${TARGET_TEMP_DB_NAME}"
docker_psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('${TARGET_DB_NAME}', '${TARGET_TEMP_DB_NAME}') AND pid <> pg_backend_pid();"

log "Swapping ${TARGET_TEMP_DB_NAME} into place as ${TARGET_DB_NAME}"
docker_psql -c "ALTER DATABASE ${TARGET_DB_NAME} RENAME TO ${archive_db_name};"
docker_psql -c "ALTER DATABASE ${TARGET_TEMP_DB_NAME} RENAME TO ${TARGET_DB_NAME};"

if [[ "${RUN_COMPOSE_RESTART}" == "1" ]]; then
  log "Starting staging application services"
  docker compose up -d ${TARGET_APP_SERVICES}
  SERVICES_RESTARTED=1
fi

log "Staging refresh completed successfully."
