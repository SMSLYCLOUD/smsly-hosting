import json
import os

path = os.path.join(os.path.dirname(__file__), '..', 'apps', 'deployments', 'fixtures', 'templates.json')
with open(path, encoding='utf-8') as f:
    data = json.load(f)

intl = [t for t in data if t.get('category') == 'intelligence']
print(f"Found {len(intl)} intelligence templates:")
for t in intl:
    print(f"  {t['id']}: {t.get('docker_image', 'N/A')}")

if not intl:
    # Check all unique categories
    cats = {t.get('category', 'NONE') for t in data}
    print(f"\nAll categories: {sorted(cats)}")
    # Check last 25 templates
    print("\nLast 25 templates:")
    for t in data[-25:]:
        print(f"  {t['id']}: cat={t.get('category')}, img={t.get('docker_image', 'N/A')}")
