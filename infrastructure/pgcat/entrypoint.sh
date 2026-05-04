#!/bin/sh
set -e

# ─── PgCat entrypoint ────────────────────────────────────────────────────────
# Templates environment variables into pgcat.toml and starts PgCat.

: "${POSTGRES_USER:?POSTGRES_USER required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
: "${POSTGRES_DB:?POSTGRES_DB required}"

CONFIG_SRC="/etc/pgcat/pgcat.toml"
CONFIG_RUNTIME="/tmp/pgcat.toml"

# Template env vars into config
sed \
    -e "s|\${POSTGRES_PASSWORD}|${POSTGRES_PASSWORD}|g" \
    -e "s|\"smsly_admin\"|\"${POSTGRES_USER}\"|g" \
    -e "s|\"smsly_hosting\"|\"${POSTGRES_DB}\"|g" \
    "$CONFIG_SRC" > "$CONFIG_RUNTIME"

echo "PgCat starting: pool_mode=transaction, pool_size=15, db=${POSTGRES_DB}"

exec pgcat "$CONFIG_RUNTIME"
