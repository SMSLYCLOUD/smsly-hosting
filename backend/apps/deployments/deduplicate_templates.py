import json
import re

with open('backend/apps/deployments/fixtures/templates.json', 'r') as f:
    templates = json.load(f)

print(f"Original count: {len(templates)}")

unique_templates = []
seen_repos = set()
seen_names = set()

# Pattern to catch "Something Preset 01" etc.
preset_pattern = re.compile(r'Preset \d+', re.IGNORECASE)

for t in templates:
    name = t.get('name', '').strip()
    repo = t.get('repository_url', '').strip()
    id_val = t.get('id', '').strip()
    
    # 1. Skip if it's explicitly a numbered preset
    if preset_pattern.search(name):
        continue
        
    # 2. Skip if we've seen this exact ID before
    if id_val and id_val in seen_repos: # Using seen_repos to store IDs now to avoid changing too much
        continue
    
    unique_templates.append(t)
    if id_val:
        seen_repos.add(id_val)

print(f"Cleaned count: {len(unique_templates)}")

with open('backend/apps/deployments/fixtures/templates.json', 'w') as f:
    json.dump(unique_templates, f, indent=2)
