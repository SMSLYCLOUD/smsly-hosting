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

    def test_mesh_addons_local_flag_applies_as_non_mesh(self):
        rows = [
            {"name": "redis", "addon_type": "REDIS",
             "connection_url": "redis://:pw@redis-svc:6379/0",
             "mesh_forward_port": None, "local": True},
        ]
        self.assertEqual(apply_mesh_addons(self.service, rows), 1)
        row = Addon.objects.get(service=self.service, name="redis")
        self.assertEqual(row.status, "ACTIVE")
        self.assertFalse(row.provider_metadata.get("mesh_backed"))
        self.assertEqual(
            row.connection_url, "redis://:pw@redis-svc:6379/0")
        # A later mesh row for the same name converts it to mesh-backed.
        mesh_rows = [
            {"name": "redis", "addon_type": "REDIS",
             "connection_url": "redis://:pw@10.100.0.1:24254/0",
             "mesh_forward_port": 24254},
        ]
        self.assertEqual(apply_mesh_addons(self.service, mesh_rows), 1)
        row.refresh_from_db()
        self.assertTrue(row.provider_metadata.get("mesh_backed"))

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

    def test_local_addon_preserves_master_id_and_container_name(self):
        import uuid
        from apps.addons.services.addon_provisioner import addon_container_name

        master_id = str(uuid.uuid4())
        rows = [
            {
                "id": master_id,
                "container_name": f"smsly-addon-postgres-{master_id}",
                "name": "postgres-smsly-helper",
                "addon_type": "POSTGRES",
                "connection_url": "postgresql://u:p@postgres-smsly-helper:5432/db",
                "mesh_forward_port": None,
                "local": True,
            },
        ]
        self.assertEqual(apply_mesh_addons(self.service, rows), 1)
        row = Addon.objects.get(service=self.service, name="postgres-smsly-helper")
        self.assertEqual(str(row.id), master_id)
        self.assertEqual(addon_container_name(row), f"smsly-addon-postgres-{master_id}")

    def test_local_addon_updates_existing_mismatched_row_container_name(self):
        import uuid
        from apps.addons.services.addon_provisioner import addon_container_name

        stale = Addon.objects.create(
            service=self.service,
            name="postgres-smsly-helper",
            addon_type="POSTGRES",
            connection_url="postgresql://u:p@old:5432/db",
            status="ACTIVE",
            provider_metadata={},
        )
        master_id = str(uuid.uuid4())
        expected_cname = f"smsly-addon-postgres-{master_id}"
        rows = [
            {
                "id": master_id,
                "container_name": expected_cname,
                "name": "postgres-smsly-helper",
                "addon_type": "POSTGRES",
                "connection_url": "postgresql://u:p@postgres-smsly-helper:5432/db",
                "mesh_forward_port": None,
                "local": True,
            },
        ]
        self.assertEqual(apply_mesh_addons(self.service, rows), 1)
        stale.refresh_from_db()
        self.assertEqual(addon_container_name(stale), expected_cname)
