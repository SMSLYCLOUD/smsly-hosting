import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS_DIR = os.path.join(BASE_DIR, 'frontend', 'public', 'logos', 'addons')
TEMPLATES_DIR = os.path.join(BASE_DIR, 'frontend', 'public', 'logos', 'templates')

def hex_to_rgb(hex_str):
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 3:
        hex_str = ''.join([c*2 for c in hex_str])
    if len(hex_str) == 6:
        return int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    if len(hex_str) == 8:
        return int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
    return 0, 0, 0

def luminance(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b

def analyze_folder(folder_name, folder_path):
    print(f"\n=======================================================")
    print(f"  ANALYZING {folder_name.upper()} LOGOS FOR DARK COLORS")
    print(f"=======================================================")
    
    dark_icons = []
    
    for f in sorted(os.listdir(folder_path)):
        if not f.endswith('.svg'):
            continue
        p = os.path.join(folder_path, f)
        with open(p, 'r', encoding='utf-8', errors='ignore') as fp:
            content = fp.read()
            
        # Find all hex colors
        hexes = re.findall(r'#([A-Fa-f0-9]{3,8})\b', content)
        # Find named colors
        named_dark = re.findall(r'\b(black|#000|#000000)\b', content, re.IGNORECASE)
        named_light = re.findall(r'\b(white|#fff|#ffffff)\b', content, re.IGNORECASE)
        
        has_white_element = bool(named_light) or '<circle' in content and 'fill="#fff' in content.lower()
        
        lum_list = []
        for h in hexes:
            r, g, b = hex_to_rgb(h)
            lum = luminance(r, g, b)
            lum_list.append((h, lum))
            
        # If no hexes found, check if fill is black or currentColor or none
        if not hexes:
            if 'fill="currentColor"' in content or 'fill="black"' in content or 'fill="#000"' in content:
                dark_icons.append((f, "No hex colors, defaults to dark/currentColor", 0, []))
            continue
            
        max_lum = max([lum for _, lum in lum_list]) if lum_list else 0
        min_lum = min([lum for _, lum in lum_list]) if lum_list else 0
        
        # If the brightest color in the icon is dark (< 80) and no white background
        if max_lum < 85 and not has_white_element:
            dark_icons.append((f, f"Max luminance is only {max_lum:.1f} (very dark)", max_lum, hexes[:4]))
        elif max_lum < 110 and not has_white_element:
            # Low contrast warning
            dark_icons.append((f, f"Low luminance {max_lum:.1f}", max_lum, hexes[:4]))

    print(f"Found {len(dark_icons)} dark/low-contrast icons in {folder_name}:")
    for f, reason, lum, cols in dark_icons:
        print(f"  - {f:25} | {reason} | Colors: {cols}")
    return dark_icons

if __name__ == '__main__':
    addons_dark = analyze_folder('addons', ADDONS_DIR)
    templates_dark = analyze_folder('templates', TEMPLATES_DIR)
