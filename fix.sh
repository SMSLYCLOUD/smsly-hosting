sed -i 's/celery -A config inspect ping --timeout 10 2>\/dev\/null | grep -q pong/curl -f http:\/\/localhost:8000\/health/g' /opt/smsly-hosting/docker-compose.prod.yml
