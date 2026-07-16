#!/bin/sh

set -e

PORT="${PORT:-8080}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
ROLE="${ROLE:-all}" # all|web|worker|beat

export PORT BACKEND_PORT FRONTEND_PORT ROLE

envsubst '${PORT} ${BACKEND_PORT} ${FRONTEND_PORT}' \
  < /etc/caddy/Caddyfile.template \
  > /etc/caddy/Caddyfile

SUP_CONF="/tmp/supervisord.conf"

cat > "$SUP_CONF" <<'EOF'
[supervisord]
nodaemon=true
logfile=/dev/null
pidfile=/tmp/supervisord.pid

[unix_http_server]
file=/tmp/supervisor.sock
chmod=0700

[supervisorctl]
serverurl=unix:///tmp/supervisor.sock
EOF

add_program() {
  # $1=name, $2=command, $3=directory, $4=user, $5=environment (optional)
  name="$1"
  cmd="$2"
  dir="$3"
  run_user="$4"
  prog_env="${5:-}"

  cat >> "$SUP_CONF" <<EOF

[program:${name}]
directory=${dir}
command=${cmd}
user=${run_user}
EOF

  if [ -n "$prog_env" ]; then
    echo "environment=${prog_env}" >> "$SUP_CONF"
  fi

  cat >> "$SUP_CONF" <<'EOF'
autostart=true
autorestart=true
startsecs=2
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
stopasgroup=true
killasgroup=true
EOF
}

if [ "$ROLE" = "all" ] || [ "$ROLE" = "web" ]; then
  add_program "backend" \
    "/app/entrypoint.sh gunicorn --bind 127.0.0.1:${BACKEND_PORT} config.asgi:application --workers 1 --worker-class uvicorn.workers.UvicornWorker --timeout -k 5 120" \
    "/app" \
    "smsly"

  add_program "frontend" \
    "/usr/local/bin/node server.js" \
    "/frontend" \
    "smsly" \
    "PORT=\"${FRONTEND_PORT}\",HOSTNAME=\"127.0.0.1\",NODE_ENV=\"production\""

  # Public entrypoint
  add_program "caddy" \
    "/usr/bin/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile" \
    "/" \
    "root"
fi

if [ "$ROLE" = "all" ] || [ "$ROLE" = "worker" ]; then
  if [ "$ROLE" = "all" ]; then
    # In single-container mode, wait for backend health before starting Celery.
    add_program "celery" \
      "/bin/sh -c \"until wget -q -O /dev/null http://127.0.0.1:${BACKEND_PORT}/health; do echo 'waiting for backend...'; sleep 2; done; celery -A config worker -l info --concurrency=1\"" \
      "/app" \
      "smsly"
  else
    add_program "celery" \
      "celery -A config worker -l info --concurrency=1" \
      "/app" \
      "smsly"
  fi
fi

if [ "$ROLE" = "all" ] || [ "$ROLE" = "beat" ]; then
  if [ "$ROLE" = "all" ]; then
    add_program "celery-beat" \
      "/bin/sh -c \"until wget -q -O /dev/null http://127.0.0.1:${BACKEND_PORT}/health; do echo 'waiting for backend...'; sleep 2; done; celery -A config beat -l info\"" \
      "/app" \
      "smsly"
  else
    add_program "celery-beat" \
      "celery -A config beat -l info" \
      "/app" \
      "smsly"
  fi
fi

exec /usr/bin/supervisord -c "$SUP_CONF"
