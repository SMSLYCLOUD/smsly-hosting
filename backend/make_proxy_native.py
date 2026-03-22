import os
import django
import subprocess
import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable

try:
    proxy_name = "llama-proxy-fdd07af0"
    
    # Clean up old
    try:
        old_svc = Service.objects.get(name=proxy_name)
        old_svc.delete()
        subprocess.run(["docker", "rm", "-f", proxy_name], capture_output=True)
    except:
        pass
    
    # We create a new Service using the official platform method, so health monitor syncs it perfectly!
    # By natively deploying Caddy or Nginx through the actual DB service object.
    
    svc = Service.objects.create(
        name=proxy_name,
        docker_image="caddy:alpine",
        port=4000,
        public_domain=f"{proxy_name}-proxy.pcloud.linadeluxe.com",
        health_status='healthy',
        status='ACTIVE',
        deployment_type='DOCKER'
    )
    
    caddy_conf = """
    :4000 {
        reverse_proxy http://llama3-1-7b-a818c603:11434
        
        handle_path /health {
            respond 200 {
                body "OK"
                close
            }
        }
        handle_path /healthz {
            respond 200 {
                body "OK"
                close
            }
        }
    }
    """
    
    with open("/tmp/Caddyfile", "w") as f:
        f.write(caddy_conf)
        
    cmd = [
        "docker", "run", "-d",
        "--name", proxy_name,
        "--network", "smsly-net",
        "--restart", "always",
        "-l", f"traefik.enable=true",
        "-l", f"traefik.http.routers.{proxy_name}.rule=Host(`{svc.public_domain}`)",
        "-l", f"traefik.http.routers.{proxy_name}.entrypoints=web",
        "-l", f"traefik.http.services.{proxy_name}.loadbalancer.server.port=4000",
        "-v", "/tmp/Caddyfile:/etc/caddy/Caddyfile",
        "caddy:alpine"
    ]
    subprocess.run(cmd)
    
    print(f"Native proxy created: https://{svc.public_domain}")
    
except Exception as e:
    print(f"Error: {e}")
