#!/bin/bash
docker logs smsly-backend 2>&1 | tail -n 100
