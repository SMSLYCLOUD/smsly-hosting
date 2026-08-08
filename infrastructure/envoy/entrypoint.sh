#!/bin/sh
set -e

# Envoy Sidecar Entrypoint
# ========================
# Generates Envoy config from template using environment variables,
# then starts Envoy with the generated config.

ENVOY_TEMPLATE="/etc/envoy/envoy.yaml.template"
ENVOY_CONFIG="/etc/envoy/envoy.yaml"

# Required environment variables
TRUST_DOMAIN="${SPIFFE_TRUST_DOMAIN:-ecosystem.local}"
SERVICE_NAME="${SERVICE_NAME:?SERVICE_NAME is required}"
APP_PORT="${APP_PORT:-8000}"
SPIRE_AGENT_SOCKET="${SPIFFE_ENDPOINT_SOCKET:-unix:///opt/spire/run/agent.sock}"

# Strip "unix://" prefix for Envoy UDS config
SOCKET_PATH="${SPIRE_AGENT_SOCKET#unix://}"

echo "[envoy-sidecar] Generating config..."
echo "  Trust domain: ${TRUST_DOMAIN}"
echo "  Service name: ${SERVICE_NAME}"
echo "  App port: ${APP_PORT}"
echo "  SPIRE socket: ${SOCKET_PATH}"

# Generate config from template
sed \
    -e "s|{{APP_PORT}}|${APP_PORT}|g" \
    -e "s|{{TRUST_DOMAIN}}|${TRUST_DOMAIN}|g" \
    -e "s|{{SERVICE_NAME}}|${SERVICE_NAME}|g" \
    -e "s|{{SPIRE_AGENT_SOCKET}}|${SOCKET_PATH}|g" \
    "${ENVOY_TEMPLATE}" > "${ENVOY_CONFIG}"

echo "[envoy-sidecar] Config generated at ${ENVOY_CONFIG}"

# Validate config
echo "[envoy-sidecar] Validating config..."
if envoy --mode validate -c "${ENVOY_CONFIG}" 2>&1; then
    echo "[envoy-sidecar] Config valid"
else
    echo "[envoy-sidecar] WARNING: Config validation failed, starting anyway"
fi

echo "[envoy-sidecar] Starting Envoy..."
exec envoy -c "${ENVOY_CONFIG}" --service-cluster "${SERVICE_NAME}" --service-node "${SERVICE_NAME}"
