#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/root/dotmac/dotmac_omni"
BACKUP_DIR="/mnt/db.backup"

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  echo "Missing ${ROOT_DIR}/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "${ROOT_DIR}/.env"
set +a

if [[ -z "${POSTGRES_USER:-}" || -z "${POSTGRES_DB:-}" || -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "Missing POSTGRES env vars in .env" >&2
  exit 1
fi

if [[ ! -d "${BACKUP_DIR}" ]]; then
  echo "Backup mount not found: ${BACKUP_DIR}" >&2
  exit 1
fi

STAMP=$(date +"%F_%H%M%S")
OUT_FILE="${BACKUP_DIR}/dotmac_omni_${STAMP}.sql.gz"

if ! mountpoint -q "${BACKUP_DIR}"; then
  echo "Backup mount is not active: ${BACKUP_DIR}" >&2
  exit 1
fi

echo "Starting DB backup to ${OUT_FILE}"

docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" dotmac_omni_db \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  | gzip > "${OUT_FILE}"

echo "Backup complete: ${OUT_FILE}"
