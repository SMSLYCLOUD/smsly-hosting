import os
import sys

# Add backend to path so we can import services
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
try:
    from apps.addons.services.addon_provisioner import AddonProvisioner
    from apps.deployments.services.app_templates import APP_TEMPLATES
except ImportError as e:
    print(f"Error importing backend modules: {e}")
    sys.exit(1)

def generate_matrix():
    matrix_lines = []
    matrix_lines.append("# Addon & Template Certification Matrix\n")
    matrix_lines.append("| Type | ID/Slug | Name | Category | Logo Path | Logo Type | Docker Image | Docs Link | Target Ports | Final Status |")
    matrix_lines.append("|---|---|---|---|---|---|---|---|---|---|")

    # Process Addons
    provisioner = AddonProvisioner()
    for addon_id, config in provisioner.GENERIC_ADDONS_CONFIG.items():
        name = addon_id.title()
        image = config.get('image', 'N/A')
        port = str(config.get('port', 'N/A'))
        if 'dashboard_port' in config:
            port += f" (Dash: {config['dashboard_port']})"

        matrix_lines.append(f"| Addon | {addon_id} | {name} | Database | /logos/addons/{addon_id.lower()}.svg | TBA | {image} | TBA | {port} | PENDING |")

    # Process Templates
    for tpl_id, tpl in APP_TEMPLATES.items():
        image = tpl.docker_image
        port = str(tpl.default_port)
        docs = tpl.docs_url if hasattr(tpl, 'docs_url') else 'N/A'

        matrix_lines.append(f"| Template | {tpl_id} | {tpl.name} | {tpl.category} | /logos/templates/{tpl_id}.svg | TBA | {image} | {docs} | {port} | PENDING |")

    out_path = os.path.join(os.path.dirname(__file__), '..', 'docs', 'ADDON_TEMPLATE_CERTIFICATION_MATRIX.md')
    with open(out_path, 'w') as f:
        f.write('\n'.join(matrix_lines))
    print(f"Generated initial matrix at {out_path}")

if __name__ == "__main__":
    os.makedirs(os.path.join(os.path.dirname(__file__), '..', 'docs'), exist_ok=True)
    generate_matrix()
