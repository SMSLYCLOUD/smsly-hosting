"""Test Audit Log Unit module."""
import hashlib
import json

from django.test import TestCase

from apps.deployments.models.audit import AuditLog


class AuditLogUnitTests(TestCase):
    def test_audit_log_hashing(self):
        log1 = AuditLog.objects.create(
            actor="test_user",
            action="CREATE",
            target="Service: test-app",
            metadata={"version": "1.0"}
        )

        self.assertEqual(log1.previous_hash, "0" * 64)

        expected_payload = {
            "prev": "0" * 64,
            "ts": str(log1.timestamp),
            "actor": "test_user",
            "user_id": log1.user_id,
            "project_id": log1.project_id,
            "action": "CREATE",
            "target": "Service: test-app",
            "meta": {"version": "1.0"}
        }
        expected_hash = hashlib.sha256(
            json.dumps(
                expected_payload,
                sort_keys=True).encode()).hexdigest()
        self.assertEqual(log1.hash, expected_hash)

    def test_audit_log_chaining(self):
        log1 = AuditLog.objects.create(actor="user1", action="A", target="T1")
        log2 = AuditLog.objects.create(actor="user2", action="B", target="T2")

        self.assertEqual(log2.previous_hash, log1.hash)
