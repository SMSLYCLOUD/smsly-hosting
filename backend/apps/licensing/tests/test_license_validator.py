# pylint: disable=invalid-name
"""Tests for the licensing validator's MITM-resistance contract.

The online license path (POSTing the key to ``license.smsly.cloud`` and
trusting whatever JSON came back) was disabled because the response was
not signed / cert-pinned and a network-adjacent attacker could flip
``tier`` / ``max_services``.  These tests pin the new contract:

* ``validate_license`` never makes an outbound HTTP call to the license
  server.  ``requests.post`` is mocked and asserted to be un-called.
* The offline path still accepts a correctly RSA-signed payload.
* A tampered / unsigned payload is rejected and the license falls back
  to the community tier instead of being silently promoted.
"""

import base64
import contextlib
import json
import tempfile
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.test import TestCase, override_settings

from apps.licensing import validator
from apps.licensing.models import PlatformLicense, PlatformTier


def _make_signed_license_data(private_key, instance_id):
    """Build a ``license_data`` blob that the offline verifier accepts.

    Mirrors the format the production license server emits:
    ``{"payload": "<b64-json>", "signature": "<b64-sig>"}`` where the
    signature is RSA-PKCS1v15-SHA256 over the *base64 payload string*
    (i.e. the same bytes ``public_key.verify`` will be called with).
    """
    payload_dict = {
        "license_id": "lic_test_abcd",
        "tier": "enterprise",
        "licensed_to": "test@example.com",
        "instance_id": instance_id,
        "max_services": -1,
        "max_team_members": -1,
        "features": ["ai", "autoscaler"],
        "expires_at": "2099-12-31T00:00:00+00:00",
    }
    payload_json = json.dumps(payload_dict)
    payload_b64 = base64.b64encode(payload_json.encode()).decode()
    signature = private_key.sign(
        payload_b64.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature).decode()
    return json.dumps({"payload": payload_b64, "signature": signature_b64})


def _gen_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_pem


class LicenseValidatorNoOnlinePathTests(TestCase):
    """The online license path must not be reachable from validate_license."""

    def setUp(self):
        PlatformLicense.objects.all().delete()

    def test_requests_post_is_never_called(self):
        # The online path was removed entirely, so even the ``requests``
        # import is gone from the validator.  Mock the module-level
        # symbol to assert validate_license never reaches for it.
        license_obj = PlatformLicense.load()
        license_obj.license_key = 'smsly_pro_whatever'
        license_obj.license_data = ''
        license_obj.save()

        fake_requests = mock.MagicMock()
        with mock.patch.dict('sys.modules', {'requests': fake_requests}), \
                mock.patch.object(validator, 'get_instance_id', return_value='instance-x'):
            validator.validate_license(license_obj)

        fake_requests.post.assert_not_called()

    def test_stub_server_is_never_invoked(self):
        # The dev stub used to be reachable through the online path; with
        # that path gone, importing it should not be triggered.
        license_obj = PlatformLicense.load()
        license_obj.license_key = 'smsly_pro_whatever'
        license_obj.license_data = ''
        license_obj.save()

        with mock.patch('apps.licensing.stub_server.validate_stub') as mock_stub, \
                mock.patch.object(validator, 'get_instance_id', return_value='instance-x'):
            validator.validate_license(license_obj)

        mock_stub.assert_not_called()


