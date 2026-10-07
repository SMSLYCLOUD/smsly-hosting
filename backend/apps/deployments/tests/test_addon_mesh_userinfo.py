"""Mesh URL rewrite must preserve password-only userinfo (redis-style).

Regression: rewrite_env_for_mesh / mesh_url_for_addon gated userinfo on
``parsed.username``, silently dropping the password from
``redis://:pass@host:6379`` URLs. Node-side containers then failed auth
against password-protected redis through the mesh forwarder.
"""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from apps.deployments.services.addon_mesh import (
    _mesh_netloc,
    mesh_url_for_addon,
    rewrite_env_for_mesh,
)
from urllib.parse import urlparse

_MIP, _PORT = "10.100.0.1", 24254


def _addon(url):
    return SimpleNamespace(id="2d6f9ad5-a30f-40da-9e44-72e4df649ab4", connection_url=url)


class MeshUserinfoTests(TestCase):
    def test_password_only_preserved(self):
        netloc = _mesh_netloc(urlparse("redis://:s3cret@redis-alias:6379/0"), _MIP, _PORT)
        self.assertEqual(netloc, f":s3cret@{_MIP}:{_PORT}")

    def test_user_and_password_preserved(self):
        netloc = _mesh_netloc(urlparse("postgres://u:p@pg-alias:5432/db"), _MIP, _PORT)
        self.assertEqual(netloc, f"u:p@{_MIP}:{_PORT}")

    def test_user_only_preserved(self):
        netloc = _mesh_netloc(urlparse("amqp://appuser@mq-alias:5672/"), _MIP, _PORT)
        self.assertEqual(netloc, f"appuser@{_MIP}:{_PORT}")

    def test_no_auth_stays_bare(self):
        netloc = _mesh_netloc(urlparse("http://alias:8080/x"), _MIP, _PORT)
        self.assertEqual(netloc, f"{_MIP}:{_PORT}")

    @patch(
        "apps.deployments.services.addon_mesh.ensure_addon_mesh_forward",
        return_value=(_MIP, _PORT),
    )
    def test_mesh_url_for_addon_password_only(self, _fwd):
        out = mesh_url_for_addon(_addon("redis://:s3cret@redis-alias:6379/0"))
        self.assertEqual(out, f"redis://:s3cret@{_MIP}:{_PORT}/0")

    @patch(
        "apps.deployments.services.addon_mesh.ensure_addon_mesh_forward",
        return_value=(_MIP, _PORT),
    )
    def test_rewrite_env_for_mesh_password_only(self, _fwd):
        addon = _addon("redis://:s3cret@redis-alias:6379/0")
        service = SimpleNamespace(
            addons=SimpleNamespace(exclude=lambda **kw: [addon]),
            env_vars=SimpleNamespace(all=lambda: [
                SimpleNamespace(key="REDIS_URL", value="redis://:s3cret@redis-alias:6379/0"),
                SimpleNamespace(key="CELERY_BROKER_URL", value="redis://:s3cret@redis-alias:6379/0"),
                SimpleNamespace(key="OTHER", value="https://example.com/x"),
            ]),
        )
        out = rewrite_env_for_mesh(service)
        self.assertEqual(out["REDIS_URL"], f"redis://:s3cret@{_MIP}:{_PORT}/0")
        self.assertEqual(out["CELERY_BROKER_URL"], f"redis://:s3cret@{_MIP}:{_PORT}/0")
        self.assertNotIn("OTHER", out)
