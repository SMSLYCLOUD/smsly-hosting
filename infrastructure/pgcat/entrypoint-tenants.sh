#!/bin/bash
set -e

# pgcat-tenants: pool shared logical databases behind stable aliases.
# The backend renders /etc/pgcat/pgcat.toml (named volume pgcat_tenants_config)
# and pushes it here on every shared provision/delete/rotate. We wait for the
# first push instead of booting with an empty config.
CONFIG="/etc/pgcat/pgcat.toml"

echo "pgcat-tenants: waiting for backend-rendered config at $CONFIG..."
# -s (non-empty), not -f: an empty file makes pgcat exit BadConfig and
# the container crash-loops instead of waiting (observed live).
while [ ! -s "$CONFIG" ]; do sleep 5; done

echo "pgcat-tenants: starting..."
exec pgcat "$CONFIG"
