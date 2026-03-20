import subprocess

nginx_conf = """
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
                return 401 '{"error": "Unauthorized"}';
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
"""
with open("/tmp/nginx.conf", "w") as f:
    f.write(nginx_conf)

subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])

cmd = [
    "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
    "--restart", "unless-stopped",
    "--network", "smsly-net",
    "-v", "/tmp/nginx.conf:/etc/nginx/nginx.conf:ro",
    "-l", "smsly.service=true",
    "-l", "smsly.service_id=fb6b6469-ecf4-4261-b46c-c7a86480b2cf",
    "-l", "smsly.public_domain=ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com",
    "-l", "smsly.internal_port=4000",
    "nginx:alpine"
]

subprocess.run(cmd, check=True)

try:
    import sys
    sys.path.append("/opt/smsly-hosting/backend")
    import os
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    from services.caddy_manager import CaddyManager
    cm = CaddyManager()
    cm.generate_global_caddyfile()
    cm.reload_caddy()
except:
    pass