class LicenseValidatorOfflineSignatureTests(TestCase):
    """The offline path is the only trust anchor and must verify signatures."""

    def setUp(self):
        PlatformLicense.objects.all().delete()
        self.private_key, public_pem = _gen_keypair()
        self._pubkey_file = tempfile.NamedTemporaryFile(
            delete=False, suffix='.pem', prefix='public_'
        )
        self._pubkey_file.write(public_pem)
        self._pubkey_file.flush()
        self._pubkey_file.close()

    def tearDown(self):
        import os
        with contextlib.suppress(OSError):
            os.unlink(self._pubkey_file.name)

    def _license(self, key='smsly_ent_abcdef'):
        obj = PlatformLicense.load()
        obj.license_key = key
        obj.save()
        return obj

    def test_valid_signed_payload_promotes_license(self):
        instance_id = 'instance-test-1234'
        license_obj = self._license()
        license_obj.license_data = _make_signed_license_data(
            self.private_key, instance_id
        )
        license_obj.save()

        with mock.patch.object(validator, 'PUBLIC_KEY_PATH', self._pubkey_file.name), \
                mock.patch.object(validator, 'get_instance_id', return_value=instance_id):
            validator.validate_license(license_obj)

        license_obj.refresh_from_db()
        self.assertTrue(license_obj.is_valid)
        self.assertEqual(license_obj.tier, PlatformTier.ENTERPRISE)
        self.assertEqual(license_obj.max_services, -1)
        self.assertEqual(license_obj.max_team_members, -1)

    def test_tampered_payload_with_wrong_signature_is_rejected(self):
        # A different key signs the payload, so the verifier should reject it.
        other_private_key, _ = _gen_keypair()
        instance_id = 'instance-test-1234'
        license_obj = self._license()
        license_obj.license_data = _make_signed_license_data(
            other_private_key, instance_id
        )
        license_obj.save()

        with mock.patch.object(validator, 'PUBLIC_KEY_PATH', self._pubkey_file.name), \
                mock.patch.object(validator, 'get_instance_id', return_value=instance_id):
            validator.validate_license(license_obj)

        license_obj.refresh_from_db()
        self.assertFalse(license_obj.is_valid)
        # Should be downgraded to community (no valid signature → no license).
        self.assertEqual(license_obj.tier, PlatformTier.COMMUNITY)
        self.assertEqual(license_obj.max_services, 3)
        self.assertEqual(license_obj.max_team_members, 1)

    def test_unsigned_payload_is_rejected(self):
        # Build a license_data blob with NO signature field at all.
        payload_dict = {
            "tier": "enterprise",
            "licensed_to": "attacker@example.com",
            "instance_id": "instance-test-1234",
            "max_services": -1,
            "max_team_members": -1,
            "expires_at": "2099-12-31T00:00:00+00:00",
        }
        payload_b64 = base64.b64encode(json.dumps(payload_dict).encode()).decode()
        license_obj = self._license()
        license_obj.license_data = json.dumps({"payload": payload_b64})
        license_obj.save()

        with mock.patch.object(validator, 'PUBLIC_KEY_PATH', self._pubkey_file.name), \
                mock.patch.object(validator, 'get_instance_id', return_value='instance-test-1234'):
            validator.validate_license(license_obj)

        license_obj.refresh_from_db()
        self.assertFalse(license_obj.is_valid)
        self.assertEqual(license_obj.tier, PlatformTier.COMMUNITY)
        self.assertEqual(license_obj.max_services, 3)

    @override_settings(SMSLY_LICENSE_OFFLINE_GRACE_DAYS=0)
    def test_attacker_cannot_exfiltrate_via_unsigned_blob(self):
        # This is the regression: even with an "ENTERPRISE" payload and no
        # signature, validate_license must NOT promote the license.
        payload_dict = {
            "tier": "ENTERPRISE",
            "max_services": -1,
            "max_team_members": -1,
        }
        payload_b64 = base64.b64encode(json.dumps(payload_dict).encode()).decode()
        license_obj = self._license()
        license_obj.license_data = json.dumps({"payload": payload_b64})
        license_obj.save()

        with mock.patch.object(validator, 'PUBLIC_KEY_PATH', self._pubkey_file.name), \
                mock.patch.object(validator, 'get_instance_id', return_value='anything'):
            validator.validate_license(license_obj)

        license_obj.refresh_from_db()
        self.assertNotEqual(license_obj.tier, PlatformTier.ENTERPRISE)
        self.assertNotEqual(license_obj.max_services, -1)
