import base64
import hashlib
import json
import logging
import os
import uuid
from datetime import timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.utils import timezone

from .models import PlatformLicense, PlatformTier

logger = logging.getLogger(__name__)

PUBLIC_KEY_PATH = os.path.join(os.path.dirname(__file__), 'keys', 'public.pem')

def get_instance_id():
    """Generate deterministic instance fingerprint."""
    # Try to persist in a file so it doesn't change on restart if logic changes
    install_dir = os.environ.get('INSTALL_DIR', '/app')
    id_file = os.path.join(install_dir, '.instance_id')

    if os.path.exists(id_file):
        try:
            with open(id_file) as f:
                return f.read().strip()
        except OSError as exc:
            logger.debug("Failed to read instance ID file: %s", exc)

    # Generate from machine-id + random salt
    machine_id = ''
    for path in ['/etc/machine-id', '/var/lib/dbus/machine-id']:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    machine_id = f.read().strip()
                break
            except OSError as exc:
                logger.debug("Failed to read machine-id from %s: %s", path, exc)

    if not machine_id:
        # Fallback to random UUID if no machine-id available (e.g. some containers)
        machine_id = str(uuid.uuid4())

    # We salt it so the raw machine-id isn't exposed
    instance_id = hashlib.sha256(f"{machine_id}-smsly-hosting".encode()).hexdigest()[:32]

    try:
        with open(id_file, 'w') as f:
            f.write(instance_id)
    except Exception:
        # If we can't write, just return it (it might change on restart if machine-id is not stable,
        # but inside Docker with persistent volume it should be okay or we rely on the file).
        pass

    return instance_id

def validate_license(license_obj=None):
    """
    Main entry point for license validation.
    Updates the PlatformLicense object state.
    """
    if not license_obj:
        license_obj = PlatformLicense.load()

    if not license_obj.license_key:
        # No key = Community
        _set_community_tier(license_obj)
        return

    # SECURITY: the online license path has been disabled. Trusting the
    # upstream ``license.smsly.cloud`` response — even over HTTPS — was
    # vulnerable to network-adjacent MITM (no cert pinning) and to a
    # compromised / hijacked origin. The offline path verifies an
    # RSA-signed payload against the platform's pinned public key, which
    # is the only safe trust anchor for tier / limit decisions.
    logger.critical("Online license path disabled for security; offline verification required.")

    # 1. Try Offline Validation
    try:
        if _validate_offline(license_obj):
            return
    except Exception as e:
        logger.error(f"Offline license validation failed: {e}")
        license_obj.validation_error = str(e)

    # If both fail, check grace period
    grace_days = int(getattr(settings, 'SMSLY_LICENSE_OFFLINE_GRACE_DAYS', 7))
    if license_obj.last_validated:
        if timezone.now() - license_obj.last_validated < timedelta(days=grace_days):
            # Still within grace period, keep current tier but mark invalid
            # Actually, we should probably mark it valid-ish or just log it?
            # Requirement says "Grace period: 7 days offline before downgrading"
            # So we don't downgrade yet.
            logger.info("License offline but within grace period.")
            return

    # Downgrade if all else fails
    logger.warning("Downgrading to Community tier due to validation failure.")
    _set_community_tier(license_obj, error="License validation failed and grace period expired.")

def _set_community_tier(license_obj, error=""):
    license_obj.tier = PlatformTier.COMMUNITY
    license_obj.is_valid = False
    license_obj.validation_error = error
    license_obj.max_services = 3
    license_obj.max_team_members = 1
    license_obj.expires_at = None
    license_obj.save()


def _validate_offline(license_obj):
    """
    Verify the RSA signature of the stored license_data.
    """
    if not license_obj.license_data:
        return False

    # Parse the stored data
    # Format expected: { "payload": "base64-json", "signature": "base64-signature" }
    try:
        data = json.loads(license_obj.license_data)
        payload_b64 = data.get('payload')
        signature_b64 = data.get('signature')

        if not payload_b64 or not signature_b64:
            raise ValueError("Invalid license data format")

        # Verify signature
        with open(PUBLIC_KEY_PATH, "rb") as key_file:
            public_key = serialization.load_pem_public_key(key_file.read())

        public_key.verify(
            base64.b64decode(signature_b64),
            base64.b64decode(payload_b64),
            padding.PKCS1v15(),
            hashes.SHA256()
        )

        # If verify passes, decode payload
        payload_json = json.loads(base64.b64decode(payload_b64))

        # Check instance ID
        if payload_json.get('instance_id') != get_instance_id():
            raise ValueError("License instance ID mismatch")

        # Check expiry
        if payload_json.get('expires_at'):
            expires_at = timezone.datetime.fromisoformat(payload_json['expires_at'].replace('Z', '+00:00'))
            if expires_at < timezone.now():
                raise ValueError("License expired")

        return _apply_license_payload(license_obj, payload_json)

    except (json.JSONDecodeError, ValueError, InvalidSignature) as e:
        logger.error(f"Offline validation error: {e}")
        raise e

def _apply_license_payload(license_obj, payload):
    """
    Apply the validated payload to the license object.
    """
    tier_value = payload.get('tier', PlatformTier.COMMUNITY)
    if tier_value not in PlatformTier.values:
        logger.warning("Rejecting license payload with invalid tier %r", tier_value)
        return False
    license_obj.tier = tier_value
    license_obj.licensed_to = payload.get('licensed_to', '')
    license_obj.instance_id = payload.get('instance_id', '')

    if payload.get('expires_at'):
        license_obj.expires_at = timezone.datetime.fromisoformat(payload['expires_at'].replace('Z', '+00:00'))
    else:
        license_obj.expires_at = None

    license_obj.max_services = payload.get('max_services', 3)
    license_obj.max_team_members = payload.get('max_team_members', 1)

    license_obj.is_valid = True
    license_obj.last_validated = timezone.now()
    license_obj.validation_error = ''
    license_obj.save()
    return True
