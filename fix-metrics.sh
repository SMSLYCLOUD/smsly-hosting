cd /opt/smsly-hosting
git log --oneline -1
echo "--- force rebuild ---"
docker compose -f docker-compose.prod.yml build --no-cache backend
echo "--- restart backend ---"
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate backend
echo "--- wait 15s ---"
sleep 15
echo "--- /metrics endpoint ---"
docker exec smsly-hosting-backend-1 wget -qO- http://127.0.0.1:8000/metrics 2>&1 | head -10
echo "--- restart prometheus ---"
docker restart smsly-prometheus
sleep 10
echo "--- final targets ---"
docker exec smsly-prometheus wget -qO- 'http://127.0.0.1:9090/api/v1/targets' 2>&1 | grep -oE '"job":"[^"]+"|"health":"[^"]+"' | paste -d'|' - - | sed 's/"//g'
