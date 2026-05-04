#!/bin/bash
set -e
echo "Starting pgbench stress test through PgCat..."

# Ensure we have pgbench installed locally or run via docker
docker run --rm --network smsly-net postgres:15-alpine pgbench -i -h pgcat -p 5432 -U smsly_admin smsly_hosting
echo "Initialization complete. Running simple protocol test..."
docker run --rm --network smsly-net postgres:15-alpine pgbench -c 50 -j 2 -t 100 -h pgcat -p 5432 -U smsly_admin smsly_hosting

echo "Stress test complete."
