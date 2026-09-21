"""Remote-spawn gVisor extra_hosts mirror (no docker/SSH needed)."""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Addon, Service
from apps.deployments.services.spawning_service import (
    _gvisor_remote_extra_hosts,
)

User = get_user_model()


def _ssh_with(networks):
    """Mock ssh whose docker inspect returns the given networks map."""
    ssh = mock.MagicMock()

    def _exec(cmd, **kwargs):
        if "docker inspect" in cmd:
            return json.dumps(networks), "", 0
        return "", "", 0

    ssh.exec_command.side_effect = _exec
    return ssh


class RemoteExtraHostsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="rspawn", password="x")
        self.service = Service.objects.create(name="rspsvc", owner=self.user)

    def _addon(self, **kwargs):
        defaults = dict(
            service=self.service, name="pg", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u:pw@pg-host:5432/db")
        defaults.update(kwargs)
        return Addon.objects.create(**defaults)

    def test_dedicated_resolves_on_remote(self):
        self._addon()
        ssh = _ssh_with({"scoped": {"IPAddress": "10.9.0.7"}})
        flags = _gvisor_remote_extra_hosts(
            ssh, self.service, {"DATABASE_URL": "x://u@pg-host/db"}, "scoped")
        self.assertEqual(flags, " --add-host pg-host:10.9.0.7")

    def test_mesh_rewritten_names_skipped(self):
        # Hostname already replaced by a mesh IP in env: nothing to inject.
        self._addon()
        ssh = _ssh_with({"scoped": {"IPAddress": "10.9.0.7"}})
        flags = _gvisor_remote_extra_hosts(
            ssh, self.service, {"DATABASE_URL": "x://u@10.100.0.1:5433/db"},
            "scoped")
        self.assertEqual(flags, "")

    def test_shared_uses_shared_server(self):
        self._addon(provision_mode="shared",
                    connection_url="postgresql://u:pw@pg-shared:5432/db")
        ssh = _ssh_with({"scoped": {"IPAddress": "10.9.0.9"}})
        seen = []

        orig_exec = ssh.exec_command.side_effect

        def _capture(cmd, **kwargs):
            seen.append(cmd)
            return orig_exec(cmd, **kwargs)

        ssh.exec_command.side_effect = _capture
        flags = _gvisor_remote_extra_hosts(
            ssh, self.service, {"DATABASE_URL": "x://u@pg-shared/db"},
            "scoped")
        self.assertEqual(flags, " --add-host pg-shared:10.9.0.9")
        self.assertTrue(any("smsly-shared-postgres" in c for c in seen))
        self.assertFalse(any("smsly-addon-postgres" in c for c in seen))

    def test_missing_backend_skipped(self):
        self._addon()
        ssh = mock.MagicMock()
        ssh.exec_command.return_value = ("", "no such container", 1)
        flags = _gvisor_remote_extra_hosts(
            ssh, self.service, {"DATABASE_URL": "x://u@pg-host/db"}, "scoped")
        self.assertEqual(flags, "")

    def test_other_services_hostnames_never_injected(self):
        other_user = User.objects.create_user(username="rother", password="x")
        other_svc = Service.objects.create(name="rothersvc", owner=other_user)
        Addon.objects.create(
            service=other_svc, name="pgx", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            connection_url="postgresql://u:pw@pg-secret:5432/db")
        ssh = _ssh_with({"scoped": {"IPAddress": "10.9.0.7"}})
        flags = _gvisor_remote_extra_hosts(
            ssh, self.service, {"DATABASE_URL": "x://u@pg-secret/db"},
            "scoped")
        self.assertEqual(flags, "")
