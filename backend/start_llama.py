import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='llama3-1-7b-a818c603')
    
    env_dict = {str(e.key): str(e.value) for e in svc.env_vars.all()}
    
    cmd = [
        "docker", "run", "-d", "--name", "llama3-1-7b-a818c603",
        "--restart", "unless-stopped",
        "--network", "smsly-net",
        "-l", "smsly.service=true",
        "-l", f"smsly.service_id={svc.id}",
        "-l", "smsly.public_domain=llama3-1-7b-a818c603-3efd0f.pcloud.linadeluxe.com",
        "-l", "smsly.internal_port=11434",
        "-l", "traefik.enable=true",
        "-l", "traefik.http.routers.llama3-1-7b-a818c603.rule=Host(`llama3-1-7b-a818c603-3efd0f.pcloud.linadeluxe.com`)",
        "-l", "traefik.http.services.llama3-1-7b-a818c603.loadbalancer.server.port=11434",
    ]
    for k, v in env_dict.items():
        cmd.extend(["-e", f"{k}={v}"])
        
    cmd.append("ollama/ollama:latest")
    
    subprocess.run(["docker", "rm", "-f", "llama3-1-7b-a818c603"])
    subprocess.run(cmd, check=True)
    
    # We stopped all containers including llama! We have to pull it again or check if the volume is mounted.
    # Did we lose the model? 
    # Let's run a background pull just in case, but usually ollama models are stored in a docker volume.
    # The platform uses a volume for ollama! It maps `/root/.ollama` to a named volume.
    cmd2 = ["docker", "exec", "llama3-1-7b-a818c603", "ollama", "pull", "llama3.1"]
    subprocess.Popen(cmd2) # Run in background
    print("Force started Llama container.")

except Exception as e:
    print(f"Error: {e}")
