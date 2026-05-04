#!/bin/bash
echo "Restarting Postgres container to simulate upstream failure..."
docker compose -f docker-compose.prod.yml restart db
echo "Postgres restarted."
