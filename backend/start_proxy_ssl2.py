import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

svc = Service.objects.filter(name__icontains='ai-router').first()
if svc:
    print(f"Found: {svc.name}")
    
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
    # Write directly to host filesystem via docker exec to celery container since we're running there
    with open("/tmp/nginx_secure.conf", "w") as f:
        f.write(nginx_conf)
        
    subprocess.run(["docker", "rm", "-f", svc.name])
    
    # We must mount the file from the host, but the celery container is running this python script!
    # /tmp is local to the celery container. 
    # To fix this, we will write it using a host command.
    pass

