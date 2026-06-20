#!/bin/sh
set -e

CONFIG=/etc/alertmanager/alertmanager.generated.yml

# Build the global section
cat > "$CONFIG" <<'HEADEOF'
global:
  resolve_timeout: 5m
HEADEOF

if [ -n "${ALERT_SLACK_WEBHOOK_URL:-}" ]; then
    echo "  slack_api_url: '${ALERT_SLACK_WEBHOOK_URL}'" >> "$CONFIG"
fi

if [ -n "${ALERT_SMTP_HOST:-}" ]; then
    SMTP_PORT="${ALERT_SMTP_PORT:-25}"
    SMTP_FROM="${ALERT_SMTP_FROM:-alertmanager@localhost}"
    cat >> "$CONFIG" <<SMTPEOF
  smtp_smarthost: '${ALERT_SMTP_HOST}:${SMTP_PORT}'
  smtp_from: '${SMTP_FROM}'
SMTPEOF
    if [ -n "${ALERT_SMTP_USER:-}" ]; then
        echo "  smtp_auth_username: '${ALERT_SMTP_USER}'" >> "$CONFIG"
    fi
    if [ -n "${ALERT_SMTP_PASS:-}" ]; then
        echo "  smtp_auth_password: '${ALERT_SMTP_PASS}'" >> "$CONFIG"
    fi
fi

ENV_LABEL="${ALERT_ENV:-smsly}"
EMAIL_TO="${ALERT_EMAIL_TO:-}"

cat >> "$CONFIG" <<ROUTEEOF

route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default'
  routes:
    - receiver: 'critical'
      matchers:
        - severity = critical
      repeat_interval: 1h
    - receiver: 'warning'
      matchers:
        - severity = warning

receivers:
  - name: 'default'
    slack_configs:
      - channel: '#alerts'
        title: '${ENV_LABEL} | {{ .GroupLabels.alertname }}'
        text: '{{ .CommonAnnotations.description }}'
ROUTEEOF

if [ -n "$EMAIL_TO" ]; then
    cat >> "$CONFIG" <<EMAILEOF
    email_configs:
      - to: '${EMAIL_TO}'
EMAILEOF
fi

cat >> "$CONFIG" <<'ENDOF'

  - name: 'critical'
    slack_configs:
      - channel: '#alerts-critical'
        title: 'CRITICAL: {{ .GroupLabels.alertname }}'
        text: '{{ .CommonAnnotations.description }}'

  - name: 'warning'
    slack_configs:
      - channel: '#alerts'
        title: 'WARNING: {{ .GroupLabels.alertname }}'
        text: '{{ .CommonAnnotations.description }}'
ENDOF

exec /bin/alertmanager --config.file="$CONFIG" "$@"
