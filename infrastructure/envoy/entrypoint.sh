#!/bin/sh
set -e

# Envoy Sidecar Entrypoint
# ========================
# Generates Envoy config from template using environment variables,
# then starts Envoy with the generated config.

ENVOY_TEMPLATE="/etc/envoy/envoy.yaml.template"
MOUNTED_CONFIG="/etc/envoy/envoy.yaml"
ENVOY_CONFIG="/tmp/envoy.yaml"

# Required environment variables
TRUST_DOMAIN="${SPIFFE_TRUST_DOMAIN:-ecosystem.local}"
SERVICE_NAME="${SERVICE_NAME:?SERVICE_NAME is required}"
APP_PORT="${APP_PORT:-8000}"
SPIRE_AGENT_SOCKET="${SPIFFE_ENDPOINT_SOCKET:-unix:///opt/spire/run/agent.sock}"

# Strip "unix://" prefix for Envoy UDS config
SOCKET_PATH="${SPIRE_AGENT_SOCKET#unix://}"

if [ -f "${MOUNTED_CONFIG}" ]; then
    ENVOY_CONFIG="${MOUNTED_CONFIG}"
    echo "[envoy-sidecar] Using mounted config at ${ENVOY_CONFIG}"
elif [ ! -f "${ENVOY_CONFIG}" ]; then
    echo "[envoy-sidecar] Generating config..."
    echo "  Trust domain: ${TRUST_DOMAIN}"
    echo "  Service name: ${SERVICE_NAME}"
    echo "  App port: ${APP_PORT}"
    echo "  SPIRE socket: ${SOCKET_PATH}"

    sed \
        -e "s|{{APP_PORT}}|${APP_PORT}|g" \
        -e "s|{{TRUST_DOMAIN}}|${TRUST_DOMAIN}|g" \
        -e "s|{{SERVICE_NAME}}|${SERVICE_NAME}|g" \
        -e "s|{{SPIRE_AGENT_SOCKET}}|${SOCKET_PATH}|g" \
        "${ENVOY_TEMPLATE}" > "${ENVOY_CONFIG}"

    echo "[envoy-sidecar] Config generated at ${ENVOY_CONFIG}"
fi

# Validate config
echo "[envoy-sidecar] Validating config..."
if envoy --mode validate -c "${ENVOY_CONFIG}" 2>&1; then
    echo "[envoy-sidecar] Config valid"
else
    echo "[envoy-sidecar] ERROR: Config validation failed, refusing to start" >&2
    exit 1
fi

echo "[envoy-sidecar] Starting Envoy..."
exec envoy -c "${ENVOY_CONFIG}" --service-cluster "${SERVICE_NAME}" --service-node "${SERVICE_NAME}"
