#!/bin/bash
while true; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com/health)
  if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 401 ]; then
    break
  fi
  sleep 5
done

curl -s -X POST "https://ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com/chat/completions" \
  -H "Authorization: Bearer sk-agbonsalo" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "braid-llm",
    "messages": [
      {
        "role": "user",
        "content": "Hello! What is your name?"
      }
    ]
  }'
