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

def parse_offline_license_data(license_key: str, raw_license_data=None) -> str:
    """Extract an RSA-signed offline license JSON blob from request inputs.

    Supports:
    - Explicit ``license_data`` (dict or JSON string with ``payload`` & ``signature``)
    - Raw JSON string passed directly as ``license_key``
    - Dot-delimited ``<payload_b64>.<signature_b64>`` token
    - Base64/urlsafe-base64 encoded JSON string (optionally prefixed with ``smsly_offline_``)

    Returns a JSON string ``{"payload": ..., "signature": ...}`` or ``""`` if
    no offline signed structure could be parsed.
    """
    if raw_license_data is not None:
        if isinstance(raw_license_data, dict):
            if raw_license_data.get('payload') and raw_license_data.get('signature'):
                return json.dumps(raw_license_data)
            return ''
        if isinstance(raw_license_data, str) and raw_license_data.strip():
            return raw_license_data.strip()

    key = (license_key or '').strip()
    if not key:
        return ''

    if key.startswith('{'):
        try:
            parsed = json.loads(key)
            if isinstance(parsed, dict) and parsed.get('payload') and parsed.get('signature'):
                return key
        except (json.JSONDecodeError, ValueError):
            return ''

    if '.' in key:
        parts = key.split('.')
        if len(parts) == 2 and parts[0] and parts[1]:
            payload_b64, sig_b64 = parts
            try:
                padded = payload_b64 + '=' * (-len(payload_b64) % 4)
                decoded_payload = base64.urlsafe_b64decode(padded).decode('utf-8')
                payload_obj = json.loads(decoded_payload)
                if isinstance(payload_obj, dict):
                    return json.dumps({'payload': payload_b64, 'signature': sig_b64})
            except Exception:
                pass

    candidate = key[len('smsly_offline_'):] if key.startswith('smsly_offline_') else key
    try:
        padded = candidate + '=' * (-len(candidate) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(padded).decode('utf-8')
                parsed = json.loads(decoded)
                if isinstance(parsed, dict) and parsed.get('payload') and parsed.get('signature'):
                    return json.dumps(parsed)
            except Exception:
                continue
    except Exception:
        pass

    return ''


def validate_license(license_obj=None, force_fresh: bool = False):
    """
    Main entry point for license validation.
    Updates the PlatformLicense object state.

    When ``force_fresh=True`` (e.g. activating a newly submitted key), the
    offline grace period is bypassed so invalid or missing payloads fail
    closed immediately.
    """
    if not license_obj:
        license_obj = PlatformLicense.load()

    if not license_obj.license_key:
        # Preserve active, non-expired SaaS billing tiers activated without an offline key.
        if (
            not force_fresh
            and license_obj.tier != PlatformTier.COMMUNITY
            and license_obj.is_valid
            and (not license_obj.expires_at or license_obj.expires_at > timezone.now())
        ):
            return True
        # No key = Community
        _set_community_tier(license_obj)
        return False

    # SECURITY: the online license path has been disabled. Trusting the
    # upstream ``license.smsly.cloud`` response — even over HTTPS — was
    # vulnerable to network-adjacent MITM (no cert pinning) and to a
    # compromised / hijacked origin. The offline path verifies an
    # RSA-signed payload against the platform's pinned public key, which
    # is the only safe trust anchor for tier / limit decisions.
    logger.critical("Online license path disabled for security; offline verification required.")

    if not license_obj.license_data:
        parsed_data = parse_offline_license_data(license_obj.license_key)
        if parsed_data:
            license_obj.license_data = parsed_data

    # 1. Try Offline Validation
    validation_err = ""
    signature_invalid = False
    try:
        if _validate_offline(license_obj):
            return True
        validation_err = "Offline license validation failed."
    except InvalidSignature as e:
        logger.error(f"Offline license signature invalid: {e}")
        validation_err = "Invalid license signature."
        signature_invalid = True
    except Exception as e:
        logger.error(f"Offline license validation failed: {e}")
        validation_err = str(e)

    license_obj.validation_error = validation_err

    # Grace period only applies to previously valid, non-expired licenses during
    # routine checks (never on fresh activation or signature tampering).
    grace_days = int(getattr(settings, 'SMSLY_LICENSE_OFFLINE_GRACE_DAYS', 7))
    not_expired = not license_obj.expires_at or license_obj.expires_at > timezone.now()
    if (
        not force_fresh
        and not signature_invalid
        and license_obj.is_valid
        and not_expired
        and license_obj.last_validated
        and timezone.now() - license_obj.last_validated < timedelta(days=grace_days)
    ):
        logger.info("License offline but within grace period.")
        license_obj.save(update_fields=['validation_error'])
        return False

    # Downgrade if all else fails
    logger.warning("Downgrading to Community tier due to validation failure.")
    _set_community_tier(license_obj, error=validation_err or "License validation failed and grace period expired.")
    return False

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
        if not os.path.isfile(PUBLIC_KEY_PATH):
            raise FileNotFoundError(f"License public key missing at {PUBLIC_KEY_PATH}")
        with open(PUBLIC_KEY_PATH, "rb") as key_file:
            public_key = serialization.load_pem_public_key(key_file.read())

        public_key.verify(
            base64.b64decode(signature_b64),
            payload_b64.encode(),
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
            if expires_at <= timezone.now():
                raise ValueError("License expired")

        return _apply_license_payload(license_obj, payload_json)

    except (json.JSONDecodeError, ValueError, InvalidSignature, FileNotFoundError, OSError) as e:
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
