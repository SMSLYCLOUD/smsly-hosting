"""gVisor hosts reconciler: drift detection + shared-backend resolution."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Addon, Service
from apps.deployments.tasks.infra.tasks_container_hygiene import (
    _backing_container_name,
    _hosts_drift,
    reconcile_gvisor_hosts_task,
)

User = get_user_model()


class HostsDriftTests(TestCase):
    def test_missing_entry_is_drift(self):
        self.assertEqual(
            _hosts_drift([], {"db": "10.0.0.5"}), {"db": "10.0.0.5"})

    def test_stale_ip_is_drift(self):
        self.assertEqual(
            _hosts_drift(["db:10.0.0.4"], {"db": "10.0.0.5"}),
            {"db": "10.0.0.5"})

    def test_matching_entry_is_clean(self):
        self.assertEqual(
            _hosts_drift(["db:10.0.0.5"], {"db": "10.0.0.5"}), {})

    def test_no_expected_ip_is_never_drift(self):
        # Backend down: no expected IP — churning would not help.
        self.assertEqual(_hosts_drift(["db:10.0.0.4"], {}), {})
        self.assertEqual(_hosts_drift([], {"db": ""}), {})

    def test_malformed_entries_ignored(self):
        self.assertEqual(
            _hosts_drift(["no-colon", ":10.0.0.5", "db:"], {"db": "10.0.0.5"}),
            {"db": "10.0.0.5"})


class BackingContainerTests(TestCase):
    def _addon(self, **kwargs):
        addon = mock.MagicMock()
        addon.id = "a1"
        addon.addon_type = "POSTGRES"
        addon.provision_mode = "shared"
        addon.pooler_routed = False
        for k, v in kwargs.items():
            setattr(addon, k, v)
        return addon

    def test_shared_resolves_to_shared_server(self):
        self.assertEqual(
            _backing_container_name(self._addon()), "smsly-shared-postgres")

    def test_pooler_routed_resolves_to_pooler(self):
        with mock.patch(
            "apps.addons.services.tenant_pooler.tenants_container_name",
            return_value="pooler-1",
        ):
            self.assertEqual(
                _backing_container_name(self._addon(pooler_routed=True)),
                "pooler-1")

    def test_dedicated_uses_canonical_name(self):
        addon = self._addon(provision_mode="container")
        addon.id = "abc"
        self.assertEqual(
            _backing_container_name(addon), "smsly-addon-postgres-abc")


class ReconcileTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="grecon", password="x")
        self.service = Service.objects.create(name="greconsvc", owner=self.user)

    def _addon(self):
        return Addon.objects.create(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="shared",
            connection_url="postgresql://u:pw@pg-alias:5432/db")

    def _sh(self, stdout="", returncode=0):
        m = mock.MagicMock()
        m.stdout = stdout
        m.returncode = returncode
        return m

    def _run_task(self, ps_out, inspect_map):
        import apps.deployments.tasks.infra.tasks_container_hygiene as mod

        def fake_sh(args, timeout=60):
            cmd = " ".join(args)
            if cmd.startswith("docker ps"):
                return self._sh(ps_out)
            for name, payload in inspect_map.items():
                if name in cmd:
                    return self._sh(payload)
            return self._sh("", returncode=1)

        with mock.patch.object(mod, "_sh", side_effect=fake_sh):
            with mock.patch(
                "apps.deployments.services.container_refresh"
                ".recreate_with_fresh_env",
                return_value={"container": "svc"},
            ) as mock_real:
                result = reconcile_gvisor_hosts_task.run(dry_run=True)
        return result, mock_real

    def test_drift_detected_in_dry_run(self):
        import json
        self._addon()
        insp = (
            "runsc\trunning\t"
            + json.dumps({"smsly.service_id": str(self.service.id)}) + "\t"
            + json.dumps([]) + "\t"
            + json.dumps({"net1": {}})
        )
        back = json.dumps({"net1": {"IPAddress": "10.0.0.9"}})
        # service lookup needs the service row to exist with this id
        result, mock_real = self._run_task(
            "svc-1\n",
            {"svc-1": insp,
             "smsly-shared-postgres": back},
        )
        # dry_run records the drift without recreating
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(
            result["skipped"][0]["drift"], {"pg-alias": "10.0.0.9"})
        mock_real.assert_not_called()

    def test_no_drift_no_action(self):
        import json
        self._addon()
        insp = (
            "runsc\trunning\t"
            + json.dumps({"smsly.service_id": str(self.service.id)}) + "\t"
            + json.dumps(["pg-alias:10.0.0.9"]) + "\t"
            + json.dumps({"net1": {}})
        )
        back = json.dumps({"net1": {"IPAddress": "10.0.0.9"}})
        result, mock_real = self._run_task(
            "svc-1\n",
            {"svc-1": insp,
             "smsly-shared-postgres": back},
        )
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["recreated"], [])
        mock_real.assert_not_called()

    def test_non_runsc_ignored(self):
        result, mock_real = self._run_task("plain-1\n", {})
        self.assertEqual(result["checked"], 0)
        mock_real.assert_not_called()
