#!/bin/bash
set -e

# Uses Python config generator's logic or replicates it cleanly.
echo "Calculating DB Connection Budget..."

POSTGRES_MAX=${POSTGRES_MAX_CONNECTIONS:-100}
APP_POOL=${PGCAT_APP_POOL_SIZE:-20}
WORKER_POOL=${PGCAT_WORKER_POOL_SIZE:-5}
RESERVED=5

TOTAL=$((APP_POOL + WORKER_POOL + RESERVED))

echo "Postgres Max Connections: $POSTGRES_MAX"
echo "Requested App Pool: $APP_POOL"
echo "Requested Worker Pool: $WORKER_POOL"
echo "Reserved (Admin/Health): $RESERVED"
echo "Total Requested: $TOTAL"

if [ "$TOTAL" -gt "$POSTGRES_MAX" ]; then
    echo "ERROR: Connection budget exceeded! Requested: $TOTAL, Max: $POSTGRES_MAX"
    bash -c "exit 1"
fi

echo "SUCCESS: Connection budget is safe."
