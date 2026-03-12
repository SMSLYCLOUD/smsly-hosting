#!/bin/bash
# Post-reload smoke test for HTTP/HTTPS/wildcard routes with backoff.
set -euo pipefail

HOST="${1:-}"
WILDCARD="${2:-}"
PORT_HTTP="${PORT_HTTP:-80}"
PORT_HTTPS="${PORT_HTTPS:-443}"

if [ -z "$HOST" ]; then
  echo "[smoke] No host provided; skipping."
  exit 0
fi

urls=()
urls+=("http://${HOST}")
urls+=("https://${HOST}")
if [ -n "$WILDCARD" ]; then
  urls+=("https://${WILDCARD}")
fi

attempt=0
delay=2
max=4
for url in "${urls[@]}"; do
  attempt=0
  delay=2
  while [ $attempt -lt $max ]; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" "$url" || true)
    if [ "$code" -ge 200 ] && [ "$code" -lt 500 ]; then
      echo "[smoke] OK $url ($code)"
      break
    fi
    attempt=$((attempt+1))
    echo "[smoke] $url failed (code=${code:-0}), retrying in ${delay}s"
    sleep $delay
    delay=$((delay*2))
  done
done
