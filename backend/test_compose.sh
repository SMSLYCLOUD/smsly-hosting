#!/bin/bash
touch .env
echo "REDIS_PASSWORD=dummy" >> .env
echo "POSTGRES_PASSWORD=dummy" >> .env
echo "FIELD_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=" >> .env
echo "SECRET_KEY=dummy" >> .env
docker compose -f ../docker-compose.yml config > /dev/null
docker compose -f ../docker-compose.prod.yml config > /dev/null
echo "Exit code: $?"
