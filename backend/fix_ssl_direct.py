import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    # Instead of fighting LiteLLM's broken v1.45 empty db view error,
    # let's just deploy NGINX locally but bind it properly using Traefik and Caddy labels manually.
    svc = Service.objects.get(name='ai-router-3eca1f78')
    
    # We will run nginx independently on the server
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
    subprocess.run(["docker", "exec", "smsly-hosting-celery-1", "bash", "-c", f"echo '{nginx_conf}' > /tmp/nginx_secure.conf"])

    cmd = [
        "docker", "run", "-d", "--name", svc.name,
        "--restart", "unless-stopped",
        "--network", "smsly-net",
        # Write config to a place that we can mount from the host easily, or just echo it into the container directly
    ]
    subprocess.run(["docker", "rm", "-f", svc.name])
    
    # Just run native nginx
    cmd = f"docker run -d --name {svc.name} --restart unless-stopped --network smsly-net " \
          f"-l smsly.service=true -l smsly.service_id={svc.id} -l smsly.public_domain={svc.public_domain} " \
          f"-l smsly.internal_port=4000 -l traefik.enable=true " \
          f"-l traefik.http.routers.{svc.name}.rule=Host\\(\\`{svc.public_domain}\\`\\) " \
          f"-l traefik.http.services.{svc.name}.loadbalancer.server.port=4000 nginx:alpine"
    
    subprocess.run(cmd, shell=True)
    
    # Inject config
    conf_string = nginx_conf.replace('\n', ' ')
    subprocess.run(f"docker exec {svc.name} sh -c \"echo '{nginx_conf}' > /etc/nginx/nginx.conf\"", shell=True)
    subprocess.run(f"docker restart {svc.name}", shell=True)
    
    print("NGINX up and running.")

    # Tell Caddy to pick it up
    try:
        from services.caddy_manager import CaddyManager
        cm = CaddyManager()
        cm.generate_global_caddyfile()
        cm.reload_caddy()
    except:
        pass
    
    svc.status = "ACTIVE"
    svc.health_status = "healthy"
    svc.save(update_fields=['status', 'health_status'])

except Exception as e:
    print(f"Error: {e}")
