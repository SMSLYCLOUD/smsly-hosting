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

is_ip() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

urls=()
urls+=("http://${HOST}")

if ! is_ip "$HOST"; then
  urls+=("https://${HOST}")
  if [ -n "$WILDCARD" ]; then
    if [[ "$WILDCARD" == \*.* ]]; then
      WILDCARD_TEST_HOST="smoke-$(date +%s).${WILDCARD#*.}"
    else
      WILDCARD_TEST_HOST="$WILDCARD"
    fi
    echo "[smoke] Wildcard probe host: ${WILDCARD_TEST_HOST}"
    urls+=("https://${WILDCARD_TEST_HOST}")
  fi
else
  echo "[smoke] IP mode detected; skipping HTTPS/wildcard probes."
fi

attempt=0
delay=2
max=4
for url in "${urls[@]}"; do
  attempt=0
  delay=2
  while [ $attempt -lt $max ]; do
    code=$(curl -sk --connect-timeout 5 --max-time 10 -o /dev/null -w "%{http_code}" "$url" || true)
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
