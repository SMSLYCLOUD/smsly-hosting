"""Addon logs: remotely-provisioned addons proxy the node, with masking."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

# Import first: apps.deployments.consumers.addon_log star-imports the
# module below, so importing it directly first would hit a partial-init
# cycle (AddonLogConsumer not yet defined). Loading the package first
# completes the star import cleanly.
import apps.deployments.consumers  # noqa: F401
from apps.addons.views.crud import AddonViewSet
from apps.deployments.models import Addon, Service
from apps.deployments.models.core import ManagedServer

User = get_user_model()


class RemoteAddonLogsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="addonrlog", password="x")
        self.node = ManagedServer.objects.create(
            name="node-1", host="10.0.0.9", is_primary=False, owner=self.owner,
        )
        self.service = Service.objects.create(
            name="addonrlogsvc", owner=self.owner, server=self.node)
        self.addon = Addon.objects.create(
            service=self.service, name="redis-r", addon_type="REDIS",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="redis://:pw@redis-r:6379/0")
        self.cname = f"smsly-addon-redis-{self.addon.id}"

    def _call(self, addon):
        return AddonViewSet._remote_addon_logs(None, addon, "cname-x", 200)

    def test_remote_logs_proxied_and_masked(self):
        raw = "READY redis://:node-secret-pw@10.0.0.9:6379/0 up"
        with mock.patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator"
        ) as orch:
            orch.return_value.get_container_logs.return_value = {
                "logs": raw, "status": "running"}
            # attach server relation for the resolver
            addon = Addon.objects.select_related(
                "service", "service__server").get(id=self.addon.id)
            resp = AddonViewSet._remote_addon_logs(
                None, addon, self.cname, 200)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["source"], "remote_node_container")
        self.assertNotIn("node-secret-pw", resp["logs"])
        self.assertIn(":***@", resp["logs"])
        orch.return_value.get_container_logs.assert_called_once_with(
            self.cname, tail=200)

    def test_empty_remote_falls_back_to_none(self):
        with mock.patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator"
        ) as orch:
            orch.return_value.get_container_logs.return_value = {
                "logs": "   ", "status": "running"}
            addon = Addon.objects.select_related(
                "service", "service__server").get(id=self.addon.id)
            self.assertIsNone(
                AddonViewSet._remote_addon_logs(None, addon, self.cname, 200))

    def test_primary_server_returns_none(self):
        primary = ManagedServer.objects.create(
            name="primary", host="10.0.0.10", is_primary=True, owner=self.owner)
        svc = Service.objects.create(
            name="addonrlocal", owner=self.owner, server=primary)
        addon = Addon.objects.create(
            service=svc, name="redis-l", addon_type="REDIS",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="redis://:pw@redis-l:6379/0")
        addon = Addon.objects.select_related(
            "service", "service__server").get(id=addon.id)
        with mock.patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator"
        ) as orch:
            self.assertIsNone(
                AddonViewSet._remote_addon_logs(None, addon, "cname-y", 200))
            orch.assert_not_called()

    def test_orchestrator_error_returns_none(self):
        with mock.patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator"
        ) as orch:
            orch.return_value.get_container_logs.side_effect = Exception("down")
            addon = Addon.objects.select_related(
                "service", "service__server").get(id=self.addon.id)
            self.assertIsNone(
                AddonViewSet._remote_addon_logs(None, addon, self.cname, 200))

    def test_remote_resolver_helper(self):
        from apps.addons.consumers.addon_log import (
            _resolve_addon_remote_server,
        )
        addon = Addon.objects.select_related(
            "service", "service__server").get(id=self.addon.id)
        server = _resolve_addon_remote_server(addon)
        self.assertIsNotNone(server)
        self.assertEqual(server.id, self.node.id)
