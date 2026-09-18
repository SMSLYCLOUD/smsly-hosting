# pylint: disable=invalid-name
"""Per-addon standby seeds must fix data-dir ownership.

Regression (2026-09-18): fresh named volumes are root-owned 0755 and
these seeds bypass the image entrypoint, so every standby seeded onto
a fresh volume crash-looped with "data directory has invalid
permissions". The shared-postgres standby proved it live; these three
per-addon seed paths carried the identical pattern.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.addons.services.addon_ha import SEED_OWNERSHIP_FIX, AddonHaManager


def _manager():
    return AddonHaManager(network_name="smsly-net")


class StandbySeedOwnershipTests(SimpleTestCase):
    def test_local_standby_seed_fixes_ownership(self):
        mgr = _manager()
        with patch.object(
            AddonHaManager, "_docker_run",
            return_value="abc123",
        ) as mock_run:
            mgr._run_postgres_standby(
                "x-ha-standby", "x", 5432, "repl", "pw")
        cmd = mock_run.call_args[0][0]
        flat = " ".join(cmd)
        self.assertIn("pg_basebackup", flat)
        self.assertIn("chown postgres:postgres /var/lib/postgresql/data", flat)
        self.assertIn("chmod 0700 /var/lib/postgresql/data", flat)

    def test_remote_standby_seed_fixes_ownership(self):
        mgr = _manager()
        ssh = MagicMock()
        ssh.exec_command.return_value = ("", "", 0)
        with patch.object(
            AddonHaManager, "_ssh_client", return_value=ssh,
        ):
            mgr._run_remote_postgres_standby(
                MagicMock(host="10.0.0.9"), "x-ha-standby",
                "10.0.0.9", 5433, "repl", "pw")
        sent = " ".join(
            c[0][0] for c in ssh.exec_command.call_args_list)
        self.assertIn("pg_basebackup", sent)
        self.assertIn("chown postgres:postgres /var/lib/postgresql/data", sent)
        self.assertIn("chmod 0700 /var/lib/postgresql/data", sent)

    def test_reseed_seed_fixes_ownership(self):
        mgr = _manager()
        addon = MagicMock()
        addon.id.hex = "ab" * 16
        addon.name = "postgres-x"
        addon.public_domain = None
        addon.connection_url = "postgresql://u:p@x:5432/db"
        with patch.object(
            AddonHaManager, "_docker_run", return_value="abc123",
        ) as mock_run, patch.object(
            AddonHaManager, "_move_alias_off",
        ), patch.object(
            AddonHaManager, "_assert_streaming",
        ), patch(
            "apps.addons.services.addon_provisioner.addon_provisioner",
        ):
            mgr.reseed_as_standby(addon, "x")
        cmd = mock_run.call_args[0][0]
        flat = " ".join(cmd)
        self.assertIn("pg_basebackup", flat)
        self.assertIn("chown postgres:postgres /var/lib/postgresql/data", flat)
        self.assertIn("chmod 0700 /var/lib/postgresql/data", flat)

    def test_ownership_fix_constant_covers_both(self):
        self.assertIn("chown postgres:postgres", SEED_OWNERSHIP_FIX)
        self.assertIn("chmod 0700", SEED_OWNERSHIP_FIX)
        self.assertIn("mindepth 1 -delete", SEED_OWNERSHIP_FIX)
