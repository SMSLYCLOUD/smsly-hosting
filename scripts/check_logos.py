import os
import re
import xml.etree.ElementTree as ET
import subprocess

def check_dir(dir_path):
    bad = []
    good = []
    for f in sorted(os.listdir(dir_path)):
        full = os.path.join(dir_path, f)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, 'r', encoding='utf-8', errors='ignore') as fp:
                content = fp.read()
            if 'circle cx="12" cy="12" r="4"' in content:
                bad.append((f, 'generic_box_circle'))
            elif '<text' in content and ('system-ui' in content or 'sans-serif' in content):
                bad.append((f, 'colored_box_letter'))
            elif len(content) < 350 and '<path' not in content:
                bad.append((f, 'too_small_no_path'))
            elif f.endswith('.svg'):
                try:
                    ET.fromstring(content)
                    good.append((f, len(content)))
                except Exception as ex:
                    bad.append((f, f'invalid_xml: {ex}'))
            else:
                good.append((f, len(content)))
        except Exception as e:
            bad.append((f, str(e)))
    return good, bad

good_addons, bad_addons = check_dir('frontend/public/logos/addons')
print(f'Addons: {len(good_addons)} good, {len(bad_addons)} bad/generic')
for f, reason in bad_addons:
    print(f'  Bad Addon: {f} ({reason})')

good_tmpl, bad_tmpl = check_dir('frontend/public/logos/templates')
print(f'Templates: {len(good_tmpl)} good, {len(bad_tmpl)} bad/generic')
for f, reason in bad_tmpl:
    print(f'  Bad Template: {f} ({reason})')

# Check ADDON_REGISTRY completeness
print('\n=== Verifying ADDON_REGISTRY references ===')
with open('frontend/src/lib/addonRegistry.ts', 'r', encoding='utf-8') as f:
    ts_content = f.read()

logos_found = re.findall(r"logo:\s*['\"]([^'\"]+)['\"]", ts_content)
missing_registry_logos = []
for logo_url in set(logos_found):
    clean_path = logo_url.lstrip('/')
    local_p = os.path.join('frontend', 'public', clean_path)
    if not os.path.exists(local_p):
        missing_registry_logos.append(logo_url)

if missing_registry_logos:
    print(f'ERROR: {len(missing_registry_logos)} missing logos from ADDON_REGISTRY:')
    for m in missing_registry_logos:
        print(f'  Missing: {m}')
else:
    print(f'All {len(set(logos_found))} logos referenced in ADDON_REGISTRY exist on disk!')
