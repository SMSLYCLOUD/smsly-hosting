import os
import sys


def main():
    docs_dir = os.path.join(os.path.dirname(__file__), '..', 'docs')
    matrix_path = os.path.join(docs_dir, 'ADDON_TEMPLATE_CERTIFICATION_MATRIX.md')
    frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'public')

    if not os.path.exists(matrix_path):
        print("Matrix file not found. Run audit first.")
        sys.exit(1)

    missing = []
    with open(matrix_path, 'r') as f:
        lines = f.readlines()
        for line in lines[3:]:
            parts = [p.strip() for p in line.split('|')]
            if len(parts) > 5:
                logo_path = parts[5]
                # Convert '/logos/addons/mysql.svg' -> 'frontend/public/logos/addons/mysql.svg'
                if logo_path.startswith('/'):
                    local_path = os.path.join(frontend_dir, logo_path[1:])
                    if not os.path.exists(local_path):
                        missing.append(logo_path)

    if missing:
        print("Missing assets:")
        for m in missing:
            print(f" - {m}")
        sys.exit(1)
    else:
        print("All assets exist.")

if __name__ == "__main__":
    main()
