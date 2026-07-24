import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
try:
    from apps.deployments.services.app_templates import APP_TEMPLATES, get_docker_run_command
except ImportError as e:
    print(f"Error importing backend modules: {e}")
    sys.exit(1)

def main():
    print("Running template static manifest tests...")

    success_count = 0
    fail_count = 0

    for tpl_id, tpl in APP_TEMPLATES.items():
        try:
            cmd = get_docker_run_command(tpl_id, name=f"smoke-{tpl_id}", domain="test.local")
            if not cmd:
                print(f"[{tpl_id}] Error generating command.")
                fail_count += 1
                continue

            print(f"[{tpl_id}] Build OK. Command: {cmd}")
            success_count += 1
        except Exception as e:
            print(f"[{tpl_id}] Failed static validation: {e}")
            fail_count += 1

    print(f"\nStatic Validation Results: {success_count} passed, {fail_count} failed.")
    if fail_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
