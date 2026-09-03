import os
import requests
import shutil

ASSETS = {
    'redis': {
        'url': 'https://cdn.jsdelivr.net/gh/devicons/devicon@master/icons/redis/redis-original.svg',
        'addons': True,
        'templates': True
    },
    'rabbitmq': {
        'url': 'https://cdn.jsdelivr.net/gh/devicons/devicon@master/icons/rabbitmq/rabbitmq-original.svg',
        'addons': True,
        'templates': True,
        'inject_eye': True
    },
    'mongodb': {
        'url': 'https://cdn.jsdelivr.net/gh/devicons/devicon@master/icons/mongodb/mongodb-original.svg',
        'addons': True,
        'templates': True
    },
    'mysql': {
        'url': 'https://cdn.jsdelivr.net/gh/devicons/devicon@master/icons/mysql/mysql-original.svg',
        'addons': True,
        'templates': True
    }
}

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frontend_addons = os.path.join(base_dir, 'frontend', 'public', 'logos', 'addons')
    frontend_templates = os.path.join(base_dir, 'frontend', 'public', 'logos', 'templates')
    backend_addons = os.path.join(base_dir, 'backend', 'apps', 'deployments', 'static', 'logos', 'addons')
    backend_templates = os.path.join(base_dir, 'backend', 'apps', 'deployments', 'static', 'logos', 'templates')

    for name, config in ASSETS.items():
        print(f"Fetching {name} from {config['url']}...")
        r = requests.get(config['url'], timeout=15)
        if r.status_code != 200 or len(r.content) < 100:
            print(f"Failed to fetch {name}: HTTP {r.status_code}")
            continue

        content = r.text
        if config.get('inject_eye'):
            # In RabbitMQ, add the bright white eye rect right after <svg ...>
            rect_eye = '<rect x="76.268" y="77.376" width="24.391" height="24.39" rx="6.524" fill="#FFFFFF"/>'
            svg_tag_end = content.find('>')
            if svg_tag_end != -1:
                content = content[:svg_tag_end+1] + '\n  ' + rect_eye + content[svg_tag_end+1:]

        filename = f"{name}.svg"
        if config.get('addons'):
            for d in [frontend_addons, backend_addons]:
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, filename)
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  [OK] Saved to {p} ({len(content)}b)")

        if config.get('templates'):
            for d in [frontend_templates, backend_templates]:
                os.makedirs(d, exist_ok=True)
                p = os.path.join(d, filename)
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"  [OK] Saved to {p} ({len(content)}b)")

if __name__ == '__main__':
    main()
