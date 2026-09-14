#!/usr/bin/env bash
# Generate mTLS certificates for gRPC DB sync between master and agents.
# Run on the master node and distribute certs to agents.
set -euo pipefail

CERT_DIR="${1:-/opt/smsly-hosting/certs/grpc}"
mkdir -p "$CERT_DIR"

# Generate CA key + cert
openssl genrsa -out "$CERT_DIR/ca.key" 4096
openssl req -new -x509 -days 3650 -key "$CERT_DIR/ca.key" \
    -out "$CERT_DIR/ca.crt" -subj "/CN=SMSLY DBSync CA"

# Generate server key + cert (for agents)
openssl genrsa -out "$CERT_DIR/server.key" 2048
openssl req -new -key "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.csr" -subj "/CN=smsly-dbsync-server"
openssl x509 -req -days 3650 -in "$CERT_DIR/server.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
    -out "$CERT_DIR/server.crt"

# Generate client key + cert (for master)
openssl genrsa -out "$CERT_DIR/client.key" 2048
openssl req -new -key "$CERT_DIR/client.key" \
    -out "$CERT_DIR/client.csr" -subj "/CN=smsly-dbsync-client"
openssl x509 -req -days 3650 -in "$CERT_DIR/client.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" -CAcreateserial \
    -out "$CERT_DIR/client.crt"

# Restrict permissions
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt

echo "mTLS certs generated in $CERT_DIR"
echo "Distribute server.{key,crt} and ca.crt to agents."
echo "Keep client.{key,crt} on the master node."
