#!/bin/bash
echo "Restarting PgCat container to simulate failure..."
docker compose -f docker-compose.prod.yml restart pgcat
echo "PgCat restarted."
