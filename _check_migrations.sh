#!/bin/sh
echo "SELECT app, name FROM django_migrations ORDER BY id;" | docker exec -i smsly-postgres-primary psql -U smsly_admin -d smsly_hosting
echo "---"
echo "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE '%servicemetric%' OR tablename LIKE '%servicereplica%';" | docker exec -i smsly-postgres-primary psql -U smsly_admin -d smsly_hosting
