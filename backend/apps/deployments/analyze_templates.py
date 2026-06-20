import collections
import json

with open('backend/apps/deployments/fixtures/templates.json') as f:
    templates = json.load(f)

ids = [t.get('id') for t in templates]
names = [t.get('name') for t in templates]
repos = [t.get('repository_url') for t in templates]

id_counts = collections.Counter(ids)
name_counts = collections.Counter(names)
repo_counts = collections.Counter(repos)

print("Duplicates by ID:")
for id, count in id_counts.items():
    if count > 1:
        print(f"  {id}: {count}")

print("\nDuplicates by Name:")
for name, count in name_counts.items():
    if count > 1:
        print(f"  {name}: {count}")

print("\nDuplicates by Repository URL:")
for repo, count in repo_counts.items():
    if count > 1 and repo:
        print(f"  {repo}: {count}")

# Check for "Generic" presets that might be redundant
print("\nRedundant/Generic Presets:")
generic_keywords = ['node', 'python', 'docker', 'static', 'webapp']
for t in templates:
    name_lower = t.get('name', '').lower()
    if any(k in name_lower for k in generic_keywords) and not t.get('docker_image'):
        print(f"  {t.get('name')} (ID: {t.get('id')}) - Potentially handled by AI Senate")

