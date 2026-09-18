#!/bin/bash
set -e

# pgcat-tenants: pool shared logical databases behind stable aliases.
# The backend renders /etc/pgcat/pgcat.toml (named volume pgcat_tenants_config)
# and pushes it here on every shared provision/delete/rotate. We wait for the
# first push instead of booting with an empty config.
CONFIG="/etc/pgcat/pgcat.toml"

echo "pgcat-tenants: waiting for backend-rendered config at $CONFIG..."
while [ ! -f "$CONFIG" ]; do sleep 5; done

echo "pgcat-tenants: starting..."
exec pgcat "$CONFIG"
