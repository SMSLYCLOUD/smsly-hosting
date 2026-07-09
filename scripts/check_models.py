import json
import subprocess

# Get all running docker containers
result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_option=True, text=True)
containers = result.stdout.strip().split("\n")

models_found = []

for container in containers:
    if "ollama" in container or "qwen" in container or "llama" in container:
        # Check if it exposes port 11434
        inspect = subprocess.run(["docker", "inspect", container], capture_option=True, text=True)
        try:
            data = json.loads(inspect.stdout)
            # Send a request to the internal port
            test = subprocess.run(
                ["docker", "exec", container, "curl", "-s", "http://localhost:11434/api/tags"],
                capture_option=True, text=True
            )
            if test.returncode == 0:
                tags = json.loads(test.stdout).get("models", [])
                model_names = [m["name"] for m in tags]
                models_found.append(f"✅ {container} is RESPONSIVE. Loaded models: {', '.join(model_names)}")
            else:
                # If curl doesn't exist, we use the host to curl it
                ip = data[0]["NetworkSettings"]["Networks"]["smsly-net"]["IPAddress"]
                test2 = subprocess.run(["curl", "-s", f"http://{ip}:11434/api/tags"], capture_option=True, text=True)
                if test2.returncode == 0:
                    tags = json.loads(test2.stdout).get("models", [])
                    model_names = [m["name"] for m in tags]
                    models_found.append(f"✅ {container} is RESPONSIVE. Loaded models: {', '.join(model_names)}")
                else:
                    models_found.append(f"❌ {container} is RUNNING but NOT RESPONSIVE on port 11434.")
        except Exception as e:
            models_found.append(f"❌ {container} failed check: {e}")

print("\n".join(models_found))
