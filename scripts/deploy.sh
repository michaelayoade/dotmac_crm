#!/usr/bin/env bash
# Deploy dotmac_omni from a registry-built image (no source checkout involved).
#
# Usage:
#   deploy.sh sha-abc1234          deploy this image tag (CI builds one per commit on main)
#   deploy.sh --status             show pinned vs running image
#   SKIP_BACKUP=1 deploy.sh ...    skip the pre-migration DB backup
#
# Procedure (validated 2026-07-03):
#   verify image on GHCR -> DB backup -> pin APP_IMAGE_TAG in .env -> pull ->
#   alembic upgrade heads (one-off container) -> recreate app/celery -> health gate.
#
# On a failed health gate the previous tag is re-pinned and containers are
# recreated on it. Migrations are NOT reverted automatically — new revisions
# here are required to be backward-compatible with the previous release.
#
# Canonical copy lives in the repo at scripts/deploy.sh; the runtime copy in
# /opt/dotmac_omni is refreshed from it. The deploy dir needs only this file,
# docker-compose.yml and .env.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/dotmac_omni}"
REPO_DIR="${REPO_DIR:-/root/dotmac/dotmac_omni}"
IMAGE_REPO="ghcr.io/michaelayoade/dotmac_crm"
APP_SERVICES=(app celery-worker celery-beat)
HEALTH_TIMEOUT_SECONDS=180

log() { printf '\n==> %s\n' "$*"; }

cd "${DEPLOY_DIR}"

pinned_tag() { grep -E '^APP_IMAGE_TAG=' .env | cut -d= -f2; }

if [[ "${1:-}" == "--status" ]]; then
  echo "pinned:  ${IMAGE_REPO}:$(pinned_tag)"
  echo "running: $(docker inspect dotmac_omni_app --format '{{.Config.Image}}' 2>/dev/null || echo 'not running')"
  exit 0
fi

TAG="${1:?usage: deploy.sh <image-tag>, e.g. deploy.sh sha-abc1234 (or --status)}"
IMAGE="${IMAGE_REPO}:${TAG}"
PREV_TAG="$(pinned_tag)"

if [[ "${TAG}" == "${PREV_TAG}" ]]; then
  log "Tag ${TAG} is already pinned — re-running deploy steps idempotently."
fi

log "Deploying ${IMAGE} (currently pinned: ${PREV_TAG})"

log "Verifying image exists on registry"
docker manifest inspect "${IMAGE}" >/dev/null

if [[ "${SKIP_BACKUP:-0}" != "1" ]]; then
  log "Backing up database before migrations (SKIP_BACKUP=1 to skip)"
  bash "${REPO_DIR}/scripts/db_backup.sh"
fi

repin_prev() { sed -i "s|^APP_IMAGE_TAG=.*|APP_IMAGE_TAG=${PREV_TAG}|" "${DEPLOY_DIR}/.env"; }
trap 'repin_prev; echo "Deploy FAILED — APP_IMAGE_TAG restored to ${PREV_TAG} (running containers untouched)" >&2' ERR

log "Pinning APP_IMAGE_TAG=${TAG}"
sed -i "s|^APP_IMAGE_TAG=.*|APP_IMAGE_TAG=${TAG}|" .env
# Best-effort deploy record: resolve the tag's short sha to a full commit sha.
if git -C "${REPO_DIR}" rev-parse --verify --quiet "${TAG#sha-}^{commit}" >/dev/null 2>&1; then
  FULL_SHA="$(git -C "${REPO_DIR}" rev-parse "${TAG#sha-}^{commit}")"
  sed -i "s|^GIT_SHA=.*|GIT_SHA=${FULL_SHA}|" .env
fi

log "Pulling image"
docker compose pull app

log "Applying migrations (alembic upgrade heads)"
docker compose run --rm --no-deps app alembic upgrade heads

log "Recreating services: ${APP_SERVICES[*]}"
docker compose up -d "${APP_SERVICES[@]}"

log "Waiting for app health (timeout ${HEALTH_TIMEOUT_SECONDS}s)"
deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
health="unknown"
while ((SECONDS < deadline)); do
  health="$(docker inspect dotmac_omni_app --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)"
  [[ "${health}" == "healthy" ]] && break
  sleep 5
done

if [[ "${health}" != "healthy" ]]; then
  trap - ERR
  log "Health gate FAILED (status=${health}) — rolling back to ${PREV_TAG}"
  repin_prev
  docker compose up -d "${APP_SERVICES[@]}"
  log "Rolled back to ${PREV_TAG}. NOTE: migrations from ${TAG} were NOT reverted."
  exit 1
fi

trap - ERR
curl -fsS -o /dev/null http://localhost:8000/health
log "Deployed ${TAG} successfully (was ${PREV_TAG})"
