"""Tests for per-project registry credential auto-provisioning."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.core import Project
from apps.deployments.models.registry_scope import ScopedRegistry
from apps.deployments.services import registry_credentials as rc

User = get_user_model()


class ProjectRegistryCredentialsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reg-user", password="x")
        self.project = Project.objects.create(name="Reg Proj", owner=self.user)

    def test_username_is_stable_and_scoped(self):
        u1 = rc.project_registry_username(self.project.id)
        u2 = rc.project_registry_username(self.project.id)
        self.assertEqual(u1, u2)
        self.assertTrue(u1.startswith("proj-"))
        self.assertLessEqual(len(u1), 39)

    def test_ensure_creates_per_project_credentials_when_htpasswd_writable(self):
        with mock.patch.object(rc, "_htpasswd_writable", return_value=True), \
             mock.patch.object(rc, "upsert_htpasswd_user", return_value=True) as up:
            result = rc.ensure_project_registry_credentials(self.project)

        self.assertTrue(result["per_project"])
        self.assertTrue(result["username"].startswith("proj-"))
        self.assertTrue(result["password"])
        self.assertIn("registry:5000", result["urls"])
        up.assert_called_once()

        scoped = ScopedRegistry.get_for_object(self.project)
        self.assertIsNotNone(scoped)
        self.assertEqual(scoped.username, result["username"])
        self.assertEqual(scoped.password, result["password"])
        self.assertTrue(scoped.is_internal)
        self.assertIn("registry:5000", scoped.allowed_registry_hosts)

    def test_ensure_is_idempotent(self):
        with mock.patch.object(rc, "_htpasswd_writable", return_value=True), \
             mock.patch.object(rc, "upsert_htpasswd_user", return_value=True):
            first = rc.ensure_project_registry_credentials(self.project)
            second = rc.ensure_project_registry_credentials(self.project)

        # Same credential returned, not regenerated
        self.assertEqual(first["username"], second["username"])
        self.assertEqual(first["password"], second["password"])

    def test_falls_back_to_platform_creds_when_not_writable(self):
        with mock.patch.object(rc, "_htpasswd_writable", return_value=False):
            result = rc.ensure_project_registry_credentials(self.project)
        self.assertFalse(result["per_project"])
        # Falls back to the platform credential (smsly-registry default
        # when PlatformConfig has none)
        self.assertTrue(result["username"])

    def test_rotate_changes_password(self):
        with mock.patch.object(rc, "_htpasswd_writable", return_value=True), \
             mock.patch.object(rc, "upsert_htpasswd_user", return_value=True):
            first = rc.ensure_project_registry_credentials(self.project)
            rotated = rc.rotate_project_registry_credentials(self.project)

        self.assertTrue(rotated["ok"])
        self.assertEqual(rotated["username"], first["username"])
        self.assertNotEqual(rotated["password"], first["password"])

        scoped = ScopedRegistry.get_for_object(self.project)
        self.assertEqual(scoped.password, rotated["password"])

    def test_htpasswd_upsert_replaces_existing_line(self):
        lines = "smsly-registry:$2y$05$oldhash\nproj-aaaaaaaa:$2y$05$old\n"
        with mock.patch("builtins.open", mock.mock_open(read_data=lines)), \
             mock.patch("os.access", return_value=True), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("tempfile.mkstemp", return_value=(3, "/auth/.htpasswd-tmp")), \
             mock.patch("os.fdopen", mock.mock_open()), \
             mock.patch("os.replace"), mock.patch("os.chmod"), \
             mock.patch.object(rc, "_bcrypt_hash", return_value="$2y$10$newhash"):
            ok = rc.upsert_htpasswd_user("proj-aaaaaaaa", "newpass")

        self.assertTrue(ok)

    def test_remove_user_line(self):
        lines = "smsly-registry:$2y$05$keep\nproj-aaaaaaaa:$2y$05$drop\n"
        with mock.patch("builtins.open", mock.mock_open(read_data=lines)), \
             mock.patch("os.access", return_value=True), \
             mock.patch("os.path.exists", return_value=True), \
             mock.patch("tempfile.mkstemp", return_value=(3, "/auth/.htpasswd-tmp")), \
             mock.patch("os.fdopen", mock.mock_open()) as m_open, \
             mock.patch("os.replace"), mock.patch("os.chmod"):
            rc.remove_htpasswd_user("proj-aaaaaaaa")

        written = m_open().write.call_args[0][0]
        self.assertIn("smsly-registry:$2y$05$keep", written)
        self.assertNotIn("proj-aaaaaaaa:", written)
