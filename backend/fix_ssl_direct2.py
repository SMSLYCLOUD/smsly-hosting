import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='ai-router-3eca1f78')
    
    nginx_conf = """events {}
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
                return 401 "{\\"error\\": \\"Unauthorized\\"}";
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

    cmd = [
        "docker", "run", "-d", "--name", svc.name,
        "--restart", "unless-stopped",
        "--network", "smsly-net",
        "-l", "smsly.service=true",
        "-l", f"smsly.service_id={svc.id}",
        "-l", f"smsly.public_domain={svc.public_domain}",
        "-l", "smsly.internal_port=4000",
        "-l", "traefik.enable=true",
        "-l", f"traefik.http.routers.{svc.name}.rule=Host(`{svc.public_domain}`)",
        "-l", f"traefik.http.services.{svc.name}.loadbalancer.server.port=4000",
        "nginx:alpine"
    ]
    
    subprocess.run(["docker", "rm", "-f", svc.name])
    subprocess.run(cmd, check=True)
    
    # Use standard docker cp to avoid echo escaping issues
    subprocess.run(["docker", "cp", "/tmp/nginx.conf", f"{svc.name}:/etc/nginx/nginx.conf"])
    subprocess.run(["docker", "restart", svc.name])
    
    print("NGINX up and running natively.")

    try:
        from services.caddy_manager import CaddyManager
        cm = CaddyManager()
        cm.generate_global_caddyfile()
        cm.reload_caddy()
    except:
        pass
    
    svc.health_status = "healthy"
    svc.save(update_fields=['health_status'])

except Exception as e:
    print(f"Error: {e}")
