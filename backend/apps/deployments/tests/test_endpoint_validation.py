"""Tests for endpoint URL validation on BackupSchedule + CloudStorageDestination."""
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cloud.models.backup import (
    BackupSchedule,
    validate_endpoint_url,
)
from apps.cloud.models.cloud_storage import CloudStorageDestination


class ValidateEndpointUrlTests(TestCase):
    def test_empty_url_is_allowed(self):
        self.assertIsNone(validate_endpoint_url(''))
        self.assertIsNone(validate_endpoint_url(None))

    def test_attacker_external_http_url_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_endpoint_url('http://attacker.example/')

    def test_https_r2_cloudflarestorage_is_ok(self):
        self.assertIsNone(
            validate_endpoint_url('https://r2.cloudflarestorage.com'),
        )

    def test_http_localhost_is_ok(self):
        self.assertIsNone(validate_endpoint_url('http://localhost:9000'))

    def test_http_127_0_0_1_is_ok(self):
        self.assertIsNone(validate_endpoint_url('http://127.0.0.1:9000'))

    def test_http_private_10_x_is_ok(self):
        self.assertIsNone(validate_endpoint_url('http://10.0.0.5:9000'))

    def test_http_private_192_168_x_is_ok(self):
        self.assertIsNone(validate_endpoint_url('http://192.168.1.10:9000'))

    def test_http_minio_internal_is_ok(self):
        self.assertIsNone(validate_endpoint_url('http://minio.internal:9000'))

    def test_http_smsly_named_host_is_ok(self):
        self.assertIsNone(
            validate_endpoint_url('http://smsly-storage.local:9000'),
        )

    def test_ftp_scheme_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_endpoint_url('ftp://files.example.com/')

    def test_gopher_scheme_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_endpoint_url('gopher://evil.example/')


class BackupScheduleEndpointValidationTests(TestCase):
    def _make(self, endpoint: str) -> BackupSchedule:
        from django.utils import timezone
        return BackupSchedule(
            s3_endpoint=endpoint,
            cron_expression='0 3 * * *',
            retention_days=7,
            enabled=True,
            last_run=timezone.now(),
            next_run=timezone.now(),
        )

    def test_http_attacker_endpoint_rejected_on_clean(self):
        schedule = self._make('http://attacker.example/')
        with self.assertRaises(ValidationError):
            schedule.full_clean()

    def test_https_endpoint_ok(self):
        schedule = self._make('https://r2.cloudflarestorage.com')
        schedule.full_clean()

    def test_http_localhost_endpoint_ok(self):
        schedule = self._make('http://localhost:9000')
        schedule.full_clean()

    def test_empty_endpoint_ok(self):
        schedule = self._make('')
        schedule.full_clean()

    def test_ftp_endpoint_rejected(self):
        schedule = self._make('ftp://files.example.com/')
        with self.assertRaises(ValidationError):
            schedule.full_clean()


class CloudStorageDestinationEndpointValidationTests(TestCase):
    def _make(self, endpoint: str) -> CloudStorageDestination:
        return CloudStorageDestination(
            name='Dest',
            provider='minio',
            bucket='b',
            region='us-east-1',
            endpoint=endpoint,
            access_key='a' * 32,
            secret_key='s' * 32,
        )

    def test_http_attacker_endpoint_rejected_on_clean(self):
        dest = self._make('http://attacker.example/')
        with self.assertRaises(ValidationError):
            dest.full_clean()

    def test_https_endpoint_ok(self):
        dest = self._make('https://r2.cloudflarestorage.com')
        dest.full_clean()

    def test_http_localhost_endpoint_ok(self):
        dest = self._make('http://localhost:9000')
        dest.full_clean()

    def test_empty_endpoint_ok(self):
        dest = self._make('')
        dest.full_clean()

    def test_ftp_endpoint_rejected(self):
        dest = self._make('ftp://files.example.com/')
        with self.assertRaises(ValidationError):
            dest.full_clean()


class BackupScheduleSerializerEndpointValidationTests(TestCase):
    def test_http_attacker_endpoint_rejected_by_serializer(self):
        from ..serializers import BackupScheduleSerializer
        serializer = BackupScheduleSerializer(data={'s3_endpoint': 'http://attacker.example/'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('s3_endpoint', serializer.errors)

    def test_https_endpoint_ok_by_serializer(self):
        from ..serializers import BackupScheduleSerializer
        serializer = BackupScheduleSerializer(data={'s3_endpoint': 'https://r2.cloudflarestorage.com'})
        self.assertTrue(serializer.is_valid())
        self.assertNotIn('s3_endpoint', serializer.errors)
