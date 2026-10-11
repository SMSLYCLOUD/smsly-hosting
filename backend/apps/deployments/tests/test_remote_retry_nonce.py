"""Regression test: HMAC retry must mint a fresh nonce per attempt.

2026-10-11 outage: RemoteClientMixin._request built auth headers once
per mode and reused them across retry attempts. When the first attempt
reached the node but its response was lost (slow/stalled node), the
retry presented the same single-use nonce and the node rejected it as
a replay ("Remote sync nonce already used", HTTP 401) — masking the
real (transient) failure and failing the deploy.
"""
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.remote_orchestrator.manager import (
    RemoteOrchestrator,
)

User = get_user_model()


class HmacRetryNonceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nonce-retry", password="x")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="nonce-node",
            host="10.99.0.2",
            api_token="",
            gateway_secret="test-gateway-secret",
        )

    def test_retry_uses_fresh_nonce(self):
        client = RemoteOrchestrator(self.server)
        seen_nonces = []

        def fake_request(method, url, **kwargs):
            headers = kwargs.get("headers") or {}
            seen_nonces.append(headers.get("X-Request-Nonce"))
            if len(seen_nonces) == 1:
                raise requests.ConnectTimeout("node slow, response lost")
            response = mock.Mock()
            response.status_code = 200
            response.headers = {}
            response.text = "{}"
            return response

        with mock.patch(
            "apps.deployments.services.remote_orchestrator.client.requests.request",
            side_effect=fake_request,
        ), mock.patch.object(
            RemoteOrchestrator, "_candidate_base_urls",
            return_value=["http://10.99.0.2:8000"],
        ), mock.patch.object(
            RemoteOrchestrator, "_try_gateway_token_exchange",
            return_value=False,
        ), mock.patch.object(
            RemoteOrchestrator, "_filter_reachable",
            side_effect=lambda urls: list(urls),
        ):
            resp = client._request("GET", "/api/v1/services/", timeout=5)

        self.assertIsNotNone(resp)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(seen_nonces), 2)
        self.assertTrue(all(seen_nonces))
        self.assertNotEqual(seen_nonces[0], seen_nonces[1])
