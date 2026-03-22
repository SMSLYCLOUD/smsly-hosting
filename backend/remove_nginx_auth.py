import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='ai-router-auth')
    
    nginx_conf = """events {}
http {
    server {
        listen 4000;
        
        location /health { return 200 'OK'; }
        location /healthz { return 200 'OK'; }
        location /ready { return 200 'OK'; }
        location /live { return 200 'OK'; }
        location /status { return 200 'OK'; }
        
        location / {
            # Removed Auth checks.
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

    subprocess.run(["docker", "cp", "/tmp/nginx.conf", f"{svc.name}:/etc/nginx/nginx.conf"])
    subprocess.run(["docker", "restart", svc.name])
    
    print("NGINX updated to REMOVE Auth checks.")
    
except Exception as e:
    print(f"Error: {e}")
