import json

with open("backend/apps/deployments/fixtures/templates.json", "r") as f:
    templates = json.load(f)

for t in templates:
    if t.get("id") == "ai-router":
        print(f"Found ai-router: docker_image={t.get('docker_image')}")
