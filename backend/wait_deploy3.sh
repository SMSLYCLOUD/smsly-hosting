#!/bin/bash
while true; do
  STATUS=$(docker ps -f name=ai-router-3eca1f78 --format '{{.Status}}')
  if [[ "$STATUS" == *"Up "* ]] && [[ "$STATUS" != *"Up 11"* ]] && [[ "$STATUS" != *"Up 12"* ]]; then
    echo "Container is running: $STATUS"
    break
  fi
  sleep 5
done
