import os

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
            # Check for dummy SVGs
            if 'circle cx="12" cy="12" r="4"' in content:
                bad.append((f, 'generic_box_circle'))
            elif '<text' in content and ('system-ui' in content or 'sans-serif' in content):
                bad.append((f, 'colored_box_letter'))
            elif len(content) < 350 and '<path' not in content:
                bad.append((f, 'too_small_no_path'))
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
