#!/bin/bash
sed -i 's/--pool=solo --concurrency=1/--concurrency=8/g' /opt/smsly-hosting/docker-compose.prod.yml
