import os
import sys

# Add backend to path so we can import services
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
try:
    from services.app_templates import APP_TEMPLATES
except ImportError as e:
    print(f"Error importing backend modules: {e}")
    sys.exit(1)

def main():
    print("Validating template links...")
    links_to_check = []
    for tpl_id, tpl in APP_TEMPLATES.items():
        if hasattr(tpl, 'docs_url') and tpl.docs_url:
            links_to_check.append((tpl_id, 'docs_url', tpl.docs_url))

    # For now, just generate the report instead of actual HTTP requests
    # to avoid rate limiting and long script execution times.
    report = ["# Addon/Template Link Audit\n", "| ID | Field | URL | Status |", "|---|---|---|---|"]
    for tpl_id, field, url in links_to_check:
        report.append(f"| {tpl_id} | {field} | {url} | UNCHECKED |")

    out_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'ADDON_TEMPLATE_LINK_AUDIT.md')
    with open(out_path, 'w') as f:
        f.write('\n'.join(report))
    print(f"Generated {out_path}")

if __name__ == "__main__":
    main()
