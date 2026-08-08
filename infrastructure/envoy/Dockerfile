# Envoy Sidecar with SPIRE Integration
# ======================================
# Custom Envoy image for transparent mTLS on the smsly-hosting platform.
# Uses SPIRE SDS for dynamic certificate rotation.
#
# Build:
#   docker build -t ghcr.io/smsly/envoy-spire-sidecar:latest .
#
# Run:
#   docker run -d \
#     --name envoy-sidecar \
#     --network host \
#     -v spire-ecosystem-agent-socket:/opt/spire/run:ro \
#     -v spire-ecosystem-agent-svids:/opt/spire/svids:ro \
#     -e SPIFFE_TRUST_DOMAIN=ecosystem.local \
#     -e SPIFFE_ENDPOINT_SOCKET=unix:///opt/spire/run/agent.sock \
#     -e SERVICE_NAME=my-service \
#     -e APP_PORT=8000 \
#     ghcr.io/smsly/envoy-spire-sidecar:latest

FROM envoyproxy/envoy:v1.31.0

LABEL maintainer="smsly-hosting"
LABEL description="Envoy sidecar with SPIRE SDS for transparent mTLS"

# Install curl for health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy Envoy config template
COPY envoy.yaml.template /etc/envoy/envoy.yaml.template

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Health check via Envoy admin interface
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:9901/ready || exit 1

# Envoy admin port (internal only)
EXPOSE 9901

# Inbound mTLS listener
EXPOSE 80

# Outbound mTLS listener (app connects here)
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
