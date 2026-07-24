import django.test

from apps.deployments.views.replication import ReplicationDeploySerializer


class ReplicationHardeningTests(django.test.TestCase):
    def test_replication_password_required_and_not_default(self):
        payload = {
            "mesh_id": "00000000-0000-0000-0000-000000000000",
            "db_password": "strong-db",
            "admin_password": "strong-admin",
        }
        ser = ReplicationDeploySerializer(data=payload)
        self.assertFalse(ser.is_valid())
        self.assertIn("replication_password", ser.errors)

        payload["replication_password"] = "repl_pass"
        ser = ReplicationDeploySerializer(data=payload)
        self.assertFalse(ser.is_valid())
        self.assertIn("replication_password", ser.errors)

        payload["replication_password"] = "correct-horse-battery-staple"
        ser = ReplicationDeploySerializer(data=payload)
        self.assertTrue(ser.is_valid(), ser.errors)
