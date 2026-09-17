"""Omitted deploy targets must not inherit stale server assignments.

The dashboard grid used to send the last deployment's target_server
(or cached node ids); services carry stale ManagedServer FKs (duplicate
auto-registered primaries). An omitted target on a primary-assigned
service must resolve local with no stored server — never drag the
stale record into the remote path or persist it onto the new row.
"""
from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models.core import ManagedServer
from apps.deployments.models import Service
from apps.deployments.views._helpers import _resolve_requested_deploy_target


def _request():
    request = MagicMock()
    request.data = {}
    return request


class ResolveRequestedDeployTargetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="target-user", password="x")
        self.provider = CloudProvider.objects.create(
            name="local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="target-svc",
            owner=self.user,
            provider=self.provider,
        )

    def _primary_server(self):
        return ManagedServer.objects.create(
            owner=self.user,
            name="Master Node",
            host="203.0.113.9",
            is_primary=True,
        )

    def test_omitted_target_with_primary_assignment_resolves_local(self):
        self.service.server = self._primary_server()
        self.service.save(update_fields=["server"])

        result = _resolve_requested_deploy_target(_request(), self.service)

        self.assertTrue(result["ok"])
        self.assertTrue(result["target_is_local"])
        self.assertIsNone(result["target_server"])
        self.assertIsNone(result["effective_server"])

    def test_omitted_target_without_server_resolves_local(self):
        result = _resolve_requested_deploy_target(_request(), self.service)

        self.assertTrue(result["ok"])
        self.assertTrue(result["target_is_local"])
        self.assertIsNone(result["target_server"])

    def test_omitted_target_with_remote_assignment_stays_remote(self):
        remote = ManagedServer.objects.create(
            owner=self.user,
            name="Worker EU",
            host="198.51.100.7",
            is_primary=False,
        )
        self.service.server = remote
        self.service.save(update_fields=["server"])

        result = _resolve_requested_deploy_target(_request(), self.service)

        self.assertTrue(result["ok"])
        self.assertFalse(result["target_is_local"])
        self.assertEqual(result["effective_server"], remote)
