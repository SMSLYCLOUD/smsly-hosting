#!/bin/sh
echo "ALTER USER smsly_admin WITH PASSWORD 'fba4f72b65ab676f31aa3e4022075bf4fba25efde3f629353e32f3cff6ee9fb8';" | docker exec -i smsly-postgres-primary psql -U smsly_admin -d smsly_hosting
