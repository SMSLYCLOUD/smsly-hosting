import json

file_path = 'backend/apps/deployments/fixtures/templates.json'
with open(file_path, 'r') as f:
    data = json.load(f)

for template in data:
    if template.get("id") == "ai-router":
        template["docker_image"] = "ghcr.io/berriai/litellm:main-v1.45.0"
        template["required_addons"] = []

        # Filter out DATABASE_URL if present, add STORE_MODEL_IN_DB=False, DISABLE_SCHEMA_UPDATE=true
        new_env = []
        found_sk = False
        found_store = False
        found_disable = False
        for env in template.get("env_vars", []):
            k = env["key"]
            if k == "DATABASE_URL":
                continue
            if k == "STORE_MODEL_IN_DB":
                env["value"] = "False"
                found_store = True
            if k == "DISABLE_SCHEMA_UPDATE":
                env["value"] = "true"
                found_disable = True
            if k == "LITELLM_MASTER_KEY":
                env["value"] = "sk-${RANDOM_PASSWORD}"
                found_sk = True
            new_env.append(env)

        if not found_store:
            new_env.append({"key": "STORE_MODEL_IN_DB", "value": "False", "is_secret": False})
        if not found_disable:
            new_env.append({"key": "DISABLE_SCHEMA_UPDATE", "value": "true", "is_secret": False})

        template["env_vars"] = new_env

with open(file_path, 'w') as f:
    json.dump(data, f, indent=2)
