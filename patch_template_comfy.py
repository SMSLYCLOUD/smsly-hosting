import json

with open('backend/apps/deployments/fixtures/templates.json', 'r') as f:
    templates = json.load(f)

has_comfy = any(t['id'] == 'comfyui' for t in templates)
if not has_comfy:
    templates.append({
        "id": "comfyui",
        "name": "ComfyUI (Generative Media)",
        "description": "The most powerful and modular stable diffusion GUI with a graph/nodes interface. Ideal for SDXL and Flux.",
        "icon": "https://raw.githubusercontent.com/comfyanonymous/ComfyUI/master/comfyui_logo.png",
        "category": "intelligence",
        "repository_url": "https://github.com/yanwk/comfyui-boot",
        "docker_image": "yanwk/comfyui-boot:latest",
        "default_port": 8188,
        "min_ram_gb": 16,
        "gpu_required": True,
        "env_vars": [
            {
                "key": "CLI_ARGS",
                "value": "--listen 0.0.0.0",
                "is_secret": False
            }
        ]
    })
    print("Added ComfyUI template")

with open('backend/apps/deployments/fixtures/templates.json', 'w') as f:
    json.dump(templates, f, indent=2)
