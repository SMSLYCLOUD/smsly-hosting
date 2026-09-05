import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ELASTICSEARCH_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
    <path fill="#00A9E0" d="M4 64c0 5.535.777 10.879 2.098 16H84c8.836 0 16-7.164 16-16s-7.164-16-16-16H6.098A63.738 63.738 0 0 0 4 64"/>
    <path fill="#FEC514" d="M111.695 30.648A61.485 61.485 0 0 0 117.922 24C106.188 9.379 88.199 0 68 0 42.715 0 20.957 14.71 10.574 36H98.04a20.123 20.123 0 0 0 13.652-5.352"/>
    <path fill="#00BFB3" d="M98.04 92H10.577C20.961 113.29 42.715 128 68 128c20.2 0 38.188-9.383 49.922-24a61.1 61.1 0 0 0-6.227-6.648A20.133 20.133 0 0 0 98.04 92"/>
</svg>'''

RECOLOR_MAP = {
    # Addons & Templates
    'kafka.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#00A8E8"'},
    'temporal.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#11E58B"'},
    'mariadb.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#00A8CF"'},
    'ferretdb.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#FF5B35"'},
    'keycloak.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#008CE5"'},
    'valkey.svg': {'old': r'fill="#123678"', 'new': 'fill="#2B7FFF"'},
    'victoriametrics.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#D946EF"'},
    'opensearch.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#00A3E0"'},
    'mysql.svg': {'old': r'fill="#00618A"', 'new': 'fill="#0096D6"'},
    # Templates specific
    'anythingllm.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#00D2FF"'},
    'ghost.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#FFFFFF"'},
    'plane.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#3F76FF"'},
    'outline.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#0060FF"'},
    'drone.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#F7931E"'},
    'directus.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#6644FF"'},
    'langsmith.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#10B981"'},
    'focalboard.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#1D7BF5"'},
    'mattermost.svg': {'old': r'fill=[\'"][^\'"]+[\'"]', 'new': 'fill="#1D7BF5"'},
}

def update_file(p, filename):
    if not os.path.exists(p):
        return
    if filename == 'elasticsearch.svg':
        with open(p, 'w', encoding='utf-8') as f:
            f.write(ELASTICSEARCH_SVG)
        print(f"  [REPLACED] {p} with vibrant 3-color Elastic vector")
        return

    if filename not in RECOLOR_MAP:
        return

    cfg = RECOLOR_MAP[filename]
    with open(p, 'r', encoding='utf-8') as f:
        content = f.read()

    new_content = re.sub(cfg['old'], cfg['new'], content, count=1)
    if new_content != content:
        with open(p, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"  [RECOLORED] {p} -> {cfg['new']}")
    else:
        print(f"  [NO CHANGE] {p}")

def main():
    dirs = [
        os.path.join(BASE_DIR, 'frontend', 'public', 'logos', 'addons'),
        os.path.join(BASE_DIR, 'frontend', 'public', 'logos', 'templates'),
        os.path.join(BASE_DIR, 'backend', 'apps', 'deployments', 'static', 'logos', 'addons'),
        os.path.join(BASE_DIR, 'backend', 'apps', 'deployments', 'static', 'logos', 'templates'),
    ]

    targets = list(RECOLOR_MAP.keys()) + ['elasticsearch.svg']

    for d in dirs:
        if not os.path.exists(d):
            continue
        print(f"\nProcessing {d}...")
        for t in targets:
            p = os.path.join(d, t)
            update_file(p, t)

if __name__ == '__main__':
    main()
