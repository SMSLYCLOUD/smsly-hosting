#!/bin/bash
while true; do
  STATUS=$(docker ps -f name=ai-router-cc22a7a5 --format '{{.Status}}')
  if [[ "$STATUS" == *"Up "* ]]; then
    echo "Container is running: $STATUS"
    break
  fi
  sleep 5
done
