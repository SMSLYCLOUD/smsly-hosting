from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from apps.deployments.services.caddy_manager import apply_caddyfile

from apps.cloud.models import CloudProvider
from apps.deployments.models import EnvironmentVariable, Service
from apps.deployments.models.addons import Addon
from apps.deployments.services.provisioner import (
    server_connection_mode,
    server_install_mode,
)
from apps.addons.tasks.crud import provision_addon_task

User = get_user_model()


class NodeModeTopologyTests(SimpleTestCase):
    def test_non_primary_full_stack_server_uses_node_installer_mode(self):
        server = SimpleNamespace(is_lite_agent=False, is_primary=False)

        self.assertEqual(server_install_mode(server), "node")
        self.assertEqual(server_connection_mode(server), "full-stack-node")

    def test_primary_server_uses_default_master_installer_mode(self):
        server = SimpleNamespace(is_lite_agent=False, is_primary=True)

        self.assertEqual(server_install_mode(server), "master")
        self.assertEqual(server_connection_mode(server), "full-install")

    def test_missing_primary_flag_is_treated_as_non_primary_node(self):
        server = SimpleNamespace(is_lite_agent=False, is_primary=None)

        self.assertEqual(server_install_mode(server), "node")
        self.assertEqual(server_connection_mode(server), "full-stack-node")

    def test_lite_agent_keeps_agent_lite_installer_mode(self):
        server = SimpleNamespace(is_lite_agent=True, is_primary=False)

        self.assertEqual(server_install_mode(server), "agent-lite")
        self.assertEqual(server_connection_mode(server), "agent-lite")

    @patch.dict("os.environ", {"MODE": "node", "NODE_TYPE": "node"}, clear=False)
    def test_node_mode_skips_caddy_apply(self):
        result = apply_caddyfile("example.com {\n    reverse_proxy backend:8000\n}\n")

        self.assertTrue(result["ok"])
        self.assertIn("Caddy is not part of this node", result["message"])

    @patch.dict("os.environ", {"MODE": "node", "NODE_TYPE": "node"}, clear=False)
    def test_node_mode_skips_startup_caddy_sync(self):
        from apps.deployments.services import startup

        with patch("apps.deployments.services.startup._store_ssh_from_env"), patch(
            "apps.deployments.services.startup.threading.Thread"
        ) as thread_mock:
            startup._started = False
            startup.schedule_startup_caddy_sync()

        thread_mock.assert_not_called()


class AddonProvisioningDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="addon-node", password="password123")
        self.provider = CloudProvider.objects.create(
            name="Local Docker",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="addon-service",
            owner=self.user,
            provider=self.provider,
        )

    @patch(
        "apps.deployments.tasks.deployment.tasks_templates.addon_provisioner.provision_dispatch",
        return_value=("postgres-cid", "postgresql://u:p@db:5432/app"),
    )
    def test_provision_addon_task_uses_dispatch_and_persists_runtime_fields(self, dispatch_mock):
        addon = Addon.objects.create(
            service=self.service,
            name="postgres-main",
            addon_type=Addon.Type.POSTGRES,
        )

        provision_addon_task.run(str(addon.id))

        addon.refresh_from_db()
        self.assertEqual(addon.coolify_uuid, "postgres-cid")
        self.assertEqual(addon.connection_url, "postgresql://u:p@db:5432/app")
        self.assertEqual(addon.status, Addon.Status.ACTIVE)
        dispatch_mock.assert_called_once()
        self.assertEqual(dispatch_mock.call_args.args[0].id, addon.id)

        env = {item.key: item.value for item in EnvironmentVariable.objects.filter(service=self.service)}
        self.assertEqual(env["POSTGRES_URL"], "postgresql://u:p@db:5432/app")
