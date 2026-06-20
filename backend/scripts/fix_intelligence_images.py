"""Fix broken Intelligence template Docker image names."""
import json
import os

path = os.path.join(os.path.dirname(__file__), '..', 'apps', 'deployments', 'fixtures', 'templates.json')

with open(path, encoding='utf-8') as f:
    data = json.load(f)

# Map of broken template IDs to corrected Docker images
fixes = {
    'khoj': 'ghcr.io/khoj-ai/khoj:latest',
    'memgpt': 'letta/letta:latest',  # MemGPT was renamed to Letta
    'gpt4all': 'nomic-ai/gpt4all:latest',  # Remove - no official Docker image
    'privategpt': 'zylon-ai/private-gpt:latest',  # Verify exists
    'sd-next': 'ghcr.io/vladmandic/automatic:latest',
}

# Templates to REMOVE (no valid Docker image exists)
remove_ids = {'gpt4all'}  # No official Docker web UI image

fixed = 0
removed = 0
new_data = []

for t in data:
    tid = t.get('id', '')

    if tid in remove_ids:
        print(f"  REMOVED: {tid} (no valid Docker image)")
        removed += 1
        continue

    if tid in fixes:
        old = t.get('docker_image', '')
        t['docker_image'] = fixes[tid]
        print(f"  FIXED: {tid}: {old} -> {fixes[tid]}")
        fixed += 1

    new_data.append(t)

with open(path, 'w', encoding='utf-8') as f:
    json.dump(new_data, f, indent=2, ensure_ascii=False)

print(f"\nDone: {fixed} fixed, {removed} removed. Total templates: {len(new_data)}")
