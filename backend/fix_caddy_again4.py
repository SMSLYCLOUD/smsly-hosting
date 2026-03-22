import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router-auth').first()
    
    svc.docker_image = "nginx:alpine"
    
    nginx_conf = """
events {}
http {
    server {
        listen 4000;
        location / {
            set $auth_ok 0;
            if ($http_authorization = "Bearer sk-agbonsalo") { set $auth_ok 1; }
            if ($request_method = OPTIONS) { set $auth_ok 1; }
            if ($request_uri ~* "^/(health|status|live|ready|healthz)$") { set $auth_ok 1; }
            if ($auth_ok = 0) { return 401 "{\\"error\\": \\"Unauthorized\\"}"; }
            add_header 'Access-Control-Allow-Origin' '*';
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
            add_header 'Access-Control-Allow-Headers' 'Authorization,Content-Type';
            if ($request_method = OPTIONS) { return 204; }
            proxy_pass http://llama3-1-7b-a818c603:11434;
        }
    }
}
"""
    svc.start_command = f"sh -c \"echo '{nginx_conf.replace(chr(10), ' ')}' > /etc/nginx/nginx.conf && nginx -g 'daemon off;'\""
    svc.save(update_fields=['docker_image', 'start_command'])
    
    subprocess.run(["docker", "rm", "-f", svc.name])
    
    from apps.deployments.models import Deployment
    from apps.deployments.tasks import smart_deploy_task
    
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print(f"Deployment triggered natively for {svc.name}!")

except Exception as e:
    print(f"Error: {e}")
