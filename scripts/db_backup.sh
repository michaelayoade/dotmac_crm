#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/root/dotmac/dotmac_omni"
BACKUP_ROOT_DIR="/mnt/db.backup"
BACKUP_SUBDIR="${DB_BACKUP_SUBDIR:-dotmac_omni}"
BACKUP_RETENTION_COUNT="${DB_BACKUP_RETENTION_COUNT:-5}"
BACKUP_BASENAME="${DB_BACKUP_BASENAME:-dotmac_omni}"

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

if [[ ! -d "${BACKUP_ROOT_DIR}" ]]; then
  echo "Backup mount not found: ${BACKUP_ROOT_DIR}" >&2
  exit 1
fi

if ! [[ "${BACKUP_RETENTION_COUNT}" =~ ^[0-9]+$ ]] || [[ "${BACKUP_RETENTION_COUNT}" -lt 1 ]]; then
  echo "DB_BACKUP_RETENTION_COUNT must be a positive integer" >&2
  exit 1
fi

if ! mountpoint -q "${BACKUP_ROOT_DIR}"; then
  echo "Backup mount is not active: ${BACKUP_ROOT_DIR}" >&2
  exit 1
fi

BACKUP_DIR="${BACKUP_ROOT_DIR}"
if [[ -n "${BACKUP_SUBDIR}" ]]; then
  BACKUP_DIR="${BACKUP_ROOT_DIR}/${BACKUP_SUBDIR}"
fi

mkdir -p "${BACKUP_DIR}"

if [[ "${BACKUP_DIR}" != "${BACKUP_ROOT_DIR}" ]]; then
  mapfile -t LEGACY_BACKUPS < <(
    find "${BACKUP_ROOT_DIR}" -maxdepth 1 -type f -name "${BACKUP_BASENAME}_*.sql.gz" | sort
  )
  if [[ "${#LEGACY_BACKUPS[@]}" -gt 0 ]]; then
    echo "Migrating legacy backups into ${BACKUP_DIR}"
    for legacy_backup in "${LEGACY_BACKUPS[@]}"; do
      mv "${legacy_backup}" "${BACKUP_DIR}/"
    done
  fi
fi

STAMP=$(date +"%F_%H%M%S")
OUT_FILE="${BACKUP_DIR}/${BACKUP_BASENAME}_${STAMP}.sql.gz"

echo "Starting DB backup to ${OUT_FILE}"

docker exec -e PGPASSWORD="${POSTGRES_PASSWORD}" dotmac_omni_db \
  pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  | gzip > "${OUT_FILE}"

echo "Backup complete: ${OUT_FILE}"

mapfile -t EXISTING_BACKUPS < <(
  find "${BACKUP_DIR}" -maxdepth 1 -type f -name "${BACKUP_BASENAME}_*.sql.gz" | sort
)

DELETE_COUNT=$((${#EXISTING_BACKUPS[@]} - BACKUP_RETENTION_COUNT))
if [[ "${DELETE_COUNT}" -gt 0 ]]; then
  for ((i = 0; i < DELETE_COUNT; i++)); do
    echo "Pruning old backup: ${EXISTING_BACKUPS[$i]}"
    rm -f "${EXISTING_BACKUPS[$i]}"
  done
fi
