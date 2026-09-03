import os
import json
import re
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PUBLIC = os.path.join(BASE_DIR, 'frontend', 'public')
ADDONS_DIR = os.path.join(FRONTEND_PUBLIC, 'logos', 'addons')
TEMPLATES_DIR = os.path.join(FRONTEND_PUBLIC, 'logos', 'templates')
FIXTURES_PATH = os.path.join(BASE_DIR, 'backend', 'apps', 'deployments', 'fixtures', 'templates.json')
REGISTRY_PATH = os.path.join(BASE_DIR, 'frontend', 'src', 'lib', 'addonRegistry.ts')

def audit_svg(file_path):
    issues = []
    if not os.path.exists(file_path):
        return ["FILE_NOT_FOUND"]
    
    size = os.path.getsize(file_path)
    if size == 0:
        return ["EMPTY_FILE"]

    with open(file_path, 'rb') as f:
        head = f.read(30)
    
    # Check if binary (PNG, ICO, etc.)
    if head.startswith(b'\x89PNG'):
        return ["BINARY_PNG_WITH_SVG_EXT"]
    if head.startswith(b'\x00\x00\x01\x00'):
        return ["BINARY_ICO_WITH_SVG_EXT"]
    if b'<!DOCTYPE html>' in head or b'<html' in head:
        return ["HTML_SCRAPE_ERROR_PAGE"]

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        return [f"READ_ERROR: {e}"]

    # Parse XML
    try:
        root = ET.fromstring(content)
    except Exception as e:
        issues.append(f"INVALID_XML: {e}")

    # Detect generic placeholders
    if 'circle cx="12" cy="12" r="4"' in content:
        issues.append("GENERIC_DUMMY_BOX_CIRCLE")
    if '<text' in content and ('system-ui' in content or 'sans-serif' in content) and len(content) < 800:
        # Check if it's just a letter inside a box
        text_matches = re.findall(r'<text[^>]*>([^<]+)</text>', content)
        if any(len(t.strip()) <= 3 for t in text_matches):
            issues.append(f"COLORED_LETTER_BOX: {text_matches}")
    
    # Check for pure black icons without background that disappear on dark mode
    # e.g., fill="#000" or fill="#000000" or fill="black" with no contrasting background
    if size < 800:
        has_white_bg = any(w in content for w in ['fill="#fff"', 'fill="#FFF"', 'fill="#ffffff"', 'fill="#FFFFFF"', 'fill="white"', '<rect', 'style="fill:#fff'])
        if ('fill="#000"' in content or 'fill="#000000"' in content or 'fill="black"' in content) and not has_white_bg:
            issues.append("PURE_BLACK_ON_DARK_RISK")

    # Check for suspiciously tiny SVG (< 250 bytes)
    if size < 250 and not issues:
        issues.append(f"SUSPICIOUSLY_TINY: {size}b")

    return issues

def main():
    print("==================================================")
    print("  COMPREHENSIVE AUDIT: ADDONS & TEMPLATES LOGOS")
    print("==================================================")

    # 1. Audit Addons from addonRegistry.ts
    print("\n--- 1. AUDITING ADDON REGISTRY (frontend/src/lib/addonRegistry.ts) ---")
    with open(REGISTRY_PATH, 'r', encoding='utf-8') as f:
        registry_text = f.read()

    addon_items = re.findall(r"id:\s*['\"]([^'\"]+)['\"].*?name:\s*['\"]([^'\"]+)['\"].*?logo:\s*['\"]([^'\"]+)['\"]", registry_text, re.DOTALL)
    print(f"Found {len(addon_items)} addon entries in addonRegistry.ts")
    
    addon_issues_count = 0
    for aid, name, logo in addon_items:
        clean_logo = logo.lstrip('/')
        full_path = os.path.join(FRONTEND_PUBLIC, clean_logo)
        issues = audit_svg(full_path)
        if issues:
            addon_issues_count += 1
            print(f"  [ISSUE] Addon '{name}' ({aid}) -> {logo}: {', '.join(issues)}")
        else:
            sz = os.path.getsize(full_path)
            # print(f"  [OK] {name:20} -> {logo} ({sz}b)")

    if addon_issues_count == 0:
        print("  -> ALL ADDON REGISTRY ICONS PASSED AUDIT (0 issues)!")
    else:
        print(f"  -> Total addon registry issues: {addon_issues_count}")

    # 2. Audit Templates from fixtures/templates.json
    print("\n--- 2. AUDITING TEMPLATES FIXTURES (templates.json) ---")
    with open(FIXTURES_PATH, 'r', encoding='utf-8-sig') as f:
        fixtures_data = json.load(f)

    print(f"Found {len(fixtures_data)} template fixtures")
    template_issues_count = 0
    checked_logos = set()

    for item in fixtures_data:
        name = item.get('name', 'Unknown')
        slug = item.get('id') or item.get('slug', 'Unknown')
        logo_url = item.get('logo_url') or item.get('icon')
        if not logo_url:
            print(f"  [ISSUE] Template '{name}' ({slug}) has NO logo_url or icon defined!")
            template_issues_count += 1
            continue

        if logo_url in checked_logos:
            continue
        checked_logos.add(logo_url)

        if logo_url.startswith('/'):
            full_path = os.path.join(FRONTEND_PUBLIC, logo_url.lstrip('/'))
            issues = audit_svg(full_path)
            if issues:
                template_issues_count += 1
                print(f"  [ISSUE] Template '{name}' ({slug}) -> {logo_url}: {', '.join(issues)}")
        elif logo_url.startswith('http'):
            # Remote URL
            pass
        else:
            print(f"  [WARN] Non-standard logo URL for '{name}': {logo_url}")

    print(f"  Checked {len(checked_logos)} distinct template logo paths.")
    if template_issues_count == 0:
        print("  -> ALL TEMPLATE FIXTURE ICONS PASSED AUDIT (0 issues)!")
    else:
        print(f"  -> Total template fixture issues: {template_issues_count}")

    # 3. Check All Physical Files in folders
    print("\n--- 3. CHECKING ALL PHYSICAL FILES IN FOLDERS ---")
    for folder_name, folder_path in [('addons', ADDONS_DIR), ('templates', TEMPLATES_DIR)]:
        files = os.listdir(folder_path)
        folder_issues = 0
        svg_count = 0
        for f in files:
            if not f.endswith('.svg'):
                continue
            svg_count += 1
            p = os.path.join(folder_path, f)
            if not os.path.isfile(p):
                continue
            issues = audit_svg(p)
            if issues:
                folder_issues += 1
                print(f"  [FILE ISSUE] {folder_name}/{f}: {', '.join(issues)}")
        print(f"  Folder '{folder_name}' has {svg_count} SVGs ({len(files)} total files). Total issues: {folder_issues}")

if __name__ == '__main__':
    main()
