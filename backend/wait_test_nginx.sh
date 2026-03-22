#!/bin/bash
echo "Waiting for health check..."
while true; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://ai-router-auth-6124bc.pcloud.linadeluxe.com/health)
  if [ "$HTTP_CODE" -eq 200 ] || [ "$HTTP_CODE" -eq 401 ]; then
    echo "Ready!"
    break
  fi
  sleep 5
done

echo "Testing unauthenticated..."
curl -s -X POST "https://ai-router-auth-6124bc.pcloud.linadeluxe.com/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3.1", "messages": [{"role": "user", "content": "Hi!"}]}'

echo -e "\nTesting authenticated..."
curl -s -X POST "https://ai-router-auth-6124bc.pcloud.linadeluxe.com/v1/chat/completions" \
  -H "Authorization: Bearer sk-agbonsalo" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3.1", "messages": [{"role": "user", "content": "Hi!"}]}'
