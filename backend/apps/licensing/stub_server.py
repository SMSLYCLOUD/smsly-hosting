import base64
import datetime
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def validate_stub(license_key, instance_id):
    """
    Simulate the license server response.
    Generates a valid RSA-signed payload using the dev private key.
    """
    # Load dev private key
    key_path = os.path.join(os.path.dirname(__file__), 'keys', 'dev_private.pem')
    if not os.path.exists(key_path):
        raise RuntimeError("Dev private key not found")

    with open(key_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None
        )

    # Determine tier based on key prefix
    if license_key.startswith('smsly_ent_'):
        tier = 'enterprise'
        max_services = -1
        max_team_members = -1
    elif license_key.startswith('smsly_pro_'):
        tier = 'pro'
        max_services = -1
        max_team_members = 5
    else:
        # Invalid key simulation
        raise ValueError("Invalid license key")

    # Construct payload
    payload_dict = {
        "license_id": "lic_stub_" + license_key[-6:],
        "tier": tier,
        "licensed_to": "dev@example.com",
        "instance_id": instance_id,
        "max_services": max_services,
        "max_team_members": max_team_members,
        "features": ["ai", "autoscaler", "custom_domains", "ssl", "marketplace", "functions", "tunnels", "topology", "transfers"],
        "issued_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "expires_at": (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)).isoformat()
    }

    payload_json = json.dumps(payload_dict)
    payload_b64 = base64.b64encode(payload_json.encode()).decode()

    # Sign
    signature = private_key.sign(
        payload_json.encode(),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    signature_b64 = base64.b64encode(signature).decode()

    # Return structure matching what validator expects
    # In online mode, we return the unwrapped fields + the signed blob for offline caching
    license_data = json.dumps({
        "payload": payload_b64,
        "signature": signature_b64
    })

    return {
        **payload_dict,
        "license_data": license_data
    }
