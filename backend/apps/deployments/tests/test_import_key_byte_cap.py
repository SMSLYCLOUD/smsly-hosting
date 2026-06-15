from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


User = get_user_model()


class ImportKeyByteCapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="import-key-cap",
            email="cap@example.com",
            password="pw",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_oversized_key_material_rejected(self):
        with patch(
            "apps.deployments.services.backup_service.BackupService.import_backup_key"
        ) as mock_imp:
            mock_imp.return_value = {
                "key_id": "abcdef01", "fingerprint": "fp",
                "source": "IMPORTED", "created": True,
            }
            resp = self.client.post(
                "/api/v1/backups/import-key/",
                {"key_id": "abcdef01", "key_material": "x" * 5000},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("4096", str(resp.data))
        mock_imp.assert_not_called()

    def test_undersized_key_material_accepted(self):
        with patch(
            "apps.deployments.services.backup_service.BackupService.import_backup_key"
        ) as mock_imp:
            mock_imp.return_value = {
                "key_id": "abcdef02", "fingerprint": "fp",
                "source": "IMPORTED", "created": True,
            }
            resp = self.client.post(
                "/api/v1/backups/import-key/",
                {"key_id": "abcdef02", "key_material": "x" * 100},
                format="json",
            )
        self.assertIn(resp.status_code, (200, 201))
        mock_imp.assert_called_once()

    def test_empty_key_material_rejected(self):
        with patch(
            "apps.deployments.services.backup_service.BackupService.import_backup_key"
        ) as mock_imp:
            resp = self.client.post(
                "/api/v1/backups/import-key/",
                {"key_id": "abcdef03", "key_material": ""},
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_imp.assert_not_called()
