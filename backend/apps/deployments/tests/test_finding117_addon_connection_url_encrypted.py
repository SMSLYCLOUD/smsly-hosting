# pylint: disable=invalid-name
from django.contrib.auth import get_user_model
from django.test import TestCase
from encrypted_model_fields.fields import EncryptedCharField

from apps.deployments.models import Service
from apps.deployments.models.addons import Addon

User = get_user_model()


class Finding117AddonConnectionUrlEncryptedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="conn-enc-117", password="x",
        )
        self.service = Service.objects.create(
            name="conn-svc-117", owner=self.user,
        )

    def test_connection_url_field_is_encrypted_char_field(self):
        field = Addon._meta.get_field("connection_url")
        self.assertIsInstance(
            field, EncryptedCharField,
            "Addon.connection_url must be EncryptedCharField to keep "
            "DB-stored credentials at rest.",
        )

    def test_connection_url_round_trips_via_model(self):
        secret_url = "postgres://u:S3cret!@db.local:5432/appdb"
        addon = Addon.objects.create(
            service=self.service,
            name="pg-enc-117",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.ACTIVE,
            connection_url=secret_url,
        )
        addon.refresh_from_db()
        self.assertEqual(addon.connection_url, secret_url)
