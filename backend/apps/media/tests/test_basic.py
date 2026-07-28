from django.test import TestCase

from apps.media.models import AttestationAuditLog


class AttestationAuditLogTests(TestCase):
    def _make_server(self):
        from apps.deployments.models import ManagedServer
        return ManagedServer.objects.create(
            name="test-node",
            host="10.0.0.1",
            status="online",
        )

    def test_create_audit_log(self):
        server = self._make_server()
        log = AttestationAuditLog.objects.create(
            server=server,
            event_type=AttestationAuditLog.EventType.STAMP_GENERATED,
            trust_score=0.95,
        )
        self.assertIsNotNone(log.pk)
        self.assertEqual(log.event_type, "stamp_generated")

    def test_str(self):
        server = self._make_server()
        log = AttestationAuditLog.objects.create(
            server=server,
            event_type=AttestationAuditLog.EventType.TAMPER_DETECTED,
        )
        result = str(log)
        self.assertIn("tamper_detected", result)
        self.assertIn(str(server.id), result)

    def test_event_type_choices(self):
        server = self._make_server()
        for event_type in AttestationAuditLog.EventType:
            log = AttestationAuditLog.objects.create(
                server=server,
                event_type=event_type,
            )
            self.assertEqual(log.event_type, event_type)
