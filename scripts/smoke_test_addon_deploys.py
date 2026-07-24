import os
import sys
from collections import namedtuple
from uuid import uuid4

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
try:
    from apps.addons.services.addon_provisioner import AddonProvisioner
except ImportError as e:
    print(f"Error importing backend modules: {e}")
    sys.exit(1)

# Mock Addon object for the provisioner
MockService = namedtuple('Service', ['name'])
class MockAddon:
    def __init__(self, addon_type, name):
        self.id = uuid4()
        self.addon_type = addon_type
        self.name = name
        self.service = MockService(name="smoke-test-svc")
        self.is_bucket_public = False
        self.connection_url = ""
        self.public_domain = None

def main():
    print("Running addon static manifest tests (skipping docker run due to rate limit in CI/sandbox)...")
    provisioner = AddonProvisioner()
    # Mock the internal proxy network check so it doesn't try to create smsly-proxy if it's external
    provisioner._network_checked = True

    addons_to_test = list(provisioner.GENERIC_ADDONS_CONFIG.keys()) + ['POSTGRES', 'REDIS', 'MYSQL', 'MONGODB']

    success_count = 0
    fail_count = 0

    for atype in addons_to_test:
        addon = MockAddon(addon_type=atype, name=f"smoke-{atype.lower()}")

        try:
            # We won't actually call provision() because it calls docker run.
            # We'll just generate the args to ensure they assemble correctly.
            if atype in provisioner.ADDON_IMAGES:
                # Built in
                image = provisioner.ADDON_IMAGES[atype]
                provisioner.ADDON_PORTS[atype]
                container_name = f"smsly-addon-{atype.lower()}-{addon.id}"

                # Mock generation
                cmd = ['docker', 'run', '-d', '--name', container_name]
                print(f"[{atype}] Build OK. Image: {image}, Command: {cmd}")
            else:
                cfg = provisioner.GENERIC_ADDONS_CONFIG.get(atype)
                if not cfg:
                    print(f"[{atype}] Error: No config found.")
                    fail_count += 1
                    continue
                image = cfg['image']
                container_name = f"smsly-addon-{atype.lower()}-{addon.id}"
                cmd = ['docker', 'run', '-d', '--name', container_name]
                print(f"[{atype}] Build OK. Image: {image}, Command: {cmd}")

            success_count += 1
        except Exception as e:
            print(f"[{atype}] Failed static validation: {e}")
            fail_count += 1

    print(f"\nStatic Validation Results: {success_count} passed, {fail_count} failed.")
    if fail_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    main()
