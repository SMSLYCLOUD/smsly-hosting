echo "--- backend healthy? ---"
docker inspect --format='{{.State.Health.Status}}' smsly-hosting-backend-1
echo ""
echo "--- /metrics (10 lines) ---"
docker exec smsly-hosting-backend-1 wget -qO- http://127.0.0.1:8000/metrics 2>&1 | head -10
echo ""
echo "--- /metrics via container name (in-cluster) ---"
docker exec smsly-prometheus wget -qO- http://backend:8000/metrics 2>&1 | head -10
echo ""
echo "--- wait 20s for prometheus first scrape ---"
sleep 20
echo "--- final targets ---"
docker exec smsly-prometheus wget -qO- 'http://127.0.0.1:9090/api/v1/targets' 2>&1 | grep -oE '"job":"[^"]+"|"health":"[^"]+"|"lastError":"[^"]*"' | paste -d'|' - - - | sed 's/"//g'
echo ""
echo "--- smsly_services_active ---"
docker exec smsly-prometheus wget -qO- 'http://127.0.0.1:9090/api/v1/query?query=smsly_services_active' 2>&1 | head -c 500
echo ""
echo "--- Loki with relative time ---"
docker exec smsly-hosting-backend-1 wget -qO- 'http://127.0.0.1:8000/api/v1/observability/loki/query/?query=%7Bcompose_service%3D~%22.%2B%22%7D&start=now-15m&limit=5' 2>&1 | head -c 500
