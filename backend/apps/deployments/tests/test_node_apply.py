"""Node-apply helper tests (mesh_env / mesh addons / volumes)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.deployments.models.storage import Volume
from apps.deployments.services.remote_orchestrator.node_apply import (
    apply_mesh_addons,
    apply_mesh_env,
    apply_volumes,
)

User = get_user_model()


class NodeApplyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="node-apply-user", password="x")
        self.service = Service.objects.create(name="node-apply-svc", owner=self.user)

    def test_mesh_env_create_and_update(self):
        self.assertEqual(
            apply_mesh_env(self.service, {"A": "1", "B": "2"}), 2,
        )
        self.assertEqual(
            apply_mesh_env(self.service, {"A": "1-changed", "BAD": 123}), 1,
        )
        from apps.deployments.models import EnvironmentVariable
        self.assertEqual(
            EnvironmentVariable.objects.get(service=self.service, key="A").value,
            "1-changed",
        )
        self.assertFalse(
            EnvironmentVariable.objects.filter(service=self.service, key="BAD").exists(),
        )

    def test_mesh_addons_create_skip_local_update_mesh(self):
        rows = [
            {"name": "pg", "addon_type": "POSTGRES",
             "connection_url": "postgres://u:p@10.100.0.1:24111/db", "mesh_forward_port": 24111},
            {"name": "bad", "addon_type": "NOPE",
             "connection_url": "postgres://u:p@10.100.0.1:24111/db"},
            {"name": "empty", "addon_type": "REDIS", "connection_url": "  "},
        ]
        self.assertEqual(apply_mesh_addons(self.service, rows), 1)
        row = Addon.objects.get(service=self.service, name="pg")
        self.assertEqual(row.status, "ACTIVE")
        self.assertTrue(row.provider_metadata.get("mesh_backed"))
        # Local (non-mesh) row with same name wins — never overwritten.
        row.provider_metadata = {}
        row.connection_url = "postgres://u:p@local:5432/db"
        row.save(update_fields=["provider_metadata", "connection_url"])
        self.assertEqual(apply_mesh_addons(self.service, rows), 0)
        row.refresh_from_db()
        self.assertEqual(row.connection_url, "postgres://u:p@local:5432/db")
        # Mesh row updates in place.
        row.provider_metadata = {"mesh_backed": True}
        row.save(update_fields=["provider_metadata"])
        self.assertEqual(apply_mesh_addons(self.service, rows), 1)
        row.refresh_from_db()
        self.assertEqual(row.connection_url, "postgres://u:p@10.100.0.1:24111/db")

    def test_volumes_create_only_valid(self):
        rows = [
            {"name": "data", "mount_path": "/data", "size_gb": 5},
            {"name": "evil", "mount_path": "/var/run/docker.sock", "size_gb": 1},
            {"name": "huge", "mount_path": "/big", "size_gb": 99999},
            {"name": "", "mount_path": "/x", "size_gb": 1},
        ]
        self.assertEqual(apply_volumes(self.service, rows), 1)
        self.assertTrue(
            Volume.objects.filter(service=self.service, name="data").exists(),
        )
        self.assertFalse(
            Volume.objects.filter(service=self.service, name="evil").exists(),
        )
        # Existing rows never modified.
        vol = Volume.objects.get(service=self.service, name="data")
        vol.mount_path = "/srv/custom"
        vol.save(update_fields=["mount_path"])
        self.assertEqual(apply_volumes(self.service, rows), 0)
        vol.refresh_from_db()
        self.assertEqual(vol.mount_path, "/srv/custom")
