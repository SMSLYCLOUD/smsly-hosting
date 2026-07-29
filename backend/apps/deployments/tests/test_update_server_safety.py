import logging
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer


@pytest.mark.django_db(transaction=True)
class UpdateServerSafetyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="updatesrv_safety", password="123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        ManagedServer.objects.filter(owner=self.user).delete()
        self.user.delete()

    def _make_server(self, provision_status):
        return ManagedServer.objects.create(
            owner=self.user,
            name="upd-safety",
            host="203.0.113.70",
            api_url="https://upd-safety.example.com",
            api_token="tok",
            ssh_user="root",
            ssh_password="ssh-pass",
            provision_status=provision_status,
        )

    def test_update_server_with_pending_logs_warning(self):
        server = self._make_server(ManagedServer.ProvisionStatus.PENDING)
        url = f"/api/v1/servers/{server.id}/update-server/"

        with patch(
            "apps.deployments.services.provisioner.provision_server"
        ) as mock_provision:
            mock_provision.delay = MagicMock()
            with self.assertLogs("apps.deployments.views_servers", level="WARNING") as log_cm:
                resp = self.client.post(url)
            self.assertEqual(resp.status_code, 202)
            joined = "\n".join(log_cm.output)
            self.assertIn("PENDING", joined)
            self.assertIn(str(server.provision_status), joined.upper()) or self.assertIn("PENDING", joined)

    def test_update_server_with_done_does_not_log_warning(self):
        server = self._make_server(ManagedServer.ProvisionStatus.DONE)
        url = f"/api/v1/servers/{server.id}/update-server/"

        logger = logging.getLogger("apps.deployments.views_servers")
        original_level = logger.level
        logger.setLevel(logging.DEBUG)
        captured_records = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record):
                captured_records.append(record)

        handler = _CaptureHandler(level=logging.DEBUG)
        logger.addHandler(handler)
        try:
            with patch(
                "apps.deployments.services.provisioner.provision_server"
            ) as mock_provision:
                mock_provision.delay = MagicMock()
                resp = self.client.post(url)
            self.assertEqual(resp.status_code, 202)
            warnings = [r for r in captured_records if r.levelno >= logging.WARNING]
            self.assertEqual(warnings, [],
                             f"Did not expect warnings, got: {[r.getMessage() for r in warnings]}")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(original_level)

    def test_update_server_queues_celery_task(self):
        server = self._make_server(ManagedServer.ProvisionStatus.DONE)
        url = f"/api/v1/servers/{server.id}/update-server/"

        with patch(
            "apps.deployments.services.provisioner.provision_server"
        ) as mock_provision:
            mock_provision.delay = MagicMock()
            resp = self.client.post(url)
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(mock_provision.delay.called,
                        "provision_server.delay was not called")
        call_args = mock_provision.delay.call_args
        self.assertEqual(str(server.id), call_args[0][0])
        if len(call_args[0]) > 1:
            self.assertTrue(call_args[0][1] is True or call_args[1].get("skip_reboot") is True)
