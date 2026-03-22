import os
import django
import subprocess
import uuid

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment
from apps.accounts.models import CustomUser

try:
    llama = Service.objects.filter(name__icontains='llama').first()
    owner = llama.owner
    provider = llama.provider
    project = llama.project

    name = "ai-router-auth"
    domain = f"ai-router-auth-{str(uuid.uuid4())[:6]}.pcloud.linadeluxe.com"

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
}"""
    conf_string = nginx_conf.replace('\n', ' ')

    svc = Service.objects.create(
        name=name,
        owner=owner,
        provider=provider,
        project=project,
        deploy_type="DOCKER",
        docker_image="nginx:alpine",
        public_domain=domain,
        internal_port=4000,
        memory_mb=1024,
        cpu_cores=1.0,
        start_command=f"sh -c \"echo '{nginx_conf}' > /etc/nginx/nginx.conf && nginx -g 'daemon off;'\""
    )

    from apps.deployments.tasks import smart_deploy_task
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print(f"Created new NGINX Auth Router: {domain}")

except Exception as e:
    print(f"Error: {e}")
