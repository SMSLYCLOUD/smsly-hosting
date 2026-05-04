#!/bin/bash
set -e

# Render the PgCat config dynamically based on environment variables
CONFIG_RUNTIME="/tmp/pgcat.toml"

echo "Rendering PgCat configuration..."
# Map general environment to expected script vars
export DB_HOST=${POSTGRES_HOST:-db}
export DB_PORT=${POSTGRES_PORT:-5432}
export DB_USER=${POSTGRES_USER:-smsly_admin}
export DB_PASSWORD=${POSTGRES_PASSWORD}
export DB_NAME=${POSTGRES_DB:-smsly_hosting}

python3 /scripts/render_pgcat_config.py "$CONFIG_RUNTIME"

# Start PgCat
echo "Starting PgCat..."
exec pgcat "$CONFIG_RUNTIME"
