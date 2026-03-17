import json

def get_docker_image(template_id):
    with open("backend/apps/deployments/fixtures/templates.json", "r") as f:
        templates = json.load(f)

    template = next((t for t in templates if t.get('id') == template_id), None)
    if template:
        return template.get('docker_image')
    return None

print(get_docker_image("ai-router"))
