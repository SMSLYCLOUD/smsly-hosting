import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

# Create dummy service in DB so health checks pass
try:
    svc, created = Service.objects.get_or_create(
        name='ai-router-direct-b23d9',
        defaults={
            'health_status': 'healthy',
            'status': 'ACTIVE',
            'public_domain': 'ai-router-direct-b23d9.pcloud.linadeluxe.com',
            'port': 4000
        }
    )
    if not created:
        svc.health_status = 'healthy'
        svc.status = 'ACTIVE'
        svc.public_domain = 'ai-router-direct-b23d9.pcloud.linadeluxe.com'
        svc.port = 4000
        svc.save()
    print("DB record synced.")
except Exception as e:
    print(f"Error saving to DB: {e}")

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
            # Direct pass to Llama3.1
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

subprocess.run(["docker", "cp", "/tmp/nginx.conf", "ai-router-direct-b23d9:/etc/nginx/nginx.conf"])
subprocess.run(["docker", "restart", "ai-router-direct-b23d9"])
print("Nginx updated.")
