"""Sleeper wake-path guard (2026-10-17).

Regression tests: all 9 sablier-enrolled services were sleeping into
503s with no wake attempt because their containers predated label
enrollment. The sleeper must never stop a container whose create-time
labels carry no sablier wake path — DB enrollment alone is not
sufficient. Redeploy enrolls; until then the service stays up.
"""
from unittest import mock

from django.test import SimpleTestCase

from apps.autoscaler.sleeper import _sleep_sablier_services, _wake_path_enrolled


def _container(labels=None):
    c = mock.Mock()
    c.status = "running"
    c.attrs = {"Config": {"Labels": dict(labels or {})}}
    return c


def _svc(name="svc-a"):
    svc = mock.Mock()
    svc.name = name
    svc.sablier_enabled = True
    svc.sablier_session = "10m"
    svc.server = None
    svc.active_target_type = ""
    return svc


def _run_one(svc, container):
    """Drive _sleep_sablier_services with everything else mocked idle."""
    client = mock.Mock()

    def _get(cname):
        if cname == svc.name:
            return container
        raise Exception(f"no container {cname}")

    client.containers.get.side_effect = _get
    with mock.patch(
        "apps.deployments.models.Service"
    ) as svc_model, mock.patch(
        "apps.deployments.models.Deployment"
    ) as dep_model, mock.patch(
        "apps.autoscaler.sleeper._service_is_idle", return_value=True
    ), mock.patch(
        "apps.deployments.services.internal_wake.service_has_mesh_sidecar",
        return_value=False,
    ):
        svc_model.objects.filter.return_value = [svc]
        dep_model.objects.filter.return_value.exists.return_value = False
        return _sleep_sablier_services(client), container


class WakePathEnrolledTests(SimpleTestCase):
    def test_enrolled(self):
        ok, why = _wake_path_enrolled(_container({
            "sablier.enable": "true", "sablier.group": "svc-a",
        }))
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_missing_enable(self):
        ok, why = _wake_path_enrolled(_container({"sablier.group": "svc-a"}))
        self.assertFalse(ok)
        self.assertIn("sablier.enable", why)

    def test_enable_not_true(self):
        ok, _ = _wake_path_enrolled(_container({
            "sablier.enable": "false", "sablier.group": "svc-a",
        }))
        self.assertFalse(ok)

    def test_missing_group(self):
        ok, why = _wake_path_enrolled(_container({"sablier.enable": "true"}))
        self.assertFalse(ok)
        self.assertIn("sablier.group", why)

    def test_blank_group(self):
        ok, _ = _wake_path_enrolled(_container({
            "sablier.enable": "true", "sablier.group": "  ",
        }))
        self.assertFalse(ok)

    def test_no_labels(self):
        ok, _ = _wake_path_enrolled(_container({}))
        self.assertFalse(ok)

    def test_broken_attrs_fail_closed(self):
        c = mock.Mock()
        c.status = "running"
        type(c).attrs = mock.PropertyMock(side_effect=RuntimeError("boom"))
        ok, _ = _wake_path_enrolled(c)
        self.assertFalse(ok)


class SleeperSkipsUnenrolledTests(SimpleTestCase):
    def test_unenrolled_container_is_skipped_not_stopped(self):
        report, container = _run_one(_svc(), _container({}))
        self.assertEqual(report["slept"], [])
        self.assertIn("svc-a", report["skipped"])
        self.assertIn("no wake path", report["skipped"]["svc-a"])
        container.stop.assert_not_called()

    def test_enrolled_container_still_sleeps(self):
        report, container = _run_one(_svc(), _container({
            "sablier.enable": "true", "sablier.group": "svc-a",
        }))
        self.assertEqual(report["slept"], ["svc-a"])
        container.stop.assert_called_once_with(timeout=30)
