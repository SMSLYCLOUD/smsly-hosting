import hashlib
import hmac
import time

import pytest
from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIRequestFactory
from django.contrib.auth import get_user_model

from apps.deployments.models_servers import ManagedServer
from apps.deployments.views_servers import _build_remote_headers
from apps.deployments.views_transfer import _verify_transfer_sync_hmac


TEST_SECRET = "nonce-test-secret-1234"


def _build_signed_request(request_factory, *, body=b"", source_ip="203.0.113.50",
                          nonce=None, method="POST", path="/api/v1/transfers/register-incoming/"):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    nonce_value = nonce if nonce is not None else "static-test-nonce"
    payload = f"{method}|{path}|{timestamp}|{body_hash}|{nonce_value}"
    signature = hmac.new(
        TEST_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    request = request_factory.post(path, data=body, content_type="json")
    request.META["HTTP_X_SMSLY_REMOTE_SYNC"] = "1"
    request.META["HTTP_X_GATEWAY_SIGNATURE_V2"] = signature
    request.META["HTTP_X_REQUEST_TIMESTAMP"] = timestamp
    request.META["HTTP_X_GATEWAY_NONCE"] = nonce_value
    request.META["REMOTE_ADDR"] = source_ip
    request._body = body
    return request, timestamp, nonce_value, body_hash


@pytest.mark.django_db(transaction=True)
class HmacNonceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="hmac_nonce", password="123"
        )
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="hmac-nonce-server",
            host="203.0.113.50",
            api_url="https://hmac-nonce.example.com",
            api_token="",
            gateway_secret=TEST_SECRET,
        )
        self.factory = APIRequestFactory()

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    def test_build_remote_headers_includes_nonce(self):
        headers = _build_remote_headers(self.server)
        self.assertIn("X-Gateway-Nonce", headers)
        self.assertTrue(headers["X-Gateway-Nonce"])

    def test_build_remote_headers_nonce_matches_signature_payload(self):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="payload-check",
            host="203.0.113.51",
            api_url="https://payload-check.example.com",
            api_token="",
            gateway_secret=TEST_SECRET,
        )
        try:
            path = "/api/v1/services/"
            body = b'{"hello":"world"}'
            headers = _build_remote_headers(
                server, method="POST", path=path, body=body
            )
            self.assertIn("X-Gateway-Nonce", headers)
            nonce = headers["X-Gateway-Nonce"]
            signature = headers.get("X-Gateway-Signature-V2", "")
            timestamp = headers.get("X-Request-Timestamp", "")

            self.assertTrue(signature)
            self.assertTrue(timestamp)

            body_hash = hashlib.sha256(body).hexdigest()
            expected_payload = f"POST|{path}|{timestamp}|{body_hash}|{nonce}"
            expected_signature = hmac.new(
                TEST_SECRET.encode(), expected_payload.encode(), hashlib.sha256
            ).hexdigest()
            self.assertTrue(
                hmac.compare_digest(expected_signature, signature),
                "Nonce was not included in the HMAC signing payload",
            )
        finally:
            server.delete()

    def test_verify_transfer_sync_hmac_rejects_missing_nonce(self):
        body = b'{"foo":"bar"}'
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"POST|/api/v1/transfers/register-incoming/|{timestamp}|{body_hash}"
        signature = hmac.new(
            TEST_SECRET.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

        request = self.factory.post(
            "/api/v1/transfers/register-incoming/",
            data=body,
            content_type="json",
        )
        request.META["HTTP_X_SMSLY_REMOTE_SYNC"] = "1"
        request.META["HTTP_X_GATEWAY_SIGNATURE_V2"] = signature
        request.META["HTTP_X_REQUEST_TIMESTAMP"] = timestamp
        request.META["REMOTE_ADDR"] = "203.0.113.50"
        request._body = body

        with patch(
            "apps.deployments.views_transfer._gateway_secret_candidates",
            return_value=[TEST_SECRET],
        ):
            self.assertFalse(_verify_transfer_sync_hmac(request, "203.0.113.50", body))

    def test_verify_transfer_sync_hmac_accepts_matching_nonce(self):
        body = b'{"foo":"bar"}'
        request, _, _, _ = _build_signed_request(
            self.factory, body=body, source_ip="203.0.113.50"
        )
        with patch(
            "apps.deployments.views_transfer._gateway_secret_candidates",
            return_value=[TEST_SECRET],
        ):
            self.assertTrue(_verify_transfer_sync_hmac(request, "203.0.113.50", body))

    def test_verify_transfer_sync_hmac_rejects_tampered_nonce(self):
        body = b'{"foo":"bar"}'
        request, timestamp, original_nonce, body_hash = _build_signed_request(
            self.factory, body=body, source_ip="203.0.113.50"
        )
        tampered_nonce = original_nonce + "-tampered"
        request.META["HTTP_X_GATEWAY_NONCE"] = tampered_nonce

        tampered_payload = (
            f"POST|/api/v1/transfers/register-incoming/|{timestamp}|{body_hash}|{original_nonce}"
        )
        original_signature = hmac.new(
            TEST_SECRET.encode(), tampered_payload.encode(), hashlib.sha256
        ).hexdigest()
        request.META["HTTP_X_GATEWAY_SIGNATURE_V2"] = original_signature

        with patch(
            "apps.deployments.views_transfer._gateway_secret_candidates",
            return_value=[TEST_SECRET],
        ):
            self.assertFalse(
                _verify_transfer_sync_hmac(request, "203.0.113.50", body)
            )
