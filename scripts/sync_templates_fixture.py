import json
import os
import sys
from dataclasses import asdict

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from services.app_templates import APP_TEMPLATES


def main():
    fixture_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'apps', 'deployments', 'fixtures', 'templates.json')
    with open(fixture_path, 'r') as f:
        json.load(f)

    for tpl_id, tpl in APP_TEMPLATES.items():
        # Let's map logo_url specifically to the logos directory
        tpl.logo_url = f"/logos/templates/{tpl_id}.svg"

    out = []
    for tpl_id, tpl in APP_TEMPLATES.items():
        data = asdict(tpl)
        # map for the UI
        data['icon'] = data['logo_url'] # Fallback or map icon to logo_url
        data['repository_url'] = data['docs_url'] or f"https://hub.docker.com/search?q={tpl.docker_image.split(':')[0]}"

        # Env vars transformation
        env_list = []
        for k, v in data.get('env_vars', {}).items():
            env_list.append({"key": k, "value": v, "is_secret": 'PASSWORD' in v or 'SECRET' in v})
        data['env_vars'] = env_list
        out.append(data)

    with open(fixture_path, 'w') as f:
        json.dump(out, f, indent=2)
    print("Synced templates.json with APP_TEMPLATES")

if __name__ == "__main__":
    main()
