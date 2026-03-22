import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable
try:
    svc = Service.objects.filter(name__icontains='ai-router-cc22').first()
    
    if svc:
        print(f"Target: {svc.name}")
        env_dict = {str(e.key): str(e.value) for e in svc.env_vars.all()}
        
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
        with open("/tmp/nginx_secure.conf", "w") as f:
            f.write(nginx_conf)

        cmd = [
            "docker", "run", "-d", "--name", svc.name,
            "--restart", "unless-stopped",
            "--network", "smsly-net",
            "-v", "/tmp/nginx_secure.conf:/etc/nginx/nginx.conf:ro",
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
        print("Container deployed with Traefik labels.")
        
        svc.status = 'ACTIVE'
        svc.save()
        print("Service marked ACTIVE in DB.")

except Exception as e:
    print(f"Error: {e}")
