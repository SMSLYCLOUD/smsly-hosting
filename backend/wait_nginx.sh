#!/bin/bash
while true; do
  STATUS=$(docker ps -f name=ai-router-auth --format '{{.Status}}')
  if [[ "$STATUS" == *"Up "* ]]; then
    echo "NGINX is running: $STATUS"
    break
  fi
  sleep 5
done

# Write config
cat << 'CONFIG' > /tmp/auth.conf
events {}
http {
    server {
        listen 4000;
        location / {
            set $auth_ok 0;
            if ($http_authorization = "Bearer sk-agbonsalo") {
                set $auth_ok 1;
            }
            if ($request_method = OPTIONS) {
                set $auth_ok 1;
            }
            if ($auth_ok = 0) {
                return 401 "{\"error\": \"Unauthorized\"}";
            }
            add_header 'Access-Control-Allow-Origin' '*';
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
            add_header 'Access-Control-Allow-Headers' 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization';
            if ($request_method = OPTIONS) {
                add_header 'Access-Control-Max-Age' 1728000;
                add_header 'Content-Type' 'text/plain; charset=utf-8';
                add_header 'Content-Length' 0;
                return 204;
            }
            proxy_pass http://llama3-1-7b-a818c603:11434;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
CONFIG

docker cp /tmp/auth.conf ai-router-auth:/etc/nginx/nginx.conf
docker restart ai-router-auth
echo "NGINX fully configured."
