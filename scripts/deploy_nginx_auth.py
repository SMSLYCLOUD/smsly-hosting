import subprocess
import os

# We will replace the ai-router entirely with a tiny Nginx proxy container that enforces a Bearer token
nginx_conf = """
events {}

http {
    server {
        listen 4000;

        location / {
            # Check for specific Authorization header
            set $auth_ok "0";
            # Replace with your own secret token
            if ($http_authorization = "Bearer YOUR_SECRET_TOKEN") {
                set $auth_ok "1";
            }
            if ($request_method = "OPTIONS") {
                set $auth_ok "1";
            }

            if ($auth_ok = "0") {
                return 401 "{\"error\": \"Unauthorized\"}";
            }

            # Pre-flight CORS headers
            add_header 'Access-Control-Allow-Origin' '*';
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
            add_header 'Access-Control-Allow-Headers' 'DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization';

            if ($request_method = 'OPTIONS') {
                add_header 'Access-Control-Max-Age' 1728000;
                add_header 'Content-Type' 'text/plain; charset=utf-8';
                add_header 'Content-Length' 0;
                return 204;
            }

            # Proxy to the Llama 3.1 container's internal port
            proxy_pass http://llama3-1-7b-a818c603:11434;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
"""

with open("/tmp/nginx.conf", "w") as f:
    f.write(nginx_conf)

subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])

# Boot an Nginx container masquerading as the ai-router to inherit all its Traefik public domain routing
cmd = [
    "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
    "--restart", "unless-stopped",
    "--network", "smsly-net",
    "-v", "/tmp/nginx.conf:/etc/nginx/nginx.conf:ro",
    "-l", "smsly.service=true",
    "-l", "smsly.public_domain=ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com",
    "-l", "smsly.internal_port=4000",
    "-l", "traefik.enable=true",
    "-l", "traefik.http.routers.ai-router-cc22a7a5.rule=Host(`ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com`)",
    "-l", "traefik.http.services.ai-router-cc22a7a5.loadbalancer.server.port=4000",
    "nginx:alpine"
]

subprocess.run(cmd, check=True)
print("NGINX Auth Proxy deployed successfully on the ai-router domain!")
