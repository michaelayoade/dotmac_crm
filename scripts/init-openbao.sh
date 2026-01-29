#!/bin/bash
# OpenBao Initialization Script
# This script initializes OpenBao with the application's secrets

set -e

# Wait for OpenBao to be ready
echo "Waiting for OpenBao to be ready..."
until curl -s http://openbao:8200/v1/sys/health > /dev/null 2>&1; do
    sleep 1
done
echo "OpenBao is ready!"

# Set OpenBao address and token
export BAO_ADDR="${OPENBAO_ADDR:-http://openbao:8200}"
export BAO_TOKEN="${OPENBAO_ROOT_TOKEN}"

# Enable KV secrets engine v2
echo "Enabling KV secrets engine..."
bao secrets enable -path=secret kv-v2 2>/dev/null || echo "KV engine already enabled"

# Store application secrets
echo "Storing application secrets..."

bao kv put secret/dotmac/auth \
    jwt_secret="${JWT_SECRET}" \
    totp_encryption_key="${TOTP_ENCRYPTION_KEY}" \
    session_secret="${SESSION_SECRET}"

bao kv put secret/dotmac/database \
    postgres_user="${POSTGRES_USER}" \
    postgres_password="${POSTGRES_PASSWORD}" \
    postgres_db="${POSTGRES_DB}" \
    database_url="${DATABASE_URL}"

bao kv put secret/dotmac/redis \
    redis_password="${REDIS_PASSWORD}" \
    redis_url="${REDIS_URL}" \
    celery_broker_url="${CELERY_BROKER_URL}" \
    celery_result_backend="${CELERY_RESULT_BACKEND}"

bao kv put secret/dotmac/wireguard \
    key_encryption_key="${WIREGUARD_KEY_ENCRYPTION_KEY}"

bao kv put secret/dotmac/meta \
    app_secret="${META_APP_SECRET:-}"

# Create application policy
echo "Creating application policy..."
bao policy write dotmac-app - <<EOF
# Allow reading secrets
path "secret/data/dotmac/*" {
  capabilities = ["read"]
}

path "secret/metadata/dotmac/*" {
  capabilities = ["list"]
}
EOF

# Create AppRole for the application (optional - for production)
echo "Setting up AppRole authentication..."
bao auth enable approle 2>/dev/null || echo "AppRole already enabled"

bao write auth/approle/role/dotmac-app \
    token_policies="dotmac-app" \
    token_ttl=1h \
    token_max_ttl=4h \
    secret_id_ttl=24h

# Get AppRole credentials
ROLE_ID=$(bao read -field=role_id auth/approle/role/dotmac-app/role-id)
SECRET_ID=$(bao write -f -field=secret_id auth/approle/role/dotmac-app/secret-id)

echo ""
echo "=============================================="
echo "OpenBao initialization complete!"
echo "=============================================="
echo ""
echo "AppRole credentials (save these for production):"
echo "  OPENBAO_ROLE_ID=$ROLE_ID"
echo "  OPENBAO_SECRET_ID=$SECRET_ID"
echo ""
echo "Secrets stored at:"
echo "  - secret/dotmac/auth"
echo "  - secret/dotmac/database"
echo "  - secret/dotmac/redis"
echo "  - secret/dotmac/wireguard"
echo "  - secret/dotmac/meta"
echo ""
