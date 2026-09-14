"""Unit tests for the addon-alias guard beat task (no DB, no docker)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.tasks.infra.tasks_maintenance import (
    ensure_addon_network_aliases as task,
)


def _addon(url="redis://:pw@redis-shared:6379/0", addon_type="REDIS"):
    addon = MagicMock()
    addon.id = "addon-1"
    addon.name = "redis-shared"
    addon.addon_type = addon_type
    addon.connection_url = url
    addon.project = None
    addon.service = MagicMock()
    addon.service.project = None
    return addon


def _run(rows, run_side_effect):
    mock_addon_cls = MagicMock()
    (
        mock_addon_cls.objects.filter.return_value.select_related.return_value.iterator.return_value
    ) = rows
    with patch(
        "apps.deployments.models.addons.Addon", mock_addon_cls
    ), patch(
        "apps.deployments.tasks.infra.tasks_maintenance.subprocess.run",
        side_effect=run_side_effect,
    ):
        return task.run()


class TestEnsureAddonNetworkAliases(TestCase):
    def _inspect_result(self, aliases, rc=0):
        res = MagicMock()
        res.returncode = rc
        res.stdout = aliases
        res.stderr = ""
        return res

    def test_alias_present_no_repair(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return self._inspect_result("redis-shared 8992f019a214 ")

        res = _run([_addon()], fake_run)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["checked"], 1)
        self.assertEqual(res["repaired"], 0)
        # Only the inspect call, no connect.
        self.assertTrue(all("connect" not in c for c in calls))

    def test_missing_alias_repaired_with_hostname(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "inspect":
                return self._inspect_result("8992f019a214 ")
            ok = MagicMock()
            ok.returncode = 0
            ok.stdout = ""
            ok.stderr = ""
            return ok

        res = _run([_addon()], fake_run)
        self.assertEqual(res["repaired"], 1)
        connects = [c for c in calls if "connect" in c]
        self.assertEqual(len(connects), 1)
        self.assertIn("--alias", connects[0])
        self.assertIn("redis-shared", connects[0])

    def test_missing_container_skipped(self):
        def fake_run(cmd, **kwargs):
            return self._inspect_result("", rc=1)

        res = _run([_addon()], fake_run)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["checked"], 0)

    def test_empty_url_skipped(self):
        res = _run([_addon(url="")], lambda *a, **k: self.fail("no docker calls expected"))
        self.assertEqual(res["skipped"], 1)

    def test_repair_failure_counted(self):
        def fake_run(cmd, **kwargs):
            if cmd[1] == "inspect":
                return self._inspect_result("other-alias ")
            bad = MagicMock()
            bad.returncode = 1
            bad.stdout = ""
            bad.stderr = "Error: some daemon error"
            return bad

        res = _run([_addon()], fake_run)
        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["repaired"], 0)
