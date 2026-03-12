import json
import os

TEMPLATES_PATH = r'c:\Users\osaretin\Documents\SMSLY\SMSLY_UTILS\smsly-hosting\backend\apps\deployments\fixtures\templates.json'

def process_templates():
    with open(TEMPLATES_PATH, 'r', encoding='utf-8') as f:
        templates = json.load(f)

    addon_to_env = {
        'POSTGRES': ('DATABASE_URL', '${DATABASE_URL}'),
        'REDIS': ('REDIS_URL', '${REDIS_URL}'),
        'MYSQL': ('MYSQL_URL', '${MYSQL_URL}'),
        'MONGODB': ('MONGO_URL', '${MONGODB_URL}'), # LibreChat and RocketChat use MONGO_URL often
        'ELASTICSEARCH': ('ELASTICSEARCH_URL', '${ELASTICSEARCH_URL}'),
    }

    modified_count = 0
    for t in templates:
        required = t.get('required_addons', [])
        if not required:
            continue
        
        env_vars = t.get('env_vars', [])
        existing_keys = {ev.get('key') for ev in env_vars}
        
        for addon in required:
            if addon in addon_to_env:
                key, value = addon_to_env[addon]
                
                # Check if it already has something for this addon
                # (Sometimes they use different keys, e.g. MONGODB_URI)
                # We prioritize existing ones but if missing, we add our standard one.
                if key not in existing_keys:
                    # Special check for MONGODB_URI vs MONGO_URL
                    if addon == 'MONGODB' and 'MONGODB_URI' in existing_keys:
                        continue
                    
                    env_vars.append({
                        "key": key,
                        "value": value,
                        "is_secret": True
                    })
                    modified_count += 1
        
        t['env_vars'] = env_vars

    with open(TEMPLATES_PATH, 'w', encoding='utf-8') as f:
        json.dump(templates, f, indent=2)
    
    print(f"Processed {len(templates)} templates. Added {modified_count} env variables.")

if __name__ == "__main__":
    process_templates()
