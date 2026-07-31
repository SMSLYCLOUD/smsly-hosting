#!/bin/sh
cd /opt/smsly-hosting
python3 scripts/generate_env_secrets.py --shell | while IFS='=' read -r k v; do
    if ! grep -q "^${k}=" .env 2>/dev/null; then
        echo "${k}=${v}" >> .env
    fi
done
echo DONE
