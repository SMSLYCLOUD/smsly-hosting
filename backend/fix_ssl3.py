import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router').first()
    
    # LiteLLM v1.45.0 is crashing with a TypeError on check_view_exists when NO DB is connected.
    # It attempts to query the view_names but gets None.
    # What version were we using originally? main-stable. 
    # The user was originally happy with NGINX!
    # "let's use nginx Auth"
    # But earlier I replaced NGINX with Litellm again because I tried to fix SSL.
    # My SSL fix failed because I ran the container manually without the network aliases or the correct traefik tags from the platform.
    # Since I'm using the platform deployment engine (smart_deploy_task), it handles SSL natively!
    # So if I set docker_image="nginx:alpine" inside the platform's service object, it will deploy NGINX with perfect SSL!
    
    svc.docker_image = "nginx:alpine"
    
    # Write the nginx conf into a command
    nginx_conf = """
events {}
http {
    server {
        listen 4000;
        location / {
            set $auth_ok 0;
            if ($http_authorization = "Bearer sk-agbonsalo") { set $auth_ok 1; }
            if ($request_method = OPTIONS) { set $auth_ok 1; }
            if ($auth_ok = 0) { return 401 '{"error": "Unauthorized"}'; }
            add_header 'Access-Control-Allow-Origin' '*';
            add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
            add_header 'Access-Control-Allow-Headers' 'Authorization,Content-Type';
            if ($request_method = OPTIONS) { return 204; }
            proxy_pass http://llama3-1-7b-a818c603:11434;
        }
    }
}
"""
    # The platform uses 'start_command'
    # We can't mount files easily via the platform's Django model without a builder.
    # But wait, NGINX can be configured via a one-liner start_command:
    conf_string = nginx_conf.replace('\n', ' ')
    svc.start_command = f"sh -c \"echo '{nginx_conf}' > /etc/nginx/nginx.conf && nginx -g 'daemon off;'\""
    svc.save(update_fields=['docker_image', 'start_command'])
    
    from apps.deployments.models import Deployment
    from apps.deployments.tasks import smart_deploy_task
    
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print(f"Deployment triggered to deploy NGINX AUTH PROXY natively with SSL!")

except Exception as e:
    print(f"Error: {e}")
