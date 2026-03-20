import subprocess
import json

# Get all running docker containers
result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True)
containers = result.stdout.strip().split("\n")

models_found = []

for container in containers:
    if "ollama" in container or "qwen" in container or "llama" in container:
        inspect = subprocess.run(["docker", "inspect", container], capture_output=True, text=True)
        try:
            data = json.loads(inspect.stdout)[0]

            # Find IP address
            networks = data.get("NetworkSettings", {}).get("Networks", {})
            ip = None
            for net_name, net_data in networks.items():
                if net_data.get("IPAddress"):
                    ip = net_data["IPAddress"]
                    break

            if not ip:
                models_found.append(f"❌ {container} has NO IP ADDRESS.")
                continue

            test2 = subprocess.run(["curl", "-s", f"http://{ip}:11434/api/tags"], capture_output=True, text=True)
            if test2.returncode == 0 and test2.stdout:
                tags = json.loads(test2.stdout).get("models", [])
                model_names = [m["name"] for m in tags]
                models_found.append(f"✅ {container} is RESPONSIVE. Loaded models: {', '.join(model_names)}")
            else:
                models_found.append(f"❌ {container} is RUNNING but NOT RESPONSIVE on {ip}:11434.")

        except Exception as e:
            models_found.append(f"❌ {container} failed check: {e}")

if not models_found:
    print("No Ollama/Qwen models found running.")
else:
    print("\n".join(models_found))
